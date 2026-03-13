"""Fallback selection for daemon-side invalid, late, or disconnected decisions."""

from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import cast

from yomi_daemon.adapters.baseline import _resolve_payload_object
from yomi_daemon.protocol import (
    ActionDecision,
    DIVector,
    DecisionExtras,
    DecisionRequest,
    FallbackMode,
    FallbackReason,
    JsonObject,
    LegalAction,
)
from yomi_daemon.validation import is_replayable_decision


_DEFENSIVE_KEYWORDS = (
    "block",
    "guard",
    "parry",
    "defend",
    "shield",
    "wait",
    "idle",
    "evade",
    "backdash",
)
_LOW_COMMITMENT_KEYWORDS = (
    "block",
    "guard",
    "wait",
    "backdash",
    "jab",
    "poke",
    "dash",
    "step",
    "move",
)
_HIGH_COMMITMENT_KEYWORDS = ("super", "burst", "charge", "launcher", "throw", "teleport")


@dataclass(frozen=True, slots=True)
class FallbackSelection:
    decision: ActionDecision
    strategy: FallbackMode


def build_fallback_decision(
    request: DecisionRequest,
    *,
    fallback_reason: FallbackReason,
    fallback_mode: FallbackMode,
    last_valid_decision: ActionDecision | None = None,
) -> FallbackSelection:
    """Build a structured fallback decision for the current request."""

    if (
        fallback_mode is FallbackMode.LAST_VALID_REPLAYABLE
        and last_valid_decision is not None
        and is_replayable_decision(request, last_valid_decision)
    ):
        return FallbackSelection(
            decision=ActionDecision(
                match_id=request.match_id,
                turn_id=request.turn_id,
                action=last_valid_decision.action,
                data=last_valid_decision.data,
                extra=last_valid_decision.extra,
                policy_id=f"fallback/{FallbackMode.LAST_VALID_REPLAYABLE.value}",
                notes="Replayed the last request-compatible decision after upstream failure.",
                fallback_reason=fallback_reason,
            ),
            strategy=FallbackMode.LAST_VALID_REPLAYABLE,
        )

    if fallback_mode in {
        FallbackMode.HEURISTIC_GUARD,
        FallbackMode.LAST_VALID_REPLAYABLE,
    }:
        guard_action = _select_guard_action(request)
        if guard_action is not None:
            return FallbackSelection(
                decision=_build_action_fallback(
                    request,
                    guard_action,
                    fallback_reason=fallback_reason,
                    strategy=FallbackMode.HEURISTIC_GUARD,
                    notes="Selected a defensive legal action after upstream failure.",
                ),
                strategy=FallbackMode.HEURISTIC_GUARD,
            )

    safe_action = _select_safe_action(request)
    return FallbackSelection(
        decision=_build_action_fallback(
            request,
            safe_action,
            fallback_reason=fallback_reason,
            strategy=FallbackMode.SAFE_CONTINUE,
            notes="Selected the safest available legal action after upstream failure.",
        ),
        strategy=FallbackMode.SAFE_CONTINUE,
    )


def _build_action_fallback(
    request: DecisionRequest,
    action: LegalAction,
    *,
    fallback_reason: FallbackReason,
    strategy: FallbackMode,
    notes: str,
) -> ActionDecision:
    return ActionDecision(
        match_id=request.match_id,
        turn_id=request.turn_id,
        action=action.action,
        data=_resolve_payload(action),
        extra=_default_extras(action),
        policy_id=f"fallback/{strategy.value}",
        notes=notes,
        fallback_reason=fallback_reason,
    )


def _resolve_payload(action: LegalAction) -> JsonObject | None:
    resolved = _resolve_payload_object(action.payload_spec, rng=Random(0))
    if isinstance(resolved, dict):
        return cast(JsonObject, resolved)
    return None


def _default_extras(action: LegalAction) -> DecisionExtras:
    return DecisionExtras(
        di=DIVector(x=0, y=0) if action.supports.di else None,
        feint=False,
        reverse=False,
        prediction=None,
    )


def _select_guard_action(request: DecisionRequest) -> LegalAction | None:
    candidates = [action for action in request.legal_actions if _guard_score(action) > 0]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda action: (
            _guard_score(action),
            _safe_score(action),
            -_action_index(request, action),
        ),
    )


def _select_safe_action(request: DecisionRequest) -> LegalAction:
    if not request.legal_actions:
        raise ValueError("decision requests must contain at least one legal action")
    return max(
        request.legal_actions,
        key=lambda action: (_safe_score(action), -_action_index(request, action)),
    )


def _action_text(action: LegalAction) -> str:
    parts = [action.action]
    if action.label:
        parts.append(action.label)
    if action.description:
        parts.append(action.description)
    return " ".join(parts).lower()


def _action_index(request: DecisionRequest, action: LegalAction) -> int:
    return next(
        index for index, candidate in enumerate(request.legal_actions) if candidate == action
    )


def _keyword_hits(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _guard_score(action: LegalAction) -> float:
    text = _action_text(action)
    return float(_keyword_hits(text, _DEFENSIVE_KEYWORDS) * 4) + _safe_score(action)


def _safe_score(action: LegalAction) -> float:
    text = _action_text(action)
    no_payload_bonus = 3.0 if not action.payload_spec else 0.0
    no_extra_bonus = 1.0 if not action.supports.di else 0.0
    defensive_bonus = float(_keyword_hits(text, _DEFENSIVE_KEYWORDS) * 4)
    low_commitment_bonus = float(_keyword_hits(text, _LOW_COMMITMENT_KEYWORDS) * 2)
    high_commitment_penalty = float(_keyword_hits(text, _HIGH_COMMITMENT_KEYWORDS) * 3)
    startup_penalty = min(_startup_value(action), 30) / 10.0
    meter_penalty = float(_meter_cost(action) * 2)
    return (
        no_payload_bonus
        + no_extra_bonus
        + defensive_bonus
        + low_commitment_bonus
        - high_commitment_penalty
        - startup_penalty
        - meter_penalty
    )


def _startup_value(action: LegalAction) -> int:
    return action.startup_frames if action.startup_frames is not None else 999


def _meter_cost(action: LegalAction) -> int:
    return action.meter_cost if action.meter_cost is not None else 0
