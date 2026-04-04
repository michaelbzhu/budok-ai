"""Higher-level tournament summary views built from reconstructed analysis."""

from __future__ import annotations

from dataclasses import dataclass, field

from yomi_daemon.analysis.tournament import BracketEntrant, TournamentAnalysis
from yomi_daemon.protocol import ProtocolModel


@dataclass(frozen=True, slots=True)
class CharacterSummary(ProtocolModel):
    """Aggregated usage and win-rate metrics for one character."""

    character: str
    games: int
    wins: int
    losses: int
    pick_rate: float
    win_rate: float


@dataclass(frozen=True, slots=True)
class ModelTournamentSummary(ProtocolModel):
    """Normalized tournament metrics for one model/policy entrant."""

    seed: int
    policy_id: str
    provider: str | None
    model: str | None
    champion: bool
    eliminated_in_round: str | None
    total_games: int
    total_series: int
    series_wins: int
    series_losses: int
    series_win_rate: float
    game_wins: int
    game_losses: int
    game_win_rate: float
    total_turns: int
    average_turns_per_game: float
    total_cost_usd: float
    average_cost_usd_per_turn: float
    total_tokens_in: int
    total_tokens_out: int
    total_reasoning_tokens: int
    average_input_tokens_per_turn: float
    average_output_tokens_per_turn: float
    average_reasoning_tokens_per_turn: float
    average_remaining_hp_on_wins: float | None
    average_remaining_hp_on_losses: float | None
    characters: list[CharacterSummary]


@dataclass(frozen=True, slots=True)
class TournamentOverviewSummary(ProtocolModel):
    """Whole-tournament normalized metrics."""

    total_series: int
    completed_series: int
    total_games: int
    total_game_turns: int
    total_model_turns: int
    average_game_turns_per_game: float
    total_cost_usd: float
    average_cost_usd_per_model_turn: float
    total_tokens_in: int
    total_tokens_out: int
    total_reasoning_tokens: int
    average_input_tokens_per_model_turn: float
    average_output_tokens_per_model_turn: float
    average_reasoning_tokens_per_model_turn: float


@dataclass(frozen=True, slots=True)
class TournamentSummary(ProtocolModel):
    """Compact model-centric summary for one tournament run."""

    tournament_dir: str
    bracket_path: str
    champion: BracketEntrant | None
    overview: TournamentOverviewSummary
    model_summaries: list[ModelTournamentSummary]
    character_summaries: list[CharacterSummary]


def summarize_tournament(analysis: TournamentAnalysis) -> TournamentSummary:
    """Build normalized per-model and global metrics from a tournament analysis."""

    tracker: dict[str, _ModelAccumulator] = {}
    character_totals: dict[str, _CharacterAccumulator] = {}

    for policy in analysis.policy_summaries:
        tracker[policy.policy_id] = _ModelAccumulator(
            seed=policy.seed,
            policy_id=policy.policy_id,
            champion=policy.champion,
            eliminated_in_round=policy.eliminated_in_round,
            series_wins=policy.series_wins,
            series_losses=policy.series_losses,
            game_wins=policy.game_wins,
            game_losses=policy.game_losses,
            total_turns=policy.total_turns,
            total_cost_usd=policy.total_cost_usd,
            total_tokens_in=policy.total_tokens_in,
            total_tokens_out=policy.total_tokens_out,
            total_reasoning_tokens=policy.total_reasoning_tokens,
        )

    for game in analysis.games:
        for participant in (game.p1, game.p2):
            model_acc = tracker[participant.policy_id]
            model_acc.total_games += 1

            if participant.provider is not None and model_acc.provider is None:
                model_acc.provider = participant.provider
            if participant.model is not None and model_acc.model is None:
                model_acc.model = participant.model

            hp = participant.last_observed_hp
            if participant.won is True and hp is not None:
                model_acc.remaining_hp_in_wins.append(hp)
            elif participant.won is False and hp is not None:
                model_acc.remaining_hp_in_losses.append(hp)

            if participant.character is None:
                continue

            char_acc = model_acc.characters.setdefault(
                participant.character,
                _CharacterAccumulator(),
            )
            char_acc.games += 1
            global_char_acc = character_totals.setdefault(
                participant.character,
                _CharacterAccumulator(),
            )
            global_char_acc.games += 1

            if participant.won is True:
                char_acc.wins += 1
                global_char_acc.wins += 1
            elif participant.won is False:
                char_acc.losses += 1
                global_char_acc.losses += 1

    model_summaries = [
        _finalize_model_summary(accumulator)
        for accumulator in sorted(tracker.values(), key=lambda item: item.seed)
    ]
    character_summaries = _finalize_character_summaries(
        character_totals,
        total_games=sum(item.games for item in character_totals.values()),
    )

    total_model_turns = sum(item.total_turns for item in model_summaries)
    total_game_turns = sum(game.total_turns or 0 for game in analysis.games)
    total_cost_usd = sum(item.total_cost_usd for item in model_summaries)
    total_tokens_in = sum(item.total_tokens_in for item in model_summaries)
    total_tokens_out = sum(item.total_tokens_out for item in model_summaries)
    total_reasoning_tokens = sum(item.total_reasoning_tokens for item in model_summaries)

    return TournamentSummary(
        tournament_dir=analysis.tournament_dir,
        bracket_path=analysis.bracket_path,
        champion=analysis.champion,
        overview=TournamentOverviewSummary(
            total_series=analysis.total_series,
            completed_series=analysis.completed_series,
            total_games=analysis.total_games,
            total_game_turns=total_game_turns,
            total_model_turns=total_model_turns,
            average_game_turns_per_game=_safe_rate(total_game_turns, analysis.total_games),
            total_cost_usd=total_cost_usd,
            average_cost_usd_per_model_turn=_safe_rate(total_cost_usd, total_model_turns),
            total_tokens_in=total_tokens_in,
            total_tokens_out=total_tokens_out,
            total_reasoning_tokens=total_reasoning_tokens,
            average_input_tokens_per_model_turn=_safe_rate(total_tokens_in, total_model_turns),
            average_output_tokens_per_model_turn=_safe_rate(total_tokens_out, total_model_turns),
            average_reasoning_tokens_per_model_turn=_safe_rate(
                total_reasoning_tokens,
                total_model_turns,
            ),
        ),
        model_summaries=model_summaries,
        character_summaries=character_summaries,
    )


