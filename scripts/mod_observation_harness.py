"""Python mirror of the mod-side observation and legal-action building pipeline.

Operates on synthetic game state fixtures (JSON files) to validate:
- Observation normalization and field correctness
- Legal action canonicalization
- Deterministic hash computation (SHA-256 of canonical JSON)
- DecisionRequest envelope shape against the schema
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "decision-request.v1.json"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "game_state"


def load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def load_decision_request_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def fighter_id(fighter: dict[str, Any]) -> str:
    return "p1" if int(fighter["id"]) == 1 else "p2"


def build_fighter_observation(fighter: dict[str, Any]) -> dict[str, Any]:
    pos = fighter["position"]
    vel = fighter["velocity"]
    return {
        "id": fighter_id(fighter),
        "character": str(fighter["name"]),
        "hp": int(fighter["hp"]),
        "max_hp": int(fighter["MAX_HEALTH"]),
        "meter": int(fighter["supers_available"]) * int(fighter["MAX_SUPER_METER"])
        + int(fighter["super_meter"]),
        "burst": int(fighter["bursts_available"]),
        "position": {"x": pos["x"], "y": pos["y"]},
        "velocity": {"x": vel["x"], "y": vel["y"]},
        "facing": "right" if int(fighter["facing_int"]) == 1 else "left",
        "current_state": str(fighter["state_name"]),
        "combo_count": int(fighter["combo_count"]),
        "hitstun": int(fighter["blockstun_ticks"]),
        "hitlag": int(fighter["hitlag_ticks"]),
    }


def build_objects(game_state: dict[str, Any]) -> list[dict[str, Any]]:
    raw_objects = game_state.get("objects", [])
    result = []
    for obj in raw_objects:
        if obj is None:
            continue
        pos = obj.get("position", {"x": 0, "y": 0})
        result.append(
            {
                "type": str(obj.get("class_name", "Unknown")),
                "position": {"x": pos["x"], "y": pos["y"]},
            }
        )
    result.sort(key=lambda o: (o["type"], o["position"]["x"], o["position"]["y"]))
    return result


def build_stage(game_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "width": int(game_state["stage_width"]),
        "ceiling_height": int(game_state["ceiling_height"]),
        "has_ceiling": bool(game_state["has_ceiling"]),
    }


def build_observation(
    game_state: dict[str, Any], active_fighter: dict[str, Any]
) -> dict[str, Any]:
    return {
        "tick": int(game_state["current_tick"]),
        "frame": int(active_fighter["state_tick"]),
        "active_player": fighter_id(active_fighter),
        "fighters": [
            build_fighter_observation(game_state["p1"]),
            build_fighter_observation(game_state["p2"]),
        ],
        "objects": build_objects(game_state),
        "stage": build_stage(game_state),
        "history": [],
    }


def build_legal_actions(
    game_state: dict[str, Any],
    fighter: dict[str, Any],
    player_id: str,
) -> list[dict[str, Any]]:
    buttons = game_state.get("action_buttons", {}).get(player_id, [])
    result = []
    for button in buttons:
        if not button.get("visible", False):
            continue
        if button.get("disabled", False):
            continue

        state = button.get("state")
        label = state["title"] if state and "title" in state else button["action_name"]

        supports_feint = False
        if (
            state is not None
            and state.get("can_feint", False)
            and fighter.get("can_feint", False)
        ):
            supports_feint = True

        result.append(
            {
                "action": str(button["action_name"]),
                "label": str(label),
                "payload_spec": {},
                "supports": {
                    "di": True,
                    "feint": supports_feint,
                    "reverse": bool(button.get("reversible", False)),
                },
            }
        )
    return result


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def sha256_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def build_decision_request(
    game_state: dict[str, Any],
    player_id: str,
    *,
    match_id: str | None = None,
    turn_id: int = 1,
    deadline_ms: int = 2500,
) -> dict[str, Any]:
    fighter = game_state[player_id]
    observation = build_observation(game_state, fighter)
    legal_actions = build_legal_actions(game_state, fighter, player_id)

    return {
        "match_id": match_id or str(uuid.uuid4()),
        "turn_id": turn_id,
        "player_id": player_id,
        "deadline_ms": deadline_ms,
        "state_hash": sha256_hash(observation),
        "legal_actions_hash": sha256_hash(legal_actions),
        "decision_type": "turn_action",
        "observation": observation,
        "legal_actions": legal_actions,
    }
