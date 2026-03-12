from __future__ import annotations

from yomi_daemon.protocol import (
    ActionDecision,
    DIVector,
    DecisionExtras,
    DecisionRequest,
    DecisionType,
    FighterObservation,
    JsonObject,
    LegalAction,
    LegalActionSupports,
    Observation,
    Vector2,
)


def build_observation() -> Observation:
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


def build_action(
    action_id: str,
    *,
    payload_spec: JsonObject | None = None,
    di: bool = False,
    feint: bool = False,
    reverse: bool = False,
    damage: float | None = None,
    startup_frames: int | None = None,
    meter_cost: int | None = None,
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
        range=None,
        meter_cost=meter_cost,
        description=description,
    )


def build_request(
    legal_actions: tuple[LegalAction, ...],
    *,
    match_id: str = "match-007",
    turn_id: int = 9,
    deadline_ms: int = 2500,
) -> DecisionRequest:
    return DecisionRequest(
        match_id=match_id,
        turn_id=turn_id,
        player_id="p1",
        deadline_ms=deadline_ms,
        state_hash="state-hash-007",
        legal_actions_hash="legal-hash-007",
        decision_type=DecisionType.TURN_ACTION,
        observation=build_observation(),
        legal_actions=legal_actions,
        trace_seed=7,
    )


def build_decision(
    request: DecisionRequest,
    *,
    action: str,
    data: JsonObject | None = None,
    di: tuple[int, int] | None = None,
    feint: bool = False,
    reverse: bool = False,
) -> ActionDecision:
    return ActionDecision(
        match_id=request.match_id,
        turn_id=request.turn_id,
        action=action,
        data=data,
        extra=DecisionExtras(
            di=DIVector(x=di[0], y=di[1]) if di is not None else None,
            feint=feint,
            reverse=reverse,
        ),
        policy_id="provider/mock",
    )
