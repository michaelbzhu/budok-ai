"""CLI entry point for tournament analysis helpers."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from yomi_daemon.analysis.outputs import (
    write_tournament_analysis,
    write_tournament_bracket_frames,
    write_tournament_summary,
    write_tournament_summary_report,
)
from yomi_daemon.analysis.summary import summarize_tournament
from yomi_daemon.analysis.tournament import analyze_tournament


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yomi-analysis",
        description="Analyze completed tournament artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tournament_parser = subparsers.add_parser(
        "tournament",
        help="Analyze a tournament directory and write JSON output.",
    )
    tournament_parser.add_argument("tournament_dir", type=Path)
    tournament_parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Root output directory. Defaults to repo analysis-outputs/.",
    )
    tournament_parser.add_argument(
        "--filename",
        default="tournament-analysis.json",
        help="Output JSON filename inside the tournament output directory.",
    )
    tournament_parser.add_argument(
        "--stdout",
        action="store_true",
        help="Also print the analysis JSON to stdout.",
    )

    summary_parser = subparsers.add_parser(
        "tournament-summary",
        help="Summarize a tournament directory and write summary JSON output.",
    )
    summary_parser.add_argument("tournament_dir", type=Path)
    summary_parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Root output directory. Defaults to repo analysis-outputs/.",
    )
    summary_parser.add_argument(
        "--filename",
        default="tournament-summary.json",
        help="Output JSON filename inside the tournament output directory.",
    )
    summary_parser.add_argument(
        "--stdout",
        action="store_true",
        help="Also print the summary JSON to stdout.",
    )

    bracket_parser = subparsers.add_parser(
        "tournament-bracket",
        help="Render progressive bracket SVG frames for a tournament directory.",
    )
    bracket_parser.add_argument("tournament_dir", type=Path)
    bracket_parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Root output directory. Defaults to repo analysis-outputs/.",
    )

    return parser


def cmd_tournament(args: argparse.Namespace) -> int:
    analysis = analyze_tournament(args.tournament_dir)
    output_path = write_tournament_analysis(
        analysis,
        output_root=args.output_root,
        filename=args.filename,
    )
    print(output_path)
    if args.stdout:
        print(json.dumps(analysis.to_dict(), indent=2))
    return 0


def cmd_tournament_summary(args: argparse.Namespace) -> int:
    analysis = analyze_tournament(args.tournament_dir)
    summary = summarize_tournament(analysis)
    output_path = write_tournament_summary(
        summary,
        output_root=args.output_root,
        filename=args.filename,
    )
    html_output_path = write_tournament_summary_report(
        summary,
        output_root=args.output_root,
    )
    print(output_path)
    print(html_output_path)
    if args.stdout:
        print(json.dumps(summary.to_dict(), indent=2))
    return 0


def cmd_tournament_bracket(args: argparse.Namespace) -> int:
    analysis = analyze_tournament(args.tournament_dir)
    manifest_path = write_tournament_bracket_frames(
        analysis,
        output_root=args.output_root,
    )
    print(manifest_path)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if args.command == "tournament":
        return cmd_tournament(args)
    if args.command == "tournament-summary":
        return cmd_tournament_summary(args)
    if args.command == "tournament-bracket":
        return cmd_tournament_bracket(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
