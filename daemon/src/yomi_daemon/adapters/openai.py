"""OpenAI Responses API adapter."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from yomi_daemon.adapters.base import (
    AdapterConstructionError,
    BasePolicyAdapter,
    PolicyAdapterMetadata,
    PolicyDecisionResult,
    PromptTrace,
    metadata_from_policy_config,
)
from yomi_daemon.orchestrator import resolve_policy_decision
from yomi_daemon.prompt import (
    DEFAULT_PROMPT_VERSION,
    decision_output_json_schema,
    render_prompt,
)
from yomi_daemon.protocol import ActionDecision, DecisionRequest, FallbackMode, JsonObject
from yomi_daemon.response_parser import ResponseParsingConfig

if TYPE_CHECKING:
    from yomi_daemon.config import PolicyConfig


class OpenAITransport(Protocol):
    async def create_response(
        self,
        *,
        api_key: str,
        payload: JsonObject,
        timeout_ms: int,
    ) -> JsonObject: ...


class OpenAIProviderError(RuntimeError):
    """Raised when the upstream OpenAI request fails before parsing."""


@dataclass(frozen=True, slots=True)
class _ProviderCallResult:
    output: object
    request_payload: JsonObject
    response_payload: JsonObject


class DefaultOpenAITransport:
    def __init__(self) -> None:
        self._client_by_api_key: dict[str, AsyncOpenAI] = {}

    async def create_response(
        self,
        *,
        api_key: str,
        payload: JsonObject,
        timeout_ms: int,
    ) -> JsonObject:
        try:
            response = await self._client_for_api_key(api_key).responses.create(
                **cast(Any, payload),
                timeout=timeout_ms / 1000,
            )
        except APIConnectionError as exc:
            raise OSError(f"OpenAI transport failed: {exc}") from exc
        except APIStatusError as exc:
            from yomi_daemon.redact import sanitize_provider_error

            raise OpenAIProviderError(sanitize_provider_error(exc)) from exc

        dumped = response.model_dump(mode="json")
        if not isinstance(dumped, dict):
            raise OpenAIProviderError("OpenAI SDK response did not serialize to an object")
        return cast(JsonObject, dumped)

    def _client_for_api_key(self, api_key: str) -> AsyncOpenAI:
        client = self._client_by_api_key.get(api_key)
        if client is None:
            client = AsyncOpenAI(api_key=api_key, max_retries=0)
            self._client_by_api_key[api_key] = client
        return client


class OpenAIAdapter(BasePolicyAdapter):
    def __init__(
        self,
        *,
        metadata: PolicyAdapterMetadata,
        api_key: str | None,
        decision_timeout_ms: int,
        fallback_mode: FallbackMode,
        parsing_config: ResponseParsingConfig,
        temperature: float | None,
        max_tokens: int | None,
        transport: OpenAITransport | None = None,
        default_trace_seed: int = 0,
    ) -> None:
        super().__init__(metadata=metadata, default_trace_seed=default_trace_seed)
        self._api_key = api_key
        self._decision_timeout_ms = decision_timeout_ms
        self._fallback_mode = fallback_mode
        self._parsing_config = parsing_config
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._transport = transport or DefaultOpenAITransport()

    async def decide(self, request: DecisionRequest) -> ActionDecision:
        return (await self.decide_with_trace(request)).decision

    async def decide_with_trace(self, request: DecisionRequest) -> PolicyDecisionResult:
        rendered_prompt = render_prompt(
            request,
            configured_prompt_version=self.metadata.prompt_version,
            policy_id=self.id,
        )
        request_attempts: list[JsonObject] = []
        response_attempts: list[JsonObject] = []

        async def decision_provider(_: DecisionRequest) -> object:
            call = await self._call_provider(
                prompt_text=rendered_prompt.prompt_text,
                request=request,
                attempt_kind="initial",
            )
            request_attempts.append(call.request_payload)
            response_attempts.append(call.response_payload)
            return call.output

        async def correction_callback(correction_prompt: str) -> object:
            call = await self._call_provider(
                prompt_text=correction_prompt,
                request=request,
                attempt_kind="correction",
            )
            request_attempts.append(call.request_payload)
            response_attempts.append(call.response_payload)
            return call.output

        result = await resolve_policy_decision(
            request,
            decision_provider=decision_provider,
            fallback_mode=self._fallback_mode,
            correction_callback=correction_callback,
            parsing_config=self._parsing_config,
            timeout_ms=self._decision_timeout_ms,
        )

        tokens_in_total, tokens_out_total = _sum_usage_tokens(response_attempts)
        decision = _with_provider_metadata(
            result.decision,
            policy_id=self.id,
            tokens_in=tokens_in_total,
            tokens_out=tokens_out_total,
        )
        return PolicyDecisionResult(
            decision=decision,
            prompt_trace=PromptTrace(
                prompt_text=rendered_prompt.prompt_text,
                prompt_version=rendered_prompt.prompt_version,
                provider_request={"attempts": deepcopy(request_attempts)},
                provider_response={"attempts": deepcopy(response_attempts)},
            ),
        )

    async def _call_provider(
        self,
        *,
        prompt_text: str,
        request: DecisionRequest,
        attempt_kind: str,
    ) -> _ProviderCallResult:
        if not self._api_key:
            raise OpenAIProviderError("OPENAI_API_KEY is not configured for this policy")

        provider_request = self._build_request_payload(
            prompt_text=prompt_text,
            request=request,
            attempt_kind=attempt_kind,
        )
        provider_response = await self._transport.create_response(
            api_key=self._api_key,
            payload=provider_request,
            timeout_ms=self._decision_timeout_ms,
        )
        output = _extract_response_output(provider_response)
        return _ProviderCallResult(
            output=output,
            request_payload=provider_request,
            response_payload=provider_response,
        )

    def _build_request_payload(
        self,
        *,
        prompt_text: str,
        request: DecisionRequest,
        attempt_kind: str,
    ) -> JsonObject:
        prompt_version = (
            request.prompt_version or self.metadata.prompt_version or DEFAULT_PROMPT_VERSION
        )
        payload: JsonObject = {
            "model": cast(str, self.metadata.model),
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt_text}],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "yomi_action_decision",
                    "schema": decision_output_json_schema(),
                }
            },
            "metadata": {
                "policy_id": self.id,
                "prompt_version": prompt_version,
                "match_id": request.match_id,
                "turn_id": request.turn_id,
                "player_id": request.player_id,
                "attempt_kind": attempt_kind,
            },
        }
        if request.trace_seed is not None:
            payload["metadata"]["trace_seed"] = request.trace_seed
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        if self._max_tokens is not None:
            payload["max_output_tokens"] = self._max_tokens
        return payload


def build_openai_adapter(
    policy_id: str,
    policy: "PolicyConfig",
    *,
    decision_timeout_ms: int,
    fallback_mode: FallbackMode,
    transport: OpenAITransport | None = None,
    default_trace_seed: int = 0,
) -> OpenAIAdapter:
    if policy.model is None:
        raise AdapterConstructionError(f"openai policy {policy_id!r} must define a model")
    metadata = metadata_from_policy_config(policy_id, policy)
    parsing_config = ResponseParsingConfig.from_policy_options(policy.options)
    return OpenAIAdapter(
        metadata=metadata,
        api_key=policy.credential.value,
        decision_timeout_ms=decision_timeout_ms,
        fallback_mode=fallback_mode,
        parsing_config=parsing_config,
        temperature=policy.temperature,
        max_tokens=policy.max_tokens,
        transport=transport,
        default_trace_seed=default_trace_seed,
    )


def _with_provider_metadata(
    decision: ActionDecision,
    *,
    policy_id: str,
    tokens_in: int,
    tokens_out: int,
) -> ActionDecision:
    return ActionDecision(
        match_id=decision.match_id,
        turn_id=decision.turn_id,
        action=decision.action,
        data=decision.data,
        extra=decision.extra,
        policy_id=decision.policy_id if decision.fallback_reason is not None else policy_id,
        latency_ms=decision.latency_ms,
        tokens_in=decision.tokens_in if decision.tokens_in is not None else tokens_in or None,
        tokens_out=decision.tokens_out if decision.tokens_out is not None else tokens_out or None,
        reasoning=decision.reasoning,
        notes=decision.notes,
        fallback_reason=decision.fallback_reason,
    )


def _sum_usage_tokens(response_attempts: list[JsonObject]) -> tuple[int, int]:
    tokens_in_total = 0
    tokens_out_total = 0
    for response in response_attempts:
        usage_raw = response.get("usage")
        if not isinstance(usage_raw, Mapping):
            continue
        usage = cast(Mapping[str, object], usage_raw)
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if isinstance(input_tokens, int) and not isinstance(input_tokens, bool):
            tokens_in_total += input_tokens
        if isinstance(output_tokens, int) and not isinstance(output_tokens, bool):
            tokens_out_total += output_tokens
    return tokens_in_total, tokens_out_total


def _extract_response_output(response_payload: JsonObject) -> object:
    status = response_payload.get("status")
    if isinstance(status, str) and status not in {"completed", "in_progress"}:
        raise OpenAIProviderError(f"OpenAI response status was {status!r}")

    if isinstance(output_text := response_payload.get("output_text"), str) and output_text.strip():
        return output_text

    output_raw = response_payload.get("output")
    if not isinstance(output_raw, list):
        raise OpenAIProviderError("OpenAI response did not include an output array")

    text_fragments: list[str] = []
    for item in output_raw:
        if not isinstance(item, Mapping):
            continue
        content_raw = item.get("content")
        if not isinstance(content_raw, list):
            continue
        for content_item in content_raw:
            if not isinstance(content_item, Mapping):
                continue
            content_type = content_item.get("type")
            if content_type == "output_text":
                text = content_item.get("text")
                if isinstance(text, str):
                    text_fragments.append(text)
            if content_type == "output_json":
                data = content_item.get("json")
                if isinstance(data, Mapping):
                    return cast(JsonObject, deepcopy(data))
            if content_type == "refusal":
                text = content_item.get("refusal")
                if isinstance(text, str):
                    raise OpenAIProviderError(text)

    if text_fragments:
        return "".join(text_fragments)

    raise OpenAIProviderError("OpenAI response did not include output text")
