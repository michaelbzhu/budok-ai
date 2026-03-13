# ruff: noqa: E402

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_FIXTURES_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "protocol" / "canonical_hash_cases.json"
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.mod_observation_harness import (
    _build_character_data,
    build_decision_request_envelope,
    build_match_ended_envelope,
    build_decision_request,
    build_fighter_observation,
    build_legal_actions,
    build_objects,
    build_observation,
    build_stage,
    canonical_json,
    load_decision_request_schema,
    load_fixture,
    sha256_hash,
)

try:
    import jsonschema
except ImportError:
    jsonschema = None  # type: ignore[assignment]


BASIC_TURN = load_fixture("basic_turn.json")
EMPTY_OBJECTS = load_fixture("empty_objects.json")
COWBOY_CHARACTER_DATA = load_fixture("cowboy_character_data.json")
COWBOY_PARAMETERIZED = load_fixture("cowboy_parameterized.json")
ROBOT_PARAMETERIZED = load_fixture("robot_parameterized.json")
NINJA_PARAMETERIZED = load_fixture("ninja_parameterized.json")
MUTANT_PARAMETERIZED = load_fixture("mutant_parameterized.json")
WIZARD_PARAMETERIZED = load_fixture("wizard_parameterized.json")


# --- Observation normalization ---


class TestFighterObservation:
    def test_fighter_id_mapping(self) -> None:
        obs = build_fighter_observation(BASIC_TURN["p1"])
        assert obs["id"] == "p1"

        obs2 = build_fighter_observation(BASIC_TURN["p2"])
        assert obs2["id"] == "p2"

    def test_meter_calculation(self) -> None:
        p1 = BASIC_TURN["p1"]
        obs = build_fighter_observation(p1)
        expected_meter = (
            p1["supers_available"] * p1["MAX_SUPER_METER"] + p1["super_meter"]
        )
        assert obs["meter"] == expected_meter

    def test_facing_conversion(self) -> None:
        obs_p1 = build_fighter_observation(BASIC_TURN["p1"])
        assert obs_p1["facing"] == "right"

        obs_p2 = build_fighter_observation(BASIC_TURN["p2"])
        assert obs_p2["facing"] == "left"

    def test_all_required_fields_present(self) -> None:
        obs = build_fighter_observation(BASIC_TURN["p1"])
        required = [
            "id",
            "character",
            "hp",
            "max_hp",
            "meter",
            "burst",
            "position",
            "velocity",
            "facing",
            "current_state",
            "combo_count",
            "hitstun",
            "hitlag",
        ]
        for field in required:
            assert field in obs, f"Missing field: {field}"

    def test_position_and_velocity_shape(self) -> None:
        obs = build_fighter_observation(BASIC_TURN["p1"])
        assert set(obs["position"].keys()) == {"x", "y"}
        assert set(obs["velocity"].keys()) == {"x", "y"}


class TestObservation:
    def test_fighters_always_p1_first(self) -> None:
        obs = build_observation(BASIC_TURN, BASIC_TURN["p1"])
        assert obs["fighters"][0]["id"] == "p1"
        assert obs["fighters"][1]["id"] == "p2"

    def test_active_player_set_correctly(self) -> None:
        obs = build_observation(BASIC_TURN, BASIC_TURN["p1"])
        assert obs["active_player"] == "p1"

        obs2 = build_observation(BASIC_TURN, BASIC_TURN["p2"])
        assert obs2["active_player"] == "p2"

    def test_tick_and_frame(self) -> None:
        obs = build_observation(BASIC_TURN, BASIC_TURN["p1"])
        assert obs["tick"] == BASIC_TURN["current_tick"]
        assert obs["frame"] == BASIC_TURN["p1"]["state_tick"]

    def test_history_is_empty_list(self) -> None:
        obs = build_observation(BASIC_TURN, BASIC_TURN["p1"])
        assert obs["history"] == []

    def test_all_required_observation_fields(self) -> None:
        obs = build_observation(BASIC_TURN, BASIC_TURN["p1"])
        required = [
            "tick",
            "frame",
            "active_player",
            "fighters",
            "objects",
            "stage",
            "history",
        ]
        for field in required:
            assert field in obs, f"Missing field: {field}"


