from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_ROOT = REPO_ROOT / "mod"
SMOKE_SCRIPT = "res://BridgeActionApplySmoke.gd"


def _godot_executable() -> str | None:
    for candidate in ("godot3", "godot"):
        executable = shutil.which(candidate)
        if executable is not None:
            return executable
    return None


def test_bridge_action_apply_smoke() -> None:
    executable = _godot_executable()
    if executable is None:
        pytest.skip("godot3/godot not installed")

    completed = subprocess.run(
        [executable, "--no-window", "--path", str(MOD_ROOT), "--script", SMOKE_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )

    output = "\n".join(
        part for part in (completed.stdout.strip(), completed.stderr.strip()) if part
    )
    assert completed.returncode == 0, output

    ok_line = next(
        (
            line
            for line in completed.stdout.splitlines()
            if line.startswith("ACTION_APPLY_SMOKE_OK ")
        ),
        None,
    )
    assert ok_line is not None, output

    payload = json.loads(ok_line.split(" ", 1)[1])
    assert payload["comparison"]["apply_path"] == "native_method"
    assert payload["comparison"]["snapshot"]["ready_state"] is True
    assert payload["comparison"]["snapshot"]["commit_count"] == 1
    assert payload["simultaneous"]["advance_count"] == 1
    assert payload["simultaneous"]["p1"]["ready_state"] is True
    assert payload["simultaneous"]["p2"]["ready_state"] is True
