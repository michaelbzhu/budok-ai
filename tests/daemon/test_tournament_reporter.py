"""Tests for tournament report generation: summary tables and matchup aggregation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yomi_daemon.tournament.ratings import MatchResult, RatingTable
from yomi_daemon.tournament.reporter import (
    MatchupStats,
    TournamentReport,
    build_matchup_table,
    build_report,
    collect_results,
)


def _write_result(
    runs_root: Path,
    *,
    match_id: str,
    p1_policy: str,
    p2_policy: str,
    winner: str | None = "p1",
    status: str = "completed",
) -> Path:
    """Write minimal manifest.json and result.json for test collection."""
    run_dir = runs_root / f"20260312T000000Z_{match_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "match_id": match_id,
        "policy_mapping": {"p1": p1_policy, "p2": p2_policy},
    }
    result = {
        "match_id": match_id,
        "status": status,
        "winner": winner,
    }
    metrics = {
        "match_id": match_id,
        "decision_count": 10,
        "fallback_count": 1,
        "total_latency_ms": 500,
        "latency_sample_count": 10,
    }

    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    return run_dir


class TestCollectResults:
    def test_collects_from_disk(self, tmp_path: Path) -> None:
        _write_result(
            tmp_path, match_id="m1", p1_policy="a", p2_policy="b", winner="p1"
        )
        _write_result(
            tmp_path, match_id="m2", p1_policy="b", p2_policy="a", winner="p2"
        )
        results = collect_results(tmp_path)
        assert len(results) == 2
        assert results[0].match_id == "m1"
        assert results[0].p1_policy == "a"
        assert results[0].winner == "p1"

    def test_filters_by_match_ids(self, tmp_path: Path) -> None:
        _write_result(tmp_path, match_id="m1", p1_policy="a", p2_policy="b")
        _write_result(tmp_path, match_id="m2", p1_policy="a", p2_policy="b")
        results = collect_results(tmp_path, match_ids=["m1"])
        assert len(results) == 1
        assert results[0].match_id == "m1"

    def test_skips_in_progress(self, tmp_path: Path) -> None:
        _write_result(
            tmp_path,
            match_id="m1",
            p1_policy="a",
            p2_policy="b",
            status="in_progress",
        )
        results = collect_results(tmp_path)
        assert len(results) == 0

    def test_includes_failed(self, tmp_path: Path) -> None:
        _write_result(
            tmp_path,
            match_id="m1",
            p1_policy="a",
            p2_policy="b",
            status="failed",
            winner=None,
        )
        results = collect_results(tmp_path)
        assert len(results) == 1

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        results = collect_results(tmp_path)
        assert results == []

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        results = collect_results(tmp_path / "nonexistent")
        assert results == []

    def test_skips_missing_manifest(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "20260312T000000Z_m1"
        run_dir.mkdir()
        (run_dir / "result.json").write_text(
            json.dumps({"match_id": "m1", "status": "completed", "winner": "p1"})
        )
        results = collect_results(tmp_path)
        assert len(results) == 0

    def test_skips_missing_policy_mapping(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "20260312T000000Z_m1"
        run_dir.mkdir()
        (run_dir / "manifest.json").write_text(json.dumps({"match_id": "m1"}))
        (run_dir / "result.json").write_text(
            json.dumps({"match_id": "m1", "status": "completed", "winner": "p1"})
        )
        results = collect_results(tmp_path)
        assert len(results) == 0


class TestBuildMatchupTable:
    def test_basic_matchup(self) -> None:
        results = [
            MatchResult(p1_policy="a", p2_policy="b", winner="p1"),
            MatchResult(p1_policy="a", p2_policy="b", winner="p2"),
            MatchResult(p1_policy="b", p2_policy="a", winner="p1"),  # b wins
        ]
        table = build_matchup_table(results)
        assert len(table) == 1
        stats = table[0]
        assert stats.policy_a == "a"
        assert stats.policy_b == "b"
        assert stats.a_wins == 1  # a won as p1 once
        assert stats.b_wins == 2  # b won as p2 once + as p1 once
        assert stats.draws == 0
        assert stats.total == 3

    def test_draws_counted(self) -> None:
        results = [
            MatchResult(p1_policy="x", p2_policy="y", winner=None),
        ]
        table = build_matchup_table(results)
        assert table[0].draws == 1
        assert table[0].a_win_rate == 0.0

    def test_mirror_matches_excluded(self) -> None:
        results = [
            MatchResult(p1_policy="a", p2_policy="a", winner="p1"),
        ]
        table = build_matchup_table(results)
        assert len(table) == 0

    def test_three_policy_matchups(self) -> None:
        results = [
            MatchResult(p1_policy="a", p2_policy="b", winner="p1"),
            MatchResult(p1_policy="a", p2_policy="c", winner="p1"),
            MatchResult(p1_policy="b", p2_policy="c", winner="p2"),
        ]
        table = build_matchup_table(results)
        assert len(table) == 3

    def test_matchup_stats_properties(self) -> None:
        stats = MatchupStats(policy_a="a", policy_b="b", a_wins=3, b_wins=1, draws=1)
        assert stats.total == 5
        assert stats.a_win_rate == pytest.approx(0.6)


class TestBuildReport:
    def test_report_structure(self) -> None:
        results = [
            MatchResult(p1_policy="a", p2_policy="b", winner="p1"),
            MatchResult(p1_policy="b", p2_policy="a", winner="p1"),
        ]
        table = RatingTable()
        table.apply_results(results)

        report = build_report(results, table)

        assert isinstance(report, TournamentReport)
        assert report.total_matches == 2
        assert len(report.leaderboard) == 2
        assert len(report.matchup_table) == 1

    def test_leaderboard_contains_expected_fields(self) -> None:
        results = [
            MatchResult(p1_policy="a", p2_policy="b", winner="p1"),
        ]
        table = RatingTable()
        table.apply_results(results)
        report = build_report(results, table)

        entry = report.leaderboard[0]
        assert "policy_id" in entry
        assert "rating" in entry
        assert "wins" in entry
        assert "losses" in entry
        assert "draws" in entry
        assert "match_count" in entry
        assert "win_rate" in entry

    def test_matchup_table_contains_expected_fields(self) -> None:
        results = [
            MatchResult(p1_policy="a", p2_policy="b", winner="p1"),
        ]
        table = RatingTable()
        table.apply_results(results)
        report = build_report(results, table)

        matchup = report.matchup_table[0]
        assert "policy_a" in matchup
        assert "policy_b" in matchup
        assert "a_wins" in matchup
        assert "b_wins" in matchup
        assert "draws" in matchup
        assert "total" in matchup
        assert "a_win_rate" in matchup

    def test_total_errors_counts_draws(self) -> None:
        results = [
            MatchResult(p1_policy="a", p2_policy="b", winner=None),
            MatchResult(p1_policy="a", p2_policy="b", winner="p1"),
        ]
        table = RatingTable()
        table.apply_results(results)
        report = build_report(results, table)
        assert report.total_errors == 1

    def test_report_with_latency_summaries(self, tmp_path: Path) -> None:
        _write_result(tmp_path, match_id="m1", p1_policy="a", p2_policy="b")
        results = collect_results(tmp_path)
        table = RatingTable()
        table.apply_results(results)
        report = build_report(results, table, runs_root=tmp_path)
        assert len(report.latency_summaries) >= 1

    def test_empty_results_produce_empty_report(self) -> None:
        table = RatingTable()
        report = build_report([], table)
        assert report.total_matches == 0
        assert report.leaderboard == []
        assert report.matchup_table == []
