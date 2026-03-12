from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DAEMON_ROOT = REPO_ROOT / "daemon"


def test_uv_entrypoint_uses_uv_managed_runtime() -> None:
    completed = subprocess.run(
        ["uv", "run", "yomi-daemon-check-uv", "--json"],
        cwd=DAEMON_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)

    assert payload["uv_managed"] is True
    assert payload["executable"]
