"""CLI entry point for tournament operations: schedule, report, recompute."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from yomi_daemon.config import load_runtime_config
from yomi_daemon.storage.writer import RUNS_DIR
from yomi_daemon.tournament.ratings import RatingTable
from yomi_daemon.tournament.reporter import build_report, collect_results
from yomi_daemon.tournament.scheduler import generate_pairings


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yomi-tournament",
        description="YOMI tournament scheduling, reporting, and rating tools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # schedule: print pairings for a tournament
    schedule_parser = subparsers.add_parser(
        "schedule", help="Generate and print tournament pairings."
    )
    schedule_parser.add_argument(
        "policy_ids", nargs="+", help="Policy IDs to include in the tournament."
    )
    schedule_parser.add_argument("--config", type=Path, default=None)

    # report: generate a report from existing run artifacts
    report_parser = subparsers.add_parser(
        "report", help="Generate a tournament report from run artifacts."
    )
    report_parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Path to runs directory (defaults to repo runs/).",
    )
    report_parser.add_argument(
        "--match-ids", nargs="*", default=None, help="Limit report to specific match IDs."
    )
    report_parser.add_argument(
        "--k-factor", type=float, default=32.0, help="Elo K-factor for rating calculations."
    )
    report_parser.add_argument(
        "--output", type=Path, default=None, help="Write report JSON to file instead of stdout."
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )

    return parser


def cmd_schedule(args: argparse.Namespace) -> int:
    config = load_runtime_config(args.config)
    pairings = generate_pairings(args.policy_ids, config.tournament)

    print(f"Tournament format: {config.tournament.format}")
    print(f"Games per pair: {config.tournament.games_per_pair}")
    print(f"Side swap: {config.tournament.side_swap}")
    print(f"Mirror matches first: {config.tournament.mirror_matches_first}")
    print(f"Total matches: {len(pairings)}")
    print()

    for i, p in enumerate(pairings):
        swap = " [side-swap]" if p.is_side_swap else ""
        mirror = " [mirror]" if p.p1_policy == p.p2_policy else ""
        print(f"  {i + 1:3d}. {p.p1_policy} vs {p.p2_policy}{swap}{mirror}")

    return 0


def cmd_report(args: argparse.Namespace) -> int:
    runs_root = args.runs_dir or RUNS_DIR
    results = collect_results(runs_root, match_ids=args.match_ids)

    if not results:
        print("No completed match results found.", file=sys.stderr)
        return 1

    table = RatingTable(k_factor=args.k_factor)
    table.apply_results(results)

    report = build_report(results, table, runs_root=runs_root)
    report_json = {
        "leaderboard": report.leaderboard,
        "matchup_table": report.matchup_table,
        "latency_summaries": report.latency_summaries,
        "total_matches": report.total_matches,
        "total_errors": report.total_errors,
    }

    output_text = json.dumps(report_json, indent=2)
    if args.output:
        args.output.write_text(output_text, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(output_text)

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "schedule":
        return cmd_schedule(args)
    if args.command == "report":
        return cmd_report(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
