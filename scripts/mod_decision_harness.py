"""Python mirror of the mod-side decision validation, fallback, and action application pipeline.

Operates on synthetic decision/request dicts to validate:
- Decision validation against pending requests and legal action sets
- Fallback action selection with guard/safe scoring heuristics
- Action application field resolution
- Telemetry event envelope shape

This harness mirrors the GDScript implementations in:
- mod/YomiLLMBridge/bridge/DecisionValidator.gd
- mod/YomiLLMBridge/bridge/FallbackHandler.gd
- mod/YomiLLMBridge/bridge/ActionApplier.gd
- mod/YomiLLMBridge/bridge/Telemetry.gd
"""

from __future__ import annotations

from typing import Any

DI_MIN = -100
DI_MAX = 100


# --- Decision Validation ---


def validate_decision(decision: dict[str, Any], request: dict[str, Any]) -> str:
    """Validate an action decision against a pending request.

    Returns "" on success or a fallback reason string on failure.
    """
    envelope_error = _validate_envelope_shape(decision)
    if envelope_error:
        return envelope_error

    payload = decision.get("payload", decision)

    # Match ID check
    if str(payload.get("match_id", "")) != str(request.get("match_id", "")):
        return "stale_response"

    # Turn ID check
    if int(payload.get("turn_id", -1)) != int(request.get("turn_id", -1)):
        return "stale_response"

    # Action must be a non-empty string
    action_name = str(payload.get("action", ""))
    if not action_name:
        return "malformed_output"

    # Extra must be a dict with feint and reverse
    extra = payload.get("extra")
    if extra is None or not isinstance(extra, dict):
        return "malformed_output"
    if "feint" not in extra or "reverse" not in extra:
        return "malformed_output"

    # Find matching legal action
    legal_actions = request.get("legal_actions", [])
    matched_action = _find_legal_action(action_name, legal_actions)
    if matched_action is None:
        return "illegal_output"

    # Validate extras against supports
    extras_error = _validate_extras(extra, matched_action)
    if extras_error:
        return extras_error

    # Validate payload data
    data_error = _validate_payload_data(payload.get("data"), matched_action)
    if data_error:
        return data_error

    return ""


def is_replayable(decision_payload: dict[str, Any], request: dict[str, Any]) -> bool:
    """Check if a prior decision can be replayed under the current legal set."""
    action_name = str(decision_payload.get("action", ""))
    if not action_name:
        return False

    extra = decision_payload.get("extra")
    if extra is None or not isinstance(extra, dict):
        return False

    legal_actions = request.get("legal_actions", [])
    matched_action = _find_legal_action(action_name, legal_actions)
    if matched_action is None:
        return False

    if _validate_extras(extra, matched_action):
        return False

    if _validate_payload_data(decision_payload.get("data"), matched_action):
        return False

    return True


def _validate_envelope_shape(decision: dict[str, Any]) -> str:
    if "type" in decision:
        msg_type = str(decision.get("type", ""))
        if msg_type == "action_decision":
            if "payload" not in decision or not isinstance(
                decision.get("payload"), dict
            ):
                return "malformed_output"
    return ""


def _find_legal_action(
    action_name: str, legal_actions: list[dict[str, Any]]
) -> dict[str, Any] | None:
    for legal_action in legal_actions:
        if not isinstance(legal_action, dict):
            continue
        if str(legal_action.get("action", "")) == action_name:
            return legal_action
    return None


def _validate_extras(extra: dict[str, Any], legal_action: dict[str, Any]) -> str:
    supports = legal_action.get("supports", {})
    if not isinstance(supports, dict):
        return "malformed_output"

    di = extra.get("di")
    if di is not None:
        if not bool(supports.get("di", False)):
            return "illegal_output"
        if not isinstance(di, dict):
            return "malformed_output"
        di_x = di.get("x")
        di_y = di.get("y")
        if di_x is None or di_y is None:
            return "malformed_output"
        if int(di_x) < DI_MIN or int(di_x) > DI_MAX:
            return "illegal_output"
        if int(di_y) < DI_MIN or int(di_y) > DI_MAX:
            return "illegal_output"

    if bool(extra.get("feint", False)) and not bool(supports.get("feint", False)):
        return "illegal_output"

    if bool(extra.get("reverse", False)) and not bool(supports.get("reverse", False)):
        return "illegal_output"

    return ""


