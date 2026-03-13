# ruff: noqa: E402

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
import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_SRC = REPO_ROOT / "daemon" / "src"
if str(DAEMON_SRC) not in sys.path:
    sys.path.insert(0, str(DAEMON_SRC))

from yomi_daemon.protocol import (
    CURRENT_PROTOCOL_VERSION,
    CURRENT_SCHEMA_VERSION,
    MAX_HISTORY_ENTRIES,
    canonical_json,
    classify_object_type,
    default_prediction_spec,
)

SCHEMA_PATH = REPO_ROOT / "schemas" / f"decision-request.{CURRENT_SCHEMA_VERSION}.json"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "game_state"


def load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def load_decision_request_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def fighter_id(fighter: dict[str, Any]) -> str:
    return "p1" if int(fighter["id"]) == 1 else "p2"


def _build_character_data(fighter: dict[str, Any]) -> dict[str, Any] | None:
    """Extract character-specific resource data based on character name."""
    name = str(fighter.get("name", ""))
    if name == "Cowboy":
        return {
            "bullets_left": int(fighter["bullets_left"])
            if "bullets_left" in fighter
            else 0,
            "has_gun": bool(fighter.get("has_gun", False)),
            "consecutive_shots": int(fighter["consecutive_shots"])
            if "consecutive_shots" in fighter
            else 0,
        }
    if name == "Robot":
        data: dict[str, Any] = {}
        if "loic_meter" in fighter:
            data["loic_meter"] = int(fighter["loic_meter"])
        if "loic_meter_max" in fighter:
            data["loic_meter_max"] = int(fighter["loic_meter_max"])
        if "can_loic" in fighter:
            data["can_loic"] = bool(fighter["can_loic"])
        if "armor_pips" in fighter:
            data["armor_pips"] = int(fighter["armor_pips"])
        if "armor_active" in fighter:
            data["armor_active"] = bool(fighter["armor_active"])
        return data or None
    if name == "Ninja":
        data = {}
        if "momentum_stores" in fighter:
            data["momentum_stores"] = int(fighter["momentum_stores"])
        if "sticky_bombs_left" in fighter:
            data["sticky_bombs_left"] = int(fighter["sticky_bombs_left"])
        if "juke_pips" in fighter:
            data["juke_pips"] = int(fighter["juke_pips"])
        if "juke_pips_max" in fighter:
            data["juke_pips_max"] = int(fighter["juke_pips_max"])
        return data or None
    if name == "Mutant":
        data = {}
        if "juke_pips" in fighter:
            data["juke_pips"] = int(fighter["juke_pips"])
        if "juke_pips_max" in fighter:
            data["juke_pips_max"] = int(fighter["juke_pips_max"])
        if "install_ticks" in fighter:
            data["install_ticks"] = int(fighter["install_ticks"])
        if "bc_charge" in fighter:
            data["bc_charge"] = int(fighter["bc_charge"])
        return data or None
    if name == "Wizard":
        data = {}
        if "hover_left" in fighter:
            data["hover_left"] = int(fighter["hover_left"])
        if "hover_max" in fighter:
            data["hover_max"] = int(fighter["hover_max"])
        if "geyser_charge" in fighter:
            data["geyser_charge"] = int(fighter["geyser_charge"])
        if "gusts_in_combo" in fighter:
            data["gusts_in_combo"] = int(fighter["gusts_in_combo"])
        return data or None
    return None


def build_fighter_observation(fighter: dict[str, Any]) -> dict[str, Any]:
    pos = fighter["position"]
    vel = fighter["velocity"]
    obs: dict[str, Any] = {
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
        "blockstun": int(fighter["blockstun_ticks"]),
        "hitlag": int(fighter["hitlag_ticks"]),
        "state_interruptable": bool(fighter.get("state_interruptable", False)),
        "can_feint": bool(fighter.get("can_feint", False)),
        "grounded": pos["y"] <= 0.0,
    }
    # Optional advanced fighter state
    for field_name in (
        "air_actions_remaining",
        "feints_remaining",
        "sadness",
    ):
        if field_name in fighter:
            obs[field_name] = int(fighter[field_name])
    for field_name in (
        "initiative",
        "wakeup_throw_immune",
    ):
        if field_name in fighter:
            obs[field_name] = bool(fighter[field_name])
    if "combo_proration" in fighter:
        obs["combo_proration"] = float(fighter["combo_proration"])
    character_data = _build_character_data(fighter)
    if character_data:
        obs["character_data"] = character_data
    return obs


