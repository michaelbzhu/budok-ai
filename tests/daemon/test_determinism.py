"""Determinism verification tests for seeded baseline runs (WU-014)."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from websockets.asyncio.client import connect

from yomi_daemon.protocol import (
    CURRENT_PROTOCOL_VERSION,
    ActionDecision,
    DecisionType,
    MessageType,
)
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
            "state_hash": f"state-{turn_id}",
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
                    "supports": {"di": False, "feint": False, "reverse": False},
                },
                {
                    "action": "attack_a",
                    "label": "Attack A",
                    "payload_spec": {},
                    "supports": {"di": True, "feint": False, "reverse": False},
                    "damage": 100.0,
                    "startup_frames": 5,
                },
                {
                    "action": "move_forward",
                    "label": "Move Forward",
                    "payload_spec": {},
                    "supports": {"di": False, "feint": False, "reverse": False},
                },
            ],
        },
    }


async def _run_seeded_match(
    *,
    seed: int,
    match_id: str,
    turns: int = 10,
) -> list[str]:
    """Run a match with the given seed and return the list of actions chosen."""
    config = _baseline_runtime_config(trace_seed=seed)
    actions: list[str] = []
    async with running_match_server(config) as server:
        async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
            await _handshake(ws)
            for turn in range(1, turns + 1):
                player = "p1" if turn % 2 == 1 else "p2"
                req = _decision_request_envelope(
                    match_id=match_id, turn_id=turn, player_id=player
                )
                await ws.send(json.dumps(req))
                raw = await ws.recv()
                resp = parse_envelope(json.loads(raw))
                decision = cast(ActionDecision, resp.payload)
                actions.append(decision.action)
            await ws.send(
                json.dumps(_match_ended_envelope(match_id=match_id, total_turns=turns))
            )
    return actions


def test_seeded_baseline_runs_produce_identical_decisions(tmp_path: Path) -> None:
    """Same seed + same match_id → identical action sequences across two runs."""

    async def scenario() -> None:
        mid = _unique_match_id()
        # Redirect RUNS_DIR to tmp_path subdirs so same match_id doesn't collide
        run1_dir = tmp_path / "run1"
        run1_dir.mkdir()
        run2_dir = tmp_path / "run2"
        run2_dir.mkdir()

        with patch("yomi_daemon.storage.writer.RUNS_DIR", run1_dir):
            run1 = await _run_seeded_match(seed=42, match_id=mid, turns=10)
        with patch("yomi_daemon.storage.writer.RUNS_DIR", run2_dir):
            run2 = await _run_seeded_match(seed=42, match_id=mid, turns=10)
        assert run1 == run2, f"Expected identical sequences, got {run1} vs {run2}"

    asyncio.run(scenario())


def test_decision_logs_identical_across_runs(tmp_path: Path) -> None:
    """Decisions from two identical seeded runs should match (minus timestamps)."""

    async def run_and_collect(mid: str) -> list[dict[str, Any]]:
        config = _baseline_runtime_config(trace_seed=42)
        records: list[dict[str, Any]] = []
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await _handshake(ws)
                for turn in range(1, 6):
                    req = _decision_request_envelope(
                        match_id=mid, turn_id=turn, player_id="p1"
                    )
                    await ws.send(json.dumps(req))
                    raw = await ws.recv()
                    resp = parse_envelope(json.loads(raw))
                    decision = cast(ActionDecision, resp.payload)
                    records.append(
                        {
                            "turn_id": decision.turn_id,
                            "action": decision.action,
                            "policy_id": decision.policy_id,
                        }
                    )
                await ws.send(
                    json.dumps(_match_ended_envelope(match_id=mid, total_turns=5))
                )
        return records

    async def scenario() -> None:
        mid = _unique_match_id()
        run1_dir = tmp_path / "run1"
        run1_dir.mkdir()
        run2_dir = tmp_path / "run2"
        run2_dir.mkdir()

        with patch("yomi_daemon.storage.writer.RUNS_DIR", run1_dir):
            run1 = await run_and_collect(mid)
        with patch("yomi_daemon.storage.writer.RUNS_DIR", run2_dir):
            run2 = await run_and_collect(mid)
        assert run1 == run2

    asyncio.run(scenario())


def test_manifest_metadata_reproducible(tmp_path: Path) -> None:
    """Manifests from two runs have identical trace_seed, policy_mapping, daemon_version."""

    async def run_match(mid: str, runs_dir: Path) -> Path:
        config = _baseline_runtime_config(trace_seed=42)
        with patch("yomi_daemon.storage.writer.RUNS_DIR", runs_dir):
            async with running_match_server(config) as server:
                async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                    await _handshake(ws)
                    req = _decision_request_envelope(
                        match_id=mid, turn_id=1, player_id="p1"
                    )
                    await ws.send(json.dumps(req))
                    await ws.recv()
                    await ws.send(
                        json.dumps(_match_ended_envelope(match_id=mid, total_turns=1))
                    )
                await asyncio.sleep(0.2)
        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir() and mid in d.name]
        assert run_dirs
        return run_dirs[0]

    async def scenario() -> None:
        mid1 = _unique_match_id()
        mid2 = _unique_match_id()
        run1_dir = tmp_path / "run1"
        run1_dir.mkdir()
        run2_dir = tmp_path / "run2"
        run2_dir.mkdir()
        dir1 = await run_match(mid1, run1_dir)
        dir2 = await run_match(mid2, run2_dir)
        m1 = json.loads((dir1 / "manifest.json").read_text())
        m2 = json.loads((dir2 / "manifest.json").read_text())
        assert m1["trace_seed"] == m2["trace_seed"]
        assert m1["policy_mapping"] == m2["policy_mapping"]
        assert m1["daemon_version"] == m2["daemon_version"]
        assert m1["protocol_version"] == m2["protocol_version"]

    asyncio.run(scenario())


def test_different_seeds_produce_different_decisions(tmp_path: Path) -> None:
    """seed=42 vs seed=999 should produce at least one different action over 10 turns."""

    async def scenario() -> None:
        mid = _unique_match_id()
        run1_dir = tmp_path / "run1"
        run1_dir.mkdir()
        run2_dir = tmp_path / "run2"
        run2_dir.mkdir()

        with patch("yomi_daemon.storage.writer.RUNS_DIR", run1_dir):
            run1 = await _run_seeded_match(seed=42, match_id=mid, turns=10)
        with patch("yomi_daemon.storage.writer.RUNS_DIR", run2_dir):
            run2 = await _run_seeded_match(seed=999, match_id=mid, turns=10)
        assert run1 != run2, "Different seeds should produce different sequences"

    asyncio.run(scenario())
