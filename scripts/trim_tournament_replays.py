#!/usr/bin/env python3
"""Trim tournament replay videos into derived files.

This utility removes the post-match result splash from the start of each
recorded replay and trims away the static replay tail at the end. It never
modifies, replaces, or deletes the original `replay.mp4` files.

Usage:
    uv run --project daemon python scripts/trim_tournament_replays.py tournaments/20260322T200240_bracket
    uv run --project daemon python scripts/trim_tournament_replays.py tournaments/20260322T200240_bracket --dry-run
    uv run --project daemon python scripts/trim_tournament_replays.py tournaments/20260322T200240_bracket --game F-1_game1
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_START_TRIM_SECONDS = 2.1
DEFAULT_END_OVERLAY_KEEP_SECONDS = 0.2
DEFAULT_OUTPUT_STEM = "replay_cropped_auto"
FPS = 30
WIDTH = 64
HEIGHT = 36
FRAME_SIZE = WIDTH * HEIGHT
ROLLING_WINDOW_FRAMES = 30
ROLLING_AVG_THRESHOLD = 0.06
SIGNIFICANT_MOTION_THRESHOLD = 0.08
TAIL_MIN_SECONDS = 2.0
OVERLAY_SEARCH_BACK_SECONDS = 3.0
OVERLAY_PIXEL_THRESHOLD = 180
OVERLAY_BRIGHT_COUNT_THRESHOLD = 8
OVERLAY_BRIGHT_RUN = 3
FRAME_DT = 1 / FPS
RUN_DIR_PATTERN = re.compile(r"artifacts at (.+?/runs/[^\s]+)")

# Central crop where the large "P1/P2 WIN" overlay appears after downscaling.
OVERLAY_XS = 20
OVERLAY_XE = 44
OVERLAY_YS = 13
OVERLAY_YE = 23
OVERLAY_CROP_IDXS = [
    y * WIDTH + x
    for y in range(OVERLAY_YS, OVERLAY_YE)
    for x in range(OVERLAY_XS, OVERLAY_XE)
]


@dataclass(frozen=True, slots=True)
class TrimResult:
    series_game: str
    run_dir: str
    source: str
    output: str
    start_trim_seconds: float
    motion_end_seconds: float
    overlay_start_seconds: float | None
    overlay_keep_seconds: float
    end_trim_seconds: float
    output_duration_seconds: float
    tail_removed_seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create trimmed derived replay videos for a tournament directory.",
    )
    parser.add_argument("tournament_dir", type=Path)
    parser.add_argument(
        "--game",
        action="append",
        default=[],
        help="Exact game stem to process, for example F-1_game1. May be repeated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute trims and write the manifest without encoding derived videos.",
    )
    parser.add_argument(
        "--start-trim-seconds",
        type=float,
        default=DEFAULT_START_TRIM_SECONDS,
        help=f"Seconds to trim from the start of each replay (default: {DEFAULT_START_TRIM_SECONDS}).",
    )
    parser.add_argument(
        "--output-stem",
        default=DEFAULT_OUTPUT_STEM,
        help=f"Base filename for derived videos, without extension (default: {DEFAULT_OUTPUT_STEM}).",
    )
    parser.add_argument(
        "--end-overlay-keep-seconds",
        type=float,
        default=DEFAULT_END_OVERLAY_KEEP_SECONDS,
        help=(
            "Seconds of the end win overlay to preserve before trimming away the "
            f"static padded tail (default: {DEFAULT_END_OVERLAY_KEEP_SECONDS})."
        ),
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        help="Optional explicit manifest path. Defaults to a timestamped JSON file in the tournament directory.",
    )
    return parser.parse_args()


def ensure_tooling() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        names = ", ".join(missing)
        raise SystemExit(f"Required tool(s) not found on PATH: {names}")


def load_game_runs(
    tournament_dir: Path, selected_games: set[str]
) -> list[tuple[str, Path, Path]]:
    rows: list[tuple[str, Path, Path]] = []
    for log_path in sorted(tournament_dir.glob("*_game*.log")):
        stem = log_path.stem
        if selected_games and stem not in selected_games:
            continue
        match = RUN_DIR_PATTERN.search(log_path.read_text())
        if not match:
            raise RuntimeError(f"Could not resolve run directory from {log_path}")
        run_dir = Path(match.group(1)).resolve()
        replay_path = run_dir / "replay.mp4"
        if not replay_path.is_file():
            raise RuntimeError(f"Missing replay video for {stem}: {replay_path}")
        rows.append((stem, run_dir, replay_path))

    if selected_games:
        found = {stem for stem, _, _ in rows}
        missing = sorted(selected_games - found)
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(
                f"Requested game(s) not found in {tournament_dir}: {joined}"
            )

    if not rows:
        raise RuntimeError(f"No tournament replay logs found in {tournament_dir}")
    return rows


def decode_frames(path: Path) -> list[bytes]:
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps={FPS},scale={WIDTH}:{HEIGHT},format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    assert proc.stdout is not None
    frames: list[bytes] = []
    while True:
        buf = proc.stdout.read(FRAME_SIZE)
        if len(buf) < FRAME_SIZE:
            break
        frames.append(buf)

    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg decode failed for {path}")
    if not frames:
        raise RuntimeError(f"No frames decoded from {path}")
    return frames


def analyze_frames(frames: list[bytes]) -> tuple[list[float], list[int]]:
    diffs: list[float] = []
    bright_counts: list[int] = []
    prev: bytes | None = None
    for frame in frames:
        bright_counts.append(
            sum(1 for idx in OVERLAY_CROP_IDXS if frame[idx] > OVERLAY_PIXEL_THRESHOLD)
        )
        if prev is not None:
            diffs.append(sum(abs(a - b) for a, b in zip(prev, frame)) / FRAME_SIZE)
        prev = frame
    return diffs, bright_counts


def detect_motion_end_seconds(diffs: list[float], start_trim_seconds: float) -> float:
    if len(diffs) < ROLLING_WINDOW_FRAMES:
        return max((len(diffs) + 1) / FPS, start_trim_seconds + 0.1)

    rolling = [
        sum(diffs[i : i + ROLLING_WINDOW_FRAMES]) / ROLLING_WINDOW_FRAMES
        for i in range(len(diffs) - ROLLING_WINDOW_FRAMES + 1)
    ]
    active = [i for i, value in enumerate(rolling) if value > ROLLING_AVG_THRESHOLD]
    if not active:
        return max(ROLLING_WINDOW_FRAMES / FPS, start_trim_seconds + 0.1)
    return max((active[-1] + ROLLING_WINDOW_FRAMES) / FPS, start_trim_seconds + 0.1)


def detect_significant_motion_end_seconds(
    diffs: list[float], start_trim_seconds: float
) -> float:
    if len(diffs) < ROLLING_WINDOW_FRAMES:
        return max((len(diffs) + 1) / FPS, start_trim_seconds + 0.1)

    rolling = [
        sum(diffs[i : i + ROLLING_WINDOW_FRAMES]) / ROLLING_WINDOW_FRAMES
        for i in range(len(diffs) - ROLLING_WINDOW_FRAMES + 1)
    ]
    active = [i for i, value in enumerate(rolling) if value > SIGNIFICANT_MOTION_THRESHOLD]
    if not active:
        return max(ROLLING_WINDOW_FRAMES / FPS, start_trim_seconds + 0.1)
    return max((active[-1] + ROLLING_WINDOW_FRAMES) / FPS, start_trim_seconds + 0.1)


def detect_overlay_start_seconds(
    bright_counts: list[int],
    motion_end_seconds: float,
    start_trim_seconds: float,
) -> float | None:
    start_frame = max(
        int(
            max(start_trim_seconds, motion_end_seconds - OVERLAY_SEARCH_BACK_SECONDS)
            * FPS
        ),
        0,
    )
    end_frame = min(
        int(motion_end_seconds * FPS),
        len(bright_counts) - OVERLAY_BRIGHT_RUN,
    )
    for frame_idx in range(start_frame, max(start_frame, end_frame)):
        if all(
            bright_counts[j] >= OVERLAY_BRIGHT_COUNT_THRESHOLD
            for j in range(frame_idx, frame_idx + OVERLAY_BRIGHT_RUN)
        ):
            return frame_idx / FPS
    return None


def detect_overlay_after_seconds(
    bright_counts: list[int],
    start_seconds: float,
    end_seconds: float,
) -> float | None:
    start_frame = max(int(start_seconds * FPS), 0)
    end_frame = min(int(end_seconds * FPS), len(bright_counts) - OVERLAY_BRIGHT_RUN)
    for frame_idx in range(start_frame, max(start_frame, end_frame)):
        if all(
            bright_counts[j] >= OVERLAY_BRIGHT_COUNT_THRESHOLD
            for j in range(frame_idx, frame_idx + OVERLAY_BRIGHT_RUN)
        ):
            return frame_idx / FPS
    return None


def choose_output_path(run_dir: Path, output_stem: str) -> Path:
    base = run_dir / f"{output_stem}.mp4"
    if not base.exists():
        return base
    for index in range(2, 100):
        candidate = run_dir / f"{output_stem}_v{index}.mp4"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not choose output path under {run_dir}")


def encode_trimmed_video(
    source: Path,
    output: Path,
    *,
    start_trim_seconds: float,
    end_trim_seconds: float,
) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_trim_seconds:.3f}",
            "-to",
            f"{end_trim_seconds:.3f}",
            "-i",
            str(source),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
    )


def build_trim_result(
    series_game: str,
    run_dir: Path,
    replay_path: Path,
    output_path: Path,
    *,
    start_trim_seconds: float,
    end_overlay_keep_seconds: float,
) -> TrimResult:
    frames = decode_frames(replay_path)
    diffs, bright_counts = analyze_frames(frames)
    duration_seconds = len(frames) / FPS
    motion_end_seconds = min(
        detect_motion_end_seconds(diffs, start_trim_seconds), duration_seconds
    )
    significant_motion_end_seconds = min(
        detect_significant_motion_end_seconds(diffs, start_trim_seconds),
        duration_seconds,
    )
    end_trim_seconds = motion_end_seconds
    overlay_start_seconds: float | None = None
    tail_removed_seconds = duration_seconds - motion_end_seconds
    if tail_removed_seconds >= TAIL_MIN_SECONDS:
        overlay_start_seconds = detect_overlay_start_seconds(
            bright_counts,
            motion_end_seconds,
            start_trim_seconds,
        )
        if overlay_start_seconds is not None:
            end_trim_seconds = min(
                max(
                    overlay_start_seconds + end_overlay_keep_seconds,
                    start_trim_seconds + 0.1,
                ),
                duration_seconds,
            )
    elif motion_end_seconds - significant_motion_end_seconds >= TAIL_MIN_SECONDS:
        fallback_overlay_start_seconds = detect_overlay_after_seconds(
            bright_counts,
            significant_motion_end_seconds,
            motion_end_seconds,
        )
        if fallback_overlay_start_seconds is not None:
            overlay_start_seconds = fallback_overlay_start_seconds
            end_trim_seconds = min(
                max(
                    overlay_start_seconds + end_overlay_keep_seconds,
                    start_trim_seconds + 0.1,
                ),
                duration_seconds,
            )

    return TrimResult(
        series_game=series_game,
        run_dir=str(run_dir),
        source=str(replay_path),
        output=str(output_path),
        start_trim_seconds=round(start_trim_seconds, 3),
        motion_end_seconds=round(motion_end_seconds, 3),
        overlay_start_seconds=(
            None if overlay_start_seconds is None else round(overlay_start_seconds, 3)
        ),
        overlay_keep_seconds=round(end_overlay_keep_seconds, 3),
        end_trim_seconds=round(end_trim_seconds, 3),
        output_duration_seconds=round(end_trim_seconds - start_trim_seconds, 3),
        tail_removed_seconds=round(duration_seconds - end_trim_seconds, 3),
    )


def write_manifest(
    manifest_path: Path,
    *,
    tournament_dir: Path,
    dry_run: bool,
    output_stem: str,
    start_trim_seconds: float,
    end_overlay_keep_seconds: float,
    results: list[TrimResult],
) -> None:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "tournament_dir": str(tournament_dir.resolve()),
        "dry_run": dry_run,
        "output_stem": output_stem,
        "start_trim_seconds": start_trim_seconds,
        "end_overlay_keep_seconds": end_overlay_keep_seconds,
        "clips": [asdict(result) for result in results],
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    args = parse_args()
    ensure_tooling()

    tournament_dir = args.tournament_dir.resolve()
    if not tournament_dir.is_dir():
        raise SystemExit(f"Tournament directory not found: {tournament_dir}")

    selected_games = set(args.game)
    runs = load_game_runs(tournament_dir, selected_games)

    manifest_path = args.manifest_path
    if manifest_path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        manifest_path = tournament_dir / f"{args.output_stem}_{stamp}.json"
    else:
        manifest_path = manifest_path.resolve()

    results: list[TrimResult] = []
    for series_game, run_dir, replay_path in runs:
        output_path = choose_output_path(run_dir, args.output_stem)
        result = build_trim_result(
            series_game,
            run_dir,
            replay_path,
            output_path,
            start_trim_seconds=args.start_trim_seconds,
            end_overlay_keep_seconds=args.end_overlay_keep_seconds,
        )
        results.append(result)
        if not args.dry_run:
            encode_trimmed_video(
                replay_path,
                output_path,
                start_trim_seconds=result.start_trim_seconds,
                end_trim_seconds=result.end_trim_seconds,
            )

        overlay_value = (
            "-"
            if result.overlay_start_seconds is None
            else f"{result.overlay_start_seconds:.3f}"
        )
        print(
            "\t".join(
                [
                    result.series_game,
                    Path(result.output).name,
                    f"start={result.start_trim_seconds:.3f}",
                    f"motion_end={result.motion_end_seconds:.3f}",
                    f"overlay={overlay_value}",
                    f"end={result.end_trim_seconds:.3f}",
                    f"out={result.output_duration_seconds:.3f}",
                ]
            )
        )

    write_manifest(
        manifest_path,
        tournament_dir=tournament_dir,
        dry_run=args.dry_run,
        output_stem=args.output_stem,
        start_trim_seconds=args.start_trim_seconds,
        end_overlay_keep_seconds=args.end_overlay_keep_seconds,
        results=results,
    )
    print(f"MANIFEST\t{manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
