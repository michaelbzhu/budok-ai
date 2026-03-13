"""Script-level tests for mod packaging and installation helpers (WU-021)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

from yomi_daemon.validation import REPO_ROOT


PACKAGE_SCRIPT = REPO_ROOT / "scripts" / "package_mod.sh"
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install_mod.sh"
MOD_ZIP = REPO_ROOT / "dist" / "YomiLLMBridge.zip"


def _run_script(
    script: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    return subprocess.run(
        [str(script), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=run_env,
    )


class TestPackageMod:
    """Tests for scripts/package_mod.sh."""

    def test_package_produces_valid_zip(self) -> None:
        """package_mod.sh creates a zip with expected mod files."""
        result = _run_script(PACKAGE_SCRIPT)
        assert result.returncode == 0, f"package_mod.sh failed: {result.stderr}"
        assert MOD_ZIP.exists(), "Expected dist/YomiLLMBridge.zip"

        with zipfile.ZipFile(MOD_ZIP, "r") as zf:
            names = zf.namelist()
            # Must contain the mod root directory entries
            assert any("YomiLLMBridge/_metadata" in n for n in names), (
                f"Missing _metadata in zip: {names}"
            )
            assert any("YomiLLMBridge/ModMain.gd" in n for n in names), (
                f"Missing ModMain.gd in zip: {names}"
            )
            assert any("YomiLLMBridge/bridge/BridgeClient.gd" in n for n in names), (
                f"Missing BridgeClient.gd in zip: {names}"
            )
            assert any(
                "YomiLLMBridge/config/default_config.json" in n for n in names
            ), f"Missing default_config.json in zip: {names}"

    def test_package_validates_metadata(self) -> None:
        """package_mod.sh exits 0 because _metadata is valid JSON."""
        result = _run_script(PACKAGE_SCRIPT)
        assert result.returncode == 0


class TestInstallMod:
    """Tests for scripts/install_mod.sh."""

    def test_install_to_valid_game_dir(self) -> None:
        """install_mod.sh extracts the mod into a game directory's mods/ folder."""
        # Ensure mod is packaged first
        pkg = _run_script(PACKAGE_SCRIPT)
        assert pkg.returncode == 0, f"package_mod.sh failed: {pkg.stderr}"

        with tempfile.TemporaryDirectory() as game_dir:
            # Create a fake game directory marker
            (Path(game_dir) / "project.binary").touch()

            result = _run_script(INSTALL_SCRIPT, "--game-dir", game_dir)
            assert result.returncode == 0, f"install_mod.sh failed: {result.stderr}"

            # Verify mod was installed
            mods_dir = Path(game_dir) / "mods"
            assert mods_dir.exists()
            assert (mods_dir / "YomiLLMBridge.zip").exists()
            assert (mods_dir / "YomiLLMBridge" / "_metadata").exists()
            assert (mods_dir / "YomiLLMBridge" / "ModMain.gd").exists()

    def test_install_fails_without_game_dir(self) -> None:
        """install_mod.sh requires --game-dir."""
        result = _run_script(INSTALL_SCRIPT)
        assert result.returncode != 0
        assert "--game-dir" in result.stderr

    def test_install_fails_with_nonexistent_dir(self) -> None:
        """install_mod.sh fails if game directory does not exist."""
        result = _run_script(INSTALL_SCRIPT, "--game-dir", "/nonexistent/path")
        assert result.returncode != 0
        assert "does not exist" in result.stderr

    def test_install_fails_with_invalid_game_dir(self) -> None:
        """install_mod.sh fails if directory doesn't look like a game install."""
        with tempfile.TemporaryDirectory() as game_dir:
            # No project.godot or project.binary
            result = _run_script(INSTALL_SCRIPT, "--game-dir", game_dir)
            assert result.returncode != 0
            assert "does not look like" in result.stderr

    def test_install_fails_without_mod_zip(self) -> None:
        """install_mod.sh fails if mod zip doesn't exist."""
        # Temporarily move the zip out of the way
        backup = None
        if MOD_ZIP.exists():
            backup = MOD_ZIP.with_suffix(".zip.bak")
            MOD_ZIP.rename(backup)

        try:
            with tempfile.TemporaryDirectory() as game_dir:
                (Path(game_dir) / "project.godot").touch()
                result = _run_script(INSTALL_SCRIPT, "--game-dir", game_dir)
                assert result.returncode != 0
                assert "package_mod.sh" in result.stderr
        finally:
            if backup is not None and backup.exists():
                backup.rename(MOD_ZIP)
