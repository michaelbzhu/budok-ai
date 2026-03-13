"""Python test wrapper for the in-engine live match smoke test (WU-021).

Starts a daemon server, then runs the BridgeLiveMatchSmoke.gd script against it.
Skipped when the godot3 binary is not available.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from typing import Any

import pytest

from yomi_daemon.config import (
    DaemonRuntimeConfig,
    PolicyConfig,
    TournamentDefaults,
    TransportConfig,
)
from yomi_daemon.protocol import (
    CharacterSelectionConfig,
    CharacterSelectionMode,
    FallbackMode,
    LoggingConfig,
    PlayerPolicyMapping,
    TimeoutProfile,
)
from yomi_daemon.server import DaemonServer
from yomi_daemon.validation import REPO_ROOT


GODOT_BINARY = os.environ.get("GODOT3_BINARY", "godot3")
MOD_DIR = REPO_ROOT / "mod"
SMOKE_SCRIPT = "res://BridgeLiveMatchSmoke.gd"


def _godot_available() -> bool:
    try:
        result = subprocess.run(
            [GODOT_BINARY, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(
    not _godot_available(),
    reason="godot3 binary not found (set GODOT3_BINARY env var)",
)
def test_live_match_smoke_in_engine() -> None:
    """Run BridgeLiveMatchSmoke.gd against a real daemon and verify it passes."""

    config = DaemonRuntimeConfig(
        version="v1",
        transport=TransportConfig(host="127.0.0.1", port=8765),
        timeout_profile=TimeoutProfile.STRICT_LOCAL,
        decision_timeout_ms=5000,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        logging=LoggingConfig(events=True, prompts=True, raw_provider_payloads=False),
        policy_mapping=PlayerPolicyMapping(p1="baseline/random", p2="baseline/random"),
        policies={
            "baseline/random": PolicyConfig(provider="baseline"),
        },
        character_selection=CharacterSelectionConfig(
            mode=CharacterSelectionMode.MIRROR
        ),
        tournament=TournamentDefaults(
            format="round_robin",
            mirror_matches_first=True,
            side_swap=True,
            games_per_pair=10,
            fixed_stage="training_room",
        ),
        trace_seed=0,
    )

    async def run() -> subprocess.CompletedProcess[Any]:
        server = DaemonServer(
            host="127.0.0.1",
            port=8765,
            policy_mapping=config.policy_mapping,
            config_snapshot=config.to_config_payload(),
            runtime_config=config,
        )
        await server.start()

        try:
            godot_result = await asyncio.to_thread(
                subprocess.run,
                [
                    GODOT_BINARY,
                    "--no-window",
                    "--path",
                    str(MOD_DIR),
                    "--script",
                    SMOKE_SCRIPT,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            await server.stop()

        return godot_result

    result = asyncio.run(run())

    # Check for success marker
    combined = result.stdout + result.stderr
    assert "LIVE_MATCH_SMOKE_OK" in combined, (
        f"Smoke test did not produce OK marker.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}\n"
        f"returncode: {result.returncode}"
    )
    assert result.returncode == 0

    # Clean up any run artifacts created during the smoke test
    for run_dir in (REPO_ROOT / "runs").glob("*smoke-*"):
        if run_dir.is_dir():
            shutil.rmtree(run_dir)
