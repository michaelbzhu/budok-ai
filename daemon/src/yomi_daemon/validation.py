"""Schema-backed validation helpers for protocol payloads and request-relative decisions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError, best_match

from yomi_daemon.protocol import (
    ActionDecision,
    CURRENT_PROTOCOL_VERSION,
    DecisionRequest,
    PAYLOAD_TYPE_BY_MESSAGE_TYPE,
    Envelope,
    FallbackReason,
    LegalAction,
    MessageType,
    ProtocolModel,
    ProtocolVersion,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "schemas"

ENVELOPE_SCHEMA_FILE = "envelope.json"
SCHEMA_FILE_BY_MESSAGE_TYPE: dict[MessageType, str] = {
    MessageType.HELLO: "hello.v1.json",
    MessageType.HELLO_ACK: "hello-ack.v1.json",
    MessageType.DECISION_REQUEST: "decision-request.v1.json",
    MessageType.ACTION_DECISION: "action-decision.v1.json",
    MessageType.EVENT: "event.v1.json",
    MessageType.MATCH_ENDED: "match-ended.v1.json",
    MessageType.CONFIG: "config.v1.json",
}


class ProtocolValidationError(ValueError):
    """Raised when a protocol payload or envelope fails validation."""

    def __init__(
        self,
        message: str,
        *,
        schema_name: str,
        location: tuple[str | int, ...] = (),
    ) -> None:
        super().__init__(message)
        self.schema_name = schema_name
        self.location = location


class DecisionValidationError(ValueError):
    """Raised when an action decision is incompatible with a live request."""

    def __init__(
        self,
        message: str,
        *,
        fallback_reason: FallbackReason,
        location: tuple[str | int, ...] = (),
    ) -> None:
        super().__init__(message)
        self.fallback_reason = fallback_reason
        self.location = location


def _schema_path(filename: str) -> Path:
    return SCHEMA_DIR / filename


@cache
def load_schema(filename: str) -> dict[str, Any]:
    with _schema_path(filename).open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return schema


@cache
def load_all_schemas() -> dict[str, dict[str, Any]]:
    return {path.name: load_schema(path.name) for path in sorted(SCHEMA_DIR.glob("*.json"))}


def _validator_for(filename: str) -> Any:
    return Draft202012Validator(load_schema(filename), format_checker=FormatChecker())


def _error_path(error: ValidationError) -> tuple[str | int, ...]:
    return tuple(error.absolute_path)


def _format_validation_error(
    error: ValidationError, *, schema_name: str
) -> ProtocolValidationError:
    location = _error_path(error)
    location_text = ".".join(str(part) for part in location) if location else "<root>"
    return ProtocolValidationError(
        f"{schema_name} validation failed at {location_text}: {error.message}",
        schema_name=schema_name,
        location=location,
    )


def _best_error(validator: Any, instance: Any) -> ValidationError | None:
    errors = list(validator.iter_errors(instance))
    if not errors:
        return None
    return best_match(errors)


def ensure_supported_protocol_version(version: str | ProtocolVersion) -> ProtocolVersion:
    try:
        normalized = ProtocolVersion(version)
    except ValueError as exc:
        supported = ", ".join(item.value for item in ProtocolVersion)
        raise ProtocolValidationError(
            f"Unsupported protocol version {version!r}. Supported versions: {supported}",
            schema_name=ENVELOPE_SCHEMA_FILE,
            location=("version",),
        ) from exc
    return normalized


def validate_payload(
    message_type: str | MessageType,
    payload: Mapping[str, object],
    *,
    version: str | ProtocolVersion = CURRENT_PROTOCOL_VERSION,
) -> None:
    normalized_version = ensure_supported_protocol_version(version)
    if normalized_version is not CURRENT_PROTOCOL_VERSION:
        raise ProtocolValidationError(
            f"Unsupported protocol version {normalized_version.value!r}",
            schema_name=ENVELOPE_SCHEMA_FILE,
            location=("version",),
        )

    normalized_type = MessageType(message_type)
    schema_name = SCHEMA_FILE_BY_MESSAGE_TYPE[normalized_type]
    validator = _validator_for(schema_name)
    if error := _best_error(validator, payload):
        raise _format_validation_error(error, schema_name=schema_name)


def validate_envelope(envelope: Mapping[str, object]) -> None:
    envelope_validator = _validator_for(ENVELOPE_SCHEMA_FILE)
    if error := _best_error(envelope_validator, envelope):
        raise _format_validation_error(error, schema_name=ENVELOPE_SCHEMA_FILE)

    normalized_type = MessageType(str(envelope["type"]))
    validate_payload(
        normalized_type,
        envelope["payload"],  # type: ignore[arg-type]
        version=str(envelope["version"]),
    )


def validate_model(
    model: ProtocolModel, *, version: str | ProtocolVersion = CURRENT_PROTOCOL_VERSION
) -> None:
    if isinstance(model, Envelope):
        validate_envelope(model.to_dict())
        return

    for message_type, payload_type in PAYLOAD_TYPE_BY_MESSAGE_TYPE.items():
        if isinstance(model, payload_type):
            validate_payload(message_type, model.to_dict(), version=version)
            return

    raise TypeError(f"Unsupported protocol model: {type(model)!r}")


def parse_envelope(envelope: Mapping[str, object]) -> Envelope:
    validate_envelope(envelope)
    return Envelope.from_dict(envelope)


_SCHEMA_DESCRIPTOR_KEYS = frozenset(
    {
        "additionalProperties",
        "choices",
        "const",
        "default",
        "enum",
        "items",
        "maximum",
        "maxItems",
        "max_items",
        "minimum",
        "minItems",
        "min_items",
        "properties",
        "required",
        "type",
    }
)
_JSON_TYPE_NAMES = frozenset({"array", "boolean", "integer", "number", "object", "string"})


def validate_action_decision_for_request(
    request: DecisionRequest,
    decision: ActionDecision,
    *,
    require_request_ids: bool = True,
) -> LegalAction:
    """Validate an action decision against the current request and legal action set."""

    validate_model(decision)

    if require_request_ids and decision.match_id != request.match_id:
        raise DecisionValidationError(
            (
                f"action_decision.match_id {decision.match_id!r} does not match "
                f"request.match_id {request.match_id!r}"
            ),
            fallback_reason=FallbackReason.STALE_RESPONSE,
            location=("match_id",),
        )
    if require_request_ids and decision.turn_id != request.turn_id:
        raise DecisionValidationError(
            (
                f"action_decision.turn_id {decision.turn_id} does not match "
                f"request.turn_id {request.turn_id}"
            ),
            fallback_reason=FallbackReason.STALE_RESPONSE,
            location=("turn_id",),
        )

    for legal_action in request.legal_actions:
        if legal_action.action == decision.action:
            _validate_decision_extras(legal_action, decision)
            _validate_decision_payload(legal_action, decision)
            return legal_action

    raise DecisionValidationError(
        f"action_decision.action {decision.action!r} is not present in the current legal action set",
        fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
        location=("action",),
    )


def is_replayable_decision(request: DecisionRequest, decision: ActionDecision) -> bool:
    """Return True when the prior decision can be replayed under the current legal set."""

    try:
        validate_action_decision_for_request(
            request,
            decision,
            require_request_ids=False,
        )
    except (DecisionValidationError, ProtocolValidationError):
        return False
    return True


def _validate_decision_extras(legal_action: LegalAction, decision: ActionDecision) -> None:
    if decision.extra.di is not None and not legal_action.supports.di:
        raise DecisionValidationError(
            f"action {decision.action!r} does not support extra.di",
            fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
            location=("extra", "di"),
        )
    if decision.extra.feint and not legal_action.supports.feint:
        raise DecisionValidationError(
            f"action {decision.action!r} does not support extra.feint",
            fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
            location=("extra", "feint"),
        )
    if decision.extra.reverse and not legal_action.supports.reverse:
        raise DecisionValidationError(
            f"action {decision.action!r} does not support extra.reverse",
            fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
            location=("extra", "reverse"),
        )


def _validate_decision_payload(legal_action: LegalAction, decision: ActionDecision) -> None:
    if decision.data is None:
        return

    if not legal_action.payload_spec:
        if decision.data:
            raise DecisionValidationError(
                f"action {decision.action!r} does not accept payload fields",
                fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
                location=("data",),
            )
        return

    _validate_payload_value(
        decision.data,
        legal_action.payload_spec,
        context="data",
        field_map_mode=_looks_like_field_map(legal_action.payload_spec),
    )


def _looks_like_field_map(spec: Mapping[str, object]) -> bool:
    return not bool(_SCHEMA_DESCRIPTOR_KEYS & set(spec))


def _validate_payload_value(
    value: object,
    spec: Mapping[str, object],
    *,
    context: str,
    field_map_mode: bool = False,
) -> None:
    if field_map_mode:
        if not isinstance(value, Mapping):
            raise DecisionValidationError(
                f"{context} must be an object for this action payload",
                fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
                location=_location_tuple(context),
            )
        mapping_value = cast(Mapping[str, object], value)
        for key in mapping_value:
            if key not in spec:
                raise DecisionValidationError(
                    f"{context}.{key} is not defined by the legal action payload spec",
                    fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
                    location=_location_tuple(f"{context}.{key}"),
                )
        for key, item in mapping_value.items():
            descriptor = spec[key]
            if isinstance(descriptor, Mapping):
                _validate_payload_value(
                    item,
                    cast(Mapping[str, object], descriptor),
                    context=f"{context}.{key}",
                    field_map_mode=_looks_like_field_map(cast(Mapping[str, object], descriptor)),
                )
        return

    _validate_descriptor_type(value, spec, context=context)

    if "const" in spec and value != spec["const"]:
        raise DecisionValidationError(
            f"{context} must equal {spec['const']!r}",
            fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
            location=_location_tuple(context),
        )

    enum_values = spec.get("enum", spec.get("choices"))
    if isinstance(enum_values, Sequence) and not isinstance(enum_values, str | bytes | bytearray):
        if value not in enum_values:
            raise DecisionValidationError(
                f"{context} must be one of the declared payload choices",
                fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
                location=_location_tuple(context),
            )

    if isinstance(value, Mapping):
        properties = spec.get("properties")
        if isinstance(properties, Mapping):
            properties_mapping = cast(Mapping[str, object], properties)
            mapping_value = cast(Mapping[str, object], value)
            additional_properties = spec.get("additionalProperties", True)
            for key in mapping_value:
                if key in properties_mapping:
                    descriptor = properties_mapping[key]
                    if isinstance(descriptor, Mapping):
                        _validate_payload_value(
                            mapping_value[key],
                            cast(Mapping[str, object], descriptor),
                            context=f"{context}.{key}",
                            field_map_mode=_looks_like_field_map(
                                cast(Mapping[str, object], descriptor)
                            ),
                        )
                    continue
                if additional_properties is False:
                    raise DecisionValidationError(
                        f"{context}.{key} is not allowed by the payload schema",
                        fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
                        location=_location_tuple(f"{context}.{key}"),
                    )

            required = spec.get("required")
            if isinstance(required, Sequence) and not isinstance(required, str | bytes | bytearray):
                for required_key in required:
                    if isinstance(required_key, str) and required_key not in mapping_value:
                        raise DecisionValidationError(
                            f"{context}.{required_key} is required by the payload schema",
                            fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
                            location=_location_tuple(f"{context}.{required_key}"),
                        )

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        items_spec = spec.get("items")
        if isinstance(items_spec, Mapping):
            descriptor = cast(Mapping[str, object], items_spec)
            for index, item in enumerate(value):
                _validate_payload_value(
                    item,
                    descriptor,
                    context=f"{context}[{index}]",
                    field_map_mode=_looks_like_field_map(descriptor),
                )

        min_items = _optional_integer(spec.get("minItems", spec.get("min_items")))
        if min_items is not None and len(value) < min_items:
            raise DecisionValidationError(
                f"{context} must contain at least {min_items} items",
                fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
                location=_location_tuple(context),
            )

        max_items = _optional_integer(spec.get("maxItems", spec.get("max_items")))
        if max_items is not None and len(value) > max_items:
            raise DecisionValidationError(
                f"{context} must contain at most {max_items} items",
                fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
                location=_location_tuple(context),
            )

    if _is_number(value):
        minimum = _optional_number(spec.get("minimum"))
        if minimum is not None and cast(float, value) < minimum:
            raise DecisionValidationError(
                f"{context} must be >= {minimum}",
                fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
                location=_location_tuple(context),
            )
        maximum = _optional_number(spec.get("maximum"))
        if maximum is not None and cast(float, value) > maximum:
            raise DecisionValidationError(
                f"{context} must be <= {maximum}",
                fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
                location=_location_tuple(context),
            )


def _validate_descriptor_type(value: object, spec: Mapping[str, object], *, context: str) -> None:
    raw_type = spec.get("type")
    if not isinstance(raw_type, str):
        return

    normalized_type = raw_type.lower()
    if normalized_type == "string":
        if not isinstance(value, str):
            raise _type_error(context, "a string")
        return
    if normalized_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _type_error(context, "an integer")
        return
    if normalized_type == "number":
        if not _is_number(value):
            raise _type_error(context, "a number")
        return
    if normalized_type == "boolean":
        if not isinstance(value, bool):
            raise _type_error(context, "a boolean")
        return
    if normalized_type == "object":
        if not isinstance(value, Mapping):
            raise _type_error(context, "an object")
        return
    if normalized_type == "array":
        if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
            raise _type_error(context, "an array")
        return
    if normalized_type not in _JSON_TYPE_NAMES and not isinstance(value, str):
        raise _type_error(context, "a string")


def _type_error(context: str, expected: str) -> DecisionValidationError:
    return DecisionValidationError(
        f"{context} must be {expected}",
        fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
        location=_location_tuple(context),
    )


def _location_tuple(context: str) -> tuple[str | int, ...]:
    return tuple(part for part in context.replace("[", ".").replace("]", "").split(".") if part)


def _optional_integer(raw: object) -> int | None:
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _optional_number(raw: object) -> float | None:
    if not _is_number(raw):
        return None
    return float(cast(int | float, raw))


def _is_number(raw: object) -> bool:
    return not isinstance(raw, bool) and isinstance(raw, int | float)
