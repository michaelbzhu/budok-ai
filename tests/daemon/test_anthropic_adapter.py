from __future__ import annotations

import asyncio
from collections.abc import Sequence

from tests.daemon._decision_fixtures import build_action, build_request
from yomi_daemon.adapters.anthropic import AnthropicTransport, build_anthropic_adapter
from yomi_daemon.config import PolicyConfig, ProviderCredential
from yomi_daemon.protocol import FallbackMode, FallbackReason, JsonObject


class StubAnthropicTransport(AnthropicTransport):
    def __init__(
        self,
        *,
        responses: Sequence[JsonObject] = (),
        delay_seconds: float = 0.0,
    ) -> None:
        self._responses = list(responses)
        self._delay_seconds = delay_seconds
        self.calls: list[JsonObject] = []

    async def create_message(
        self,
        *,
        api_key: str,
        payload: JsonObject,
        timeout_ms: int,
    ) -> JsonObject:
        self.calls.append(
            {
                "api_key": api_key,
                "payload": payload,
                "timeout_ms": timeout_ms,
            }
        )
        if self._delay_seconds > 0:
            await asyncio.sleep(self._delay_seconds)
        if not self._responses:
            raise AssertionError("stub transport ran out of responses")
        return self._responses.pop(0)


def _policy_config(*, credential_value: str | None = "test-key") -> PolicyConfig:
    return PolicyConfig(
        provider="anthropic",
        model="claude-sonnet-4-5",
        prompt_version="strategic_v1",
        credential=ProviderCredential(
            env_var="ANTHROPIC_API_KEY",
            value=credential_value,
        ),
        temperature=0.1,
        max_tokens=256,
    )


def _tool_response(
    tool_input: JsonObject, *, input_tokens: int = 17, output_tokens: int = 5
) -> JsonObject:
    return {
        "id": "msg_test",
        "stop_reason": "tool_use",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "content": [
            {
                "type": "tool_use",
                "name": "submit_action_decision",
                "id": "toolu_123",
                "input": tool_input,
            }
        ],
    }


def test_anthropic_adapter_returns_schema_valid_decision_on_success() -> None:
    request = build_request((build_action("guard"), build_action("slash")))
    transport = StubAnthropicTransport(
        responses=[_tool_response({"action": "guard", "notes": "Block the scramble."})],
    )
    adapter = build_anthropic_adapter(
        "provider/anthropic-main",
        _policy_config(),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.HEURISTIC_GUARD,
        transport=transport,
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.action == "guard"
    assert result.decision.policy_id == "provider/anthropic-main"
    assert result.decision.tokens_in == 17
    assert result.decision.tokens_out == 5
    assert result.prompt_trace is not None


def test_anthropic_adapter_falls_back_on_illegal_structured_tool_output() -> None:
    request = build_request((build_action("guard"),))
    transport = StubAnthropicTransport(
        responses=[_tool_response({"action": "teleport"})],
    )
    adapter = build_anthropic_adapter(
        "provider/anthropic-main",
        _policy_config(),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.HEURISTIC_GUARD,
        transport=transport,
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.fallback_reason is FallbackReason.ILLEGAL_OUTPUT
    assert result.decision.action == "guard"


def test_anthropic_adapter_falls_back_on_timeout() -> None:
    request = build_request((build_action("guard"),), deadline_ms=10)
    transport = StubAnthropicTransport(
        responses=[_tool_response({"action": "guard"})],
        delay_seconds=0.05,
    )
    adapter = build_anthropic_adapter(
        "provider/anthropic-main",
        _policy_config(),
        decision_timeout_ms=10,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        transport=transport,
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.fallback_reason is FallbackReason.TIMEOUT
    assert result.decision.action == "guard"


def test_anthropic_adapter_falls_back_when_credentials_are_missing() -> None:
    request = build_request((build_action("guard"),))
    adapter = build_anthropic_adapter(
        "provider/anthropic-main",
        _policy_config(credential_value=None),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        transport=StubAnthropicTransport(),
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.fallback_reason is FallbackReason.MALFORMED_OUTPUT