def build_objects(game_state: dict[str, Any]) -> list[dict[str, Any]]:
    raw_objects = game_state.get("objects", [])
    result = []
    for obj in raw_objects:
        if obj is None:
            continue
        pos = obj.get("position", {"x": 0, "y": 0})
        raw_type = str(obj.get("class_name", obj.get("type", "Unknown")))
        entry: dict[str, Any] = {
            "type": raw_type,
            "position": {"x": pos["x"], "y": pos["y"]},
        }
        category = classify_object_type(raw_type)
        if category != "unknown":
            entry["category"] = category
        if "owner" in obj:
            entry["owner"] = str(obj["owner"])
        result.append(entry)
    result.sort(key=lambda o: (o["type"], o["position"]["x"], o["position"]["y"]))
    return result


def build_stage(game_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "width": int(game_state["stage_width"]),
        "ceiling_height": int(game_state["ceiling_height"]),
        "has_ceiling": bool(game_state["has_ceiling"]),
    }


def build_observation(
    game_state: dict[str, Any],
    active_fighter: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bounded_history = (history or [])[-MAX_HISTORY_ENTRIES:]
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
        "history": bounded_history,
    }


def build_history_entry(
    turn_id: int,
    player_id: str,
    action: str,
    *,
    was_fallback: bool = False,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "turn_id": turn_id,
        "player_id": player_id,
        "action": action,
    }
    if was_fallback:
        entry["was_fallback"] = True
    return entry


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

        supports_prediction = _supports_prediction(button)
        action_entry = {
            "action": str(button["action_name"]),
            "label": str(label),
            "payload_spec": _build_payload_spec(button),
            "supports": {
                "di": True,
                "feint": supports_feint,
                "reverse": bool(button.get("reversible", False)),
                "prediction": supports_prediction,
            },
        }
        prediction_spec = _build_prediction_spec(button)
        if prediction_spec is not None:
            action_entry["prediction_spec"] = prediction_spec

        result.append(action_entry)
    return result


def _supports_prediction(button: dict[str, Any]) -> bool:
    state = button.get("state")
    if isinstance(button.get("supports_prediction"), bool):
        return bool(button["supports_prediction"])
    if isinstance(state, dict) and isinstance(state.get("supports_prediction"), bool):
        return bool(state["supports_prediction"])
    return button.get("prediction_spec") is not None or (
        isinstance(state, dict) and state.get("prediction_spec") is not None
    )


def _build_prediction_spec(button: dict[str, Any]) -> dict[str, Any] | None:
    state = button.get("state")
    for candidate in (
        button.get("prediction_spec"),
        state.get("prediction_spec") if isinstance(state, dict) else None,
    ):
        if isinstance(candidate, dict):
            return dict(candidate)
    if _supports_prediction(button):
        return default_prediction_spec()
    return None


def _build_payload_spec(button: dict[str, Any]) -> dict[str, Any]:
    data_node = button.get("data_node")
    if data_node is None:
        return {}

    children = _data_children(data_node)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for child in children:
        field_name = str(child.get("name", "")).strip()
        if not field_name:
            continue
        descriptor = _build_field_descriptor(child)
        if descriptor is None:
            continue
        properties[field_name] = descriptor
        if bool(child.get("required", True)):
            required.append(field_name)

    if not properties:
        return {}

    payload_spec: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        payload_spec["required"] = required
    return payload_spec


def _data_children(data_node: Any) -> list[dict[str, Any]]:
    if isinstance(data_node, dict):
        children = data_node.get("children", [])
        if isinstance(children, list):
            return [child for child in children if isinstance(child, dict)]
    if isinstance(data_node, list):
        return [child for child in data_node if isinstance(child, dict)]
    return []


