#!/usr/bin/env python3
"""Standalone latency and fallback benchmark for the YOMI daemon.

Usage:
    uv run --project daemon python scripts/benchmark_latency.py
    uv run --project daemon python scripts/benchmark_latency.py --profile strict_local --turns 30
    uv run --project daemon python scripts/benchmark_latency.py --profile llm_tournament --policy baseline/random
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
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
    ActionDecision,
    CharacterSelectionConfig,
    CharacterSelectionMode,
    DecisionType,
    FallbackMode,
    HelloAck,
    LoggingConfig,
    MessageType,
    PlayerPolicyMapping,
    TimeoutProfile,
)
from yomi_daemon.server import DaemonServer
from yomi_daemon.validation import parse_envelope


# Spec budgets
BUDGETS: dict[str, dict[str, float]] = {
    "strict_local": {"p95_ms": 1200, "fallback_rate_pct": 0},
    "llm_tournament": {"p95_ms": 15000, "fallback_rate_pct": 5},
}


def _build_config(profile: str, policy: str, trace_seed: int) -> DaemonRuntimeConfig:
    policies = {policy: PolicyConfig(provider="baseline")}
    return DaemonRuntimeConfig(
        version="v1",
        transport=TransportConfig(host="127.0.0.1", port=0),
        timeout_profile=TimeoutProfile(profile),
        decision_timeout_ms=2500 if profile == "strict_local" else 10000,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        logging=LoggingConfig(events=True, prompts=False, raw_provider_payloads=False),
        policy_mapping=PlayerPolicyMapping(p1=policy, p2=policy),
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
        trace_seed=trace_seed,
    )


def _hello_envelope() -> dict[str, object]:
    return {
        "type": MessageType.HELLO.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-12T00:00:00Z",
        "payload": {
            "game_version": "1.0.0",
            "mod_version": "0.1.0",
            "schema_version": "v1",
            "supported_protocol_versions": [CURRENT_PROTOCOL_VERSION.value],
        },
    }


def _decision_request_envelope(
    match_id: str, turn_id: int, player_id: str
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


def _match_ended_envelope(match_id: str, total_turns: int) -> dict[str, Any]:
    return {
        "type": MessageType.MATCH_ENDED.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-12T00:01:00Z",
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


def percentile(sorted_values: list[float], p: float) -> float:
    idx = int(len(sorted_values) * p / 100)
    return sorted_values[min(idx, len(sorted_values) - 1)]


async def run_benchmark(
    profile: str, turns: int, policy: str, trace_seed: int
) -> dict[str, Any]:
    config = _build_config(profile, policy, trace_seed)
    match_id = f"bench-{uuid.uuid4().hex[:8]}"

    server = DaemonServer(
        port=0,
        policy_mapping=config.policy_mapping,
        config_snapshot=config.to_config_payload(),
        runtime_config=config,
    )
    await server.start()

    latencies: list[float] = []
    fallbacks = 0

    try:
        async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
            await ws.send(json.dumps(_hello_envelope()))
            raw_ack = await ws.recv()
            ack_env = parse_envelope(json.loads(raw_ack))
            assert isinstance(ack_env.payload, HelloAck)

            for turn in range(1, turns + 1):
                player = "p1" if turn % 2 == 1 else "p2"
                req = _decision_request_envelope(match_id, turn, player)
                t0 = time.monotonic()
                await ws.send(json.dumps(req))
                raw = await ws.recv()
                t1 = time.monotonic()
                latencies.append((t1 - t0) * 1000)

                resp = parse_envelope(json.loads(raw))
                decision = cast(ActionDecision, resp.payload)
                if decision.fallback_reason is not None:
                    fallbacks += 1

            await ws.send(json.dumps(_match_ended_envelope(match_id, turns)))
    finally:
        await server.stop()

    latencies.sort()
    fallback_rate = (fallbacks / turns * 100) if turns > 0 else 0.0

    return {
        "profile": profile,
        "policy": policy,
        "turns": turns,
        "p50_ms": round(percentile(latencies, 50), 2),
        "p95_ms": round(percentile(latencies, 95), 2),
        "p99_ms": round(percentile(latencies, 99), 2),
        "fallback_count": fallbacks,
        "fallback_rate_pct": round(fallback_rate, 2),
    }


def print_report(results: dict[str, Any]) -> None:
    profile = results["profile"]
    budget = BUDGETS.get(profile, {})

    print(f"\n{'=' * 60}")
    print("  YOMI Daemon Latency Benchmark")
    print(f"{'=' * 60}")
    print(f"  Profile:  {profile}")
    print(f"  Policy:   {results['policy']}")
    print(f"  Turns:    {results['turns']}")
    print(f"{'─' * 60}")
    print(f"  {'Metric':<25} {'Value':>10} {'Budget':>10} {'Status':>10}")
    print(f"  {'─' * 55}")

    p95 = results["p95_ms"]
    p95_budget = budget.get("p95_ms", float("inf"))
    p95_ok = p95 <= p95_budget
    print(f"  {'p50 latency (ms)':<25} {results['p50_ms']:>10.2f} {'':>10} {'':>10}")
    print(
        f"  {'p95 latency (ms)':<25} {p95:>10.2f} {p95_budget:>10.0f} {'PASS' if p95_ok else 'FAIL':>10}"
    )
    print(f"  {'p99 latency (ms)':<25} {results['p99_ms']:>10.2f} {'':>10} {'':>10}")

    fb_rate = results["fallback_rate_pct"]
    fb_budget = budget.get("fallback_rate_pct", float("inf"))
    fb_ok = fb_rate <= fb_budget
    print(
        f"  {'Fallback rate (%)':<25} {fb_rate:>10.2f} {fb_budget:>10.1f} {'PASS' if fb_ok else 'FAIL':>10}"
    )
    print(f"  {'Fallback count':<25} {results['fallback_count']:>10}")
    print(f"{'=' * 60}\n")

    if not p95_ok or not fb_ok:
        print("  RESULT: FAIL — budget exceeded")
    else:
        print("  RESULT: PASS — all budgets met")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="YOMI daemon latency benchmark")
    parser.add_argument(
        "--profile",
        choices=["strict_local", "llm_tournament"],
        default="strict_local",
        help="Timeout profile to benchmark (default: strict_local)",
    )
    parser.add_argument(
        "--turns",
        type=int,
        default=20,
        help="Number of turns to simulate (default: 20)",
    )
    parser.add_argument(
        "--policy",
        default="baseline/random",
        help="Policy ID to use (default: baseline/random)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Trace seed (default: 42)",
    )
    args = parser.parse_args()

    results = asyncio.run(
        run_benchmark(args.profile, args.turns, args.policy, args.seed)
    )
    print_report(results)

    budget = BUDGETS.get(args.profile, {})
    if results["p95_ms"] > budget.get("p95_ms", float("inf")):
        sys.exit(1)
    if results["fallback_rate_pct"] > budget.get("fallback_rate_pct", float("inf")):
        sys.exit(1)


if __name__ == "__main__":
    main()
