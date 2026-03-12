from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_schema_check_script_parses_all_schema_files() -> None:
    command = [sys.executable, "scripts/check_schemas.py", "--json"]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)

    assert payload["schema_count"] == 9
    assert [entry["path"] for entry in payload["schemas"]] == [
        "schemas/action-decision.v1.json",
        "schemas/config.v1.json",
        "schemas/daemon-config.v1.json",
        "schemas/decision-request.v1.json",
        "schemas/envelope.json",
        "schemas/event.v1.json",
        "schemas/hello-ack.v1.json",
        "schemas/hello.v1.json",
        "schemas/match-ended.v1.json",
    ]
