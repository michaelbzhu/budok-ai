"""Tournament-level analysis helpers built from bracket and run artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from yomi_daemon.analysis.game import GameAnalysis, analyze_game_log
from yomi_daemon.protocol import ProtocolModel
from yomi_daemon.validation import REPO_ROOT

_GAME_LOG_PATTERN = re.compile(r"(?P<series>.+)_game(?P<game>\d+)\.log$")


@dataclass(frozen=True, slots=True)
class BracketEntrant(ProtocolModel):
    """Seeded entrant in the stored bracket state."""

    seed: int
    policy_id: str


@dataclass(frozen=True, slots=True)
class SeriesAnalysis(ProtocolModel):
    """Enriched series-level summary with joined game artifacts."""

    series_id: str
    round_name: str
    high_seed: BracketEntrant | None
    low_seed: BracketEntrant | None
    status: str
    best_of: int
    high_seed_wins: int
    low_seed_wins: int
    winner: BracketEntrant | None
    loser: BracketEntrant | None
    games: list[GameAnalysis]


@dataclass(frozen=True, slots=True)
class PolicyTournamentSummary(ProtocolModel):
    """Tournament-wide summary for one policy."""

    seed: int
    policy_id: str
    champion: bool
    eliminated_in_round: str | None
    series_wins: int
    series_losses: int
    game_wins: int
    game_losses: int
    total_turns: int
    total_tokens_in: int
    total_tokens_out: int
    total_reasoning_tokens: int
    total_cost_usd: float
    character_usage: dict[str, dict[str, int]]


@dataclass(frozen=True, slots=True)
class TournamentAnalysis(ProtocolModel):
    """Joined bracket analysis for an entire tournament directory."""

    tournament_dir: str
    bracket_path: str
    best_of: int
    champion: BracketEntrant | None
    total_series: int
    completed_series: int
    total_games: int
    series: list[SeriesAnalysis]
    games: list[GameAnalysis]
    policy_summaries: list[PolicyTournamentSummary]


def analyze_tournament(
    tournament_dir: Path,
    *,
    repo_root: Path | None = None,
) -> TournamentAnalysis:
    """Reconstruct a completed or in-progress bracket from tournament artifacts."""

    root = repo_root or REPO_ROOT
    bracket_path = tournament_dir / "bracket.json"
    bracket = _load_json_object(bracket_path)

    series_analyses: list[SeriesAnalysis] = []
    all_games: list[GameAnalysis] = []
    best_of = _expect_int(bracket.get("best_of", 0), context="bracket.best_of")

    for round_series in _expect_list(bracket.get("rounds"), context="bracket.rounds"):
        for series_payload in _expect_list(round_series, context="bracket.rounds[]"):
            series = _analyze_series(
                tournament_dir,
                _expect_object(series_payload, context="series"),
                best_of=best_of,
                repo_root=root,
            )
            series_analyses.append(series)
            all_games.extend(series.games)

    champion = _parse_entrant(bracket.get("champion"))
    policy_summaries = _build_policy_summaries(
        seeds=_expect_list(bracket.get("seeds"), context="bracket.seeds"),
        series=series_analyses,
        champion=champion,
    )

    return TournamentAnalysis(
        tournament_dir=str(tournament_dir.resolve()),
        bracket_path=str(bracket_path.resolve()),
        best_of=best_of,
        champion=champion,
        total_series=len(series_analyses),
        completed_series=sum(1 for series in series_analyses if series.status == "completed"),
        total_games=len(all_games),
        series=series_analyses,
        games=all_games,
        policy_summaries=policy_summaries,
    )


def _analyze_series(
    tournament_dir: Path,
    series_payload: dict[str, Any],
    *,
    best_of: int,
    repo_root: Path,
) -> SeriesAnalysis:
    series_id = _expect_string(series_payload.get("series_id"), context="series.series_id")
    round_name = _expect_string(series_payload.get("round_name"), context="series.round_name")
    high_seed = _parse_entrant(series_payload.get("high_seed"))
    low_seed = _parse_entrant(series_payload.get("low_seed"))
    status = _expect_string(series_payload.get("status", "pending"), context="series.status")

    wins = _expect_object(series_payload.get("wins", {}), context="series.wins")
    high_seed_wins = _expect_int(wins.get("high", 0), context="series.wins.high")
    low_seed_wins = _expect_int(wins.get("low", 0), context="series.wins.low")

    logs = sorted(
        tournament_dir.glob(f"{series_id}_game*.log"),
        key=_game_index_from_log_path,
    )
    games = [
        analyze_game_log(
            log_path,
            series_id=series_id,
            game_index=_game_index_from_log_path(log_path),
            round_name=round_name,
            repo_root=repo_root,
        )
        for log_path in logs
    ]

    winner: BracketEntrant | None = None
    loser: BracketEntrant | None = None
    if high_seed is not None and low_seed is not None:
        if high_seed_wins > low_seed_wins:
            winner, loser = high_seed, low_seed
        elif low_seed_wins > high_seed_wins:
            winner, loser = low_seed, high_seed

    return SeriesAnalysis(
        series_id=series_id,
        round_name=round_name,
        high_seed=high_seed,
        low_seed=low_seed,
        status=status,
        best_of=best_of,
        high_seed_wins=high_seed_wins,
        low_seed_wins=low_seed_wins,
        winner=winner,
        loser=loser,
        games=games,
    )


def _build_policy_summaries(
    *,
    seeds: list[Any],
    series: list[SeriesAnalysis],
    champion: BracketEntrant | None,
) -> list[PolicyTournamentSummary]:
    tracker: dict[str, _PolicyAccumulator] = {}
    for seed_payload in seeds:
        entrant = _parse_entrant(seed_payload)
        if entrant is None:
            continue
        tracker[entrant.policy_id] = _PolicyAccumulator(seed=entrant.seed)

    champion_policy = champion.policy_id if champion is not None else None

    for series_entry in series:
        if series_entry.winner is not None:
            tracker[series_entry.winner.policy_id].series_wins += 1
            tracker[series_entry.winner.policy_id].champion = (
                series_entry.winner.policy_id == champion_policy
            )
        if series_entry.loser is not None:
            loser = tracker[series_entry.loser.policy_id]
            loser.series_losses += 1
            loser.eliminated_in_round = series_entry.round_name

        for game in series_entry.games:
            for participant in (game.p1, game.p2):
                acc = tracker[participant.policy_id]
                acc.total_turns += game.total_turns or 0
                acc.total_tokens_in += participant.tokens_in
                acc.total_tokens_out += participant.tokens_out
                acc.total_reasoning_tokens += participant.reasoning_tokens
                acc.total_cost_usd += participant.cost_usd

                if participant.character:
                    char_stats = acc.character_usage.setdefault(
                        participant.character,
                        {"games": 0, "wins": 0, "losses": 0},
                    )
                    char_stats["games"] += 1
                    if participant.won is True:
                        char_stats["wins"] += 1
                    elif participant.won is False:
                        char_stats["losses"] += 1

                if participant.won is True:
                    acc.game_wins += 1
                elif participant.won is False:
                    acc.game_losses += 1

    summaries = [
        PolicyTournamentSummary(
            seed=acc.seed,
            policy_id=policy_id,
            champion=acc.champion,
            eliminated_in_round=acc.eliminated_in_round,
            series_wins=acc.series_wins,
            series_losses=acc.series_losses,
            game_wins=acc.game_wins,
            game_losses=acc.game_losses,
            total_turns=acc.total_turns,
            total_tokens_in=acc.total_tokens_in,
            total_tokens_out=acc.total_tokens_out,
            total_reasoning_tokens=acc.total_reasoning_tokens,
            total_cost_usd=acc.total_cost_usd,
            character_usage=acc.character_usage,
        )
        for policy_id, acc in tracker.items()
    ]
    return sorted(summaries, key=lambda item: item.seed)


@dataclass(slots=True)
class _PolicyAccumulator:
    seed: int
    champion: bool = False
    eliminated_in_round: str | None = None
    series_wins: int = 0
    series_losses: int = 0
    game_wins: int = 0
    game_losses: int = 0
    total_turns: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_reasoning_tokens: int = 0
    total_cost_usd: float = 0.0
    character_usage: dict[str, dict[str, int]] = field(default_factory=dict)


def _game_index_from_log_path(path: Path) -> int:
    match = _GAME_LOG_PATTERN.match(path.name)
    if match is None:
        raise ValueError(f"Unexpected game log name: {path.name}")
    return int(match.group("game"))


def _parse_entrant(raw: object) -> BracketEntrant | None:
    if raw is None:
        return None
    mapping = _expect_object(raw, context="entrant")
    seed = _expect_int(mapping.get("seed"), context="entrant.seed")
    policy_id = _expect_string(mapping.get("policy_id"), context="entrant.policy_id")
    if seed == 0 or policy_id == "TBD":
        return None
    return BracketEntrant(seed=seed, policy_id=policy_id)


def _load_json_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{path} did not contain a JSON object")
    return raw


def _expect_object(raw: object, *, context: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{context} must be a JSON object")
    return cast(dict[str, Any], raw)


def _expect_list(raw: object, *, context: str) -> list[Any]:
    if not isinstance(raw, list):
        raise ValueError(f"{context} must be a JSON array")
    return raw


def _expect_string(raw: object, *, context: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{context} must be a non-empty string")
    return raw


def _expect_int(raw: object, *, context: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{context} must be an integer")
    return raw