class TestStage:
    def test_stage_fields(self) -> None:
        stage = build_stage(BASIC_TURN)
        assert stage["width"] == 2000
        assert stage["ceiling_height"] == 800
        assert stage["has_ceiling"] is False

    def test_stage_with_ceiling(self) -> None:
        stage = build_stage(EMPTY_OBJECTS)
        assert stage["has_ceiling"] is True


class TestObjects:
    def test_basic_objects(self) -> None:
        objects = build_objects(BASIC_TURN)
        assert len(objects) == 1
        assert objects[0]["type"] == "Bullet"
        assert objects[0]["position"] == {"x": 100, "y": 50}

    def test_empty_objects(self) -> None:
        objects = build_objects(EMPTY_OBJECTS)
        assert objects == []

    def test_objects_sorted_deterministically(self) -> None:
        state = {
            "objects": [
                {"class_name": "Zap", "position": {"x": 200, "y": 0}},
                {"class_name": "Arrow", "position": {"x": 100, "y": 0}},
                {"class_name": "Arrow", "position": {"x": 50, "y": 0}},
            ]
        }
        objects = build_objects(state)
        assert objects[0]["type"] == "Arrow"
        assert objects[0]["position"]["x"] == 50
        assert objects[1]["type"] == "Arrow"
        assert objects[1]["position"]["x"] == 100
        assert objects[2]["type"] == "Zap"


# --- Legal action canonicalization ---


class TestLegalActions:
    def test_disabled_buttons_excluded(self) -> None:
        actions = build_legal_actions(BASIC_TURN, BASIC_TURN["p1"], "p1")
        action_names = [a["action"] for a in actions]
        assert "Special1" not in action_names

    def test_visible_enabled_buttons_included(self) -> None:
        actions = build_legal_actions(BASIC_TURN, BASIC_TURN["p1"], "p1")
        action_names = [a["action"] for a in actions]
        assert "Jab" in action_names
        assert "Heavy" in action_names
        assert "Super" in action_names
        assert "Continue" in action_names

    def test_label_from_state_title(self) -> None:
        actions = build_legal_actions(BASIC_TURN, BASIC_TURN["p1"], "p1")
        heavy = next(a for a in actions if a["action"] == "Heavy")
        assert heavy["label"] == "Heavy Punch"

    def test_label_fallback_to_action_name(self) -> None:
        actions = build_legal_actions(BASIC_TURN, BASIC_TURN["p1"], "p1")
        cont = next(a for a in actions if a["action"] == "Continue")
        assert cont["label"] == "Continue"

    def test_di_always_true(self) -> None:
        actions = build_legal_actions(BASIC_TURN, BASIC_TURN["p1"], "p1")
        for action in actions:
            assert action["supports"]["di"] is True

    def test_feint_requires_both_state_and_fighter(self) -> None:
        actions = build_legal_actions(BASIC_TURN, BASIC_TURN["p1"], "p1")
        jab = next(a for a in actions if a["action"] == "Jab")
        assert jab["supports"]["feint"] is True

        super_action = next(a for a in actions if a["action"] == "Super")
        assert super_action["supports"]["feint"] is False

    def test_reverse_flag(self) -> None:
        actions = build_legal_actions(BASIC_TURN, BASIC_TURN["p1"], "p1")
        heavy = next(a for a in actions if a["action"] == "Heavy")
        assert heavy["supports"]["reverse"] is True

        jab = next(a for a in actions if a["action"] == "Jab")
        assert jab["supports"]["reverse"] is False

    def test_all_actions_have_required_fields(self) -> None:
        actions = build_legal_actions(BASIC_TURN, BASIC_TURN["p1"], "p1")
        for action in actions:
            assert "action" in action
            assert "payload_spec" in action
            assert "supports" in action
            assert set(action["supports"].keys()) == {
                "di",
                "feint",
                "reverse",
                "prediction",
            }

    def test_prediction_defaults_false(self) -> None:
        actions = build_legal_actions(BASIC_TURN, BASIC_TURN["p1"], "p1")
        for action in actions:
            assert action["supports"]["prediction"] is False

    def test_empty_player_buttons(self) -> None:
        actions = build_legal_actions(BASIC_TURN, BASIC_TURN["p2"], "p2")
        assert actions == []

    @pytest.mark.parametrize(
        ("fixture", "action_name", "expected_fields"),
        [
            (
                COWBOY_PARAMETERIZED,
                "Gun Toss",
                {"distance", "target"},
            ),
            (
                ROBOT_PARAMETERIZED,
                "Drive Impact",
                {"armor", "direction"},
            ),
            (
                NINJA_PARAMETERIZED,
                "Sticky Bomb",
                {"target_point"},
            ),
            (
                MUTANT_PARAMETERIZED,
                "Install Choice",
                {"stance", "hold_position"},
            ),
            (
                WIZARD_PARAMETERIZED,
                "Conjure Storm",
                {"charge_frames", "element", "direction"},
            ),
        ],
    )
    def test_parameterized_character_actions_emit_structured_payload_specs(
        self,
        fixture: dict[str, object],
        action_name: str,
        expected_fields: set[str],
    ) -> None:
        actions = build_legal_actions(
            fixture,
            cast(dict[str, object], fixture["p1"]),
            "p1",
        )
        action = next(action for action in actions if action["action"] == action_name)

        assert action["payload_spec"]["type"] == "object"
        assert action["payload_spec"]["additionalProperties"] is False
        assert set(action["payload_spec"]["properties"]) == expected_fields
        assert set(action["payload_spec"]["required"]) == expected_fields

    def test_prediction_capable_action_emits_prediction_spec(self) -> None:
        actions = build_legal_actions(
            COWBOY_PARAMETERIZED, COWBOY_PARAMETERIZED["p1"], "p1"
        )
        action = next(action for action in actions if action["action"] == "Gun Toss")

        assert action["supports"]["prediction"] is True
        assert action["prediction_spec"]["required"] == ["horizon"]
        assert action["prediction_spec"]["properties"]["confidence"]["enum"] == [
            "low",
            "medium",
            "high",
        ]


