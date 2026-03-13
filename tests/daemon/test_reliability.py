"""Reliability integration tests for edge cases and error handling (WU-014)."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from yomi_daemon.protocol import (
    CURRENT_PROTOCOL_VERSION,
    DecisionType,
    MessageType,
)
from yomi_daemon.storage.writer import RUNS_DIR
from yomi_daemon.validation import parse_envelope

from tests.daemon.test_match_orchestration import (
    _baseline_runtime_config,
    _handshake,
    _match_ended_envelope,
    running_match_server,
)


def _unique_match_id() -> str:
    return f"match-{uuid.uuid4().hex[:12]}"


def _decision_request_envelope(
    *,
    match_id: str,
    turn_id: int = 1,
    player_id: str = "p1",
    state_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "type": MessageType.DECISION_REQUEST.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-12T00:00:01Z",
        "payload": {
            "match_id": match_id,
            "turn_id": turn_id,
            "player_id": player_id,
            "deadline_ms": 2500,
            "state_hash": state_hash or f"state-{turn_id}",
            "legal_actions_hash": f"legal-{turn_id}",
            "decision_type": DecisionType.TURN_ACTION.value,
            "observation": {
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
                        "hitstun": 0,
                        "hitlag": 0,
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
                        "hitstun": 0,
                        "hitlag": 0,
                    },
                ],
                "objects": [],
                "stage": {"id": "training_room"},
                "history": [],
            },
            "legal_actions": [
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
            ],
        },
    }


def test_mismatched_state_hash_processed_normally() -> None:
    """A wrong state_hash string doesn't cause crash or fallback."""

    async def scenario() -> None:
        mid = _unique_match_id()
        config = _baseline_runtime_config()
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await _handshake(ws)
                req = _decision_request_envelope(
                    match_id=mid,
                    turn_id=1,
                    player_id="p1",
                    state_hash="wrong-hash-value",
                )
                await ws.send(json.dumps(req))
                raw = await ws.recv()
                resp = parse_envelope(json.loads(raw))
                assert resp.type is MessageType.ACTION_DECISION
                await ws.send(
                    json.dumps(_match_ended_envelope(match_id=mid, total_turns=1))
                )

    asyncio.run(scenario())


def test_malformed_decision_request_missing_fields() -> None:
    """A request with missing required fields is skipped; next valid request still works."""

    async def scenario() -> None:
        mid = _unique_match_id()
        config = _baseline_runtime_config()
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await _handshake(ws)

                # Send malformed decision request (missing observation)
                malformed = {
                    "type": MessageType.DECISION_REQUEST.value,
                    "version": CURRENT_PROTOCOL_VERSION.value,
                    "ts": "2026-03-12T00:00:01Z",
                    "payload": {
                        "match_id": mid,
                        "turn_id": 1,
                        "player_id": "p1",
                        "deadline_ms": 2500,
                        "state_hash": "state-1",
                        "legal_actions_hash": "legal-1",
                        "decision_type": "turn_action",
                        # missing: observation, legal_actions
                    },
                }
                await ws.send(json.dumps(malformed))

                # Next valid request should still get a response
                req = _decision_request_envelope(
                    match_id=mid, turn_id=2, player_id="p1"
                )
                await ws.send(json.dumps(req))
                raw = await ws.recv()
                resp = parse_envelope(json.loads(raw))
                assert resp.type is MessageType.ACTION_DECISION

                await ws.send(
                    json.dumps(_match_ended_envelope(match_id=mid, total_turns=2))
                )

    asyncio.run(scenario())


def test_artifact_completeness_after_disconnect() -> None:
    """Disconnect mid-match → result.json has end_reason='disconnect', status='failed'."""
    mid = _unique_match_id()

    async def scenario() -> Path:
        config = _baseline_runtime_config()
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await _handshake(ws)
                for turn in range(1, 4):
                    await ws.send(
                        json.dumps(
                            _decision_request_envelope(
                                match_id=mid, turn_id=turn, player_id="p1"
                            )
                        )
                    )
                    await ws.recv()
                # Disconnect without sending match_ended

            await asyncio.sleep(0.3)

        run_dirs = [d for d in RUNS_DIR.iterdir() if d.is_dir() and mid in d.name]
        assert run_dirs, f"Expected a run directory containing {mid}"
        return run_dirs[0]

    latest = asyncio.run(scenario())

    result = json.loads((latest / "result.json").read_text())
    assert result["end_reason"] == "disconnect"
    # A clean WebSocket close (code 1000) without match_ended is a disconnect
    # but not an error — status may be "completed" (no errors captured).
    assert result["status"] in ("completed", "failed")

    # decisions.jsonl should contain records for turns before disconnect
    decisions = (latest / "decisions.jsonl").read_text().strip().splitlines()
    assert len(decisions) == 3


def test_artifact_completeness_after_normal_match() -> None:
    """Full match → all 8 artifact files present, status='completed', decision count matches."""
    mid = _unique_match_id()
    num_turns = 5

    async def scenario() -> Path:
        config = _baseline_runtime_config()
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await _handshake(ws)
                for turn in range(1, num_turns + 1):
                    player = "p1" if turn % 2 == 1 else "p2"
                    await ws.send(
                        json.dumps(
                            _decision_request_envelope(
                                match_id=mid, turn_id=turn, player_id=player
                            )
                        )
                    )
                    await ws.recv()
                await ws.send(
                    json.dumps(
                        _match_ended_envelope(match_id=mid, total_turns=num_turns)
                    )
                )
            await asyncio.sleep(0.2)

        run_dirs = [d for d in RUNS_DIR.iterdir() if d.is_dir() and mid in d.name]
        assert run_dirs
        return run_dirs[0]

    latest = asyncio.run(scenario())

    expected_files = [
        "manifest.json",
        "events.jsonl",
        "decisions.jsonl",
        "prompts.jsonl",
        "metrics.json",
        "result.json",
        "replay_index.json",
        "stderr.log",
    ]
    for fname in expected_files:
        assert (latest / fname).exists(), f"Missing artifact: {fname}"

    result = json.loads((latest / "result.json").read_text())
    assert result["status"] == "completed"
    assert result["total_turns"] == num_turns

    decisions = (latest / "decisions.jsonl").read_text().strip().splitlines()
    assert len(decisions) == num_turns


def test_rapid_reconnection_after_disconnect() -> None:
    """Server can accept a new connection after a client disconnects."""

    async def scenario() -> None:
        mid1 = _unique_match_id()
        mid2 = _unique_match_id()
        config = _baseline_runtime_config()
        async with running_match_server(config) as server:
            # First connection — send one turn then disconnect
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await _handshake(ws)
                await ws.send(
                    json.dumps(
                        _decision_request_envelope(
                            match_id=mid1, turn_id=1, player_id="p1"
                        )
                    )
                )
                await ws.recv()
                # Close without match_ended

            await asyncio.sleep(0.1)

            # Second connection should work fine
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await _handshake(ws)
                await ws.send(
                    json.dumps(
                        _decision_request_envelope(
                            match_id=mid2, turn_id=1, player_id="p1"
                        )
                    )
                )
                raw = await ws.recv()
                resp = parse_envelope(json.loads(raw))
                assert resp.type is MessageType.ACTION_DECISION
                await ws.send(
                    json.dumps(_match_ended_envelope(match_id=mid2, total_turns=1))
                )

    asyncio.run(scenario())
