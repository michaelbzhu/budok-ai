"""Python mirror of WU-017 mod-side runtime checks and match finalization."""
# ruff: noqa: E402

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_SRC = REPO_ROOT / "daemon" / "src"
if str(DAEMON_SRC) not in sys.path:
    sys.path.insert(0, str(DAEMON_SRC))

from yomi_daemon.ids import prefixed_identifier
from yomi_daemon.protocol import CURRENT_PROTOCOL_VERSION


SUPPORTED_BUILD_ID = 16151810
SUPPORTED_GAME_VERSION = "supported-build-16151810"
SUPPORTED_GLOBAL_VERSION = "1.9.20-steam"
SUPPORTED_ENGINE_VERSION = "3.5.1"
REQUIRED_GAME_SIGNALS = ("player_actionable", "game_ended", "game_won")
REQUIRED_ACTION_BUTTON_NODES = ("P1ActionButtons", "P2ActionButtons")
REQUIRED_GAME_PROPERTIES = ("p1", "p2", "current_tick", "game_end_tick", "time")
REQUIRED_FIGHTER_PROPERTIES = (
    "hp",
    "queued_action",
    "queued_data",
    "queued_extra",
    "state_interruptable",
    "game_over",
    "state_tick",
)
REQUIRED_FIGHTER_METHODS = ("on_action_selected",)


@dataclass(frozen=True, slots=True)
class MatchIdentifierFactory:
    token_factory: Callable[[], str]

    def new_match_id(self, prefix: str = "match") -> str:
        return prefixed_identifier(prefix, self.token_factory())


def evaluate_runtime_compatibility(
    *,
    expected_game_version: str = SUPPORTED_GAME_VERSION,
    live_game_version: str,
    engine_version: str,
    game_script_path: str,
    game_signals: Sequence[str],
    scene_nodes: Sequence[str],
    game_properties: Iterable[str],
    fighters: Mapping[str, Mapping[str, Sequence[str] | set[str]]],
) -> dict[str, Any]:
    errors: list[str] = []
    details = {
        "supported_build_id": SUPPORTED_BUILD_ID,
        "supported_game_version": SUPPORTED_GAME_VERSION,
        "supported_global_version": SUPPORTED_GLOBAL_VERSION,
        "supported_engine_version": SUPPORTED_ENGINE_VERSION,
        "expected_game_version": expected_game_version,
        "live_game_version": live_game_version,
        "engine_version": engine_version,
        "game_script_path": game_script_path,
        "action_button_nodes": sorted(
            node_name
            for node_name in scene_nodes
            if node_name in REQUIRED_ACTION_BUTTON_NODES
        ),
    }

    if live_game_version != SUPPORTED_GLOBAL_VERSION:
        errors.append(
            f"unsupported game version: expected {SUPPORTED_GLOBAL_VERSION}, got {live_game_version}"
        )
    if not engine_version.startswith(SUPPORTED_ENGINE_VERSION):
        errors.append(
            f"unsupported engine version: expected {SUPPORTED_ENGINE_VERSION}.x, got {engine_version}"
        )
    if not game_script_path.endswith("game.gd"):
        errors.append(
            f"unsupported game script path: expected suffix game.gd, got {game_script_path}"
        )

    signal_names = set(game_signals)
    for signal_name in REQUIRED_GAME_SIGNALS:
        if signal_name not in signal_names:
            errors.append(f"missing required game signal: {signal_name}")

    property_names = set(game_properties)
    for property_name in REQUIRED_GAME_PROPERTIES:
        if property_name not in property_names:
            errors.append(f"missing required game property: {property_name}")

    root_nodes = set(scene_nodes)
    for node_name in REQUIRED_ACTION_BUTTON_NODES:
        if node_name not in root_nodes:
            errors.append(f"missing required scene node: {node_name}")

    for player_id in ("p1", "p2"):
        fighter = fighters.get(player_id)
        if fighter is None:
            errors.append(f"missing fighter object: {player_id}")
            continue
        fighter_properties = set(fighter.get("properties", ()))
        fighter_methods = set(fighter.get("methods", ()))
        for property_name in REQUIRED_FIGHTER_PROPERTIES:
            if property_name not in fighter_properties:
                errors.append(f"missing {player_id} fighter property: {property_name}")
        for method_name in REQUIRED_FIGHTER_METHODS:
            if method_name not in fighter_methods:
                errors.append(f"missing {player_id} fighter method: {method_name}")

    return {
        "compatible": not errors,
        "state": "compatible" if not errors else "compatibility_failed",
        "errors": errors,
        "details": details,
    }


def build_match_ended_payload(
    *,
    match_id: str,
    total_turns: int,
    current_tick: int,
    time_limit: int,
    p1_hp: int,
    p2_hp: int,
    p1_state_tick: int,
    p2_state_tick: int,
    winner_signal: int | None,
    game_end_tick: int | None = None,
    replay_files_before: Mapping[str, float] | None = None,
    replay_files_after: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "match_id": match_id,
        "winner": _normalize_winner(winner_signal, p1_hp=p1_hp, p2_hp=p2_hp),
        "end_reason": _derive_end_reason(
            current_tick=current_tick,
            time_limit=time_limit,
            p1_hp=p1_hp,
            p2_hp=p2_hp,
        ),
        "total_turns": total_turns,
        "end_tick": game_end_tick if game_end_tick is not None else current_tick,
        "end_frame": max(p1_state_tick, p2_state_tick),
        "errors": [],
    }
    replay_path = _find_replay_path(
        before=replay_files_before or {},
        after=replay_files_after or {},
    )
    if replay_path is not None:
        payload["replay_path"] = replay_path
    return payload


def build_match_ended_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "match_ended",
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-13T00:00:00Z",
        "payload": dict(payload),
    }


def snapshot_replay_directory(entries: Sequence[Path]) -> dict[str, float]:
    return {str(path): path.stat().st_mtime for path in entries}


def _normalize_winner(
    winner_signal: int | None, *, p1_hp: int, p2_hp: int
) -> str | None:
    if winner_signal == 1:
        return "p1"
    if winner_signal == 2:
        return "p2"
    if p1_hp > p2_hp:
        return "p1"
    if p2_hp > p1_hp:
        return "p2"
    return None


def _derive_end_reason(
    *,
    current_tick: int,
    time_limit: int,
    p1_hp: int,
    p2_hp: int,
) -> str:
    if current_tick > time_limit:
        return "timeout"
    if p1_hp <= 0 or p2_hp <= 0:
        return "ko"
    return "resolved"


def _find_replay_path(
    *,
    before: Mapping[str, float],
    after: Mapping[str, float],
) -> str | None:
    candidates = [
        (path, modified_at)
        for path, modified_at in after.items()
        if path.endswith(".replay") and modified_at > before.get(path, -1.0)
    ]
    if not candidates:
        candidates = [
            (path, modified_at)
            for path, modified_at in after.items()
            if path.endswith(".replay")
        ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[1], item[0]))
    return candidates[-1][0]
