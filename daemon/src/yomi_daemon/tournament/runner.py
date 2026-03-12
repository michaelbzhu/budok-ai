"""Tournament runner: orchestrates multi-match tournaments with ratings and reporting."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from yomi_daemon.config import DaemonRuntimeConfig, TournamentDefaults
from yomi_daemon.tournament.ratings import MatchResult, RatingTable
from yomi_daemon.tournament.reporter import (
    TournamentReport,
    build_report,
    collect_results,
)
from yomi_daemon.tournament.scheduler import MatchPairing, generate_pairings


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TournamentConfig:
    """Configuration for a tournament run."""

    policy_ids: Sequence[str]
    tournament: TournamentDefaults
    base_runtime_config: DaemonRuntimeConfig
    k_factor: float = 32.0
    concurrency: int = 1
    runs_root: Path | None = None


@dataclass(slots=True)
class TournamentState:
    """Mutable state of a tournament in progress."""

    pairings: list[MatchPairing] = field(default_factory=list)
    match_ids: list[str] = field(default_factory=list)
    completed: int = 0
    errors: list[str] = field(default_factory=list)
    rating_table: RatingTable = field(default_factory=RatingTable)


def plan_tournament(config: TournamentConfig) -> list[MatchPairing]:
    """Generate the pairing schedule for a tournament without executing matches."""
    return generate_pairings(config.policy_ids, config.tournament)


async def run_tournament(
    config: TournamentConfig,
    *,
    match_executor: MatchExecutor | None = None,
) -> TournamentResult:
    """Execute a full tournament: schedule, run matches, compute ratings, report."""
    pairings = plan_tournament(config)
    state = TournamentState(
        pairings=pairings,
        rating_table=RatingTable(k_factor=config.k_factor),
    )

    executor = match_executor or DaemonMatchExecutor(config.base_runtime_config)

    # Run matches sequentially (concurrency=1 for MVP to respect rate limits)
    for pairing in pairings:
        match_id = f"match-{uuid.uuid4().hex[:12]}"
        state.match_ids.append(match_id)

        try:
            result = await executor.execute_match(
                pairing=pairing,
                match_id=match_id,
                runtime_config=config.base_runtime_config,
            )
            state.rating_table.record_result(result)
            state.completed += 1
            logger.info(
                "Match %s completed: %s vs %s -> winner=%s",
                match_id,
                pairing.p1_policy,
                pairing.p2_policy,
                result.winner,
            )
        except Exception as exc:
            msg = f"Match {match_id} failed: {exc}"
            state.errors.append(msg)
            logger.error(msg)

    # Build report from rating table and collected results
    results = list(_results_from_rating_table(state))
    report = build_report(
        results,
        state.rating_table,
        runs_root=config.runs_root,
    )

    return TournamentResult(
        report=report,
        match_ids=list(state.match_ids),
        errors=list(state.errors),
    )


def recompute_ratings(
    runs_root: Path | None = None,
    *,
    match_ids: Sequence[str] | None = None,
    k_factor: float = 32.0,
) -> tuple[RatingTable, list[MatchResult]]:
    """Recompute ratings from persisted result.json artifacts."""
    results = collect_results(runs_root, match_ids=match_ids)
    table = RatingTable(k_factor=k_factor)
    table.apply_results(results)
    return table, results


def _results_from_rating_table(state: TournamentState) -> list[MatchResult]:
    """Extract MatchResult list from a completed tournament state.

    This is used when results were collected in-memory rather than from disk.
    """
    # The rating table has already processed results; we reconstruct from its
    # internal state for reporting purposes. For tournaments driven through
    # run_tournament(), the executor yields results directly.
    # Fallback: collect from disk if match_ids are available.
    return collect_results(match_ids=state.match_ids) if state.match_ids else []


@dataclass(frozen=True, slots=True)
class TournamentResult:
    """Output of a tournament run."""

    report: TournamentReport
    match_ids: list[str]
    errors: list[str]


class MatchExecutor:
    """Protocol for match execution strategies."""

    async def execute_match(
        self,
        *,
        pairing: MatchPairing,
        match_id: str,
        runtime_config: DaemonRuntimeConfig,
    ) -> MatchResult:
        raise NotImplementedError


class DaemonMatchExecutor(MatchExecutor):
    """Executes matches by running a daemon server and waiting for a game connection.

    This executor starts a DaemonServer configured for the given pairing,
    waits for the game to connect and complete one match, then collects
    the result from the artifact directory.
    """

    def __init__(self, base_config: DaemonRuntimeConfig) -> None:
        self._base_config = base_config

    async def execute_match(
        self,
        *,
        pairing: MatchPairing,
        match_id: str,
        runtime_config: DaemonRuntimeConfig,
    ) -> MatchResult:
        from yomi_daemon.server import DaemonServer

        # Reconfigure for this pairing
        config = _reconfigure_for_pairing(runtime_config, pairing)
        server = DaemonServer(
            host=config.transport.host,
            port=config.transport.port,
            policy_mapping=config.policy_mapping,
            config_snapshot=config.to_config_payload(),
            runtime_config=config,
        )

        await server.start()
        try:
            await server.serve_forever()
        finally:
            await server.stop()

        # Collect result from artifacts
        results = collect_results(match_ids=[match_id])
        if results:
            return results[0]

        return MatchResult(
            p1_policy=pairing.p1_policy,
            p2_policy=pairing.p2_policy,
            winner=None,
            match_id=match_id,
        )


def _reconfigure_for_pairing(
    base: DaemonRuntimeConfig,
    pairing: MatchPairing,
) -> DaemonRuntimeConfig:
    """Create a runtime config variant with policy assignments from the pairing."""
    from yomi_daemon.protocol import PlayerPolicyMapping

    return DaemonRuntimeConfig(
        version=base.version,
        transport=base.transport,
        timeout_profile=base.timeout_profile,
        decision_timeout_ms=base.decision_timeout_ms,
        fallback_mode=base.fallback_mode,
        logging=base.logging,
        policy_mapping=PlayerPolicyMapping(
            p1=pairing.p1_policy,
            p2=pairing.p2_policy,
        ),
        policies=base.policies,
        character_selection=base.character_selection,
        tournament=base.tournament,
        trace_seed=base.trace_seed,
        stage_id=base.stage_id,
    )
