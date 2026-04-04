"""Seeded single-elimination bracket with best-of-N series."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class BracketSeed:
    """A seeded entrant in the bracket."""

    seed: int
    policy_id: str


@dataclass(frozen=True, slots=True)
class SeriesMatchup:
    """A best-of-N series between two policies in the bracket."""

    series_id: str
    round_name: str
    round_index: int
    high_seed: BracketSeed
    low_seed: BracketSeed
    best_of: int = 3


@dataclass(slots=True)
class SeriesResult:
    """Result of a completed series."""

    series_id: str
    high_seed: BracketSeed
    low_seed: BracketSeed
    winner: BracketSeed
    loser: BracketSeed
    high_seed_wins: int = 0
    low_seed_wins: int = 0
    games: list[GameResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GameResult:
    """Result of a single game within a series."""

    game_index: int
    winner_policy: str
    loser_policy: str
    winner_character: str
    loser_character: str
    winner_hp: int
    loser_hp: int
    end_reason: str
    total_turns: int
    match_id: str


@dataclass(slots=True)
class BracketState:
    """Full bracket state tracking rounds and results."""

    seeds: list[BracketSeed]
    rounds: list[list[SeriesMatchup]]
    results: list[SeriesResult] = field(default_factory=list)
    best_of: int = 3

    @property
    def champion(self) -> BracketSeed | None:
        if not self.results:
            return None
        final_round = self.rounds[-1]
        if not final_round:
            return None
        final_series_id = final_round[0].series_id
        for result in self.results:
            if result.series_id == final_series_id:
                return result.winner
        return None

    def next_series(self) -> SeriesMatchup | None:
        """Return the next unplayed series, or None if bracket is complete."""
        completed_ids = {r.series_id for r in self.results}
        for round_matchups in self.rounds:
            for matchup in round_matchups:
                if matchup.series_id not in completed_ids:
                    return matchup
        return None

    def record_result(self, result: SeriesResult) -> None:
        self.results.append(result)
        self._advance_winner(result)

    def _advance_winner(self, result: SeriesResult) -> None:
        """Advance the winner to their next round matchup."""
        # Find which round this series was in
        series_round_idx = -1
        series_position = -1
        for ri, round_matchups in enumerate(self.rounds):
            for si, matchup in enumerate(round_matchups):
                if matchup.series_id == result.series_id:
                    series_round_idx = ri
                    series_position = si
                    break
            if series_round_idx >= 0:
                break

        if series_round_idx < 0:
            return

        next_round_idx = series_round_idx + 1
        if next_round_idx >= len(self.rounds):
            return  # This was the final

        # Winner goes to position series_position // 2 in next round
        next_position = series_position // 2
        next_round = self.rounds[next_round_idx]
        if next_position >= len(next_round):
            return

        # Update the next round matchup with the winner
        old_matchup = next_round[next_position]
        is_high_slot = series_position % 2 == 0
        if is_high_slot:
            next_round[next_position] = SeriesMatchup(
                series_id=old_matchup.series_id,
                round_name=old_matchup.round_name,
                round_index=old_matchup.round_index,
                high_seed=result.winner,
                low_seed=old_matchup.low_seed,
                best_of=old_matchup.best_of,
            )
        else:
            next_round[next_position] = SeriesMatchup(
                series_id=old_matchup.series_id,
                round_name=old_matchup.round_name,
                round_index=old_matchup.round_index,
                high_seed=old_matchup.high_seed,
                low_seed=result.winner,
                best_of=old_matchup.best_of,
            )

    def to_dict(self) -> dict[str, object]:
        """Serialize bracket state for persistence."""
        rounds_data = []
        for round_matchups in self.rounds:
            round_data = []
            for m in round_matchups:
                matchup_data: dict[str, object] = {
                    "series_id": m.series_id,
                    "round_name": m.round_name,
                    "round_index": m.round_index,
                    "high_seed": {"seed": m.high_seed.seed, "policy_id": m.high_seed.policy_id},
                    "low_seed": {"seed": m.low_seed.seed, "policy_id": m.low_seed.policy_id},
                    "best_of": m.best_of,
                }
                round_data.append(matchup_data)
            rounds_data.append(round_data)

        results_data = []
        for r in self.results:
            result_data: dict[str, object] = {
                "series_id": r.series_id,
                "winner": {"seed": r.winner.seed, "policy_id": r.winner.policy_id},
                "loser": {"seed": r.loser.seed, "policy_id": r.loser.policy_id},
                "high_seed_wins": r.high_seed_wins,
                "low_seed_wins": r.low_seed_wins,
                "games": [
                    {
                        "game_index": g.game_index,
                        "winner_policy": g.winner_policy,
                        "loser_policy": g.loser_policy,
                        "winner_character": g.winner_character,
                        "loser_character": g.loser_character,
                        "winner_hp": g.winner_hp,
                        "loser_hp": g.loser_hp,
                        "end_reason": g.end_reason,
                        "total_turns": g.total_turns,
                        "match_id": g.match_id,
                    }
                    for g in r.games
                ],
            }
            results_data.append(result_data)

        return {
            "seeds": [{"seed": s.seed, "policy_id": s.policy_id} for s in self.seeds],
            "rounds": rounds_data,
            "results": results_data,
            "best_of": self.best_of,
            "champion": (
                {"seed": self.champion.seed, "policy_id": self.champion.policy_id}
                if self.champion
                else None
            ),
        }


_ROUND_NAMES = {
    8: ["Quarterfinals", "Semifinals", "Final"],
    4: ["Semifinals", "Final"],
    2: ["Final"],
}


def build_bracket(
    seeds: list[BracketSeed],
    *,
    best_of: int = 3,
) -> BracketState:
    """Build a seeded single-elimination bracket.

    Supports 2, 4, or 8 entrants. Seeds are paired 1v8, 2v7, 3v6, 4v5
    (or 1v4, 2v3 for 4 entrants, or 1v2 for 2).
    """
    n = len(seeds)
    if n not in (2, 4, 8):
        raise ValueError(f"Bracket requires 2, 4, or 8 entrants, got {n}")

    sorted_seeds = sorted(seeds, key=lambda s: s.seed)
    round_names = _ROUND_NAMES[n]

    # Build first round pairings in bracket order so that the highest
    # seeds stay on opposite sides.  For 8 entrants the position order
    # is 1v8, 4v5, 3v6, 2v7 — this guarantees seed 1 and seed 2 can
    # only meet in the final (standard tournament seeding).
    half = n // 2
    # Generate pairs by seed index: (0,n-1), (1,n-2), …
    pairs = [(sorted_seeds[i], sorted_seeds[n - 1 - i]) for i in range(half)]
    # Bracket-order: reorder pairs so adjacent pairs feed the same semi
    _BRACKET_ORDER: dict[int, list[int]] = {
        1: [0],
        2: [0, 1],
        4: [0, 3, 2, 1],
    }
    order = _BRACKET_ORDER[half]
    first_round: list[SeriesMatchup] = []
    for pos, pair_idx in enumerate(order):
        high, low = pairs[pair_idx]
        first_round.append(
            SeriesMatchup(
                series_id=f"R0-S{pos}",
                round_name=round_names[0],
                round_index=0,
                high_seed=high,
                low_seed=low,
                best_of=best_of,
            )
        )

    rounds: list[list[SeriesMatchup]] = [first_round]

    # Build subsequent rounds with TBD placeholders
    current_count = half
    round_idx = 1
    while current_count > 1:
        next_count = current_count // 2
        next_round: list[SeriesMatchup] = []
        for i in range(next_count):
            placeholder_high = BracketSeed(seed=0, policy_id="TBD")
            placeholder_low = BracketSeed(seed=0, policy_id="TBD")
            next_round.append(
                SeriesMatchup(
                    series_id=f"R{round_idx}-S{i}",
                    round_name=round_names[round_idx],
                    round_index=round_idx,
                    high_seed=placeholder_high,
                    low_seed=placeholder_low,
                    best_of=best_of,
                )
            )
        rounds.append(next_round)
        current_count = next_count
        round_idx += 1

    return BracketState(
        seeds=sorted_seeds,
        rounds=rounds,
        best_of=best_of,
    )
