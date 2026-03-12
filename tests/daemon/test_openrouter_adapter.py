from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import cast

from tests.daemon._decision_fixtures import build_action, build_request
from yomi_daemon.adapters.openrouter import (
    OpenRouterTransport,
    build_openrouter_adapter,
)
from yomi_daemon.config import PolicyConfig, ProviderCredential
from yomi_daemon.protocol import FallbackMode, FallbackReason, JsonObject


class StubOpenRouterTransport(OpenRouterTransport):
    def __init__(
        self,
        *,
        responses: Sequence[JsonObject] = (),
        delay_seconds: float = 0.0,
    ) -> None:
        self._responses = list(responses)
        self._delay_seconds = delay_seconds
        self.calls: list[JsonObject] = []

    async def create_completion(
        self,
        *,
        api_key: str,
        payload: JsonObject,
        timeout_ms: int,
        http_referer: str | None,
        title: str | None,
    ) -> JsonObject:
        self.calls.append(
            {
                "api_key": api_key,
                "payload": payload,
                "timeout_ms": timeout_ms,
                "http_referer": http_referer,
                "title": title,
            }
        )
        if self._delay_seconds > 0:
            await asyncio.sleep(self._delay_seconds)
        if not self._responses:
            raise AssertionError("stub transport ran out of responses")
        return self._responses.pop(0)


def _policy_config(*, credential_value: str | None = "test-key") -> PolicyConfig:
    return PolicyConfig(
        provider="openrouter",
        model="openai/gpt-5-mini",
        prompt_version="strategic_v1",
        credential=ProviderCredential(
            env_var="OPENROUTER_API_KEY",
            value=credential_value,
        ),
        temperature=0.1,
        max_tokens=128,
        options={"http_referer": "https://example.test", "title": "yomi-ai"},
    )


def _completion_response(
    message: JsonObject,
    *,
    prompt_tokens: int = 12,
    completion_tokens: int = 3,
) -> JsonObject:
    return {
        "id": "chatcmpl_test",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def test_openrouter_adapter_returns_schema_valid_decision_on_success() -> None:
    request = build_request((build_action("guard"), build_action("slash")))
    transport = StubOpenRouterTransport(
        responses=[
            _completion_response(
                {
                    "role": "assistant",
                    "content": '{"action":"guard","notes":"Reset safely."}',
                }
            )
        ],
    )
    adapter = build_openrouter_adapter(
        "provider/openrouter-main",
        _policy_config(),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.HEURISTIC_GUARD,
        transport=transport,
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.action == "guard"
    assert result.decision.policy_id == "provider/openrouter-main"
    assert result.decision.tokens_in == 12
    assert result.decision.tokens_out == 3
    first_call = cast(dict[str, object], transport.calls[0])
    assert first_call["http_referer"] == "https://example.test"
    assert first_call["title"] == "yomi-ai"


def test_openrouter_adapter_falls_back_on_illegal_structured_output() -> None:
    request = build_request((build_action("guard"),))
    transport = StubOpenRouterTransport(
        responses=[
            _completion_response(
                {"role": "assistant", "parsed": {"action": "teleport"}}
            )
        ],
    )
    adapter = build_openrouter_adapter(
        "provider/openrouter-main",
        _policy_config(),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.HEURISTIC_GUARD,
        transport=transport,
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.fallback_reason is FallbackReason.ILLEGAL_OUTPUT
    assert result.decision.action == "guard"


def test_openrouter_adapter_falls_back_on_timeout() -> None:
    request = build_request((build_action("guard"),), deadline_ms=10)
    transport = StubOpenRouterTransport(
        responses=[
            _completion_response({"role": "assistant", "content": '{"action":"guard"}'})
        ],
        delay_seconds=0.05,
    )
    adapter = build_openrouter_adapter(
        "provider/openrouter-main",
        _policy_config(),
        decision_timeout_ms=10,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        transport=transport,
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.fallback_reason is FallbackReason.TIMEOUT
    assert result.decision.action == "guard"


def test_openrouter_adapter_falls_back_when_credentials_are_missing() -> None:
    request = build_request((build_action("guard"),))
    adapter = build_openrouter_adapter(
        "provider/openrouter-main",
        _policy_config(credential_value=None),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        transport=StubOpenRouterTransport(),
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.fallback_reason is FallbackReason.MALFORMED_OUTPUT
