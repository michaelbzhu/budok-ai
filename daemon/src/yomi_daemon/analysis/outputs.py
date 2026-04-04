"""Writers for persisted analysis output."""

from __future__ import annotations

import json
from pathlib import Path

from yomi_daemon.analysis.bracket import build_bracket_frames, render_bracket_png
from yomi_daemon.analysis.report import render_tournament_summary_html
from yomi_daemon.analysis.summary import TournamentSummary
from yomi_daemon.analysis.tournament import TournamentAnalysis
from yomi_daemon.validation import REPO_ROOT

ANALYSIS_OUTPUTS_DIR = REPO_ROOT / "analysis-outputs"


def ensure_analysis_output_dir(*, output_root: Path | None = None) -> Path:
    """Create the root analysis output directory if needed."""

    root = output_root or ANALYSIS_OUTPUTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_tournament_analysis(
    analysis: TournamentAnalysis,
    *,
    output_root: Path | None = None,
    filename: str = "tournament-analysis.json",
) -> Path:
    """Persist a tournament analysis payload under analysis-outputs/<tournament-name>/."""

    root = ensure_analysis_output_dir(output_root=output_root)
    tournament_name = Path(analysis.tournament_dir).name
    target_dir = root / tournament_name
    target_dir.mkdir(parents=True, exist_ok=True)

    target_path = target_dir / filename
    target_path.write_text(
        json.dumps(analysis.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    return target_path


def write_tournament_summary(
    summary: TournamentSummary,
    *,
    output_root: Path | None = None,
    filename: str = "tournament-summary.json",
) -> Path:
    """Persist a tournament summary payload under analysis-outputs/<tournament-name>/."""

    root = ensure_analysis_output_dir(output_root=output_root)
    tournament_name = Path(summary.tournament_dir).name
    target_dir = root / tournament_name
    target_dir.mkdir(parents=True, exist_ok=True)

    target_path = target_dir / filename
    target_path.write_text(
        json.dumps(summary.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    return target_path


def write_tournament_summary_report(
    summary: TournamentSummary,
    *,
    output_root: Path | None = None,
    filename: str = "tournament-summary.html",
) -> Path:
    """Persist a rendered HTML report under analysis-outputs/<tournament-name>/."""

    root = ensure_analysis_output_dir(output_root=output_root)
    tournament_name = Path(summary.tournament_dir).name
    target_dir = root / tournament_name
    target_dir.mkdir(parents=True, exist_ok=True)

    target_path = target_dir / filename
    target_path.write_text(
        render_tournament_summary_html(summary),
        encoding="utf-8",
    )
    return target_path


def write_tournament_bracket_frames(
    analysis: TournamentAnalysis,
    *,
    output_root: Path | None = None,
    directory_name: str = "bracket-frames",
) -> Path:
    """Persist progressive SVG and PNG bracket frames plus an index manifest."""

    root = ensure_analysis_output_dir(output_root=output_root)
    tournament_name = Path(analysis.tournament_dir).name
    target_dir = root / tournament_name / directory_name
    target_dir.mkdir(parents=True, exist_ok=True)

    frames = build_bracket_frames(analysis)
    manifest_frames: list[dict[str, object]] = []
    for frame in frames:
        slug = (
            "initial"
            if frame.revealed_series_id is None
            else f"after-{frame.revealed_series_id.lower()}"
        )
        svg_filename = f"{frame.index:02d}-{slug}.svg"
        png_filename = f"{frame.index:02d}-{slug}.png"
        svg_path = target_dir / svg_filename
        png_path = target_dir / png_filename
        svg_path.write_text(frame.svg, encoding="utf-8")
        png_path.write_bytes(
            render_bracket_png(
                analysis,
                completed_series_ids=set(frame.completed_series_ids),
                frame_label=frame.label,
            )
        )
        manifest_frames.append(
            {
                "index": frame.index,
                "label": frame.label,
                "revealed_series_id": frame.revealed_series_id,
                "completed_series_ids": frame.completed_series_ids,
                "svg_filename": svg_filename,
                "svg_path": str(svg_path),
                "png_filename": png_filename,
                "png_path": str(png_path),
            }
        )

    manifest_path = target_dir / "index.json"
    manifest_path.write_text(
        json.dumps(
            {
                "tournament_dir": analysis.tournament_dir,
                "frame_count": len(manifest_frames),
                "frames": manifest_frames,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path
