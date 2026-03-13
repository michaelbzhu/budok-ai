"""Integration tests proving prompt traces are persisted through the live server decision path."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import cast

from websockets.asyncio.client import connect

from yomi_daemon.config import (
    DaemonRuntimeConfig,
    parse_runtime_config_document,
)
from yomi_daemon.protocol import (
    CURRENT_PROTOCOL_VERSION,
    CURRENT_SCHEMA_VERSION,
    DecisionType,
    MessageType,
)
from yomi_daemon.server import DaemonServer
from yomi_daemon.storage.writer import RUNS_DIR
from yomi_daemon.validation import parse_envelope

from tests.daemon._decision_fixtures import build_action, build_observation


def _runtime_config() -> DaemonRuntimeConfig:
    return parse_runtime_config_document(
        {
            "version": "v1",
            "trace_seed": 42,
            "fallback_mode": "safe_continue",
            "timeout_profile": "strict_local",
            "logging": {
                "events": True,
                "prompts": True,
                "raw_provider_payloads": True,
            },
            "policy_mapping": {
                "p1": "baseline/random",
                "p2": "baseline/random",
            },
            "policies": {
                "baseline/random": {
                    "provider": "baseline",
                },
            },
        },
    )


def _build_hello_envelope() -> dict[str, object]:
    return {
        "type": MessageType.HELLO.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-12T00:00:00Z",
        "payload": {
            "game_version": "1.9.20-steam",
            "mod_version": "0.2.0",
            "schema_version": CURRENT_SCHEMA_VERSION,
            "supported_protocol_versions": [CURRENT_PROTOCOL_VERSION.value],
        },
    }


def _build_decision_request_envelope(
    *,
    match_id: str,
    turn_id: int = 1,
    player_id: str = "p1",
) -> dict[str, object]:
    obs = build_observation()
    actions = (
        build_action("guard", description="Block safely."),
        build_action("slash", damage=50.0),
    )
    return {
        "type": MessageType.DECISION_REQUEST.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-12T00:00:01Z",
        "payload": {
            "match_id": match_id,
            "turn_id": turn_id,
            "player_id": player_id,
            "deadline_ms": 2500,
            "state_hash": "state-hash-trace-001",
            "legal_actions_hash": "legal-hash-trace-001",
            "decision_type": DecisionType.TURN_ACTION.value,
            "observation": obs.to_dict(),
            "legal_actions": [a.to_dict() for a in actions],
        },
    }


def _build_match_ended_envelope(*, match_id: str) -> dict[str, object]:
    return {
        "type": MessageType.MATCH_ENDED.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-12T00:01:00Z",
        "payload": {
            "match_id": match_id,
            "winner": "p1",
            "end_reason": "knockout",
            "total_turns": 1,
            "end_tick": 120,
        },
    }


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        cast(dict[str, object], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _find_match_dir(match_id: str) -> Path | None:
    """Find a match artifact dir by match_id suffix."""
    match_dirs = list(RUNS_DIR.glob(f"*{match_id}"))
    if len(match_dirs) == 1:
        return match_dirs[0]
    return None


def test_server_decision_path_writes_decision_artifacts() -> None:
    """The server match loop persists decision artifacts for baseline adapters."""
    config = _runtime_config()
    match_id = f"sdt-{uuid.uuid4().hex[:12]}"

    async def scenario() -> None:
        server = DaemonServer(
            port=0,
            policy_mapping=config.policy_mapping,
            runtime_config=config,
        )
        await server.start()
        try:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await ws.send(json.dumps(_build_hello_envelope()))
                ack_raw = await ws.recv()
                assert isinstance(ack_raw, str)
                ack = parse_envelope(json.loads(ack_raw))
                assert ack.type is MessageType.HELLO_ACK

                await ws.send(json.dumps(_build_decision_request_envelope(match_id=match_id)))
                decision_raw = await ws.recv()
                assert isinstance(decision_raw, str)
                decision_envelope = parse_envelope(json.loads(decision_raw))
                assert decision_envelope.type is MessageType.ACTION_DECISION

                await ws.send(json.dumps(_build_match_ended_envelope(match_id=match_id)))
        finally:
            await server.stop()

    match_dir: Path | None = None
    try:
        asyncio.run(scenario())
        match_dir = _find_match_dir(match_id)
        assert match_dir is not None, f"No artifact dir found for {match_id}"

        decisions = _load_jsonl(match_dir / "decisions.jsonl")
        assert len(decisions) >= 1
        decision_payload = cast(dict[str, object], decisions[0]["decision_payload"])
        assert decision_payload["action"] in ("guard", "slash")
    finally:
        if match_dir is not None and match_dir.exists():
            shutil.rmtree(match_dir)


def test_server_decision_path_persists_events_for_turn_lifecycle() -> None:
    """The server emits TURN_REQUESTED and DECISION_RECEIVED events through the artifact writer."""
    config = _runtime_config()
    match_id = f"sde-{uuid.uuid4().hex[:12]}"

    async def scenario() -> None:
        server = DaemonServer(
            port=0,
            policy_mapping=config.policy_mapping,
            runtime_config=config,
        )
        await server.start()
        try:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await ws.send(json.dumps(_build_hello_envelope()))
                await ws.recv()  # hello_ack

                await ws.send(json.dumps(_build_decision_request_envelope(match_id=match_id)))
                await ws.recv()  # action_decision

                await ws.send(json.dumps(_build_match_ended_envelope(match_id=match_id)))
        finally:
            await server.stop()

    match_dir: Path | None = None
    try:
        asyncio.run(scenario())
        match_dir = _find_match_dir(match_id)
        assert match_dir is not None, f"No artifact dir found for {match_id}"

        events = _load_jsonl(match_dir / "events.jsonl")
        event_names = [cast(dict[str, object], e["payload"]).get("event") for e in events]
        assert "MatchStarted" in event_names
        assert "TurnRequested" in event_names
        assert "DecisionReceived" in event_names
    finally:
        if match_dir is not None and match_dir.exists():
            shutil.rmtree(match_dir)
