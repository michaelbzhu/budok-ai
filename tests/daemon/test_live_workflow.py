"""End-to-end live workflow integration tests for WU-021.

Simulates a mod client driving a full multi-turn match against a real daemon
server, including parameterized moves, and verifies complete artifact output.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, cast

from websockets.asyncio.client import connect

from yomi_daemon.config import (
    DaemonRuntimeConfig,
    PolicyConfig,
    TournamentDefaults,
    TransportConfig,
)
from yomi_daemon.protocol import (
    CURRENT_PROTOCOL_VERSION,
    CURRENT_SCHEMA_VERSION,
    ActionDecision,
    CharacterSelectionConfig,
    CharacterSelectionMode,
    DecisionType,
    EventName,
    FallbackMode,
    LoggingConfig,
    MessageType,
    PlayerPolicyMapping,
)
from yomi_daemon.server import DaemonServer
from yomi_daemon.storage.writer import RUNS_DIR
from yomi_daemon.validation import parse_envelope


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _unique_match_id() -> str:
    return f"live-{uuid.uuid4().hex[:12]}"


def _runtime_config(
    *,
    p1: str = "baseline/random",
    p2: str = "baseline/random",
    prompts: bool = True,
) -> DaemonRuntimeConfig:
    policies = {
        "baseline/random": PolicyConfig(provider="baseline"),
        "baseline/block_always": PolicyConfig(provider="baseline"),
    }
    return DaemonRuntimeConfig(
        version="v1",
        transport=TransportConfig(host="127.0.0.1", port=0),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        logging=LoggingConfig(
            events=True, prompts=prompts, raw_provider_payloads=False
        ),
        policy_mapping=PlayerPolicyMapping(p1=p1, p2=p2),
        policies=policies,
        character_selection=CharacterSelectionConfig(
            mode=CharacterSelectionMode.MIRROR
        ),
        tournament=TournamentDefaults(
            format="round_robin",
            mirror_matches_first=True,
            side_swap=True,
            games_per_pair=10,
            fixed_stage="training_room",
        ),
        trace_seed=21,
    )


def _hello_envelope() -> dict[str, object]:
    return {
        "type": MessageType.HELLO.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-13T00:00:00Z",
        "payload": {
            "game_version": "1.9.20-steam",
            "mod_version": "0.0.1",
            "schema_version": CURRENT_SCHEMA_VERSION,
            "supported_protocol_versions": [CURRENT_PROTOCOL_VERSION.value],
        },
    }


def _observation(turn_id: int, player_id: str) -> dict[str, Any]:
    return {
        "tick": 100 + turn_id,
        "frame": 12,
        "active_player": player_id,
        "fighters": [
            {
                "id": "p1",
                "character": "Cowboy",
                "hp": 1000,
                "max_hp": 1000,
                "meter": 1,
                "burst": 1,
                "position": {"x": -5.0, "y": 0.0},
                "velocity": {"x": 0.0, "y": 0.0},
                "facing": "right",
                "current_state": "neutral",
                "combo_count": 0,
                "blockstun": 0,
                "hitlag": 0,
                "state_interruptable": True,
                "can_feint": True,
                "grounded": True,
            },
            {
                "id": "p2",
                "character": "Ninja",
                "hp": 1000,
                "max_hp": 1000,
                "meter": 1,
                "burst": 1,
                "position": {"x": 5.0, "y": 0.0},
                "velocity": {"x": 0.0, "y": 0.0},
                "facing": "left",
                "current_state": "neutral",
                "combo_count": 0,
                "blockstun": 0,
                "hitlag": 0,
                "state_interruptable": True,
                "can_feint": False,
                "grounded": True,
            },
        ],
        "objects": [],
        "stage": {"id": "training_room"},
        "history": [],
    }


def _legal_actions(*, include_parameterized: bool = False) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = [
        {
            "action": "block",
            "label": "Block",
            "payload_spec": {},
            "supports": {
                "di": False,
                "feint": False,
                "reverse": False,
                "prediction": False,
            },
        },
        {
            "action": "attack_a",
            "label": "Attack A",
            "payload_spec": {},
            "supports": {
                "di": True,
                "feint": False,
                "reverse": False,
                "prediction": False,
            },
            "damage": 100.0,
            "startup_frames": 5,
        },
        {
            "action": "move_forward",
            "label": "Move Forward",
            "payload_spec": {},
            "supports": {
                "di": False,
                "feint": False,
                "reverse": False,
                "prediction": False,
            },
        },
    ]
    if include_parameterized:
        actions.append(
            {
                "action": "directional_shot",
                "label": "Directional Shot",
                "payload_spec": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "angle": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 360,
                            "default": 90,
                            "semantic": "Direction angle in degrees",
                        },
                    },
                },
                "supports": {
                    "di": True,
                    "feint": False,
                    "reverse": False,
                    "prediction": False,
                },
                "damage": 80.0,
                "startup_frames": 8,
            }
        )
    return actions


def _decision_request_envelope(
    *,
    match_id: str,
    turn_id: int,
    player_id: str,
    include_parameterized: bool = False,
) -> dict[str, Any]:
    return {
        "type": MessageType.DECISION_REQUEST.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-13T00:00:01Z",
        "payload": {
            "match_id": match_id,
            "turn_id": turn_id,
            "player_id": player_id,
            "deadline_ms": 2500,
            "state_hash": f"state-{turn_id}",
            "legal_actions_hash": f"legal-{turn_id}",
            "decision_type": DecisionType.TURN_ACTION.value,
            "observation": _observation(turn_id, player_id),
            "legal_actions": _legal_actions(
                include_parameterized=include_parameterized
            ),
        },
    }


def _match_ended_envelope(*, match_id: str, total_turns: int) -> dict[str, Any]:
    return {
        "type": MessageType.MATCH_ENDED.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-13T00:01:00Z",
        "payload": {
            "match_id": match_id,
            "winner": "p1",
            "end_reason": "ko",
            "total_turns": total_turns,
            "end_tick": 500,
            "end_frame": 60,
            "errors": [],
        },
    }


def _find_match_dir(match_id: str) -> Path | None:
    match_dirs = list(RUNS_DIR.glob(f"*{match_id}"))
    if len(match_dirs) == 1:
        return match_dirs[0]
    return None


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        cast(dict[str, object], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


TOTAL_TURNS = 6


def test_full_live_workflow_with_artifact_completeness() -> None:
    """Full multi-turn match with both players, parameterized moves, and complete artifact verification."""

    mid = _unique_match_id()
    config = _runtime_config(prompts=True)
    match_dir: Path | None = None

    async def scenario() -> None:
        server = DaemonServer(
            port=0,
            policy_mapping=config.policy_mapping,
            config_snapshot=config.to_config_payload(),
            runtime_config=config,
        )
        await server.start()
        try:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                # Handshake
                await ws.send(json.dumps(_hello_envelope()))
                ack_raw = await ws.recv()
                ack = parse_envelope(json.loads(ack_raw))
                assert ack.type is MessageType.HELLO_ACK

                # Multi-turn match: alternate players, include parameterized on turn 3
                decisions: list[ActionDecision] = []
                for turn in range(1, TOTAL_TURNS + 1):
                    player = "p1" if turn % 2 == 1 else "p2"
                    include_param = turn == 3  # parameterized action on turn 3
                    req = _decision_request_envelope(
                        match_id=mid,
                        turn_id=turn,
                        player_id=player,
                        include_parameterized=include_param,
                    )
                    await ws.send(json.dumps(req))
                    raw_resp = await ws.recv()
                    resp = parse_envelope(json.loads(raw_resp))
                    assert resp.type is MessageType.ACTION_DECISION
                    decisions.append(cast(ActionDecision, resp.payload))

                assert len(decisions) == TOTAL_TURNS

                # All decisions should pick from legal actions
                valid_actions = {
                    "block",
                    "attack_a",
                    "move_forward",
                    "directional_shot",
                }
                for d in decisions:
                    assert d.action in valid_actions

                # Send match_ended
                await ws.send(
                    json.dumps(
                        _match_ended_envelope(match_id=mid, total_turns=TOTAL_TURNS)
                    )
                )
        finally:
            await server.stop()

    try:
        asyncio.run(scenario())
        match_dir = _find_match_dir(mid)
        assert match_dir is not None, f"No artifact dir found for {mid}"

        # --- Artifact completeness checks ---

        # All expected files exist
        assert (match_dir / "manifest.json").exists()
        assert (match_dir / "events.jsonl").exists()
        assert (match_dir / "decisions.jsonl").exists()
        assert (match_dir / "prompts.jsonl").exists()
        assert (match_dir / "metrics.json").exists()
        assert (match_dir / "result.json").exists()
        assert (match_dir / "replay_index.json").exists()
        assert (match_dir / "stderr.log").exists()

        # result.json has winner, end_reason, total_turns, status=completed
        result = json.loads((match_dir / "result.json").read_text())
        assert result["status"] == "completed"
        assert result["winner"] == "p1"
        assert result["end_reason"] == "ko"
        assert result["total_turns"] == TOTAL_TURNS

        # decisions.jsonl has one record per turn
        decisions_records = _load_jsonl(match_dir / "decisions.jsonl")
        assert len(decisions_records) == TOTAL_TURNS

        # events.jsonl has full lifecycle
        events = _load_jsonl(match_dir / "events.jsonl")
        event_names = [
            cast(dict[str, object], e["payload"]).get("event") for e in events
        ]
        assert EventName.MATCH_STARTED.value in event_names
        assert EventName.TURN_REQUESTED.value in event_names
        assert EventName.DECISION_RECEIVED.value in event_names

        # prompts.jsonl exists (baseline adapters have no prompt traces, so it's empty)
        assert (match_dir / "prompts.jsonl").exists()

        # manifest.json has config data
        manifest = json.loads((match_dir / "manifest.json").read_text())
        assert manifest["trace_seed"] == 21
        assert manifest["policy_mapping"]["p1"] == "baseline/random"

        # metrics.json has completed status
        metrics = json.loads((match_dir / "metrics.json").read_text())
        assert metrics["status"] == "completed"
        assert metrics["decision_count"] == TOTAL_TURNS

    finally:
        if match_dir is not None and match_dir.exists():
            shutil.rmtree(match_dir)


def test_parameterized_move_in_live_play() -> None:
    """Verify that parameterized actions (with structured payload_spec) work in live play."""

    mid = _unique_match_id()
    config = _runtime_config()
    match_dir: Path | None = None

    async def scenario() -> list[str]:
        server = DaemonServer(
            port=0,
            policy_mapping=config.policy_mapping,
            config_snapshot=config.to_config_payload(),
            runtime_config=config,
        )
        await server.start()
        actions: list[str] = []
        try:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await ws.send(json.dumps(_hello_envelope()))
                await ws.recv()  # hello_ack

                # Send 3 turns, each with the parameterized directional_shot action
                for turn in range(1, 4):
                    req = _decision_request_envelope(
                        match_id=mid,
                        turn_id=turn,
                        player_id="p1",
                        include_parameterized=True,
                    )
                    await ws.send(json.dumps(req))
                    raw = await ws.recv()
                    resp = parse_envelope(json.loads(raw))
                    decision = cast(ActionDecision, resp.payload)
                    actions.append(decision.action)

                await ws.send(
                    json.dumps(_match_ended_envelope(match_id=mid, total_turns=3))
                )
        finally:
            await server.stop()
        return actions

    try:
        actions = asyncio.run(scenario())
        # All should be valid actions from the extended set
        valid = {"block", "attack_a", "move_forward", "directional_shot"}
        for a in actions:
            assert a in valid
    finally:
        match_dir = _find_match_dir(mid)
        if match_dir is not None and match_dir.exists():
            shutil.rmtree(match_dir)


def test_artifact_completeness_with_match_ended() -> None:
    """Fully finished live match with match_ended produces complete, well-formed artifacts."""

    mid = _unique_match_id()
    config = _runtime_config()
    match_dir: Path | None = None

    async def scenario() -> None:
        server = DaemonServer(
            port=0,
            policy_mapping=config.policy_mapping,
            config_snapshot=config.to_config_payload(),
            runtime_config=config,
        )
        await server.start()
        try:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await ws.send(json.dumps(_hello_envelope()))
                await ws.recv()

                # Single turn + match ended
                await ws.send(
                    json.dumps(
                        _decision_request_envelope(
                            match_id=mid, turn_id=1, player_id="p1"
                        )
                    )
                )
                await ws.recv()

                await ws.send(
                    json.dumps(_match_ended_envelope(match_id=mid, total_turns=1))
                )
        finally:
            await server.stop()

    try:
        asyncio.run(scenario())
        match_dir = _find_match_dir(mid)
        assert match_dir is not None

        result = json.loads((match_dir / "result.json").read_text())
        assert result["status"] == "completed"
        assert result["winner"] == "p1"
        assert result["end_reason"] == "ko"
        assert result["total_turns"] == 1
        assert result.get("match_ended_payload") is not None

        # Verify match_ended_payload is preserved in result
        ended = result["match_ended_payload"]
        assert ended["winner"] == "p1"
        assert ended["end_reason"] == "ko"

    finally:
        if match_dir is not None and match_dir.exists():
            shutil.rmtree(match_dir)
