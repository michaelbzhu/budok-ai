#!/usr/bin/env python3
"""Build per-series aggregate videos from titled game replays.

This utility reads bracket metadata plus tournament game logs, groups
`replay_titlecard_v5.mp4` clips by series, prepends a series-level intro card,
and concatenates the games in order. It never modifies or replaces existing
videos.

Usage:
    uv run --project daemon python scripts/build_tournament_series_videos.py tournaments/20260322T200240_bracket
    uv run --project daemon python scripts/build_tournament_series_videos.py tournaments/20260322T200240_bracket --dry-run
    uv run --project daemon python scripts/build_tournament_series_videos.py tournaments/20260322T200240_bracket --series SF-2
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_NAME = "replay_titlecard_v5.mp4"
DEFAULT_OUTPUT_STEM = "series_replay_v3"
DEFAULT_TITLE_SECONDS = 2.5
RUN_DIR_PATTERN = re.compile(r"artifacts at (.+?/runs/[^\s]+)")
GAME_STEM_PATTERN = re.compile(r"^(?P<series_id>[A-Z]+-\d+)_game(?P<game_number>\d+)$")
FONT_CANDIDATES = [
    REPO_ROOT / "docs/decompile-output/project/ui/PixeloidSans.ttf",
    REPO_ROOT / "docs/decompile-output/project/ui/monobit.ttf",
]
STAGE_LABELS = {
    "Quarterfinals": "Quarterfinals",
    "Semifinals": "Semifinals",
    "Final": "Finals",
}


@dataclass(frozen=True, slots=True)
class SeriesVideoInfo:
    series_id: str
    round_name: str
    stage_label: str
    output: str
    p1_model: str
    p2_model: str
    game_count: int
    source_clips: list[str]
    title_seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create per-series aggregate tournament videos.",
    )
    parser.add_argument("tournament_dir", type=Path)
    parser.add_argument(
        "--series",
        action="append",
        default=[],
        help="Exact series id to process, for example SF-2. May be repeated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute outputs and write the manifest without encoding videos.",
    )
    parser.add_argument(
        "--source-name",
        default=DEFAULT_SOURCE_NAME,
        help=f"Input game video filename inside each run directory (default: {DEFAULT_SOURCE_NAME}).",
    )
    parser.add_argument(
        "--output-stem",
        default=DEFAULT_OUTPUT_STEM,
        help=f"Series output stem, appended after the series id (default: {DEFAULT_OUTPUT_STEM}).",
    )
    parser.add_argument(
        "--title-seconds",
        type=float,
        default=DEFAULT_TITLE_SECONDS,
        help=f"Series intro card duration in seconds (default: {DEFAULT_TITLE_SECONDS}).",
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


def choose_font_file() -> Path | None:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def choose_output_path(tournament_dir: Path, series_id: str, output_stem: str) -> Path:
    base = tournament_dir / f"{series_id}_{output_stem}.mp4"
    if not base.exists():
        return base
    for index in range(2, 100):
        candidate = tournament_dir / f"{series_id}_{output_stem}_v{index}.mp4"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not choose output path under {tournament_dir}")


def load_bracket_series(bracket_path: Path) -> dict[str, dict[str, object]]:
    bracket = json.loads(bracket_path.read_text())
    series_map: dict[str, dict[str, object]] = {}
    for round_series in bracket["rounds"]:
        for series in round_series:
            series_map[series["series_id"]] = series
    return series_map


def load_series_runs(
    tournament_dir: Path,
    source_name: str,
    selected_series: set[str],
) -> dict[str, list[tuple[int, Path]]]:
    grouped: dict[str, list[tuple[int, Path]]] = {}
    for log_path in sorted(tournament_dir.glob("*_game*.log")):
        match = GAME_STEM_PATTERN.match(log_path.stem)
        if match is None:
            continue
        series_id = match.group("series_id")
        if selected_series and series_id not in selected_series:
            continue
        run_match = RUN_DIR_PATTERN.search(log_path.read_text())
        if run_match is None:
            raise RuntimeError(f"Could not resolve run directory from {log_path}")
        run_dir = Path(run_match.group(1)).resolve()
        source_path = run_dir / source_name
        if not source_path.is_file():
            raise RuntimeError(
                f"Missing source video for {log_path.stem}: {source_path}"
            )
        grouped.setdefault(series_id, []).append(
            (int(match.group("game_number")), source_path)
        )

    if selected_series:
        missing = sorted(selected_series - set(grouped))
        if missing:
            raise RuntimeError(
                f"Requested series not found in {tournament_dir}: {', '.join(missing)}"
            )
    if not grouped:
        raise RuntimeError(f"No tournament game logs found in {tournament_dir}")
    return grouped


def load_run_models(run_dir: Path) -> tuple[str, str]:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    return manifest["policy_mapping"]["p1"], manifest["policy_mapping"]["p2"]


def build_series_infos(
    tournament_dir: Path,
    bracket_series: dict[str, dict[str, object]],
    grouped_runs: dict[str, list[tuple[int, Path]]],
    output_stem: str,
    title_seconds: float,
) -> list[SeriesVideoInfo]:
    results: list[SeriesVideoInfo] = []
    for series_id in sorted(grouped_runs):
        if series_id not in bracket_series:
            raise RuntimeError(f"Series missing from bracket.json: {series_id}")
        series_meta = bracket_series[series_id]
        round_name = str(series_meta["round_name"])
        stage_label = STAGE_LABELS.get(round_name, round_name)
        ordered_sources = [
            source
            for _, source in sorted(grouped_runs[series_id], key=lambda row: row[0])
        ]
        p1_model, p2_model = load_run_models(ordered_sources[0].parent)
        results.append(
            SeriesVideoInfo(
                series_id=series_id,
                round_name=round_name,
                stage_label=stage_label,
                output=str(choose_output_path(tournament_dir, series_id, output_stem)),
                p1_model=p1_model,
                p2_model=p2_model,
                game_count=len(ordered_sources),
                source_clips=[str(path) for path in ordered_sources],
                title_seconds=round(title_seconds, 3),
            )
        )
    return results


def ffprobe_video_info(path: Path) -> tuple[int, int, Fraction]:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    stream = payload["streams"][0]
    return (
        int(stream["width"]),
        int(stream["height"]),
        Fraction(stream["avg_frame_rate"]),
    )


def escape_drawtext(value: str) -> str:
    return (
        value.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def drawtext_filter(
    *,
    text: str,
    x: str,
    y: str,
    font_size: int,
    font_file: Path | None,
) -> str:
    parts = [
        f"drawtext=text='{escape_drawtext(text)}'",
        f"x={x}",
        f"y={y}",
        f"fontsize={font_size}",
        "fontcolor=white",
    ]
    if font_file is not None:
        parts.append(f"fontfile={font_file}")
    return ":".join(parts)


def build_series_card_filter(
    info: SeriesVideoInfo,
    *,
    width: int,
    height: int,
    font_file: Path | None,
) -> str:
    left_x = 92
    right_x = width - 92 - 430
    panel_y = 250
    panel_w = 430
    panel_h = 210
    panel_mid_y = panel_y + panel_h // 2
    center_x = width // 2
    connector_gap = 46
    left_connector_w = max(center_x - (left_x + panel_w) - connector_gap, 24)
    right_connector_w = max(right_x - (center_x + connector_gap), 24)
    filters = [
        f"color=c=black:s={width}x{height}:r=30:d={info.title_seconds}",
        f"drawbox=x=12:y=12:w={width - 24}:h={height - 24}:color=white:t=2",
        f"drawbox=x=48:y=56:w={width - 96}:h=2:color=white:t=fill",
        f"drawbox=x={left_x}:y={panel_y}:w={panel_w}:h={panel_h}:color=white:t=2",
        f"drawbox=x={right_x}:y={panel_y}:w={panel_w}:h={panel_h}:color=white:t=2",
        f"drawbox=x={left_x + panel_w}:y={panel_mid_y}:w={left_connector_w}:h=2:color=white:t=fill",
        f"drawbox=x={center_x + connector_gap}:y={panel_mid_y}:w={right_connector_w}:h=2:color=white:t=fill",
        f"drawbox=x={center_x - 30}:y={panel_mid_y - 34}:w=60:h=2:color=white:t=fill",
        f"drawbox=x={center_x - 30}:y={panel_mid_y + 34}:w=60:h=2:color=white:t=fill",
        drawtext_filter(
            text=info.stage_label,
            x="(w-text_w)/2",
            y="74",
            font_size=38,
            font_file=font_file,
        ),
        drawtext_filter(
            text=info.series_id,
            x="(w-text_w)/2",
            y="130",
            font_size=26,
            font_file=font_file,
        ),
        drawtext_filter(
            text="P1",
            x=str(left_x + 24),
            y=str(panel_y + 28),
            font_size=24,
            font_file=font_file,
        ),
        drawtext_filter(
            text="P2",
            x=str(right_x + 24),
            y=str(panel_y + 28),
            font_size=24,
            font_file=font_file,
        ),
        drawtext_filter(
            text=info.p1_model,
            x=str(left_x + 24),
            y=str(panel_y + 98),
            font_size=22,
            font_file=font_file,
        ),
        drawtext_filter(
            text=info.p2_model,
            x=str(right_x + 24),
            y=str(panel_y + 98),
            font_size=22,
            font_file=font_file,
        ),
        drawtext_filter(
            text="VS",
            x="(w-text_w)/2",
            y=str(panel_mid_y - 22),
            font_size=34,
            font_file=font_file,
        ),
        "format=yuv420p",
    ]
    return ",".join(filters)


def encode_series_video(info: SeriesVideoInfo) -> None:
    width, height, _ = ffprobe_video_info(Path(info.source_clips[0]))
    font_file = choose_font_file()
    card_filter = build_series_card_filter(
        info, width=width, height=height, font_file=font_file
    )
    with tempfile.TemporaryDirectory(prefix="yomi_series_video_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        card_path = temp_dir / "series_card.mp4"
        list_path = temp_dir / "concat.txt"
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                card_filter,
                "-t",
                f"{info.title_seconds:.3f}",
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
                str(card_path),
            ],
            check=True,
        )
        concat_lines = [f"file '{card_path.as_posix()}'"]
        concat_lines.extend(
            f"file '{Path(path).as_posix()}'" for path in info.source_clips
        )
        list_path.write_text("\n".join(concat_lines) + "\n")
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
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
                info.output,
            ],
            check=True,
        )


def write_manifest(
    manifest_path: Path,
    *,
    tournament_dir: Path,
    source_name: str,
    output_stem: str,
    title_seconds: float,
    dry_run: bool,
    results: list[SeriesVideoInfo],
) -> None:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "tournament_dir": str(tournament_dir.resolve()),
        "source_name": source_name,
        "output_stem": output_stem,
        "title_seconds": title_seconds,
        "dry_run": dry_run,
        "series_videos": [asdict(result) for result in results],
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    args = parse_args()
    ensure_tooling()
    if args.title_seconds <= 0:
        raise SystemExit("--title-seconds must be positive")

    tournament_dir = args.tournament_dir.resolve()
    if not tournament_dir.is_dir():
        raise SystemExit(f"Tournament directory not found: {tournament_dir}")

    selected_series = set(args.series)
    bracket_series = load_bracket_series(tournament_dir / "bracket.json")
    grouped_runs = load_series_runs(tournament_dir, args.source_name, selected_series)
    results = build_series_infos(
        tournament_dir,
        bracket_series,
        grouped_runs,
        args.output_stem,
        args.title_seconds,
    )

    manifest_path = args.manifest_path
    if manifest_path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        manifest_path = tournament_dir / f"{args.output_stem}_{stamp}.json"
    else:
        manifest_path = manifest_path.resolve()

    for info in results:
        if not args.dry_run:
            encode_series_video(info)
        print(
            "\t".join(
                [
                    info.series_id,
                    Path(info.output).name,
                    info.stage_label,
                    f"games={info.game_count}",
                    f"p1={info.p1_model}",
                    f"p2={info.p2_model}",
                ]
            )
        )

    write_manifest(
        manifest_path,
        tournament_dir=tournament_dir,
        source_name=args.source_name,
        output_stem=args.output_stem,
        title_seconds=args.title_seconds,
        dry_run=args.dry_run,
        results=results,
    )
    print(f"MANIFEST\t{manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
