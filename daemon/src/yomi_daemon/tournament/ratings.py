"""Elo rating calculations and win-rate aggregation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field


DEFAULT_RATING = 1500.0
DEFAULT_K_FACTOR = 32.0


@dataclass(slots=True)
class PolicyRecord:
    """Mutable record tracking a single policy's tournament performance."""

    policy_id: str
    rating: float = DEFAULT_RATING
    wins: int = 0
    losses: int = 0
    draws: int = 0

    @property
    def match_count(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def win_rate(self) -> float:
        total = self.match_count
        if total == 0:
            return 0.0
        return self.wins / total


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Outcome of a single match used as rating input."""

    p1_policy: str
    p2_policy: str
    winner: str | None
    match_id: str = ""


def expected_score(rating_a: float, rating_b: float) -> float:
    """Standard Elo expected score for player A against player B."""
    return 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / 400.0))


def elo_update(
    winner_rating: float,
    loser_rating: float,
    *,
    k: float = DEFAULT_K_FACTOR,
) -> tuple[float, float]:
    """Return (new_winner_rating, new_loser_rating) after a decisive result."""
    e_winner = expected_score(winner_rating, loser_rating)
    new_winner = winner_rating + k * (1.0 - e_winner)
    new_loser = loser_rating + k * (0.0 - (1.0 - e_winner))
    return new_winner, new_loser


def elo_draw(
    rating_a: float,
    rating_b: float,
    *,
    k: float = DEFAULT_K_FACTOR,
) -> tuple[float, float]:
    """Return (new_rating_a, new_rating_b) after a draw."""
    e_a = expected_score(rating_a, rating_b)
    new_a = rating_a + k * (0.5 - e_a)
    new_b = rating_b + k * (0.5 - (1.0 - e_a))
    return new_a, new_b


@dataclass(slots=True)
class RatingTable:
    """Accumulates match results and maintains per-policy Elo ratings."""

    k_factor: float = DEFAULT_K_FACTOR
    initial_rating: float = DEFAULT_RATING
    records: dict[str, PolicyRecord] = field(default_factory=dict)

    def _ensure_record(self, policy_id: str) -> PolicyRecord:
        if policy_id not in self.records:
            self.records[policy_id] = PolicyRecord(
                policy_id=policy_id,
                rating=self.initial_rating,
            )
        return self.records[policy_id]

    def record_result(self, result: MatchResult) -> None:
        """Apply a single match result to the rating table."""
        # Skip mirror matches for rating purposes
        if result.p1_policy == result.p2_policy:
            return

        p1 = self._ensure_record(result.p1_policy)
        p2 = self._ensure_record(result.p2_policy)

        if result.winner == "p1":
            p1.wins += 1
            p2.losses += 1
            p1.rating, p2.rating = elo_update(p1.rating, p2.rating, k=self.k_factor)
        elif result.winner == "p2":
            p2.wins += 1
            p1.losses += 1
            p2.rating, p1.rating = elo_update(p2.rating, p1.rating, k=self.k_factor)
        else:
            p1.draws += 1
            p2.draws += 1
            p1.rating, p2.rating = elo_draw(p1.rating, p2.rating, k=self.k_factor)

    def apply_results(self, results: Sequence[MatchResult]) -> None:
        """Apply a sequence of match results in order."""
        for result in results:
            self.record_result(result)

    def leaderboard(self) -> list[PolicyRecord]:
        """Return records sorted by rating descending."""
        return sorted(self.records.values(), key=lambda r: r.rating, reverse=True)
