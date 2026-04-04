"""Tests for seeded single-elimination bracket."""

import pytest

from yomi_daemon.tournament.bracket import (
    BracketSeed,
    SeriesResult,
    build_bracket,
)


def _seeds(n: int) -> list[BracketSeed]:
    return [BracketSeed(seed=i + 1, policy_id=f"policy-{i + 1}") for i in range(n)]


def test_bracket_8_first_round_pairings():
    """8-entrant bracket should pair in bracket order: 1v8, 4v5, 3v6, 2v7.

    This ensures the top two seeds are on opposite sides and can only
    meet in the final (standard tournament seeding).
    """
    bracket = build_bracket(_seeds(8))
    first_round = bracket.rounds[0]
    assert len(first_round) == 4
    pairs = [(m.high_seed.seed, m.low_seed.seed) for m in first_round]
    assert pairs == [(1, 8), (4, 5), (3, 6), (2, 7)]


def test_bracket_8_has_three_rounds():
    bracket = build_bracket(_seeds(8))
    assert len(bracket.rounds) == 3
    assert bracket.rounds[0][0].round_name == "Quarterfinals"
    assert bracket.rounds[1][0].round_name == "Semifinals"
    assert bracket.rounds[2][0].round_name == "Final"


def test_bracket_4_pairings():
    bracket = build_bracket(_seeds(4))
    first_round = bracket.rounds[0]
    pairs = [(m.high_seed.seed, m.low_seed.seed) for m in first_round]
    assert pairs == [(1, 4), (2, 3)]
    assert len(bracket.rounds) == 2


def test_bracket_2_pairings():
    bracket = build_bracket(_seeds(2))
    assert len(bracket.rounds) == 1
    assert bracket.rounds[0][0].high_seed.seed == 1
    assert bracket.rounds[0][0].low_seed.seed == 2


def test_bracket_invalid_size():
    with pytest.raises(ValueError, match="2, 4, or 8"):
        build_bracket(_seeds(3))
    with pytest.raises(ValueError, match="2, 4, or 8"):
        build_bracket(_seeds(6))


def test_next_series_returns_first_unplayed():
    bracket = build_bracket(_seeds(4))
    first = bracket.next_series()
    assert first is not None
    assert first.series_id == "R0-S0"


def test_winner_advances():
    """Winner of a first-round series should appear in the next round."""
    bracket = build_bracket(_seeds(4))
    seed1 = bracket.seeds[0]
    seed4 = bracket.seeds[3]

    # Seed 1 beats seed 4
    bracket.record_result(
        SeriesResult(
            series_id="R0-S0",
            high_seed=seed1,
            low_seed=seed4,
            winner=seed1,
            loser=seed4,
            high_seed_wins=2,
            low_seed_wins=0,
        )
    )

    # Check that seed 1 is now in the final
    final = bracket.rounds[1][0]
    assert final.high_seed.seed == 1


def test_full_4_bracket():
    """Run a complete 4-entrant bracket."""
    bracket = build_bracket(_seeds(4))
    s1, s2, s3, s4 = bracket.seeds

    # QF1: 1 beats 4
    bracket.record_result(
        SeriesResult(
            series_id="R0-S0",
            high_seed=s1,
            low_seed=s4,
            winner=s1,
            loser=s4,
            high_seed_wins=2,
            low_seed_wins=1,
        )
    )
    # QF2: 3 upsets 2
    bracket.record_result(
        SeriesResult(
            series_id="R0-S1",
            high_seed=s2,
            low_seed=s3,
            winner=s3,
            loser=s2,
            high_seed_wins=0,
            low_seed_wins=2,
        )
    )
    # Final: 1 beats 3
    bracket.record_result(
        SeriesResult(
            series_id="R1-S0",
            high_seed=s1,
            low_seed=s3,
            winner=s1,
            loser=s3,
            high_seed_wins=2,
            low_seed_wins=0,
        )
    )

    assert bracket.champion is not None
    assert bracket.champion.seed == 1
    assert bracket.next_series() is None


def test_to_dict():
    bracket = build_bracket(_seeds(4), best_of=3)
    d = bracket.to_dict()
    assert d["best_of"] == 3
    assert len(d["seeds"]) == 4
    assert len(d["rounds"]) == 2
    assert d["champion"] is None


def test_best_of_configurable():
    bracket = build_bracket(_seeds(4), best_of=5)
    assert bracket.rounds[0][0].best_of == 5
