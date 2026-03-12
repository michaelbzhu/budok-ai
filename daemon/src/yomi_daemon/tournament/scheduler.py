"""Tournament pairing generation for supported scheduling formats."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

from yomi_daemon.config import TournamentDefaults


class SchedulerError(ValueError):
    """Raised when a tournament schedule cannot be generated."""


@dataclass(frozen=True, slots=True)
class MatchPairing:
    """A single scheduled match between two policies."""

    p1_policy: str
    p2_policy: str
    game_index: int
    round_index: int
    is_side_swap: bool = False


def generate_pairings(
    policy_ids: Sequence[str],
    config: TournamentDefaults,
) -> list[MatchPairing]:
    """Generate match pairings for the configured tournament format.

    Supported formats: single, side_swapped_pair, round_robin, double_round_robin.
    """
    if len(policy_ids) < 2:
        raise SchedulerError("at least two policy IDs are required for a tournament")

    unique = list(dict.fromkeys(policy_ids))
    if len(unique) < 2:
        raise SchedulerError("at least two distinct policy IDs are required")

    fmt = config.format
    if fmt == "single":
        return _single(unique)
    if fmt == "side_swapped_pair":
        return _side_swapped_pair(unique)
    if fmt == "round_robin":
        return _round_robin(unique, config, passes=1)
    if fmt == "double_round_robin":
        return _round_robin(unique, config, passes=2)

    raise SchedulerError(f"unsupported tournament format: {fmt!r}")


def _single(policy_ids: list[str]) -> list[MatchPairing]:
    return [
        MatchPairing(
            p1_policy=policy_ids[0],
            p2_policy=policy_ids[1],
            game_index=0,
            round_index=0,
        )
    ]


def _side_swapped_pair(policy_ids: list[str]) -> list[MatchPairing]:
    return [
        MatchPairing(
            p1_policy=policy_ids[0],
            p2_policy=policy_ids[1],
            game_index=0,
            round_index=0,
        ),
        MatchPairing(
            p1_policy=policy_ids[1],
            p2_policy=policy_ids[0],
            game_index=1,
            round_index=1,
            is_side_swap=True,
        ),
    ]


def _round_robin(
    policy_ids: list[str],
    config: TournamentDefaults,
    *,
    passes: int,
) -> list[MatchPairing]:
    """Round-robin (or double round-robin) across all policy pairs.

    For each pair, schedule ``games_per_pair`` games.  When ``side_swap``
    is enabled, odd-indexed games swap sides.  When ``mirror_matches_first``
    is enabled, self-play mirror matches for each policy precede cross-policy
    matches.
    """
    pairings: list[MatchPairing] = []
    round_counter = 0

    for _ in range(passes):
        if config.mirror_matches_first:
            for policy in policy_ids:
                for game_idx in range(config.games_per_pair):
                    pairings.append(
                        MatchPairing(
                            p1_policy=policy,
                            p2_policy=policy,
                            game_index=game_idx,
                            round_index=round_counter,
                        )
                    )
                    round_counter += 1

        for a, b in combinations(policy_ids, 2):
            for game_idx in range(config.games_per_pair):
                if config.side_swap and game_idx % 2 == 1:
                    pairings.append(
                        MatchPairing(
                            p1_policy=b,
                            p2_policy=a,
                            game_index=game_idx,
                            round_index=round_counter,
                            is_side_swap=True,
                        )
                    )
                else:
                    pairings.append(
                        MatchPairing(
                            p1_policy=a,
                            p2_policy=b,
                            game_index=game_idx,
                            round_index=round_counter,
                        )
                    )
                round_counter += 1

    return pairings
