"""Tournament report generation from match artifacts."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from yomi_daemon.protocol import JsonObject
from yomi_daemon.storage.writer import RUNS_DIR
from yomi_daemon.tournament.ratings import MatchResult, RatingTable


@dataclass(frozen=True, slots=True)
class MatchupStats:
    """Head-to-head record between two policies."""

    policy_a: str
    policy_b: str
    a_wins: int = 0
    b_wins: int = 0
    draws: int = 0

    @property
    def total(self) -> int:
        return self.a_wins + self.b_wins + self.draws

    @property
    def a_win_rate(self) -> float:
        return self.a_wins / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class LatencySummary:
    """Per-policy latency, fallback, token, and cost summary."""

    policy_id: str
    total_decisions: int = 0
    total_fallbacks: int = 0
    average_latency_ms: float | None = None
    total_latency_ms: int = 0
    tokens_in_total: int = 0
    tokens_out_total: int = 0
    total_prompt_chars: int = 0
    estimated_cost_usd: float | None = None

    @property
    def fallback_rate(self) -> float:
        return self.total_fallbacks / self.total_decisions if self.total_decisions else 0.0

    @property
    def legality_rate(self) -> float:
        """Fraction of decisions that did not require fallback."""
        return 1.0 - self.fallback_rate


@dataclass(frozen=True, slots=True)
class TournamentReport:
    """Complete tournament output: leaderboard, matchups, and summaries."""

    leaderboard: list[JsonObject]
    matchup_table: list[JsonObject]
    latency_summaries: list[JsonObject]
    total_matches: int
    total_errors: int


def collect_results(
    runs_root: Path | None = None,
    *,
    match_ids: Sequence[str] | None = None,
) -> list[MatchResult]:
    """Read result.json files from artifact directories and return MatchResult list.

    If ``match_ids`` is provided, only collect results whose match_id is in the set.
    """
    root = runs_root or RUNS_DIR
    if not root.exists():
        return []

    allowed_ids = set(match_ids) if match_ids is not None else None
    results: list[MatchResult] = []

    for run_dir in sorted(root.iterdir()):
        result_path = run_dir / "result.json"
        manifest_path = run_dir / "manifest.json"
        if not result_path.exists() or not manifest_path.exists():
            continue

        try:
            result_data = json.loads(result_path.read_text(encoding="utf-8"))
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        match_id = result_data.get("match_id", "")
        if allowed_ids is not None and match_id not in allowed_ids:
            continue

        if result_data.get("status") not in ("completed", "failed"):
            continue

        policy_mapping = manifest_data.get("policy_mapping", {})
        p1_policy = policy_mapping.get("p1", "")
        p2_policy = policy_mapping.get("p2", "")
        if not p1_policy or not p2_policy:
            continue

        results.append(
            MatchResult(
                p1_policy=p1_policy,
                p2_policy=p2_policy,
                winner=result_data.get("winner"),
                match_id=match_id,
            )
        )

    return results


def _collect_metrics(
    runs_root: Path,
    match_ids: set[str],
) -> list[Mapping[str, object]]:
    """Read metrics.json files for the given match IDs.

    Also reads prompt character counts from prompts.jsonl when available.
    """
    entries: list[Mapping[str, object]] = []
    for run_dir in sorted(runs_root.iterdir()):
        metrics_path = run_dir / "metrics.json"
        manifest_path = run_dir / "manifest.json"
        if not metrics_path.exists() or not manifest_path.exists():
            continue
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if metrics.get("match_id", "") not in match_ids:
            continue

        # Sum prompt character lengths from prompts.jsonl
        prompt_chars = 0
        prompts_path = run_dir / "prompts.jsonl"
        if prompts_path.exists():
            try:
                for line in prompts_path.read_text(encoding="utf-8").strip().splitlines():
                    record = json.loads(line)
                    prompt_chars += len(record.get("prompt_text", ""))
            except (json.JSONDecodeError, OSError):
                pass

        entries.append(
            {
                "metrics": metrics,
                "manifest": manifest,
                "prompt_chars": prompt_chars,
            }
        )
    return entries


# Default cost rates in USD per token.  These are rough approximations used
# for tournament-level estimates — actual per-provider rates can differ.
_DEFAULT_INPUT_COST_PER_TOKEN = 3.0 / 1_000_000  # $3/MTok
_DEFAULT_OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000  # $15/MTok


def estimate_cost_usd(
    tokens_in: int,
    tokens_out: int,
    *,
    input_cost_per_token: float = _DEFAULT_INPUT_COST_PER_TOKEN,
    output_cost_per_token: float = _DEFAULT_OUTPUT_COST_PER_TOKEN,
) -> float:
    """Estimate cost in USD from token counts using per-token rates."""
    return tokens_in * input_cost_per_token + tokens_out * output_cost_per_token


def build_matchup_table(results: Sequence[MatchResult]) -> list[MatchupStats]:
    """Aggregate head-to-head records from match results."""
    tracker: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"a_wins": 0, "b_wins": 0, "draws": 0}
    )

    for result in results:
        if result.p1_policy == result.p2_policy:
            continue
        key = tuple(sorted([result.p1_policy, result.p2_policy]))
        a, b = key[0], key[1]
        entry = tracker[(a, b)]

        if result.winner == "p1":
            winning_policy = result.p1_policy
        elif result.winner == "p2":
            winning_policy = result.p2_policy
        else:
            winning_policy = None

        if winning_policy == a:
            entry["a_wins"] += 1
        elif winning_policy == b:
            entry["b_wins"] += 1
        else:
            entry["draws"] += 1

    return [
        MatchupStats(
            policy_a=a,
            policy_b=b,
            a_wins=stats["a_wins"],
            b_wins=stats["b_wins"],
            draws=stats["draws"],
        )
        for (a, b), stats in sorted(tracker.items())
    ]


def build_latency_summaries(
    runs_root: Path | None = None,
    *,
    match_ids: Sequence[str] | None = None,
    results: Sequence[MatchResult] | None = None,
) -> list[LatencySummary]:
    """Build per-policy latency and fallback summaries from metrics files."""
    root = runs_root or RUNS_DIR
    if not root.exists():
        return []

    ids = set(match_ids or [])
    if not ids and results:
        ids = {r.match_id for r in results if r.match_id}

    if not ids:
        return []

    metrics_entries = _collect_metrics(root, ids)

    # Accumulate per-policy stats
    per_policy: dict[str, dict[str, int | float]] = defaultdict(
        lambda: {
            "decisions": 0,
            "fallbacks": 0,
            "total_latency": 0,
            "latency_samples": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "prompt_chars": 0,
        }
    )

    for entry in metrics_entries:
        metrics = entry["metrics"]
        manifest = entry["manifest"]
        policy_mapping = manifest.get("policy_mapping", {})  # type: ignore[union-attr]
        decisions = metrics.get("decision_count", 0)  # type: ignore[union-attr]
        fallbacks = metrics.get("fallback_count", 0)  # type: ignore[union-attr]
        total_lat = metrics.get("total_latency_ms", 0)  # type: ignore[union-attr]
        lat_samples = metrics.get("latency_sample_count", 0)  # type: ignore[union-attr]
        tokens_in = metrics.get("tokens_in_total", 0)  # type: ignore[union-attr]
        tokens_out = metrics.get("tokens_out_total", 0)  # type: ignore[union-attr]
        prompt_chars = entry.get("prompt_chars", 0)

        for slot in ("p1", "p2"):
            pid = policy_mapping.get(slot, "")
            if pid:
                # Split evenly between policies as a heuristic
                acc = per_policy[pid]
                share = 0.5 if policy_mapping.get("p1") != policy_mapping.get("p2") else 1.0
                acc["decisions"] = int(acc["decisions"]) + int(decisions * share)
                acc["fallbacks"] = int(acc["fallbacks"]) + int(fallbacks * share)
                acc["total_latency"] = int(acc["total_latency"]) + int(total_lat * share)
                acc["latency_samples"] = int(acc["latency_samples"]) + int(lat_samples * share)
                acc["tokens_in"] = int(acc["tokens_in"]) + int(tokens_in * share)
                acc["tokens_out"] = int(acc["tokens_out"]) + int(tokens_out * share)
                acc["prompt_chars"] = int(acc["prompt_chars"]) + int(int(prompt_chars) * share)  # type: ignore[arg-type]

    summaries: list[LatencySummary] = []
    for pid in sorted(per_policy):
        acc = per_policy[pid]
        samples = int(acc["latency_samples"])
        total = int(acc["total_latency"])
        tok_in = int(acc["tokens_in"])
        tok_out = int(acc["tokens_out"])
        cost = estimate_cost_usd(tok_in, tok_out) if (tok_in + tok_out) > 0 else None
        summaries.append(
            LatencySummary(
                policy_id=pid,
                total_decisions=int(acc["decisions"]),
                total_fallbacks=int(acc["fallbacks"]),
                average_latency_ms=total / samples if samples > 0 else None,
                total_latency_ms=total,
                tokens_in_total=tok_in,
                tokens_out_total=tok_out,
                total_prompt_chars=int(acc["prompt_chars"]),
                estimated_cost_usd=cost,
            )
        )
    return summaries


def build_report(
    results: Sequence[MatchResult],
    rating_table: RatingTable,
    *,
    runs_root: Path | None = None,
) -> TournamentReport:
    """Build a complete tournament report from results and ratings."""
    leaderboard: list[JsonObject] = []
    for record in rating_table.leaderboard():
        leaderboard.append(
            {
                "policy_id": record.policy_id,
                "rating": round(record.rating, 1),
                "wins": record.wins,
                "losses": record.losses,
                "draws": record.draws,
                "match_count": record.match_count,
                "win_rate": round(record.win_rate, 4),
            }
        )

    matchup_table: list[JsonObject] = []
    for stats in build_matchup_table(results):
        matchup_table.append(
            {
                "policy_a": stats.policy_a,
                "policy_b": stats.policy_b,
                "a_wins": stats.a_wins,
                "b_wins": stats.b_wins,
                "draws": stats.draws,
                "total": stats.total,
                "a_win_rate": round(stats.a_win_rate, 4),
            }
        )

    latency = build_latency_summaries(runs_root=runs_root, results=results)
    latency_out: list[JsonObject] = []
    for summary in latency:
        latency_out.append(
            {
                "policy_id": summary.policy_id,
                "total_decisions": summary.total_decisions,
                "total_fallbacks": summary.total_fallbacks,
                "fallback_rate": round(summary.fallback_rate, 4),
                "legality_rate": round(summary.legality_rate, 4),
                "average_latency_ms": (
                    round(summary.average_latency_ms, 1)
                    if summary.average_latency_ms is not None
                    else None
                ),
                "tokens_in_total": summary.tokens_in_total,
                "tokens_out_total": summary.tokens_out_total,
                "total_prompt_chars": summary.total_prompt_chars,
                "estimated_cost_usd": (
                    round(summary.estimated_cost_usd, 6)
                    if summary.estimated_cost_usd is not None
                    else None
                ),
            }
        )

    total_errors = sum(1 for r in results if r.winner is None)

    return TournamentReport(
        leaderboard=leaderboard,
        matchup_table=matchup_table,
        latency_summaries=latency_out,
        total_matches=len(results),
        total_errors=total_errors,
    )
