"""Benchmark regression tests for latency and fallback rate (WU-014)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, cast

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
            ],
        },
    }


def test_baseline_latency_p95_under_budget() -> None:
    """20-turn baseline-vs-baseline: p95 wall-clock per turn < 500ms."""
    num_turns = 20

    async def scenario() -> list[float]:
        mid = _unique_match_id()
        config = _baseline_runtime_config()
        latencies: list[float] = []
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await _handshake(ws)
                for turn in range(1, num_turns + 1):
                    player = "p1" if turn % 2 == 1 else "p2"
                    req = _decision_request_envelope(
                        match_id=mid, turn_id=turn, player_id=player
                    )
                    t0 = time.monotonic()
                    await ws.send(json.dumps(req))
                    await ws.recv()
                    t1 = time.monotonic()
                    latencies.append((t1 - t0) * 1000)
                await ws.send(
                    json.dumps(
                        _match_ended_envelope(match_id=mid, total_turns=num_turns)
                    )
                )
        return latencies

    latencies = asyncio.run(scenario())
    latencies.sort()
    p95_index = int(len(latencies) * 0.95)
    p95 = latencies[min(p95_index, len(latencies) - 1)]
    assert p95 < 500, f"p95 latency {p95:.1f}ms exceeds 500ms budget"


def test_baseline_zero_fallback_rate() -> None:
    """Baseline policies should never trigger fallbacks."""
    num_turns = 20

    async def scenario() -> list[ActionDecision]:
        mid = _unique_match_id()
        config = _baseline_runtime_config()
        decisions: list[ActionDecision] = []
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await _handshake(ws)
                for turn in range(1, num_turns + 1):
                    player = "p1" if turn % 2 == 1 else "p2"
                    req = _decision_request_envelope(
                        match_id=mid, turn_id=turn, player_id=player
                    )
                    await ws.send(json.dumps(req))
                    raw = await ws.recv()
                    resp = parse_envelope(json.loads(raw))
                    decisions.append(cast(ActionDecision, resp.payload))
                await ws.send(
                    json.dumps(
                        _match_ended_envelope(match_id=mid, total_turns=num_turns)
                    )
                )
        return decisions

    decisions = asyncio.run(scenario())
    fallback_count = sum(1 for d in decisions if d.fallback_reason is not None)
    assert fallback_count == 0, f"Expected 0 fallbacks, got {fallback_count}"
