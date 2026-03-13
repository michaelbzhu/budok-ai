# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DAEMON_SRC = REPO_ROOT / "daemon" / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(DAEMON_SRC) not in sys.path:
    sys.path.insert(0, str(DAEMON_SRC))

from scripts.mod_runtime_harness import (
    MatchIdentifierFactory,
    build_match_ended_envelope,
    build_match_ended_payload,
    evaluate_runtime_compatibility,
)


def _supported_fighters() -> dict[str, dict[str, set[str]]]:
    common_properties = {
        "hp",
        "queued_action",
        "queued_data",
        "queued_extra",
        "state_interruptable",
        "game_over",
        "state_tick",
    }
    common_methods = {"on_action_selected"}
    return {
        "p1": {"properties": common_properties, "methods": common_methods},
        "p2": {"properties": common_properties, "methods": common_methods},
    }


def test_runtime_compatibility_accepts_supported_build_surface() -> None:
    result = evaluate_runtime_compatibility(
        live_game_version="1.9.20-steam",
        engine_version="3.5.1-stable",
        game_script_path="res://game.gd",
        game_signals=["player_actionable", "game_ended", "game_won"],
        scene_nodes=["P1ActionButtons", "P2ActionButtons"],
        game_properties=["p1", "p2", "current_tick", "game_end_tick", "time"],
        fighters=_supported_fighters(),
    )

    assert result["compatible"] is True
    assert result["state"] == "compatible"
    assert result["errors"] == []


def test_runtime_compatibility_rejects_missing_scene_node() -> None:
    result = evaluate_runtime_compatibility(
        live_game_version="1.9.20-steam",
        engine_version="3.5.1-stable",
        game_script_path="res://game.gd",
        game_signals=["player_actionable", "game_ended", "game_won"],
        scene_nodes=["P1ActionButtons"],
        game_properties=["p1", "p2", "current_tick", "game_end_tick", "time"],
        fighters=_supported_fighters(),
    )

    assert result["compatible"] is False
    assert "missing required scene node: P2ActionButtons" in result["errors"]


def test_runtime_compatibility_rejects_unsupported_game_version() -> None:
    result = evaluate_runtime_compatibility(
        live_game_version="1.9.21-steam",
        engine_version="3.5.1-stable",
        game_script_path="res://game.gd",
        game_signals=["player_actionable", "game_ended", "game_won"],
        scene_nodes=["P1ActionButtons", "P2ActionButtons"],
        game_properties=["p1", "p2", "current_tick", "game_end_tick", "time"],
        fighters=_supported_fighters(),
    )

    assert result["compatible"] is False
    assert (
        "unsupported game version: expected 1.9.20-steam, got 1.9.21-steam"
        in result["errors"]
    )


def test_runtime_compatibility_rejects_missing_match_signal() -> None:
    result = evaluate_runtime_compatibility(
        live_game_version="1.9.20-steam",
        engine_version="3.5.1-stable",
        game_script_path="res://game.gd",
        game_signals=["player_actionable", "game_ended"],
        scene_nodes=["P1ActionButtons", "P2ActionButtons"],
        game_properties=["p1", "p2", "current_tick", "game_end_tick", "time"],
        fighters=_supported_fighters(),
    )

    assert result["compatible"] is False
    assert "missing required game signal: game_won" in result["errors"]


def test_match_identifier_factory_uses_injected_tokens() -> None:
    values = iter(["abc123", "deadbeef"])
    factory = MatchIdentifierFactory(token_factory=lambda: next(values))

    assert factory.new_match_id() == "match-abc123"
    assert factory.new_match_id(prefix="custom") == "custom-deadbeef"


def test_match_ended_payload_prefers_signal_winner_and_new_replay() -> None:
    payload = build_match_ended_payload(
        match_id="match-123",
        total_turns=6,
        current_tick=420,
        time_limit=999,
        p1_hp=250,
        p2_hp=0,
        p1_state_tick=14,
        p2_state_tick=9,
        winner_signal=1,
        game_end_tick=417,
        replay_files_before={"user://replay/autosave/old.replay": 10.0},
        replay_files_after={
            "user://replay/autosave/old.replay": 10.0,
            "user://replay/autosave/new.replay": 20.0,
        },
    )

    assert payload == {
        "match_id": "match-123",
        "winner": "p1",
        "end_reason": "ko",
        "total_turns": 6,
        "end_tick": 417,
        "end_frame": 14,
        "errors": [],
        "replay_path": "user://replay/autosave/new.replay",
    }
    envelope = build_match_ended_envelope(payload)
    assert envelope["type"] == "match_ended"
    assert envelope["payload"]["winner"] == "p1"


def test_match_ended_payload_uses_timeout_and_draw_when_needed() -> None:
    payload = build_match_ended_payload(
        match_id="match-124",
        total_turns=2,
        current_tick=601,
        time_limit=600,
        p1_hp=50,
        p2_hp=50,
        p1_state_tick=3,
        p2_state_tick=7,
        winner_signal=0,
        replay_files_before={},
        replay_files_after={},
    )

    assert payload["winner"] is None
    assert payload["end_reason"] == "timeout"
    assert payload["end_frame"] == 7
    assert "replay_path" not in payload
