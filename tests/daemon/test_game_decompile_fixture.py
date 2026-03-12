from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_supported_build_fixture_records_confirmed_game_hooks() -> None:
    fixture_path = (
        REPO_ROOT
        / "tests"
        / "fixtures"
        / "decompile"
        / "yomi_hustle_supported_build_16151810.json"
    )

    fixture = json.loads(fixture_path.read_text())

    assert fixture["app_id"] == 2212330
    assert fixture["public_build_id"] == 16151810
    assert fixture["depot_id"] == 2232859
    assert fixture["manifest_gid"] == "2006404455181526055"
    assert fixture["engine_version_detected"] == "3.5.1"
    assert fixture["hooks"]["decision_interception"] == {
        "game_script": "game.gd",
        "signal": "player_actionable",
        "main_connect_script": "main.gd",
        "main_connect_line": 208,
    }
    assert fixture["hooks"]["legal_action_ui"]["main_node_names"] == [
        "P1ActionButtons",
        "P2ActionButtons",
    ]
    assert fixture["hooks"]["action_application"]["queued_fields"] == [
        "queued_action",
        "queued_data",
        "queued_extra",
    ]
    assert {entry["path"] for entry in fixture["files"]} == {
        "game.gd",
        "main.gd",
        "ui/ActionSelector/ActionButtons.gd",
        "ui/CSS/CharacterSelect.gd",
        "characters/BaseChar.gd",
    }
