"""Response parsing utilities for structured, JSON, and constrained text outputs."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from json import JSONDecodeError
from typing import cast

from yomi_daemon.protocol import (
    ActionDecision,
    DecisionRequest,
    FallbackReason,
    JsonObject,
    JsonValue,
    ProtocolModel,
)
from yomi_daemon.validation import (
    DecisionValidationError,
    ProtocolValidationError,
    validate_action_decision_for_request,
)


class ParseSource(StrEnum):
    STRUCTURED = "structured"
    JSON = "json"
    TEXT = "text"


class ResponseParsingError(ValueError):
    """Raised when a provider response cannot be parsed into a valid decision."""

    def __init__(
        self,
        message: str,
        *,
        fallback_reason: FallbackReason,
        location: tuple[str | int, ...] = (),
        source: ParseSource | None = None,
    ) -> None:
        super().__init__(message)
        self.fallback_reason = fallback_reason
        self.location = location
        self.source = source


CorrectionCallback = Callable[[str], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class ResponseParsingConfig:
    enable_correction_retry: bool = False
    max_correction_retries: int = 0

    @classmethod
    def from_policy_options(
        cls, options: Mapping[str, object] | None = None
    ) -> "ResponseParsingConfig":
        if options is None:
            return cls()

        parser_options_raw = options.get("response_parser", {})
        if parser_options_raw is None:
            return cls()
        if not isinstance(parser_options_raw, Mapping):
            raise TypeError("policy options.response_parser must be an object")

        parser_options = cast(Mapping[str, object], parser_options_raw)
        enable_retry_raw = parser_options.get("enable_correction_retry", False)
        if not isinstance(enable_retry_raw, bool):
            raise TypeError(
                "policy options.response_parser.enable_correction_retry must be a boolean"
            )
        enable_retry = enable_retry_raw

        max_retries_raw = parser_options.get(
            "max_correction_retries",
            1 if enable_retry else 0,
        )
        if isinstance(max_retries_raw, bool) or not isinstance(max_retries_raw, int):
            raise TypeError(
                "policy options.response_parser.max_correction_retries must be an integer"
            )
        if max_retries_raw < 0 or max_retries_raw > 1:
            raise ValueError("policy options.response_parser.max_correction_retries must be 0 or 1")

        return cls(
            enable_correction_retry=enable_retry and max_retries_raw > 0,
            max_correction_retries=max_retries_raw if enable_retry else 0,
        )


@dataclass(frozen=True, slots=True)
class ParsedActionDecision:
    decision: ActionDecision
    source: ParseSource
    correction_attempts: int = 0


def parse_action_decision_response(
    raw_response: object,
    request: DecisionRequest,
) -> ParsedActionDecision:
    """Parse one raw model response into a request-validated ActionDecision."""

    if isinstance(raw_response, ProtocolModel):
        return _parse_mapping_candidate(
            raw_response.to_dict(),
            request,
            source=ParseSource.STRUCTURED,
        )
    if isinstance(raw_response, Mapping):
        return _parse_mapping_candidate(
            cast(Mapping[str, object], raw_response),
            request,
            source=ParseSource.STRUCTURED,
        )
    if isinstance(raw_response, str):
        json_candidate = _extract_json_object(raw_response)
        if json_candidate is not None:
            try:
                return _parse_mapping_candidate(
                    json_candidate,
                    request,
                    source=ParseSource.JSON,
                )
            except ResponseParsingError as exc:
                if exc.fallback_reason is FallbackReason.STALE_RESPONSE:
                    raise

        return _parse_mapping_candidate(
            _extract_text_mapping(raw_response, request),
            request,
            source=ParseSource.TEXT,
        )

    raise ResponseParsingError(
        f"unsupported provider response type {type(raw_response)!r}",
        fallback_reason=FallbackReason.MALFORMED_OUTPUT,
    )


async def parse_action_decision_with_correction(
    raw_response: object,
    request: DecisionRequest,
    *,
    correction_callback: CorrectionCallback | None = None,
    config: ResponseParsingConfig | None = None,
) -> ParsedActionDecision:
    """Parse a response and optionally perform one correction retry on invalid output."""

    resolved_config = config or ResponseParsingConfig()
    try:
        return parse_action_decision_response(raw_response, request)
    except ResponseParsingError as exc:
        if not _can_retry(exc, correction_callback, resolved_config):
            raise

        assert correction_callback is not None
        correction_prompt = build_correction_prompt(request, exc)
        corrected_response = await correction_callback(correction_prompt)
        parsed = parse_action_decision_response(corrected_response, request)
        return ParsedActionDecision(
            decision=parsed.decision,
            source=parsed.source,
            correction_attempts=1,
        )


def build_correction_prompt(request: DecisionRequest, error: ResponseParsingError) -> str:
    """Create a bounded correction prompt for one retry attempt."""

    legal_actions = ", ".join(action.action for action in request.legal_actions)
    return (
        "Previous response could not be applied.\n"
        f"Failure reason: {error}.\n"
        "Return ONLY a JSON object for ActionDecision.\n"
        f'Use "match_id": "{request.match_id}" and "turn_id": {request.turn_id}.\n'
        f'Choose "action" from: {legal_actions}.\n'
        'Always include "data" (use null when unsure) and "extra".\n'
        "Match any structured payload_spec or prediction_spec exactly, including required keys and bounds.\n"
        'Use "extra": {"di": null, "feint": false, "reverse": false, "prediction": null} '
        "unless the chosen action explicitly supports those extras.\n"
    )


def _can_retry(
    error: ResponseParsingError,
    correction_callback: CorrectionCallback | None,
    config: ResponseParsingConfig,
) -> bool:
    if correction_callback is None:
        return False
    if not config.enable_correction_retry or config.max_correction_retries < 1:
        return False
    return error.fallback_reason in {
        FallbackReason.MALFORMED_OUTPUT,
        FallbackReason.ILLEGAL_OUTPUT,
    }


def _parse_mapping_candidate(
    candidate: Mapping[str, object],
    request: DecisionRequest,
    *,
    source: ParseSource,
) -> ParsedActionDecision:
    normalized = _normalize_decision_mapping(candidate, request)
    try:
        decision = ActionDecision.from_dict(normalized)
        validate_action_decision_for_request(request, decision)
    except DecisionValidationError as exc:
        raise ResponseParsingError(
            str(exc),
            fallback_reason=exc.fallback_reason,
            location=exc.location,
            source=source,
        ) from exc
    except (ProtocolValidationError, TypeError, ValueError) as exc:
        raise ResponseParsingError(
            str(exc),
            fallback_reason=FallbackReason.MALFORMED_OUTPUT,
            source=source,
        ) from exc

    return ParsedActionDecision(decision=decision, source=source)


def _normalize_decision_mapping(
    candidate: Mapping[str, object],
    request: DecisionRequest,
) -> JsonObject:
    normalized = _coerce_json_object(candidate)
    normalized.setdefault("match_id", request.match_id)
    normalized.setdefault("turn_id", request.turn_id)
    normalized.setdefault("data", None)

    extra_raw = normalized.get("extra")
    extra_mapping: JsonObject
    if extra_raw is None:
        extra_mapping = {}
    elif isinstance(extra_raw, Mapping):
        extra_mapping = _coerce_json_object(cast(Mapping[str, object], extra_raw))
    else:
        raise ResponseParsingError(
            "action_decision.extra must be an object",
            fallback_reason=FallbackReason.MALFORMED_OUTPUT,
            location=("extra",),
        )

    extra_mapping.setdefault("di", None)
    extra_mapping.setdefault("feint", False)
    extra_mapping.setdefault("reverse", False)
    extra_mapping.setdefault("prediction", None)

    # Silently strip prediction if the chosen action doesn't support it.
    # LLMs frequently include prediction even when the action has supports.prediction=false.
    action_name = normalized.get("action")
    if action_name and extra_mapping.get("prediction") is not None:
        for la in request.legal_actions:
            if la.action == action_name and not la.supports.prediction:
                extra_mapping["prediction"] = None
                break

    normalized["extra"] = extra_mapping
    return normalized


def _extract_json_object(text: str) -> Mapping[str, object] | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
        except JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            return cast(Mapping[str, object], parsed)
    return None


def _extract_text_mapping(text: str, request: DecisionRequest) -> JsonObject:
    stripped = text.strip()
    if not stripped:
        raise ResponseParsingError(
            "provider response was empty",
            fallback_reason=FallbackReason.MALFORMED_OUTPUT,
            source=ParseSource.TEXT,
        )

    if stripped in {action.action for action in request.legal_actions}:
        return {"action": stripped}

    segments = [
        segment.strip()
        for line in stripped.splitlines()
        for segment in line.split(";")
        if segment.strip()
    ]
    parsed: JsonObject = {}

    for segment in segments:
        key, separator, raw_value = segment.partition(":")
        if not separator:
            key, separator, raw_value = segment.partition("=")
        if not separator:
            continue

        normalized_key = key.strip().lower()
        value = raw_value.strip()
        if normalized_key in {"action", "match_id", "policy_id", "notes", "reasoning"}:
            parsed[normalized_key] = _parse_text_scalar(value)
            continue
        if normalized_key in {"turn_id", "latency_ms", "tokens_in", "tokens_out"}:
            parsed[normalized_key] = _parse_text_int(value, key=normalized_key)
            continue
        if normalized_key == "data":
            parsed["data"] = _parse_text_json(value, key="data")
            continue
        if normalized_key == "extra":
            parsed["extra"] = _parse_text_json(value, key="extra")
            continue
        if normalized_key == "di":
            parsed.setdefault("extra", {})
            cast(JsonObject, parsed["extra"])["di"] = _parse_text_di(value)
            continue
        if normalized_key in {"feint", "reverse"}:
            parsed.setdefault("extra", {})
            cast(JsonObject, parsed["extra"])[normalized_key] = _parse_text_bool(
                value,
                key=normalized_key,
            )

    if "action" not in parsed:
        raise ResponseParsingError(
            "provider response did not contain an action field",
            fallback_reason=FallbackReason.MALFORMED_OUTPUT,
            location=("action",),
            source=ParseSource.TEXT,
        )
    return parsed


def _parse_text_scalar(value: str) -> str:
    if value.startswith(('"', "'")) and value.endswith(('"', "'")) and len(value) >= 2:
        return value[1:-1]
    return value


def _parse_text_int(value: str, *, key: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ResponseParsingError(
            f"{key} must be an integer",
            fallback_reason=FallbackReason.MALFORMED_OUTPUT,
            location=(key,),
            source=ParseSource.TEXT,
        ) from exc


def _parse_text_json(value: str, *, key: str) -> JsonObject | None:
    try:
        parsed = json.loads(value)
    except JSONDecodeError as exc:
        raise ResponseParsingError(
            f"{key} must be valid JSON",
            fallback_reason=FallbackReason.MALFORMED_OUTPUT,
            location=(key,),
            source=ParseSource.TEXT,
        ) from exc
    if parsed is None:
        return None
    if not isinstance(parsed, Mapping):
        raise ResponseParsingError(
            f"{key} must decode to a JSON object or null",
            fallback_reason=FallbackReason.MALFORMED_OUTPUT,
            location=(key,),
            source=ParseSource.TEXT,
        )
    return _coerce_json_object(cast(Mapping[str, object], parsed))


def _parse_text_di(value: str) -> JsonObject | None:
    if value.lower() == "null":
        return None
    if value.startswith("{"):
        parsed = _parse_text_json(value, key="di")
        return cast(JsonObject, parsed)

    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        raise ResponseParsingError(
            "di must be null, a JSON object, or an 'x,y' pair",
            fallback_reason=FallbackReason.MALFORMED_OUTPUT,
            location=("extra", "di"),
            source=ParseSource.TEXT,
        )
    return {
        "x": _parse_text_int(parts[0], key="di.x"),
        "y": _parse_text_int(parts[1], key="di.y"),
    }


def _parse_text_bool(value: str, *, key: str) -> bool:
    normalized = value.lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    raise ResponseParsingError(
        f"{key} must be a boolean",
        fallback_reason=FallbackReason.MALFORMED_OUTPUT,
        location=("extra", key),
        source=ParseSource.TEXT,
    )


def _coerce_json_object(mapping: Mapping[str, object]) -> JsonObject:
    return {str(key): _coerce_json_value(value) for key, value in mapping.items()}


def _coerce_json_value(value: object) -> JsonValue:
    if isinstance(value, ProtocolModel):
        return value.to_dict()
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Mapping):
        return _coerce_json_object(cast(Mapping[str, object], value))
    if isinstance(value, list | tuple):
        return [_coerce_json_value(item) for item in value]
    raise ResponseParsingError(
        f"provider response contains a non-JSON value: {type(value)!r}",
        fallback_reason=FallbackReason.MALFORMED_OUTPUT,
    )
