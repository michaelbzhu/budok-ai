#!/usr/bin/env python3
"""Prepend tournament title cards to cropped replay videos.

This utility reads bracket metadata plus per-game run artifacts, derives the
stage, models, characters, game number, and series score entering each game,
then creates a new derived video from `replay_cropped_v3.mp4`. It never
modifies, replaces, or deletes the source replay files.

Usage:
    uv run --project daemon python scripts/add_tournament_title_cards.py tournaments/20260322T200240_bracket
    uv run --project daemon python scripts/add_tournament_title_cards.py tournaments/20260322T200240_bracket --dry-run
    uv run --project daemon python scripts/add_tournament_title_cards.py tournaments/20260322T200240_bracket --game QF-1_game1
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
DEFAULT_SOURCE_NAME = "replay_cropped_v3.mp4"
DEFAULT_OUTPUT_STEM = "replay_titlecard_v3"
DEFAULT_TITLE_SECONDS = 2.5
RUN_DIR_PATTERN = re.compile(r"artifacts at (.+?/runs/[^\s]+)")
GAME_STEM_PATTERN = re.compile(r"^(?P<series_id>[A-Z]+-\d+)_game(?P<game_number>\d+)$")
FONT_CANDIDATES = [
    REPO_ROOT / "docs/decompile-output/project/ui/PixeloidSans.ttf",
    REPO_ROOT / "docs/decompile-output/project/ui/monobit.ttf",
    Path("/System/Library/Fonts/SFNS.ttf"),
    Path("/System/Library/Fonts/Supplemental/Helvetica.ttc"),
    Path("/System/Library/Fonts/Supplemental/Menlo.ttc"),
]
STAGE_LABELS = {
    "Quarterfinals": "Quarterfinals",
    "Semifinals": "Semifinals",
    "Final": "Finals",
}


@dataclass(frozen=True, slots=True)
class GameCardInfo:
    series_id: str
    series_game: str
    round_name: str
    stage_label: str
    game_number: int
    run_dir: str
    source: str
    output: str
    p1_model: str
    p1_character: str
    p2_model: str
    p2_character: str
    score_entering: str
    score_after: str
    title_seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create title-card derived replay videos for a tournament directory.",
    )
    parser.add_argument("tournament_dir", type=Path)
    parser.add_argument(
        "--game",
        action="append",
        default=[],
        help="Exact game stem to process, for example QF-1_game1. May be repeated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute metadata and write the manifest without encoding derived videos.",
    )
    parser.add_argument(
        "--source-name",
        default=DEFAULT_SOURCE_NAME,
        help=f"Replay source filename inside each run directory (default: {DEFAULT_SOURCE_NAME}).",
    )
    parser.add_argument(
        "--output-stem",
        default=DEFAULT_OUTPUT_STEM,
        help=f"Base filename for derived videos, without extension (default: {DEFAULT_OUTPUT_STEM}).",
    )
    parser.add_argument(
        "--title-seconds",
        type=float,
        default=DEFAULT_TITLE_SECONDS,
        help=f"Duration of the prepended title card in seconds (default: {DEFAULT_TITLE_SECONDS}).",
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


def choose_output_path(run_dir: Path, output_stem: str) -> Path:
    base = run_dir / f"{output_stem}.mp4"
    if not base.exists():
        return base
    for index in range(2, 100):
        candidate = run_dir / f"{output_stem}_v{index}.mp4"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not choose output path under {run_dir}")


def load_bracket_series(bracket_path: Path) -> dict[str, dict[str, object]]:
    bracket = json.loads(bracket_path.read_text())
    series_map: dict[str, dict[str, object]] = {}
    for round_series in bracket["rounds"]:
        for series in round_series:
            series_map[series["series_id"]] = series
    return series_map


def load_tournament_runs(
    tournament_dir: Path,
    source_name: str,
    selected_games: set[str],
) -> list[tuple[str, str, int, Path, Path]]:
    rows: list[tuple[str, str, int, Path, Path]] = []
    for log_path in sorted(tournament_dir.glob("*_game*.log")):
        stem = log_path.stem
        if selected_games and stem not in selected_games:
            continue
        match = GAME_STEM_PATTERN.match(stem)
        if match is None:
            continue
        run_match = RUN_DIR_PATTERN.search(log_path.read_text())
        if run_match is None:
            raise RuntimeError(f"Could not resolve run directory from {log_path}")
        run_dir = Path(run_match.group(1)).resolve()
        source_path = run_dir / source_name
        if not source_path.is_file():
            raise RuntimeError(f"Missing source video for {stem}: {source_path}")
        series_id = match.group("series_id")
        game_number = int(match.group("game_number"))
        rows.append((stem, series_id, game_number, run_dir, source_path))

    if selected_games:
        found = {stem for stem, _, _, _, _ in rows}
        missing = sorted(selected_games - found)
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(
                f"Requested game(s) not found in {tournament_dir}: {joined}"
            )

    if not rows:
        raise RuntimeError(f"No tournament game logs found in {tournament_dir}")
    return rows


def load_run_metadata(run_dir: Path) -> tuple[dict[str, str], dict[str, str], str]:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    character_selection = json.loads((run_dir / "character_selection.json").read_text())
    result = json.loads((run_dir / "result.json").read_text())

    policy_mapping = manifest["policy_mapping"]
    characters = {
        entry["player_slot"]: entry["character"] for entry in character_selection
    }
    winner = result["winner"]
    return policy_mapping, characters, winner


def build_card_infos(
    bracket_series: dict[str, dict[str, object]],
    runs: list[tuple[str, str, int, Path, Path]],
    output_stem: str,
    title_seconds: float,
) -> list[GameCardInfo]:
    grouped: dict[str, list[tuple[str, str, int, Path, Path]]] = {}
    for row in runs:
        grouped.setdefault(row[1], []).append(row)

    results: list[GameCardInfo] = []
    for series_id in sorted(grouped):
        if series_id not in bracket_series:
            raise RuntimeError(f"Series missing from bracket.json: {series_id}")
        series_meta = bracket_series[series_id]
        round_name = str(series_meta["round_name"])
        stage_label = STAGE_LABELS.get(round_name, round_name)
        p1_wins = 0
        p2_wins = 0
        for series_game, _, game_number, run_dir, source_path in sorted(
            grouped[series_id], key=lambda row: row[2]
        ):
            policy_mapping, characters, winner = load_run_metadata(run_dir)
            score_entering = f"{p1_wins}-{p2_wins}"
            if winner == "p1":
                p1_wins += 1
            elif winner == "p2":
                p2_wins += 1
            else:
                raise RuntimeError(
                    f"Unexpected winner in {run_dir / 'result.json'}: {winner}"
                )

            results.append(
                GameCardInfo(
                    series_id=series_id,
                    series_game=series_game,
                    round_name=round_name,
                    stage_label=stage_label,
                    game_number=game_number,
                    run_dir=str(run_dir),
                    source=str(source_path),
                    output=str(choose_output_path(run_dir, output_stem)),
                    p1_model=policy_mapping["p1"],
                    p1_character=characters["p1"],
                    p2_model=policy_mapping["p2"],
                    p2_character=characters["p2"],
                    score_entering=score_entering,
                    score_after=f"{p1_wins}-{p2_wins}",
                    title_seconds=round(title_seconds, 3),
                )
            )
    return sorted(results, key=lambda item: item.series_game)


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
    fps = Fraction(stream["avg_frame_rate"])
    return int(stream["width"]), int(stream["height"]), fps


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
    font_color: str,
    font_file: Path | None,
    shadow: bool = False,
    alpha: str = "1",
) -> str:
    parts = [
        f"drawtext=text='{escape_drawtext(text)}'",
        f"x={x}",
        f"y={y}",
        f"fontsize={font_size}",
        f"fontcolor={font_color}",
        f"alpha={alpha}",
    ]
    if font_file is not None:
        parts.append(f"fontfile={font_file}")
    if shadow:
        parts.append("shadowcolor=0x000000")
        parts.append("shadowx=2")
        parts.append("shadowy=2")
    return ":".join(parts)


def build_card_filter(
    info: GameCardInfo,
    *,
    width: int,
    height: int,
    font_file: Path | None,
) -> str:
    left_x = 78
    right_x = width - 78 - 500
    panel_y = 250
    panel_w = 500
    panel_h = 275
    panel_mid_y = panel_y + panel_h // 2
    center_x = width // 2
    connector_gap = 44
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
        f"drawbox=x={center_x - 28}:y={panel_mid_y - 34}:w=56:h=2:color=white:t=fill",
        f"drawbox=x={center_x - 28}:y={panel_mid_y + 34}:w=56:h=2:color=white:t=fill",
        drawtext_filter(
            text=info.stage_label,
            x="(w-text_w)/2",
            y="70",
            font_size=38,
            font_color="white",
            font_file=font_file,
        ),
        drawtext_filter(
            text=f"{info.series_id} / GAME {info.game_number}",
            x="(w-text_w)/2",
            y="130",
            font_size=24,
            font_color="white",
            font_file=font_file,
        ),
        drawtext_filter(
            text=f"Series score entering game: {info.score_entering}",
            x="(w-text_w)/2",
            y="170",
            font_size=22,
            font_color="white",
            font_file=font_file,
        ),
        drawtext_filter(
            text="P1",
            x=str(left_x + 26),
            y=str(panel_y + 28),
            font_size=24,
            font_color="white",
            font_file=font_file,
        ),
        drawtext_filter(
            text="P2",
            x=str(right_x + 26),
            y=str(panel_y + 28),
            font_size=24,
            font_color="white",
            font_file=font_file,
        ),
        drawtext_filter(
            text=info.p1_model,
            x=str(left_x + 26),
            y=str(panel_y + 74),
            font_size=21,
            font_color="white",
            font_file=font_file,
        ),
        drawtext_filter(
            text=info.p2_model,
            x=str(right_x + 26),
            y=str(panel_y + 74),
            font_size=21,
            font_color="white",
            font_file=font_file,
        ),
        drawtext_filter(
            text=info.p1_character,
            x=str(left_x + 26),
            y=str(panel_y + 150),
            font_size=44,
            font_color="white",
            font_file=font_file,
        ),
        drawtext_filter(
            text=info.p2_character,
            x=str(right_x + 26),
            y=str(panel_y + 150),
            font_size=44,
            font_color="white",
            font_file=font_file,
        ),
        drawtext_filter(
            text="VS",
            x="(w-text_w)/2",
            y=str(panel_mid_y - 22),
            font_size=34,
            font_color="white",
            font_file=font_file,
        ),
        "format=yuv420p",
    ]
    return ",".join(filters)


def encode_title_card_video(info: GameCardInfo, *, title_seconds: float) -> None:
    width, height, _ = ffprobe_video_info(Path(info.source))
    font_file = choose_font_file()
    card_filter = build_card_filter(
        info, width=width, height=height, font_file=font_file
    )
    with tempfile.TemporaryDirectory(prefix="yomi_titlecard_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        card_path = temp_dir / "card.mp4"
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
                f"{title_seconds:.3f}",
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
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(card_path),
                "-i",
                info.source,
                "-filter_complex",
                "[0:v][1:v]concat=n=2:v=1:a=0[outv]",
                "-map",
                "[outv]",
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
    results: list[GameCardInfo],
) -> None:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "tournament_dir": str(tournament_dir.resolve()),
        "source_name": source_name,
        "output_stem": output_stem,
        "title_seconds": title_seconds,
        "dry_run": dry_run,
        "clips": [asdict(result) for result in results],
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

    bracket_series = load_bracket_series(tournament_dir / "bracket.json")
    selected_games = set(args.game)
    runs = load_tournament_runs(tournament_dir, args.source_name, selected_games)
    results = build_card_infos(
        bracket_series,
        runs,
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
            encode_title_card_video(info, title_seconds=args.title_seconds)
        print(
            "\t".join(
                [
                    info.series_game,
                    Path(info.output).name,
                    info.stage_label,
                    f"game={info.game_number}",
                    f"score_in={info.score_entering}",
                    f"p1={info.p1_model}/{info.p1_character}",
                    f"p2={info.p2_model}/{info.p2_character}",
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
