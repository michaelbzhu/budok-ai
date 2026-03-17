"""FFmpeg-based replay video capture for headless VM environments.

Starts and stops ffmpeg x11grab recording on a virtual display inside an
OrbStack VM, then pulls the resulting video file to the local run directory.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReplayCaptureConfig:
    """Configuration for replay video capture."""

    enabled: bool = False
    vm_machine: str = "ubuntu"
    display: str = ":99"
    resolution: str = "1280x720"
    framerate: int = 30
    video_codec: str = "libx264"
    preset: str = "fast"


class ReplayCaptureSession:
    """Manages a single ffmpeg recording session inside an OrbStack VM."""

    def __init__(
        self,
        *,
        config: ReplayCaptureConfig,
        match_id: str,
        run_dir: Path,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._match_id = match_id
        self._run_dir = run_dir
        self._logger = logger or logging.getLogger("yomi_daemon.replay_capture")
        self._process: asyncio.subprocess.Process | None = None
        self._vm_video_path = f"/tmp/yomi_replay_{match_id}.mp4"

    @property
    def is_recording(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start_recording(self, display: str | None = None) -> bool:
        """Start ffmpeg x11grab recording on the VM's virtual display."""

        if self._process is not None:
            self._logger.warning("Recording already in progress for match %s", self._match_id)
            return False

        if not await self._check_orb_available():
            self._logger.warning("orb command not available — skipping replay recording")
            return False

        resolved_display = display or self._config.display
        cfg = self._config

        ffmpeg_cmd = (
            f"ffmpeg -y -f x11grab "
            f"-video_size {cfg.resolution} "
            f"-framerate {cfg.framerate} "
            f"-i {resolved_display} "
            f"-c:v {cfg.video_codec} -preset {cfg.preset} -pix_fmt yuv420p "
            f"{self._vm_video_path} "
            f"</dev/null 2>/tmp/yomi_ffmpeg_{self._match_id}.log"
        )

        try:
            self._process = await asyncio.create_subprocess_exec(
                "orb",
                "run",
                "-m",
                cfg.vm_machine,
                "bash",
                "-c",
                ffmpeg_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._logger.info(
                "Started replay recording for match %s on display %s (pid %s)",
                self._match_id,
                resolved_display,
                self._process.pid,
            )
            return True
        except (OSError, FileNotFoundError) as exc:
            self._logger.error("Failed to start ffmpeg recording: %s", exc)
            self._process = None
            return False

    async def stop_recording(self) -> Path | None:
        """Stop the ffmpeg recording and pull the video to the local run directory."""

        if self._process is None:
            self._logger.warning("No recording in progress for match %s", self._match_id)
            return None

        # Stop ffmpeg cleanly inside the VM. The orb wrapper doesn't reliably
        # forward signals, so we send SIGINT directly via a separate orb run,
        # then give ffmpeg 3 seconds to write the MP4 trailer before force-killing.
        video_marker = f"yomi_replay_{self._match_id}"
        await self._vm_exec(
            f"PID=$(pgrep -f '{video_marker}'); "
            f'if [ -n "$PID" ]; then '
            f"kill -INT $PID; sleep 3; "
            f"kill -0 $PID 2>/dev/null && kill -9 $PID; "
            f"fi; true"
        )

        # Wait for the orb wrapper process to exit
        try:
            await asyncio.wait_for(self._process.wait(), timeout=10.0)
        except TimeoutError:
            self._logger.warning("orb wrapper did not exit, killing")
            try:
                self._process.kill()
            except (ProcessLookupError, OSError):
                pass
            await self._process.wait()

        self._process = None

        # Brief pause for filesystem sync
        await asyncio.sleep(1)

        # Pull video from VM to local run directory
        local_video_path = self._run_dir / "replay.mp4"
        return await self._pull_video(local_video_path)

    async def pull_replay_file(self, vm_replay_path: str) -> Path | None:
        """Pull the .replay file from the VM to the local run directory."""

        if not vm_replay_path:
            return None

        local_path = self._run_dir / "match.replay"
        try:
            proc = await asyncio.create_subprocess_exec(
                "orb",
                "pull",
                "-m",
                self._config.vm_machine,
                vm_replay_path,
                str(local_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            if proc.returncode == 0:
                self._logger.info("Pulled replay file to %s", local_path)
                return local_path
            self._logger.warning(
                "Failed to pull replay file (exit %d): %s",
                proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
        except (OSError, TimeoutError) as exc:
            self._logger.warning("Failed to pull replay file: %s", exc)

        return None

    async def _pull_video(self, local_path: Path) -> Path | None:
        """Pull the video file from VM to local filesystem."""

        try:
            proc = await asyncio.create_subprocess_exec(
                "orb",
                "pull",
                "-m",
                self._config.vm_machine,
                self._vm_video_path,
                str(local_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            if proc.returncode == 0:
                self._logger.info("Pulled replay video to %s", local_path)
                return local_path
            self._logger.warning(
                "Failed to pull video (exit %d): %s",
                proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
        except (OSError, TimeoutError) as exc:
            self._logger.warning("Failed to pull video file: %s", exc)

        return None

    async def _vm_exec(self, command: str) -> int:
        """Run a command in the VM and return exit code."""

        try:
            proc = await asyncio.create_subprocess_exec(
                "orb",
                "run",
                "-m",
                self._config.vm_machine,
                "bash",
                "-c",
                command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10.0)
            return proc.returncode or 0
        except (OSError, TimeoutError):
            return 1

    async def _check_orb_available(self) -> bool:
        """Check if the orb CLI is available."""

        try:
            proc = await asyncio.create_subprocess_exec(
                "orb",
                "list",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            return proc.returncode == 0
        except (OSError, FileNotFoundError, TimeoutError):
            return False

    async def cleanup(self) -> None:
        """Clean up VM temp files."""

        await self._vm_exec(f"rm -f {self._vm_video_path} /tmp/yomi_ffmpeg_{self._match_id}.log")
