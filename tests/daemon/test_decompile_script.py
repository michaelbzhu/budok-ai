from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_decompile_script_prepares_documented_output_layout() -> None:
    completed = subprocess.run(
        ["bash", "scripts/decompile.sh"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    output_dir = REPO_ROOT / "docs" / "decompile-output"
    manifest_path = output_dir / "manifest.json"
    reference_hooks_path = output_dir / "reference-hooks.json"
    readme_path = output_dir / "README.md"

    assert "Prepared decompile workspace" in completed.stdout
    assert manifest_path.exists()
    assert reference_hooks_path.exists()
    assert readme_path.exists()
    assert (output_dir / "project").is_dir()
    assert (output_dir / "reports").is_dir()

    manifest = json.loads(manifest_path.read_text())
    hooks = json.loads(reference_hooks_path.read_text())

    assert manifest["mode"] == "smoke"
    assert manifest["output_dir"] == str(output_dir)
    assert manifest["project_dir"] == str(output_dir / "project")
    assert manifest["report_dir"] == str(output_dir / "reports")
    assert manifest["inputs"] == {"game_pck_path": None, "gdre_command": None}

    assert [entry["script_path"] for entry in hooks] == [
        "res://game.gd",
        "player object",
        "Main scene action buttons",
        "res://ui/CSS/CharacterSelect.gd",
    ]

    readme = readme_path.read_text()
    assert "`project/`" in readme
    assert "`reports/`" in readme
