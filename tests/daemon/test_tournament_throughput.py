"""Tests for WU-024: tournament concurrency, extended reporting, and multi-match validation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
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
)
from yomi_daemon.tournament.ratings import MatchResult, RatingTable
from yomi_daemon.tournament.reporter import (
    LatencySummary,
    build_report,
    collect_results,
    estimate_cost_usd,
)
from yomi_daemon.tournament.runner import (
    MAX_CONCURRENCY,
    MatchExecutor,
    TournamentConfig,
    plan_tournament,
    run_tournament,
)
from yomi_daemon.tournament.scheduler import MatchPairing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tournament_defaults(**overrides: object) -> TournamentDefaults:
    base: dict[str, object] = {
        "format": "round_robin",
        "mirror_matches_first": False,
        "side_swap": False,
        "games_per_pair": 1,
        "fixed_stage": "training_room",
    }
    base.update(overrides)
    return TournamentDefaults(**base)  # type: ignore[arg-type]


def _baseline_runtime_config(
    *,
    p1: str = "baseline/random",
    p2: str = "baseline/random",
) -> DaemonRuntimeConfig:
    policies = {
        "baseline/random": PolicyConfig(provider="baseline"),
        "baseline/block_always": PolicyConfig(provider="baseline"),
        "baseline/greedy_damage": PolicyConfig(provider="baseline"),
    }
    return DaemonRuntimeConfig(
        version="v1",
        transport=TransportConfig(host="127.0.0.1", port=0),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        logging=LoggingConfig(events=True, prompts=True, raw_provider_payloads=False),
        policy_mapping=PlayerPolicyMapping(p1=p1, p2=p2),
        policies=policies,
        character_selection=CharacterSelectionConfig(
            mode=CharacterSelectionMode.MIRROR
        ),
        tournament=_tournament_defaults(),
        trace_seed=42,
    )


def _write_artifacts(
    runs_root: Path,
    *,
    match_id: str,
    p1_policy: str,
    p2_policy: str,
    winner: str | None = "p1",
    status: str = "completed",
    decision_count: int = 10,
    fallback_count: int = 0,
    total_latency_ms: int = 500,
    latency_sample_count: int = 10,
    tokens_in: int = 0,
    tokens_out: int = 0,
    prompt_texts: Sequence[str] = (),
) -> Path:
    """Write minimal artifact files for testing report generation."""
    run_dir = runs_root / f"20260313T000000Z_{match_id}"
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
        "decision_count": decision_count,
        "fallback_count": fallback_count,
        "total_latency_ms": total_latency_ms,
        "latency_sample_count": latency_sample_count,
        "tokens_in_total": tokens_in,
        "tokens_out_total": tokens_out,
    }

    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

    # Write prompts.jsonl if prompt_texts given
    if prompt_texts:
        lines = [json.dumps({"prompt_text": t}) for t in prompt_texts]
        (run_dir / "prompts.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    else:
        (run_dir / "prompts.jsonl").write_text("", encoding="utf-8")

    return run_dir


class FakeMatchExecutor(MatchExecutor):
    """Executor that records calls and returns canned results."""

    def __init__(
        self,
        *,
        results: dict[str, MatchResult] | None = None,
        delay: float = 0.0,
        error_match_ids: set[str] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results = results or {}
        self._delay = delay
        self._error_ids = error_match_ids or set()
        self._concurrency_tracker: list[int] = []
        self._active = 0
        self._lock = asyncio.Lock()

    async def execute_match(
        self,
        *,
        pairing: MatchPairing,
        match_id: str,
        runtime_config: DaemonRuntimeConfig,
    ) -> MatchResult:
        async with self._lock:
            self._active += 1
            self._concurrency_tracker.append(self._active)

        self.calls.append(
            {
                "pairing": pairing,
                "match_id": match_id,
            }
        )

        if self._delay > 0:
            await asyncio.sleep(self._delay)

        async with self._lock:
            self._active -= 1

        if match_id in self._error_ids:
            raise RuntimeError(f"Simulated failure for {match_id}")

        if match_id in self._results:
            return self._results[match_id]

        return MatchResult(
            p1_policy=pairing.p1_policy,
            p2_policy=pairing.p2_policy,
            winner="p1",
            match_id=match_id,
        )

    @property
    def max_observed_concurrency(self) -> int:
        return max(self._concurrency_tracker) if self._concurrency_tracker else 0


# ---------------------------------------------------------------------------
# Scheduler / runner tests for bounded parallel execution
# ---------------------------------------------------------------------------


class TestBoundedParallelExecution:
    def test_sequential_execution_default(self) -> None:
        """concurrency=1 (default) runs matches one at a time."""
        config = TournamentConfig(
            policy_ids=["a", "b", "c"],
            tournament=_tournament_defaults(format="round_robin"),
            base_runtime_config=_baseline_runtime_config(),
            concurrency=1,
        )
        executor = FakeMatchExecutor(delay=0.01)
        result = asyncio.run(run_tournament(config, match_executor=executor))
        assert result.errors == []
        assert len(executor.calls) == 3  # a-b, a-c, b-c
        # Sequential → max concurrency should be 1
        assert executor.max_observed_concurrency == 1

    def test_parallel_execution_respects_bound(self) -> None:
        """concurrency=2 runs at most 2 matches simultaneously."""
        config = TournamentConfig(
            policy_ids=["a", "b", "c", "d"],
            tournament=_tournament_defaults(format="round_robin"),
            base_runtime_config=_baseline_runtime_config(),
            concurrency=2,
        )
        # Delay to ensure overlap
        executor = FakeMatchExecutor(delay=0.05)
        result = asyncio.run(run_tournament(config, match_executor=executor))
        assert result.errors == []
        # round_robin of 4, no side_swap, 1 game each → C(4,2) = 6 matches
        assert len(executor.calls) == 6
        assert executor.max_observed_concurrency <= 2

    def test_parallel_execution_actually_parallelizes(self) -> None:
        """With concurrency > 1 and delay, observed concurrency should exceed 1."""
        config = TournamentConfig(
            policy_ids=["a", "b", "c", "d"],
            tournament=_tournament_defaults(format="round_robin"),
            base_runtime_config=_baseline_runtime_config(),
            concurrency=4,
        )
        executor = FakeMatchExecutor(delay=0.05)
        result = asyncio.run(run_tournament(config, match_executor=executor))
        assert result.errors == []
        # 6 matches with concurrency 4 and delay → should see > 1 concurrent
        assert executor.max_observed_concurrency > 1

    def test_concurrency_clamped_to_max(self) -> None:
        """Concurrency above MAX_CONCURRENCY is clamped."""
        config = TournamentConfig(
            policy_ids=["a", "b"],
            tournament=_tournament_defaults(format="single"),
            base_runtime_config=_baseline_runtime_config(),
            concurrency=MAX_CONCURRENCY + 100,
        )
        executor = FakeMatchExecutor()
        result = asyncio.run(run_tournament(config, match_executor=executor))
        assert result.errors == []
        assert len(executor.calls) == 1

    def test_concurrency_zero_treated_as_one(self) -> None:
        """concurrency=0 falls back to sequential."""
        config = TournamentConfig(
            policy_ids=["a", "b"],
            tournament=_tournament_defaults(format="single"),
            base_runtime_config=_baseline_runtime_config(),
            concurrency=0,
        )
        executor = FakeMatchExecutor()
        result = asyncio.run(run_tournament(config, match_executor=executor))
        assert result.errors == []
        assert executor.max_observed_concurrency == 1

    def test_error_in_parallel_does_not_abort_others(self) -> None:
        """A failing match in parallel mode should not prevent other matches."""
        config = TournamentConfig(
            policy_ids=["a", "b", "c"],
            tournament=_tournament_defaults(format="round_robin"),
            base_runtime_config=_baseline_runtime_config(),
            concurrency=3,
        )

        # We can't pre-assign error match IDs since they're generated, so
        # use a custom executor that fails every other call.
        class FailEveryOther(FakeMatchExecutor):
            def __init__(self) -> None:
                super().__init__()
                self._call_count = 0

            async def execute_match(
                self,
                *,
                pairing: MatchPairing,
                match_id: str,
                runtime_config: DaemonRuntimeConfig,
            ) -> MatchResult:
                self._call_count += 1
                if self._call_count % 2 == 0:
                    raise RuntimeError("boom")
                return await super().execute_match(
                    pairing=pairing, match_id=match_id, runtime_config=runtime_config
                )

        executor = FailEveryOther()
        result = asyncio.run(run_tournament(config, match_executor=executor))
        # 3 matches total (round_robin of 3), every other fails → 1 failure
        assert len(result.errors) == 1
        assert len(result.match_ids) == 3

    def test_ratings_applied_in_pairing_order(self) -> None:
        """Ratings should be deterministic regardless of execution order."""
        config = TournamentConfig(
            policy_ids=["a", "b"],
            tournament=_tournament_defaults(format="round_robin", games_per_pair=2),
            base_runtime_config=_baseline_runtime_config(),
            concurrency=4,
        )

        # Run twice, check rating determinism
        executor1 = FakeMatchExecutor(delay=0.01)
        result1 = asyncio.run(run_tournament(config, match_executor=executor1))
        executor2 = FakeMatchExecutor(delay=0.01)
        result2 = asyncio.run(run_tournament(config, match_executor=executor2))

        lb1 = {e["policy_id"]: e["rating"] for e in result1.report.leaderboard}
        lb2 = {e["policy_id"]: e["rating"] for e in result2.report.leaderboard}
        assert lb1 == lb2


# ---------------------------------------------------------------------------
# Report generation tests for token, cost, latency, legality, and fallback
# ---------------------------------------------------------------------------


class TestExtendedReportFields:
    def test_token_fields_in_latency_summary(self, tmp_path: Path) -> None:
        _write_artifacts(
            tmp_path,
            match_id="m1",
            p1_policy="a",
            p2_policy="b",
            tokens_in=1000,
            tokens_out=200,
        )
        results = collect_results(tmp_path)
        table = RatingTable()
        table.apply_results(results)
        report = build_report(results, table, runs_root=tmp_path)

        for summary in report.latency_summaries:
            assert "tokens_in_total" in summary
            assert "tokens_out_total" in summary
            assert summary["tokens_in_total"] == 500  # split evenly
            assert summary["tokens_out_total"] == 100

    def test_cost_estimation_in_report(self, tmp_path: Path) -> None:
        _write_artifacts(
            tmp_path,
            match_id="m1",
            p1_policy="a",
            p2_policy="b",
            tokens_in=1_000_000,
            tokens_out=100_000,
        )
        results = collect_results(tmp_path)
        table = RatingTable()
        table.apply_results(results)
        report = build_report(results, table, runs_root=tmp_path)

        for summary in report.latency_summaries:
            assert summary["estimated_cost_usd"] is not None
            assert summary["estimated_cost_usd"] > 0  # type: ignore[operator]

    def test_no_cost_when_zero_tokens(self, tmp_path: Path) -> None:
        _write_artifacts(
            tmp_path,
            match_id="m1",
            p1_policy="a",
            p2_policy="b",
            tokens_in=0,
            tokens_out=0,
        )
        results = collect_results(tmp_path)
        table = RatingTable()
        table.apply_results(results)
        report = build_report(results, table, runs_root=tmp_path)

        for summary in report.latency_summaries:
            assert summary["estimated_cost_usd"] is None

    def test_legality_rate_in_report(self, tmp_path: Path) -> None:
        _write_artifacts(
            tmp_path,
            match_id="m1",
            p1_policy="a",
            p2_policy="b",
            decision_count=10,
            fallback_count=2,
        )
        results = collect_results(tmp_path)
        table = RatingTable()
        table.apply_results(results)
        report = build_report(results, table, runs_root=tmp_path)

        for summary in report.latency_summaries:
            assert "legality_rate" in summary
            # 10 decisions, 2 fallbacks → per policy 5 decisions, 1 fallback
            assert summary["legality_rate"] == pytest.approx(0.8)

    def test_prompt_size_in_report(self, tmp_path: Path) -> None:
        _write_artifacts(
            tmp_path,
            match_id="m1",
            p1_policy="a",
            p2_policy="b",
            prompt_texts=["hello world", "another prompt"],
        )
        results = collect_results(tmp_path)
        table = RatingTable()
        table.apply_results(results)
        report = build_report(results, table, runs_root=tmp_path)

        total_chars = sum(s["total_prompt_chars"] for s in report.latency_summaries)
        assert total_chars > 0

    def test_multiple_matches_aggregate_correctly(self, tmp_path: Path) -> None:
        _write_artifacts(
            tmp_path,
            match_id="m1",
            p1_policy="a",
            p2_policy="b",
            decision_count=10,
            fallback_count=1,
            tokens_in=500,
            tokens_out=100,
        )
        _write_artifacts(
            tmp_path,
            match_id="m2",
            p1_policy="a",
            p2_policy="b",
            decision_count=10,
            fallback_count=1,
            tokens_in=500,
            tokens_out=100,
        )
        results = collect_results(tmp_path)
        table = RatingTable()
        table.apply_results(results)
        report = build_report(results, table, runs_root=tmp_path)

        a_summary = next(s for s in report.latency_summaries if s["policy_id"] == "a")
        # 2 matches × 10 decisions × 0.5 share = 10 decisions for policy a
        assert a_summary["total_decisions"] == 10
        assert a_summary["tokens_in_total"] == 500


class TestEstimateCost:
    def test_zero_tokens(self) -> None:
        assert estimate_cost_usd(0, 0) == 0.0

    def test_known_cost(self) -> None:
        # 1M input at $3/MTok + 1M output at $15/MTok = $18
        cost = estimate_cost_usd(1_000_000, 1_000_000)
        assert cost == pytest.approx(18.0)

    def test_custom_rates(self) -> None:
        cost = estimate_cost_usd(
            100,
            100,
            input_cost_per_token=0.01,
            output_cost_per_token=0.02,
        )
        assert cost == pytest.approx(3.0)


class TestLatencySummaryProperties:
    def test_legality_rate_no_fallbacks(self) -> None:
        s = LatencySummary(policy_id="a", total_decisions=10, total_fallbacks=0)
        assert s.legality_rate == 1.0

    def test_legality_rate_all_fallbacks(self) -> None:
        s = LatencySummary(policy_id="a", total_decisions=10, total_fallbacks=10)
        assert s.legality_rate == 0.0

    def test_legality_rate_zero_decisions(self) -> None:
        s = LatencySummary(policy_id="a", total_decisions=0, total_fallbacks=0)
        assert s.legality_rate == 1.0  # 1 - 0/0 → 1 - 0.0


# ---------------------------------------------------------------------------
# Soak-style regression tests for multi-match tournament runs
# ---------------------------------------------------------------------------


class TestMultiMatchSoakValidation:
    def test_full_tournament_three_policies_sequential(self) -> None:
        """Run a full round-robin tournament sequentially and verify report completeness."""
        config = TournamentConfig(
            policy_ids=[
                "baseline/random",
                "baseline/block_always",
                "baseline/greedy_damage",
            ],
            tournament=_tournament_defaults(format="round_robin", games_per_pair=2),
            base_runtime_config=_baseline_runtime_config(),
            concurrency=1,
        )
        executor = FakeMatchExecutor()
        result = asyncio.run(run_tournament(config, match_executor=executor))

        assert result.errors == []
        # round_robin of 3 policies with 2 games each → C(3,2) × 2 = 6 matches
        assert len(result.match_ids) == 6
        # Leaderboard comes from in-memory ratings (FakeMatchExecutor doesn't write disk artifacts)
        assert len(result.report.leaderboard) == 3
        for entry in result.report.leaderboard:
            assert isinstance(entry["match_count"], int) and entry["match_count"] > 0

    def test_full_tournament_three_policies_parallel(self) -> None:
        """Run the same tournament concurrently and verify identical structure."""
        config = TournamentConfig(
            policy_ids=[
                "baseline/random",
                "baseline/block_always",
                "baseline/greedy_damage",
            ],
            tournament=_tournament_defaults(format="round_robin", games_per_pair=2),
            base_runtime_config=_baseline_runtime_config(),
            concurrency=4,
        )
        executor = FakeMatchExecutor(delay=0.01)
        result = asyncio.run(run_tournament(config, match_executor=executor))

        assert result.errors == []
        assert len(result.match_ids) == 6
        assert len(result.report.leaderboard) == 3

    def test_double_round_robin_side_swap(self) -> None:
        """Double round-robin with side swap produces correct pairing count."""
        config = TournamentConfig(
            policy_ids=["a", "b"],
            tournament=_tournament_defaults(
                format="double_round_robin",
                side_swap=True,
                games_per_pair=1,
            ),
            base_runtime_config=_baseline_runtime_config(),
            concurrency=2,
        )
        pairings = plan_tournament(config)
        executor = FakeMatchExecutor()
        result = asyncio.run(run_tournament(config, match_executor=executor))

        assert result.errors == []
        assert len(result.match_ids) == len(pairings)

    def test_tournament_with_mixed_outcomes(self) -> None:
        """Tournament handles wins, draws, and errors gracefully."""
        config = TournamentConfig(
            policy_ids=["a", "b"],
            tournament=_tournament_defaults(format="round_robin", games_per_pair=3),
            base_runtime_config=_baseline_runtime_config(),
            concurrency=2,
        )

        call_count = 0

        class MixedExecutor(FakeMatchExecutor):
            async def execute_match(
                self,
                *,
                pairing: MatchPairing,
                match_id: str,
                runtime_config: DaemonRuntimeConfig,
            ) -> MatchResult:
                nonlocal call_count
                call_count += 1
                if call_count == 3:
                    return MatchResult(
                        p1_policy=pairing.p1_policy,
                        p2_policy=pairing.p2_policy,
                        winner=None,
                        match_id=match_id,
                    )
                return MatchResult(
                    p1_policy=pairing.p1_policy,
                    p2_policy=pairing.p2_policy,
                    winner="p1" if call_count % 2 == 1 else "p2",
                    match_id=match_id,
                )

        executor = MixedExecutor()
        result = asyncio.run(run_tournament(config, match_executor=executor))
        assert result.errors == []
        assert len(result.match_ids) == 3  # round_robin of 2, gpp=3
        # Leaderboard should reflect the mixed outcomes
        assert len(result.report.leaderboard) == 2
        # At least one policy should have draws
        total_draws = sum(e["draws"] for e in result.report.leaderboard)
        assert total_draws >= 1