# --- Deterministic hashing ---


class TestDeterminism:
    def test_canonical_json_is_stable(self) -> None:
        data = {"b": 2, "a": 1, "c": [3, 1, 2]}
        assert canonical_json(data) == '{"a":1,"b":2,"c":[3,1,2]}'

    def test_same_observation_produces_same_hash(self) -> None:
        obs1 = build_observation(BASIC_TURN, BASIC_TURN["p1"])
        obs2 = build_observation(BASIC_TURN, BASIC_TURN["p1"])
        assert sha256_hash(obs1) == sha256_hash(obs2)

    def test_same_legal_actions_produce_same_hash(self) -> None:
        la1 = build_legal_actions(BASIC_TURN, BASIC_TURN["p1"], "p1")
        la2 = build_legal_actions(BASIC_TURN, BASIC_TURN["p1"], "p1")
        assert sha256_hash(la1) == sha256_hash(la2)

    def test_different_states_produce_different_hashes(self) -> None:
        obs1 = build_observation(BASIC_TURN, BASIC_TURN["p1"])
        obs2 = build_observation(EMPTY_OBJECTS, EMPTY_OBJECTS["p1"])
        assert sha256_hash(obs1) != sha256_hash(obs2)

    def test_hash_is_64_char_hex(self) -> None:
        obs = build_observation(BASIC_TURN, BASIC_TURN["p1"])
        h = sha256_hash(obs)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_canonical_hash_fixtures_match_reference_values(self) -> None:
        fixtures = json.loads(CANONICAL_FIXTURES_PATH.read_text(encoding="utf-8"))
        for fixture in fixtures:
            assert canonical_json(fixture["payload"]) == fixture["canonical_json"]
            assert sha256_hash(fixture["payload"]) == fixture["sha256"]


# --- DecisionRequest envelope ---


