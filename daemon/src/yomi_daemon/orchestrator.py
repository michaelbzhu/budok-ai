"""Decision orchestration for provider calls, parsing, validation, and fallbacks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic

from yomi_daemon.fallback import build_fallback_decision
from yomi_daemon.protocol import ActionDecision, DecisionRequest, FallbackMode, FallbackReason
from yomi_daemon.response_parser import (
    CorrectionCallback,
    ParseSource,
    ParsedActionDecision,
    ResponseParsingConfig,
    ResponseParsingError,
    parse_action_decision_with_correction,
)


DecisionProvider = Callable[[DecisionRequest], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class OrchestratedDecision:
    decision: ActionDecision
    used_fallback: bool
    fallback_reason: FallbackReason | None = None
    fallback_strategy: FallbackMode | None = None
    parse_source: ParseSource | None = None
    correction_attempts: int = 0


async def resolve_policy_decision(
    request: DecisionRequest,
    *,
    decision_provider: DecisionProvider,
    fallback_mode: FallbackMode,
    correction_callback: CorrectionCallback | None = None,
    parsing_config: ResponseParsingConfig | None = None,
    last_valid_decision: ActionDecision | None = None,
    timeout_ms: int | None = None,
) -> OrchestratedDecision:
    """Resolve one decision request without allowing provider failures to escape."""

    resolved_timeout_ms = timeout_ms if timeout_ms is not None else request.deadline_ms

    try:
        started_at = monotonic()
        async with asyncio.timeout(resolved_timeout_ms / 1000):
            raw_response = await decision_provider(request)
        parsed = await parse_action_decision_with_correction(
            raw_response,
            request,
            correction_callback=correction_callback,
            config=parsing_config,
        )
        return OrchestratedDecision(
            decision=_with_latency(parsed, started_at),
            used_fallback=False,
            parse_source=parsed.source,
            correction_attempts=parsed.correction_attempts,
        )
    except TimeoutError:
        return _fallback_result(
            request,
            fallback_reason=FallbackReason.TIMEOUT,
            fallback_mode=fallback_mode,
            last_valid_decision=last_valid_decision,
        )
    except (ConnectionError, OSError):
        return _fallback_result(
            request,
            fallback_reason=FallbackReason.DISCONNECT,
            fallback_mode=fallback_mode,
            last_valid_decision=last_valid_decision,
        )
    except ResponseParsingError as exc:
        return _fallback_result(
            request,
            fallback_reason=exc.fallback_reason,
            fallback_mode=fallback_mode,
            last_valid_decision=last_valid_decision,
        )
    except Exception:
        return _fallback_result(
            request,
            fallback_reason=FallbackReason.MALFORMED_OUTPUT,
            fallback_mode=fallback_mode,
            last_valid_decision=last_valid_decision,
        )


def _with_latency(parsed: ParsedActionDecision, started_at: float) -> ActionDecision:
    decision = parsed.decision
    latency_ms = max(0, int(round((monotonic() - started_at) * 1000)))
    return ActionDecision(
        match_id=decision.match_id,
        turn_id=decision.turn_id,
        action=decision.action,
        data=decision.data,
        extra=decision.extra,
        policy_id=decision.policy_id,
        latency_ms=decision.latency_ms if decision.latency_ms is not None else latency_ms,
        tokens_in=decision.tokens_in,
        tokens_out=decision.tokens_out,
        reasoning=decision.reasoning,
        notes=decision.notes,
        fallback_reason=decision.fallback_reason,
    )


def _fallback_result(
    request: DecisionRequest,
    *,
    fallback_reason: FallbackReason,
    fallback_mode: FallbackMode,
    last_valid_decision: ActionDecision | None,
) -> OrchestratedDecision:
    selection = build_fallback_decision(
        request,
        fallback_reason=fallback_reason,
        fallback_mode=fallback_mode,
        last_valid_decision=last_valid_decision,
    )
    return OrchestratedDecision(
        decision=selection.decision,
        used_fallback=True,
        fallback_reason=fallback_reason,
        fallback_strategy=selection.strategy,
    )
