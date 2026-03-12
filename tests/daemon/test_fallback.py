from __future__ import annotations

from itertools import product

from tests.daemon._decision_fixtures import build_action, build_decision, build_request
from yomi_daemon.fallback import build_fallback_decision
from yomi_daemon.protocol import FallbackMode, FallbackReason
from yomi_daemon.validation import is_replayable_decision


def test_safe_continue_prefers_conservative_actions() -> None:
    request = build_request(
        (
            build_action("super_slash", meter_cost=3, startup_frames=24, damage=180.0),
            build_action("wait", description="Hold position safely."),
            build_action("backdash", description="Create space safely."),
        )
    )

    selection = build_fallback_decision(
        request,
        fallback_reason=FallbackReason.TIMEOUT,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
    )

    assert selection.strategy is FallbackMode.SAFE_CONTINUE
    assert selection.decision.action == "backdash"
    assert selection.decision.fallback_reason is FallbackReason.TIMEOUT
    assert selection.decision.policy_id == "fallback/safe_continue"


def test_heuristic_guard_prefers_guard_actions_when_available() -> None:
    request = build_request(
        (
            build_action("slash", damage=70.0),
            build_action("guard", description="Block incoming pressure."),
            build_action("dash"),
        )
    )

    selection = build_fallback_decision(
        request,
        fallback_reason=FallbackReason.DISCONNECT,
        fallback_mode=FallbackMode.HEURISTIC_GUARD,
    )

    assert selection.strategy is FallbackMode.HEURISTIC_GUARD
    assert selection.decision.action == "guard"
    assert selection.decision.fallback_reason is FallbackReason.DISCONNECT


def test_last_valid_replayable_reuses_prior_decision_when_still_legal() -> None:
    request = build_request(
        (
            build_action(
                "slash",
                payload_spec={"target": {"type": "enemy"}},
                reverse=True,
            ),
            build_action("guard"),
        ),
        turn_id=10,
    )
    last_valid = build_decision(
        build_request(
            (
                build_action(
                    "slash",
                    payload_spec={"target": {"type": "enemy"}},
                    reverse=True,
                ),
            ),
            turn_id=9,
        ),
        action="slash",
        data={"target": "enemy"},
        reverse=True,
    )

    selection = build_fallback_decision(
        request,
        fallback_reason=FallbackReason.MALFORMED_OUTPUT,
        fallback_mode=FallbackMode.LAST_VALID_REPLAYABLE,
        last_valid_decision=last_valid,
    )

    assert selection.strategy is FallbackMode.LAST_VALID_REPLAYABLE
    assert selection.decision.action == "slash"
    assert selection.decision.turn_id == request.turn_id
    assert selection.decision.extra.reverse is True
    assert selection.decision.policy_id == "fallback/last_valid_replayable"


def test_last_valid_replayable_falls_back_to_guard_when_prior_action_is_not_legal() -> (
    None
):
    request = build_request((build_action("guard"), build_action("wait")))
    last_valid = build_decision(request, action="slash")

    selection = build_fallback_decision(
        request,
        fallback_reason=FallbackReason.ILLEGAL_OUTPUT,
        fallback_mode=FallbackMode.LAST_VALID_REPLAYABLE,
        last_valid_decision=last_valid,
    )

    assert selection.strategy is FallbackMode.HEURISTIC_GUARD
    assert selection.decision.action == "guard"


def test_replayable_legality_checks_hold_across_generated_support_variants() -> None:
    for (
        action_present,
        supports_di,
        supports_feint,
        supports_reverse,
        payload_valid,
    ) in product(
        (False, True),
        (False, True),
        (False, True),
        (False, True),
        (False, True),
    ):
        action_id = "slash" if action_present else "guard"
        request = build_request(
            (
                build_action(
                    action_id,
                    payload_spec={"target": {"type": "enemy"}},
                    di=supports_di,
                    feint=supports_feint,
                    reverse=supports_reverse,
                ),
            )
        )
        prior_request = build_request(
            (
                build_action(
                    "slash",
                    payload_spec={"target": {"type": "enemy"}},
                    di=True,
                    feint=True,
                    reverse=True,
                ),
            ),
            turn_id=request.turn_id - 1,
        )
        prior_decision = build_decision(
            prior_request,
            action="slash",
            data={"target": "enemy" if payload_valid else 7},
            di=(0, 0),
            feint=True,
            reverse=True,
        )

        expected = (
            action_present
            and supports_di
            and supports_feint
            and supports_reverse
            and payload_valid
        )
        assert is_replayable_decision(request, prior_decision) is expected