class TestDecisionRequest:
    def test_envelope_has_all_required_fields(self) -> None:
        req = build_decision_request(BASIC_TURN, "p1", match_id="test-match", turn_id=1)
        required = [
            "match_id",
            "turn_id",
            "player_id",
            "deadline_ms",
            "state_hash",
            "legal_actions_hash",
            "decision_type",
            "observation",
            "legal_actions",
        ]
        for field in required:
            assert field in req, f"Missing field: {field}"

    def test_envelope_field_values(self) -> None:
        req = build_decision_request(
            BASIC_TURN, "p1", match_id="m-001", turn_id=5, deadline_ms=3000
        )
        assert req["match_id"] == "m-001"
        assert req["turn_id"] == 5
        assert req["player_id"] == "p1"
        assert req["deadline_ms"] == 3000
        assert req["decision_type"] == "turn_action"

    def test_envelope_hashes_are_consistent(self) -> None:
        req1 = build_decision_request(BASIC_TURN, "p1", match_id="m-001", turn_id=1)
        req2 = build_decision_request(BASIC_TURN, "p1", match_id="m-002", turn_id=2)
        assert req1["state_hash"] == req2["state_hash"]
        assert req1["legal_actions_hash"] == req2["legal_actions_hash"]

    def test_envelope_legal_actions_nonempty(self) -> None:
        req = build_decision_request(BASIC_TURN, "p1", match_id="test", turn_id=1)
        assert len(req["legal_actions"]) >= 1

    @pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
    def test_envelope_validates_against_schema(self) -> None:
        schema = load_decision_request_schema()
        req = build_decision_request(BASIC_TURN, "p1", match_id="test-match", turn_id=1)
        jsonschema.validate(instance=req, schema=schema)

    @pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
    def test_empty_objects_envelope_validates(self) -> None:
        schema = load_decision_request_schema()
        req = build_decision_request(
            EMPTY_OBJECTS, "p1", match_id="test-match", turn_id=1
        )
        jsonschema.validate(instance=req, schema=schema)

    @pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
    def test_both_players_envelope_validates(self) -> None:
        schema = load_decision_request_schema()
        for player_id in ("p1", "p2"):
            req = build_decision_request(
                EMPTY_OBJECTS, player_id, match_id="test-match", turn_id=1
            )
            jsonschema.validate(instance=req, schema=schema)

    def test_decision_request_envelope_uses_v2_contract(self) -> None:
        envelope = build_decision_request_envelope(
            BASIC_TURN, "p1", match_id="test-match", turn_id=1
        )
        assert envelope["type"] == "decision_request"
        assert envelope["version"] == "v2"
        assert envelope["payload"]["state_hash"]
        assert envelope["payload"]["legal_actions_hash"]

    def test_match_ended_envelope_uses_v2_contract(self) -> None:
        envelope = build_match_ended_envelope(match_id="test-match", total_turns=3)
        assert envelope["type"] == "match_ended"
        assert envelope["version"] == "v2"
        assert envelope["payload"]["total_turns"] == 3


# --- Character data extraction ---


class TestCharacterData:
    def test_cowboy_produces_character_data(self) -> None:
        obs = build_fighter_observation(COWBOY_CHARACTER_DATA["p1"])
        assert "character_data" in obs
        cd = obs["character_data"]
        assert cd["bullets_left"] == 4
        assert cd["has_gun"] is True
        assert cd["consecutive_shots"] == 2

    def test_unknown_character_no_character_data(self) -> None:
        """A fighter with an unrecognized character name produces no character_data key."""
        fighter = dict(BASIC_TURN["p1"])
        fighter["name"] = "Alien"
        obs = build_fighter_observation(fighter)
        assert "character_data" not in obs

    def test_robot_without_specific_fields_no_character_data(self) -> None:
        """Robot fighter without any character-specific fields → no character_data."""
        obs = build_fighter_observation(BASIC_TURN["p2"])
        assert "character_data" not in obs

    def test_character_data_extraction_function(self) -> None:
        data = _build_character_data(COWBOY_CHARACTER_DATA["p1"])
        assert data is not None
        assert set(data.keys()) == {"bullets_left", "has_gun", "consecutive_shots"}

    def test_character_data_none_for_unknown(self) -> None:
        fighter = {"name": "Alien"}
        assert _build_character_data(fighter) is None

    @pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
    def test_cowboy_character_data_validates_against_schema(self) -> None:
        schema = load_decision_request_schema()
        req = build_decision_request(
            COWBOY_CHARACTER_DATA, "p1", match_id="test-match", turn_id=1
        )
        jsonschema.validate(instance=req, schema=schema)
