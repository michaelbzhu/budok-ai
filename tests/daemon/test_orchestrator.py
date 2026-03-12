from __future__ import annotations

import asyncio

from tests.daemon._decision_fixtures import build_action, build_decision, build_request
from yomi_daemon.orchestrator import resolve_policy_decision
from yomi_daemon.protocol import FallbackMode, FallbackReason
from yomi_daemon.response_parser import ResponseParsingConfig


def test_orchestrator_returns_provider_decision_on_success() -> None:
    request = build_request((build_action("guard"),))

    async def provider(_: object) -> object:
        return {"action": "guard"}

    result = asyncio.run(
        resolve_policy_decision(
            request,
            decision_provider=provider,
            fallback_mode=FallbackMode.SAFE_CONTINUE,
        )
    )

    assert result.used_fallback is False
    assert result.decision.action == "guard"
    assert result.parse_source is not None
    assert result.decision.latency_ms is not None


def test_orchestrator_returns_fallback_on_timeout() -> None:
    request = build_request((build_action("guard"),), deadline_ms=10)

    async def slow_provider(_: object) -> object:
        await asyncio.sleep(0.05)
        return {"action": "guard"}

    result = asyncio.run(
        resolve_policy_decision(
            request,
            decision_provider=slow_provider,
            fallback_mode=FallbackMode.SAFE_CONTINUE,
        )
    )

    assert result.used_fallback is True
    assert result.fallback_reason is FallbackReason.TIMEOUT
    assert result.decision.fallback_reason is FallbackReason.TIMEOUT


def test_orchestrator_returns_fallback_on_disconnect() -> None:
    request = build_request((build_action("guard"),))

    async def broken_provider(_: object) -> object:
        raise ConnectionError("socket closed")

    result = asyncio.run(
        resolve_policy_decision(
            request,
            decision_provider=broken_provider,
            fallback_mode=FallbackMode.HEURISTIC_GUARD,
        )
    )

    assert result.used_fallback is True
    assert result.fallback_reason is FallbackReason.DISCONNECT
    assert result.decision.action == "guard"


def test_orchestrator_uses_correction_retry_before_fallback() -> None:
    request = build_request((build_action("guard"),))

    async def provider(_: object) -> object:
        return '{"action":"teleport"}'

    correction_prompts: list[str] = []

    async def correction_callback(prompt: str) -> object:
        correction_prompts.append(prompt)
        return {"action": "guard"}

    result = asyncio.run(
        resolve_policy_decision(
            request,
            decision_provider=provider,
            fallback_mode=FallbackMode.SAFE_CONTINUE,
            correction_callback=correction_callback,
            parsing_config=ResponseParsingConfig(
                enable_correction_retry=True,
                max_correction_retries=1,
            ),
            last_valid_decision=build_decision(request, action="guard"),
        )
    )

    assert result.used_fallback is False
    assert result.decision.action == "guard"
    assert result.correction_attempts == 1
    assert len(correction_prompts) == 1
