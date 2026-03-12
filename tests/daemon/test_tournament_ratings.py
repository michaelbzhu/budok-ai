"""Tests for Elo rating calculations with known numeric examples."""

from __future__ import annotations


import pytest

from yomi_daemon.tournament.ratings import (
    DEFAULT_RATING,
    MatchResult,
    PolicyRecord,
    RatingTable,
    elo_draw,
    elo_update,
    expected_score,
)


class TestExpectedScore:
    def test_equal_ratings_give_half(self) -> None:
        assert expected_score(1500.0, 1500.0) == pytest.approx(0.5)

    def test_higher_rating_gives_higher_expected(self) -> None:
        assert expected_score(1700.0, 1500.0) > 0.5

    def test_lower_rating_gives_lower_expected(self) -> None:
        assert expected_score(1300.0, 1500.0) < 0.5

    def test_400_point_advantage(self) -> None:
        # 400-point difference should give ~0.909 expected score
        e = expected_score(1900.0, 1500.0)
        assert e == pytest.approx(10.0 / 11.0, abs=0.001)

    def test_scores_sum_to_one(self) -> None:
        e_a = expected_score(1600.0, 1400.0)
        e_b = expected_score(1400.0, 1600.0)
        assert e_a + e_b == pytest.approx(1.0)


class TestEloUpdate:
    def test_equal_ratings_decisive(self) -> None:
        new_w, new_l = elo_update(1500.0, 1500.0)
        assert new_w == pytest.approx(1516.0)
        assert new_l == pytest.approx(1484.0)

    def test_upset_yields_larger_gain(self) -> None:
        # Lower-rated player wins: larger gain
        new_w, new_l = elo_update(1200.0, 1800.0, k=32.0)
        assert new_w > 1200.0 + 16.0  # gain > 16 because upset
        assert new_l < 1800.0 - 16.0

    def test_expected_win_yields_smaller_gain(self) -> None:
        # Higher-rated player wins: smaller gain
        new_w, new_l = elo_update(1800.0, 1200.0, k=32.0)
        assert new_w > 1800.0
        assert new_w - 1800.0 < 16.0  # gain < 16

    def test_ratings_conserved(self) -> None:
        new_w, new_l = elo_update(1600.0, 1400.0)
        assert new_w + new_l == pytest.approx(3000.0)

    def test_custom_k_factor(self) -> None:
        new_w, new_l = elo_update(1500.0, 1500.0, k=16.0)
        assert new_w == pytest.approx(1508.0)
        assert new_l == pytest.approx(1492.0)


class TestEloDraw:
    def test_equal_ratings_draw_no_change(self) -> None:
        new_a, new_b = elo_draw(1500.0, 1500.0)
        assert new_a == pytest.approx(1500.0)
        assert new_b == pytest.approx(1500.0)

    def test_draw_moves_toward_center(self) -> None:
        new_a, new_b = elo_draw(1600.0, 1400.0)
        # Higher-rated player loses rating, lower-rated gains
        assert new_a < 1600.0
        assert new_b > 1400.0

    def test_draw_ratings_conserved(self) -> None:
        new_a, new_b = elo_draw(1700.0, 1300.0)
        assert new_a + new_b == pytest.approx(3000.0)


class TestPolicyRecord:
    def test_defaults(self) -> None:
        r = PolicyRecord(policy_id="test")
        assert r.rating == DEFAULT_RATING
        assert r.match_count == 0
        assert r.win_rate == 0.0

    def test_win_rate(self) -> None:
        r = PolicyRecord(policy_id="test", wins=3, losses=1, draws=1)
        assert r.match_count == 5
        assert r.win_rate == pytest.approx(0.6)


class TestRatingTable:
    def test_single_decisive_result(self) -> None:
        table = RatingTable()
        table.record_result(MatchResult(p1_policy="a", p2_policy="b", winner="p1"))
        assert table.records["a"].wins == 1
        assert table.records["b"].losses == 1
        assert table.records["a"].rating > DEFAULT_RATING
        assert table.records["b"].rating < DEFAULT_RATING

    def test_draw_result(self) -> None:
        table = RatingTable()
        table.record_result(MatchResult(p1_policy="a", p2_policy="b", winner=None))
        assert table.records["a"].draws == 1
        assert table.records["b"].draws == 1

    def test_mirror_match_skipped(self) -> None:
        table = RatingTable()
        table.record_result(MatchResult(p1_policy="a", p2_policy="a", winner="p1"))
        assert "a" not in table.records

    def test_apply_results_sequence(self) -> None:
        table = RatingTable()
        results = [
            MatchResult(p1_policy="a", p2_policy="b", winner="p1"),
            MatchResult(p1_policy="b", p2_policy="a", winner="p1"),  # b wins
            MatchResult(p1_policy="a", p2_policy="b", winner="p1"),
        ]
        table.apply_results(results)
        assert table.records["a"].wins == 2
        assert table.records["a"].losses == 1
        assert table.records["b"].wins == 1
        assert table.records["b"].losses == 2

    def test_leaderboard_sorted_by_rating(self) -> None:
        table = RatingTable()
        results = [
            MatchResult(p1_policy="a", p2_policy="b", winner="p1"),
            MatchResult(p1_policy="a", p2_policy="c", winner="p1"),
            MatchResult(p1_policy="b", p2_policy="c", winner="p1"),
        ]
        table.apply_results(results)
        board = table.leaderboard()
        assert board[0].policy_id == "a"
        assert board[0].rating >= board[1].rating >= board[2].rating

    def test_known_elo_sequence(self) -> None:
        """Verify a known 3-result sequence against hand-calculated values."""
        table = RatingTable(k_factor=32.0, initial_rating=1500.0)

        # Game 1: A (1500) beats B (1500) -> A=1516, B=1484
        table.record_result(MatchResult(p1_policy="A", p2_policy="B", winner="p1"))
        assert table.records["A"].rating == pytest.approx(1516.0)
        assert table.records["B"].rating == pytest.approx(1484.0)

        # Game 2: B (1484) beats A (1516) -> compute expected for B
        e_b = expected_score(1484.0, 1516.0)
        expected_b_new = 1484.0 + 32.0 * (1.0 - e_b)
        expected_a_new = 1516.0 + 32.0 * (0.0 - (1.0 - e_b))
        table.record_result(MatchResult(p1_policy="A", p2_policy="B", winner="p2"))
        assert table.records["B"].rating == pytest.approx(expected_b_new, abs=0.1)
        assert table.records["A"].rating == pytest.approx(expected_a_new, abs=0.1)

    def test_ratings_reproducible_from_results(self) -> None:
        """Two tables with the same result sequence yield identical ratings."""
        results = [
            MatchResult(p1_policy="x", p2_policy="y", winner="p1"),
            MatchResult(p1_policy="y", p2_policy="z", winner="p2"),
            MatchResult(p1_policy="x", p2_policy="z", winner=None),
        ]
        t1 = RatingTable(k_factor=32.0)
        t1.apply_results(results)
        t2 = RatingTable(k_factor=32.0)
        t2.apply_results(results)

        for pid in ("x", "y", "z"):
            assert t1.records[pid].rating == pytest.approx(t2.records[pid].rating)
            assert t1.records[pid].wins == t2.records[pid].wins
            assert t1.records[pid].losses == t2.records[pid].losses
            assert t1.records[pid].draws == t2.records[pid].draws