def _build_field_descriptor(child: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(child.get("schema"), dict):
        return dict(child["schema"])

    kind = str(child.get("kind", child.get("type", ""))).lower()
    semantic_hint = child.get("semantic_hint")

    if kind == "slider":
        descriptor: dict[str, Any] = {
            "type": "integer",
            "minimum": int(child.get("minimum", child.get("min", 0))),
            "maximum": int(child.get("maximum", child.get("max", 100))),
            "default": int(
                child.get("default", child.get("minimum", child.get("min", 0)))
            ),
            "ui_kind": "slider",
        }
        if semantic_hint is not None:
            descriptor["semantic_hint"] = str(semantic_hint)
        return descriptor

    if kind in {"option", "enum"}:
        choices = child.get("choices", [])
        descriptor = {
            "type": "string",
            "enum": [str(choice) for choice in choices],
            "default": str(
                child.get(
                    "default",
                    choices[0] if isinstance(choices, list) and choices else "",
                )
            ),
            "ui_kind": "enum",
        }
        if semantic_hint is not None:
            descriptor["semantic_hint"] = str(semantic_hint)
        return descriptor

    if kind in {"check", "checkbox"}:
        descriptor = {
            "type": "boolean",
            "default": bool(child.get("default", False)),
            "ui_kind": "checkbox",
        }
        if semantic_hint is not None:
            descriptor["semantic_hint"] = str(semantic_hint)
        return descriptor

    if kind in {"8way", "direction", "direction8"}:
        choices = child.get(
            "choices",
            [
                "neutral",
                "up",
                "down",
                "forward",
                "back",
                "up_forward",
                "up_back",
                "down_forward",
                "down_back",
            ],
        )
        descriptor = {
            "type": "direction",
            "enum": [str(choice) for choice in choices],
            "default": str(child.get("default", choices[0] if choices else "neutral")),
            "ui_kind": "direction8",
        }
        if semantic_hint is not None:
            descriptor["semantic_hint"] = str(semantic_hint)
        return descriptor

    if kind in {"xy_plot", "xy", "vector2"}:
        x_bounds = child.get("x", {})
        y_bounds = child.get("y", {})
        descriptor = {
            "type": "object",
            "additionalProperties": False,
            "required": ["x", "y"],
            "ui_kind": "xy",
            "properties": {
                "x": {
                    "type": "number",
                    "minimum": float(x_bounds.get("minimum", x_bounds.get("min", 0.0))),
                    "maximum": float(x_bounds.get("maximum", x_bounds.get("max", 0.0))),
                    "default": float(
                        x_bounds.get(
                            "default",
                            x_bounds.get("minimum", x_bounds.get("min", 0.0)),
                        )
                    ),
                },
                "y": {
                    "type": "number",
                    "minimum": float(y_bounds.get("minimum", y_bounds.get("min", 0.0))),
                    "maximum": float(y_bounds.get("maximum", y_bounds.get("max", 0.0))),
                    "default": float(
                        y_bounds.get(
                            "default",
                            y_bounds.get("minimum", y_bounds.get("min", 0.0)),
                        )
                    ),
                },
            },
        }
        if semantic_hint is not None:
            descriptor["semantic_hint"] = str(semantic_hint)
        return descriptor

    return None


def sha256_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def build_decision_request(
    game_state: dict[str, Any],
    player_id: str,
    *,
    match_id: str | None = None,
    turn_id: int = 1,
    deadline_ms: int = 2500,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fighter = game_state[player_id]
    observation = build_observation(game_state, fighter, history=history)
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


def build_envelope(
    message_type: str,
    payload: dict[str, Any],
    *,
    ts: str = "2026-03-12T00:00:00Z",
    version: str = CURRENT_PROTOCOL_VERSION.value,
) -> dict[str, Any]:
    return {
        "type": message_type,
        "version": version,
        "ts": ts,
        "payload": payload,
    }


def build_decision_request_envelope(
    game_state: dict[str, Any],
    player_id: str,
    *,
    match_id: str | None = None,
    turn_id: int = 1,
    deadline_ms: int = 2500,
    ts: str = "2026-03-12T00:00:00Z",
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return build_envelope(
        "decision_request",
        build_decision_request(
            game_state,
            player_id,
            match_id=match_id,
            turn_id=turn_id,
            deadline_ms=deadline_ms,
            history=history,
        ),
        ts=ts,
    )


def build_match_ended_envelope(
    *,
    match_id: str,
    winner: str | None = "p1",
    end_reason: str = "ko",
    total_turns: int = 1,
    end_tick: int = 100,
    end_frame: int = 10,
    replay_path: str | None = None,
    errors: list[str] | None = None,
    ts: str = "2026-03-12T00:01:00Z",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "match_id": match_id,
        "winner": winner,
        "end_reason": end_reason,
        "total_turns": total_turns,
        "end_tick": end_tick,
        "end_frame": end_frame,
        "errors": list(errors or []),
    }
    if replay_path is not None:
        payload["replay_path"] = replay_path
    return build_envelope("match_ended", payload, ts=ts)
