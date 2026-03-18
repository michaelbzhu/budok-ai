"""Shared fixtures and helpers for daemon integration tests."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from yomi_daemon.config import (
    DaemonRuntimeConfig,
    PolicyConfig,
    TournamentDefaults,
    TransportConfig,
)
from yomi_daemon.protocol import (
    CURRENT_PROTOCOL_VERSION,
    CURRENT_SCHEMA_VERSION,
    CharacterSelectionConfig,
    CharacterSelectionMode,
    DecisionType,
    FallbackMode,
    HelloAck,
    LoggingConfig,
    MessageType,
    PlayerPolicyMapping,
)
from yomi_daemon.server import DaemonServer
from yomi_daemon.validation import parse_envelope


# ---------------------------------------------------------------------------
# Mark registration
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: tests that spin up a real WebSocket server"
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark tests in files that import server infrastructure as integration."""
    integration_marker = pytest.mark.integration
    for item in items:
        # Tests already explicitly marked are left alone
        if "integration" in item.keywords:
            continue
        # Auto-mark based on module-level attribute
        module = getattr(item, "module", None)
        if module and getattr(module, "_INTEGRATION", False):
            item.add_marker(integration_marker)


# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------


def baseline_runtime_config(
    *,
    p1: str = "baseline/random",
    p2: str = "baseline/random",
    trace_seed: int = 42,
    prompts: bool = True,
) -> DaemonRuntimeConfig:
    """Build a minimal DaemonRuntimeConfig for integration tests."""
    policies = {
        "baseline/random": PolicyConfig(provider="baseline"),
        "baseline/block_always": PolicyConfig(provider="baseline"),
        "baseline/greedy_damage": PolicyConfig(provider="baseline"),
        "baseline/scripted_safe": PolicyConfig(provider="baseline"),
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
            mode=CharacterSelectionMode.MIRROR,
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


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


def unique_match_id(prefix: str = "match") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def hello_envelope(
    *,
    auth_token: str | None = None,
    supported_versions: list[str] | None = None,
    schema_version: str = CURRENT_SCHEMA_VERSION,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "game_version": "1.0.0",
        "mod_version": "0.1.0",
        "schema_version": schema_version,
        "supported_protocol_versions": supported_versions
        or [CURRENT_PROTOCOL_VERSION.value],
    }
    if auth_token is not None:
        payload["auth_token"] = auth_token
    return {
        "type": MessageType.HELLO.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-12T00:00:00Z",
        "payload": payload,
    }


def decision_request_envelope(
    *,
    match_id: str,
    turn_id: int = 1,
    player_id: str = "p1",
    state_hash: str | None = None,
    observation: dict[str, Any] | None = None,
    legal_actions: list[dict[str, Any]] | None = None,
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
            "observation": observation or _default_observation(turn_id, player_id),
            "legal_actions": legal_actions or _default_legal_actions(),
        },
    }


def match_ended_envelope(
    *, match_id: str, total_turns: int = 5, winner: str = "p1"
) -> dict[str, Any]:
    return {
        "type": MessageType.MATCH_ENDED.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-12T00:01:00Z",
        "payload": {
            "match_id": match_id,
            "winner": winner,
            "end_reason": "ko",
            "total_turns": total_turns,
            "end_tick": 500,
            "end_frame": 60,
            "errors": [],
        },
    }


# ---------------------------------------------------------------------------
# Server context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def running_match_server(
    runtime_config: DaemonRuntimeConfig | None = None,
    *,
    auth_secret: str | None = None,
) -> AsyncIterator[DaemonServer]:
    config = runtime_config or baseline_runtime_config()
    server = DaemonServer(
        port=0,
        policy_mapping=config.policy_mapping,
        config_snapshot=config.to_config_payload(),
        runtime_config=config,
        auth_secret=auth_secret,
    )
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


async def handshake(ws: Any, *, auth_token: str | None = None) -> HelloAck:
    await ws.send(json.dumps(hello_envelope(auth_token=auth_token)))
    response = await ws.recv()
    envelope = parse_envelope(json.loads(response))
    assert isinstance(envelope.payload, HelloAck)
    return envelope.payload


# ---------------------------------------------------------------------------
# Default observation / legal actions (shared across many integration tests)
# ---------------------------------------------------------------------------


def _default_observation(turn_id: int, player_id: str) -> dict[str, Any]:
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


def _default_legal_actions() -> list[dict[str, Any]]:
    return [
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
