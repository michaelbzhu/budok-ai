"""Benchmark regression tests for latency (WU-014)."""

from __future__ import annotations

import asyncio
import json
import time

from websockets.asyncio.client import connect

from tests.daemon.conftest import (
    baseline_runtime_config,
    decision_request_envelope,
    handshake,
    match_ended_envelope,
    running_match_server,
    unique_match_id,
)

_INTEGRATION = True


def test_baseline_latency_p95_under_budget() -> None:
    """20-turn baseline-vs-baseline: p95 wall-clock per turn < 500ms."""
    num_turns = 20

    async def scenario() -> list[float]:
        mid = unique_match_id()
        config = baseline_runtime_config()
        latencies: list[float] = []
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await handshake(ws)
                for turn in range(1, num_turns + 1):
                    player = "p1" if turn % 2 == 1 else "p2"
                    req = decision_request_envelope(
                        match_id=mid, turn_id=turn, player_id=player
                    )
                    t0 = time.monotonic()
                    await ws.send(json.dumps(req))
                    await ws.recv()
                    t1 = time.monotonic()
                    latencies.append((t1 - t0) * 1000)
                await ws.send(
                    json.dumps(
                        match_ended_envelope(match_id=mid, total_turns=num_turns)
                    )
                )
        return latencies

    latencies = asyncio.run(scenario())
    latencies.sort()
    p95_index = int(len(latencies) * 0.95)
    p95 = latencies[min(p95_index, len(latencies) - 1)]
    assert p95 < 500, f"p95 latency {p95:.1f}ms exceeds 500ms budget"
