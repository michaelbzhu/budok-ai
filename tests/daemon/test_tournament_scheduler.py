"""Tests for tournament pairing generation across supported formats."""

from __future__ import annotations

import pytest

from yomi_daemon.config import TournamentDefaults
from yomi_daemon.tournament.scheduler import (
    SchedulerError,
    generate_pairings,
)


def _defaults(**overrides: object) -> TournamentDefaults:
    base: dict[str, object] = {
        "format": "round_robin",
        "mirror_matches_first": True,
        "side_swap": True,
        "games_per_pair": 2,
        "fixed_stage": "training_room",
    }
    base.update(overrides)
    return TournamentDefaults(**base)  # type: ignore[arg-type]


POLICIES = ["baseline/random", "baseline/block_always", "baseline/greedy_damage"]


class TestSingleFormat:
    def test_single_produces_one_match(self) -> None:
        config = _defaults(format="single")
        pairings = generate_pairings(POLICIES[:2], config)
        assert len(pairings) == 1
        assert pairings[0].p1_policy == "baseline/random"
        assert pairings[0].p2_policy == "baseline/block_always"

    def test_single_ignores_extra_policies(self) -> None:
        config = _defaults(format="single")
        pairings = generate_pairings(POLICIES, config)
        assert len(pairings) == 1


class TestSideSwappedPairFormat:
    def test_side_swapped_produces_two_matches(self) -> None:
        config = _defaults(format="side_swapped_pair")
        pairings = generate_pairings(POLICIES[:2], config)
        assert len(pairings) == 2

    def test_sides_are_swapped(self) -> None:
        config = _defaults(format="side_swapped_pair")
        pairings = generate_pairings(POLICIES[:2], config)
        assert pairings[0].p1_policy == "baseline/random"
        assert pairings[0].p2_policy == "baseline/block_always"
        assert pairings[0].is_side_swap is False
        assert pairings[1].p1_policy == "baseline/block_always"
        assert pairings[1].p2_policy == "baseline/random"
        assert pairings[1].is_side_swap is True


class TestRoundRobinFormat:
    def test_round_robin_two_policies_no_mirror(self) -> None:
        config = _defaults(
            format="round_robin",
            mirror_matches_first=False,
            side_swap=False,
            games_per_pair=3,
        )
        pairings = generate_pairings(POLICIES[:2], config)
        assert len(pairings) == 3
        for p in pairings:
            assert p.p1_policy == "baseline/random"
            assert p.p2_policy == "baseline/block_always"

    def test_round_robin_with_side_swap(self) -> None:
        config = _defaults(
            format="round_robin",
            mirror_matches_first=False,
            side_swap=True,
            games_per_pair=4,
        )
        pairings = generate_pairings(POLICIES[:2], config)
        assert len(pairings) == 4
        swapped = [p for p in pairings if p.is_side_swap]
        normal = [p for p in pairings if not p.is_side_swap]
        assert len(swapped) == 2
        assert len(normal) == 2

    def test_round_robin_with_mirror(self) -> None:
        config = _defaults(
            format="round_robin",
            mirror_matches_first=True,
            side_swap=False,
            games_per_pair=1,
        )
        pairings = generate_pairings(POLICIES[:2], config)
        # 2 mirror matches (one per policy) + 1 cross match = 3
        assert len(pairings) == 3
        mirrors = [p for p in pairings if p.p1_policy == p.p2_policy]
        assert len(mirrors) == 2

    def test_round_robin_three_policies(self) -> None:
        config = _defaults(
            format="round_robin",
            mirror_matches_first=False,
            side_swap=False,
            games_per_pair=2,
        )
        pairings = generate_pairings(POLICIES, config)
        # C(3,2) = 3 pairs * 2 games = 6
        assert len(pairings) == 6

    def test_round_robin_three_with_mirror_and_swap(self) -> None:
        config = _defaults(
            format="round_robin",
            mirror_matches_first=True,
            side_swap=True,
            games_per_pair=2,
        )
        pairings = generate_pairings(POLICIES, config)
        # mirrors: 3 policies * 2 games = 6
        # cross: 3 pairs * 2 games = 6
        assert len(pairings) == 12
        mirrors = [p for p in pairings if p.p1_policy == p.p2_policy]
        assert len(mirrors) == 6


class TestDoubleRoundRobinFormat:
    def test_double_round_robin_doubles_matches(self) -> None:
        config = _defaults(
            format="double_round_robin",
            mirror_matches_first=False,
            side_swap=False,
            games_per_pair=1,
        )
        pairings = generate_pairings(POLICIES[:2], config)
        # 2 passes * 1 pair * 1 game = 2
        assert len(pairings) == 2


class TestValidation:
    def test_fewer_than_two_policies_raises(self) -> None:
        config = _defaults()
        with pytest.raises(SchedulerError, match="at least two"):
            generate_pairings(["only_one"], config)

    def test_duplicate_policies_raises(self) -> None:
        config = _defaults()
        with pytest.raises(SchedulerError, match="at least two distinct"):
            generate_pairings(["same", "same"], config)

    def test_unsupported_format_raises(self) -> None:
        config = _defaults(format="swiss")
        with pytest.raises(SchedulerError, match="unsupported"):
            generate_pairings(POLICIES[:2], config)


class TestPairingStructure:
    def test_round_indexes_are_unique(self) -> None:
        config = _defaults(
            format="round_robin",
            mirror_matches_first=False,
            side_swap=True,
            games_per_pair=4,
        )
        pairings = generate_pairings(POLICIES, config)
        round_indexes = [p.round_index for p in pairings]
        assert len(round_indexes) == len(set(round_indexes))

    def test_game_indexes_within_pair(self) -> None:
        config = _defaults(
            format="round_robin",
            mirror_matches_first=False,
            side_swap=False,
            games_per_pair=3,
        )
        pairings = generate_pairings(POLICIES[:2], config)
        game_indexes = [p.game_index for p in pairings]
        assert game_indexes == [0, 1, 2]