@dataclass(slots=True)
class _CharacterAccumulator:
    games: int = 0
    wins: int = 0
    losses: int = 0


@dataclass(slots=True)
class _ModelAccumulator:
    seed: int
    policy_id: str
    champion: bool
    eliminated_in_round: str | None
    series_wins: int
    series_losses: int
    game_wins: int
    game_losses: int
    total_turns: int
    total_cost_usd: float
    total_tokens_in: int
    total_tokens_out: int
    total_reasoning_tokens: int
    provider: str | None = None
    model: str | None = None
    total_games: int = 0
    remaining_hp_in_wins: list[int] = field(default_factory=list)
    remaining_hp_in_losses: list[int] = field(default_factory=list)
    characters: dict[str, _CharacterAccumulator] = field(default_factory=dict)


def _finalize_model_summary(acc: _ModelAccumulator) -> ModelTournamentSummary:
    total_series = acc.series_wins + acc.series_losses
    return ModelTournamentSummary(
        seed=acc.seed,
        policy_id=acc.policy_id,
        provider=acc.provider,
        model=acc.model,
        champion=acc.champion,
        eliminated_in_round=acc.eliminated_in_round,
        total_games=acc.total_games,
        total_series=total_series,
        series_wins=acc.series_wins,
        series_losses=acc.series_losses,
        series_win_rate=_safe_rate(acc.series_wins, total_series),
        game_wins=acc.game_wins,
        game_losses=acc.game_losses,
        game_win_rate=_safe_rate(acc.game_wins, acc.total_games),
        total_turns=acc.total_turns,
        average_turns_per_game=_safe_rate(acc.total_turns, acc.total_games),
        total_cost_usd=acc.total_cost_usd,
        average_cost_usd_per_turn=_safe_rate(acc.total_cost_usd, acc.total_turns),
        total_tokens_in=acc.total_tokens_in,
        total_tokens_out=acc.total_tokens_out,
        total_reasoning_tokens=acc.total_reasoning_tokens,
        average_input_tokens_per_turn=_safe_rate(acc.total_tokens_in, acc.total_turns),
        average_output_tokens_per_turn=_safe_rate(acc.total_tokens_out, acc.total_turns),
        average_reasoning_tokens_per_turn=_safe_rate(
            acc.total_reasoning_tokens,
            acc.total_turns,
        ),
        average_remaining_hp_on_wins=_safe_average(acc.remaining_hp_in_wins),
        average_remaining_hp_on_losses=_safe_average(acc.remaining_hp_in_losses),
        characters=_finalize_character_summaries(
            acc.characters,
            total_games=acc.total_games,
        ),
    )


def _finalize_character_summaries(
    tracker: dict[str, _CharacterAccumulator],
    *,
    total_games: int,
) -> list[CharacterSummary]:
    summaries = [
        CharacterSummary(
            character=character,
            games=acc.games,
            wins=acc.wins,
            losses=acc.losses,
            pick_rate=_safe_rate(acc.games, total_games),
            win_rate=_safe_rate(acc.wins, acc.games),
        )
        for character, acc in tracker.items()
    ]
    return sorted(summaries, key=lambda item: (-item.games, item.character))


def _safe_rate(numerator: int | float, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return float(numerator) / denominator


def _safe_average(values: list[int]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
