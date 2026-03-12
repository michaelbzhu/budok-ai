from __future__ import annotations

import asyncio

from yomi_daemon.adapters import build_player_policy_adapters, build_policy_registry
from yomi_daemon.config import parse_runtime_config_document
from yomi_daemon.protocol import (
    ActionDecision,
    DIVector,
    DecisionRequest,
    DecisionType,
    FighterObservation,
    JsonObject,
    LegalAction,
    LegalActionSupports,
    Observation,
    PlayerSlot,
    Vector2,
)
from yomi_daemon.validation import validate_model


def _observation() -> Observation:
    return Observation(
        tick=100,
        frame=12,
        active_player="p1",
        fighters=(
            FighterObservation(
                id="p1",
                character="Cowboy",
                hp=1000,
                max_hp=1000,
                meter=1,
                burst=1,
                position=Vector2(x=-5.0, y=0.0),
                velocity=Vector2(x=0.0, y=0.0),
                facing="right",
                current_state="neutral",
                combo_count=0,
                hitstun=0,
                hitlag=0,
            ),
            FighterObservation(
                id="p2",
                character="Ninja",
                hp=1000,
                max_hp=1000,
                meter=1,
                burst=1,
                position=Vector2(x=5.0, y=0.0),
                velocity=Vector2(x=0.0, y=0.0),
                facing="left",
                current_state="neutral",
                combo_count=0,
                hitstun=0,
                hitlag=0,
            ),
        ),
        objects=(),
        stage={"id": "training_room"},
        history=(),
    )


def _action(
    action_id: str,
    *,
    payload_spec: JsonObject | None = None,
    damage: float | None = None,
    startup_frames: int | None = None,
    range: float | None = None,
    meter_cost: int | None = None,
    di: bool = False,
    feint: bool = False,
    reverse: bool = False,
    label: str | None = None,
    description: str | None = None,
) -> LegalAction:
    return LegalAction(
        action=action_id,
        label=label,
        payload_spec=payload_spec or {},
        supports=LegalActionSupports(di=di, feint=feint, reverse=reverse),
        damage=damage,
        startup_frames=startup_frames,
        range=range,
        meter_cost=meter_cost,
        description=description,
    )


def _request(
    actions: tuple[LegalAction, ...],
    *,
    trace_seed: int = 17,
    turn_id: int = 7,
) -> DecisionRequest:
    return DecisionRequest(
        match_id="match-006",
        turn_id=turn_id,
        player_id="p1",
        deadline_ms=2500,
        state_hash="state-hash-006",
        legal_actions_hash="legal-hash-006",
        decision_type=DecisionType.TURN_ACTION,
        observation=_observation(),
        legal_actions=actions,
        trace_seed=trace_seed,
    )


def _runtime_config() -> dict[str, object]:
    return {
        "version": "v1",
        "trace_seed": 99,
        "policy_mapping": {
            "p1": "baseline/scripted_safe",
            "p2": "baseline/greedy_damage",
        },
        "policies": {
            "baseline/random": {"provider": "baseline", "prompt_version": "none"},
            "baseline/block_always": {"provider": "baseline", "prompt_version": "none"},
            "baseline/greedy_damage": {
                "provider": "baseline",
                "prompt_version": "none",
            },
            "baseline/scripted_safe": {
                "provider": "baseline",
                "prompt_version": "none",
            },
        },
    }


def _decide(policy_id: str, request: DecisionRequest) -> ActionDecision:
    runtime_config = parse_runtime_config_document(_runtime_config())
    registry = build_policy_registry(runtime_config)
    return asyncio.run(registry[policy_id].decide(request))


def test_build_player_policy_adapters_assigns_slots_from_runtime_config() -> None:
    runtime_config = parse_runtime_config_document(_runtime_config())
    registry = build_policy_registry(runtime_config)
    assignments = build_player_policy_adapters(runtime_config, registry=registry)

    assert set(registry) == {
        "baseline/random",
        "baseline/block_always",
        "baseline/greedy_damage",
        "baseline/scripted_safe",
    }
    assert assignments[PlayerSlot.P1].id == "baseline/scripted_safe"
    assert assignments[PlayerSlot.P2].id == "baseline/greedy_damage"
    assert assignments[PlayerSlot.P1] is registry["baseline/scripted_safe"]


def test_random_baseline_is_deterministic_for_seeded_requests() -> None:
    request = _request(
        (
            _action("slash", damage=40.0),
            _action("jump"),
            _action("throw", payload_spec={"target": {"type": "enemy"}}),
        )
    )

    first = _decide("baseline/random", request)
    second = _decide("baseline/random", request)

    assert first == second
    validate_model(first)


def test_block_always_prefers_defensive_actions_and_disables_unsupported_extras() -> (
    None
):
    decision = _decide(
        "baseline/block_always",
        _request(
            (
                _action("slash", damage=85.0, reverse=True, di=True),
                _action("guard", description="Block and wait safely."),
                _action("jab", damage=15.0),
            )
        ),
    )

    assert decision.action == "guard"
    assert decision.data == {}
    assert decision.extra.di is None
    assert decision.extra.feint is False
    assert decision.extra.reverse is False
    validate_model(decision)


def test_greedy_damage_prefers_high_damage_then_lower_startup() -> None:
    decision = _decide(
        "baseline/greedy_damage",
        _request(
            (
                _action("heavy_slash", damage=120.0, startup_frames=9, range=12.0),
                _action("fast_slash", damage=120.0, startup_frames=5, range=8.0),
                _action("guard"),
            )
        ),
    )

    assert decision.action == "fast_slash"
    validate_model(decision)


def test_greedy_damage_uses_keyword_fallbacks_when_metadata_is_missing() -> None:
    decision = _decide(
        "baseline/greedy_damage",
        _request(
            (
                _action("wait"),
                _action("dash"),
                _action("slash", description="Fast advancing attack."),
            )
        ),
    )

    assert decision.action == "slash"


def test_scripted_safe_prefers_low_commitment_resolved_actions() -> None:
    decision = _decide(
        "baseline/scripted_safe",
        _request(
            (
                _action(
                    "super_slash",
                    payload_spec={"target": {"type": "enemy"}},
                    meter_cost=2,
                    startup_frames=18,
                    damage=180.0,
                ),
                _action("wait"),
                _action("backdash", description="Create space safely."),
            )
        ),
    )

    assert decision.action == "backdash"
    assert decision.data == {}
    validate_model(decision)


def test_baselines_resolve_supported_payloads_and_keep_supported_di_in_bounds() -> None:
    request = _request(
        (
            _action(
                "throw",
                payload_spec={
                    "target": {"type": "enemy"},
                    "strength": {"type": "integer", "minimum": 1},
                },
                di=True,
            ),
        )
    )

    for policy_id in (
        "baseline/random",
        "baseline/block_always",
        "baseline/greedy_damage",
        "baseline/scripted_safe",
    ):
        decision = _decide(policy_id, request)
        assert decision.data == {"target": "enemy", "strength": 1}
        assert decision.extra.di == DIVector(x=0, y=0)
        validate_model(decision)