def _validate_payload_data(data: Any, legal_action: dict[str, Any]) -> str:
    if data is None:
        return ""

    payload_spec = legal_action.get("payload_spec", {})
    if not isinstance(payload_spec, dict) or not payload_spec:
        if isinstance(data, dict) and data:
            return "illegal_output"
        return ""

    if not isinstance(data, dict):
        return "illegal_output"

    return ""


# --- Fallback Selection ---

DEFENSIVE_KEYWORDS = (
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
LOW_COMMITMENT_KEYWORDS = (
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
HIGH_COMMITMENT_KEYWORDS = ("super", "burst", "charge", "launcher", "throw", "teleport")


def choose_fallback(
    request: dict[str, Any],
    fallback_reason: str,
    fallback_mode: str,
    last_valid_decisions: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Choose a fallback action for the given request."""
    legal_actions = request.get("legal_actions", [])
    if not legal_actions:
        return _empty_fallback(request, fallback_reason)

    player_id = str(request.get("player_id", ""))

    if fallback_mode == "last_valid_replayable" and last_valid_decisions:
        last_decision = last_valid_decisions.get(player_id)
        if last_decision is not None and is_replayable(last_decision, request):
            return _build_fallback_from_prior(request, last_decision, fallback_reason)

    if fallback_mode in ("heuristic_guard", "last_valid_replayable"):
        guard_action = _select_guard_action(legal_actions)
        if guard_action is not None:
            return _build_fallback_from_action(
                request, guard_action, fallback_reason, "heuristic_guard"
            )

    safe_action = _select_safe_action(legal_actions)
    return _build_fallback_from_action(
        request, safe_action, fallback_reason, "safe_continue"
    )


def _empty_fallback(request: dict[str, Any], fallback_reason: str) -> dict[str, Any]:
    return {
        "match_id": request.get("match_id", ""),
        "turn_id": request.get("turn_id", 0),
        "action": "",
        "data": None,
        "extra": {"di": None, "feint": False, "reverse": False},
        "policy_id": "fallback/safe_continue",
        "notes": "No legal actions available for fallback.",
        "fallback_reason": fallback_reason,
    }


def _build_fallback_from_prior(
    request: dict[str, Any],
    prior_decision: dict[str, Any],
    fallback_reason: str,
) -> dict[str, Any]:
    return {
        "match_id": request.get("match_id", ""),
        "turn_id": request.get("turn_id", 0),
        "action": prior_decision.get("action", ""),
        "data": prior_decision.get("data"),
        "extra": prior_decision.get(
            "extra", {"di": None, "feint": False, "reverse": False}
        ),
        "policy_id": "fallback/last_valid_replayable",
        "notes": "Replayed the last request-compatible decision after upstream failure.",
        "fallback_reason": fallback_reason,
    }


def _build_fallback_from_action(
    request: dict[str, Any],
    action: dict[str, Any],
    fallback_reason: str,
    strategy: str,
) -> dict[str, Any]:
    supports = action.get("supports", {})
    di_value = {"x": 0, "y": 0} if bool(supports.get("di", False)) else None

    return {
        "match_id": request.get("match_id", ""),
        "turn_id": request.get("turn_id", 0),
        "action": str(action.get("action", "")),
        "data": None,
        "extra": {
            "di": di_value,
            "feint": False,
            "reverse": False,
        },
        "policy_id": f"fallback/{strategy}",
        "notes": "Selected fallback action after upstream failure.",
        "fallback_reason": fallback_reason,
    }


def _action_text(action: dict[str, Any]) -> str:
    parts = [str(action.get("action", ""))]
    label = action.get("label")
    if label is not None and str(label):
        parts.append(str(label))
    description = action.get("description")
    if description is not None and str(description):
        parts.append(str(description))
    return " ".join(parts).lower()


def _keyword_hits(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _guard_score(action: dict[str, Any]) -> float:
    text = _action_text(action)
    return float(_keyword_hits(text, DEFENSIVE_KEYWORDS) * 4) + _safe_score(action)


def _safe_score(action: dict[str, Any]) -> float:
    text = _action_text(action)
    payload_spec = action.get("payload_spec", {})
    supports = action.get("supports", {})

    no_payload_bonus = 3.0 if not payload_spec else 0.0
    no_extra_bonus = 1.0 if not bool(supports.get("di", False)) else 0.0
    defensive_bonus = float(_keyword_hits(text, DEFENSIVE_KEYWORDS) * 4)
    low_commitment_bonus = float(_keyword_hits(text, LOW_COMMITMENT_KEYWORDS) * 2)
    high_commitment_penalty = float(_keyword_hits(text, HIGH_COMMITMENT_KEYWORDS) * 3)

    startup_frames = action.get("startup_frames")
    startup_value = int(startup_frames) if startup_frames is not None else 999
    startup_penalty = min(startup_value, 30) / 10.0

    meter_cost = action.get("meter_cost")
    meter_penalty = float(int(meter_cost) * 2) if meter_cost is not None else 0.0

    return (
        no_payload_bonus
        + no_extra_bonus
        + defensive_bonus
        + low_commitment_bonus
        - high_commitment_penalty
        - startup_penalty
        - meter_penalty
    )


def _select_guard_action(legal_actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [(i, a) for i, a in enumerate(legal_actions) if _guard_score(a) > 0]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda pair: (_guard_score(pair[1]), _safe_score(pair[1]), -pair[0]),
    )[1]


def _select_safe_action(legal_actions: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        enumerate(legal_actions),
        key=lambda pair: (_safe_score(pair[1]), -pair[0]),
    )[1]


# --- Action Application ---


def apply_decision(decision_payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve the fields that would be written to a fighter node.

    Returns a result dict with the resolved queued_action, queued_data, queued_extra.
    """
    action_name = str(decision_payload.get("action", ""))
    if not action_name:
        return {"applied": False, "error": "empty action name"}

    return {
        "applied": True,
        "action": action_name,
        "queued_action": action_name,
        "queued_data": _resolve_queued_data(decision_payload.get("data")),
        "queued_extra": _resolve_queued_extra(decision_payload.get("extra")),
    }


def _resolve_queued_data(data: Any) -> Any:
    if data is None:
        return None
    if isinstance(data, dict):
        return dict(data)
    return None


def _resolve_queued_extra(extra: Any) -> dict[str, Any] | None:
    if extra is None or not isinstance(extra, dict):
        return None

    resolved: dict[str, Any] = {}
    di = extra.get("di")
    if di is not None and isinstance(di, dict):
        resolved["di"] = {"x": int(di.get("x", 0)), "y": int(di.get("y", 0))}
    else:
        resolved["di"] = None

    resolved["feint"] = bool(extra.get("feint", False))
    resolved["reverse"] = bool(extra.get("reverse", False))
    return resolved


# --- Telemetry Event Building ---


def build_event_envelope(
    event_name: str,
    match_id: str,
    *,
    turn_id: int | None = None,
    player_id: str | None = None,
    fallback_reason: str | None = None,
    latency_ms: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a telemetry event envelope matching the protocol Event shape."""
    payload: dict[str, Any] = {
        "match_id": match_id,
        "event": event_name,
    }
    if turn_id is not None:
        payload["turn_id"] = turn_id
    if player_id is not None:
        payload["player_id"] = player_id
    if fallback_reason is not None:
        payload["fallback_reason"] = fallback_reason
    if latency_ms is not None:
        payload["latency_ms"] = latency_ms
    if details is not None:
        payload["details"] = details

    return {
        "type": "event",
        "version": "v1",
        "ts": "2026-01-01T00:00:00Z",  # placeholder for tests
        "payload": payload,
    }
