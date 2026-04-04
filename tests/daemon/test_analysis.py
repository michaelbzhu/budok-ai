"""Tests for analysis utilities that reconstruct bracket runs from artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from yomi_daemon.analysis.bracket import build_bracket_frames
from yomi_daemon.analysis.game import analyze_game_log, parse_run_dir_from_log
from yomi_daemon.analysis.outputs import (
    write_tournament_analysis,
    write_tournament_bracket_frames,
    write_tournament_summary,
    write_tournament_summary_report,
)
from yomi_daemon.analysis.report import render_tournament_summary_html
from yomi_daemon.analysis.summary import summarize_tournament
from yomi_daemon.analysis.tournament import analyze_tournament


def _write_game_artifacts(
    root: Path,
    *,
    run_name: str,
    match_id: str,
    p1_policy: str,
    p2_policy: str,
    p1_character: str,
    p2_character: str,
    winner: str,
    total_turns: int,
    fallback_count: int,
    p1_prompt_tokens: int,
    p1_completion_tokens: int,
    p1_reasoning_tokens: int,
    p1_cost: float,
    p2_prompt_tokens: int,
    p2_completion_tokens: int,
    p2_reasoning_tokens: int,
    p2_cost: float,
    p1_hp: int,
    p2_hp: int,
    p1_provider: str = "openrouter",
    p1_model: str | None = None,
    p2_provider: str = "openrouter",
    p2_model: str | None = None,
) -> Path:
    run_dir = root / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "match_id": match_id,
                "policy_mapping": {"p1": p1_policy, "p2": p2_policy},
                "policies": {
                    p1_policy: {
                        "provider": p1_provider,
                        "model": p1_model or p1_policy,
                    },
                    p2_policy: {
                        "provider": p2_provider,
                        "model": p2_model or p2_policy,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "match_id": match_id,
                "status": "completed",
                "started_at": "2026-03-23T10:00:00Z",
                "completed_at": "2026-03-23T10:05:00Z",
                "winner": winner,
                "end_reason": "ko",
                "total_turns": total_turns,
                "replay_path": None,
                "metrics": {
                    "fallback_count": fallback_count,
                    "error_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "character_selection.json").write_text(
        json.dumps(
            [
                {"player_slot": "p1", "policy_id": p1_policy, "character": p1_character},
                {"player_slot": "p2", "policy_id": p2_policy, "character": p2_character},
            ]
        ),
        encoding="utf-8",
    )
    (run_dir / "prompts.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "player_id": "p1",
                        "provider_response": {
                            "attempts": [
                                {
                                    "usage": {
                                        "prompt_tokens": p1_prompt_tokens,
                                        "completion_tokens": p1_completion_tokens,
                                        "completion_tokens_details": {
                                            "reasoning_tokens": p1_reasoning_tokens
                                        },
                                        "cost": p1_cost,
                                    }
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "player_id": "p2",
                        "provider_response": {
                            "attempts": [
                                {
                                    "usage": {
                                        "prompt_tokens": p2_prompt_tokens,
                                        "completion_tokens": p2_completion_tokens,
                                        "completion_tokens_details": {
                                            "reasoning_tokens": p2_reasoning_tokens
                                        },
                                        "cost": p2_cost,
                                    }
                                }
                            ]
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "decisions.jsonl").write_text(
        json.dumps(
            {
                "request_payload": {
                    "observation": {
                        "fighters": [
                            {"id": "p1", "hp": p1_hp},
                            {"id": "p2", "hp": p2_hp},
                        ]
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def _write_game_log(
    tournament_dir: Path, *, series_id: str, game_index: int, run_name: str
) -> Path:
    log_path = tournament_dir / f"{series_id}_game{game_index}.log"
    log_path.write_text(
        "\n".join(
            [
                "[run_match] Match completed successfully!",
                f"[run_match] Artifacts: runs/{run_name}/",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return log_path


def _write_bracket(tournament_dir: Path) -> None:
    bracket = {
        "seeds": [
            {"seed": 1, "policy_id": "policy/a"},
            {"seed": 2, "policy_id": "policy/b"},
            {"seed": 3, "policy_id": "policy/c"},
            {"seed": 4, "policy_id": "policy/d"},
        ],
        "rounds": [
            [
                {
                    "series_id": "SF-1",
                    "round_name": "Semifinals",
                    "high_seed": {"seed": 1, "policy_id": "policy/a"},
                    "low_seed": {"seed": 4, "policy_id": "policy/d"},
                    "wins": {"high": 1, "low": 0},
                    "games": [],
                    "status": "completed",
                },
                {
                    "series_id": "SF-2",
                    "round_name": "Semifinals",
                    "high_seed": {"seed": 2, "policy_id": "policy/b"},
                    "low_seed": {"seed": 3, "policy_id": "policy/c"},
                    "wins": {"high": 0, "low": 1},
                    "games": [],
                    "status": "completed",
                },
            ],
            [
                {
                    "series_id": "F-1",
                    "round_name": "Final",
                    "high_seed": {"seed": 1, "policy_id": "policy/a"},
                    "low_seed": {"seed": 3, "policy_id": "policy/c"},
                    "wins": {"high": 0, "low": 1},
                    "games": [],
                    "status": "completed",
                }
            ],
        ],
        "best_of": 1,
        "champion": {"seed": 3, "policy_id": "policy/c"},
    }
    (tournament_dir / "bracket.json").write_text(
        json.dumps(bracket, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_synthetic_tournament(tmp_path: Path) -> Path:
    tournament_dir = tmp_path / "tournaments" / "synthetic_bracket"
    tournament_dir.mkdir(parents=True, exist_ok=True)
    _write_bracket(tournament_dir)

    _write_game_artifacts(
        tmp_path,
        run_name="20260323T100000Z_match-sf1",
        match_id="match-sf1",
        p1_policy="policy/a",
        p2_policy="policy/d",
        p1_character="Wizard",
        p2_character="Ninja",
        winner="p1",
        total_turns=42,
        fallback_count=1,
        p1_prompt_tokens=100,
        p1_completion_tokens=20,
        p1_reasoning_tokens=5,
        p1_cost=0.12,
        p2_prompt_tokens=110,
        p2_completion_tokens=30,
        p2_reasoning_tokens=7,
        p2_cost=0.22,
        p1_hp=250,
        p2_hp=0,
    )
    _write_game_artifacts(
        tmp_path,
        run_name="20260323T101000Z_match-sf2",
        match_id="match-sf2",
        p1_policy="policy/b",
        p2_policy="policy/c",
        p1_character="Cowboy",
        p2_character="Robot",
        winner="p2",
        total_turns=55,
        fallback_count=0,
        p1_prompt_tokens=120,
        p1_completion_tokens=40,
        p1_reasoning_tokens=6,
        p1_cost=0.14,
        p2_prompt_tokens=140,
        p2_completion_tokens=50,
        p2_reasoning_tokens=8,
        p2_cost=0.24,
        p1_hp=0,
        p2_hp=90,
    )
    _write_game_artifacts(
        tmp_path,
        run_name="20260323T102000Z_match-f1",
        match_id="match-f1",
        p1_policy="policy/a",
        p2_policy="policy/c",
        p1_character="Wizard",
        p2_character="Robot",
        winner="p2",
        total_turns=61,
        fallback_count=2,
        p1_prompt_tokens=150,
        p1_completion_tokens=45,
        p1_reasoning_tokens=9,
        p1_cost=0.2,
        p2_prompt_tokens=160,
        p2_completion_tokens=60,
        p2_reasoning_tokens=10,
        p2_cost=0.3,
        p1_hp=0,
        p2_hp=120,
    )

    _write_game_log(
        tournament_dir,
        series_id="SF-1",
        game_index=1,
        run_name="20260323T100000Z_match-sf1",
    )
    _write_game_log(
        tournament_dir,
        series_id="SF-2",
        game_index=1,
        run_name="20260323T101000Z_match-sf2",
    )
    _write_game_log(
        tournament_dir,
        series_id="F-1",
        game_index=1,
        run_name="20260323T102000Z_match-f1",
    )

    return tournament_dir


class TestGameAnalysis:
    def test_parse_run_dir_from_log(self, tmp_path: Path) -> None:
        tournament_dir = _build_synthetic_tournament(tmp_path)
        log_path = tournament_dir / "SF-1_game1.log"

        run_dir = parse_run_dir_from_log(log_path, repo_root=tmp_path)

        assert run_dir == tmp_path / "runs" / "20260323T100000Z_match-sf1"

    def test_analyze_game_log(self, tmp_path: Path) -> None:
        tournament_dir = _build_synthetic_tournament(tmp_path)
        log_path = tournament_dir / "F-1_game1.log"

        game = analyze_game_log(
            log_path,
            series_id="F-1",
            game_index=1,
            round_name="Final",
            repo_root=tmp_path,
        )

        assert game.winner_policy == "policy/c"
        assert game.loser_policy == "policy/a"
        assert game.total_turns == 61
        assert game.fallback_count == 2
        assert game.p1.character == "Wizard"
        assert game.p2.character == "Robot"
        assert game.p1.last_observed_hp == 0
        assert game.p2.last_observed_hp == 120
        assert game.p2.cost_usd == 0.3
        assert game.p2.cost_source == "provider_usage"

    def test_analyze_game_log_estimates_anthropic_cost(self, tmp_path: Path) -> None:
        tournament_dir = tmp_path / "tournaments" / "anthropic_bracket"
        tournament_dir.mkdir(parents=True, exist_ok=True)

        run_dir = _write_game_artifacts(
            tmp_path,
            run_name="20260323T110000Z_match-anthropic",
            match_id="match-anthropic",
            p1_policy="anthropic/claude-opus",
            p2_policy="anthropic/claude-sonnet",
            p1_character="Wizard",
            p2_character="Cowboy",
            winner="p1",
            total_turns=30,
            fallback_count=0,
            p1_prompt_tokens=1_000_000,
            p1_completion_tokens=100_000,
            p1_reasoning_tokens=0,
            p1_cost=0.0,
            p2_prompt_tokens=500_000,
            p2_completion_tokens=50_000,
            p2_reasoning_tokens=0,
            p2_cost=0.0,
            p1_hp=200,
            p2_hp=0,
            p1_provider="anthropic",
            p1_model="claude-opus-4-6",
            p2_provider="anthropic",
            p2_model="claude-sonnet-4-6",
        )
        (run_dir / "prompts.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "player_id": "p1",
                            "provider_response": {
                                "attempts": [
                                    {
                                        "usage": {
                                            "input_tokens": 1_000_000,
                                            "output_tokens": 100_000,
                                        }
                                    }
                                ]
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "player_id": "p2",
                            "provider_response": {
                                "attempts": [
                                    {
                                        "usage": {
                                            "input_tokens": 500_000,
                                            "output_tokens": 50_000,
                                        }
                                    }
                                ]
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        log_path = _write_game_log(
            tournament_dir,
            series_id="F-1",
            game_index=1,
            run_name="20260323T110000Z_match-anthropic",
        )

        game = analyze_game_log(
            log_path,
            series_id="F-1",
            game_index=1,
            round_name="Final",
            repo_root=tmp_path,
        )

        assert game.p1.provider == "anthropic"
        assert game.p1.model == "claude-opus-4-6"
        assert game.p1.cost_source == "estimated_anthropic_public_pricing"
        assert game.p1.cost_usd == 7.5
        assert game.p2.cost_source == "estimated_anthropic_public_pricing"
        assert game.p2.cost_usd == 2.25


class TestTournamentAnalysis:
    def test_analyze_tournament(self, tmp_path: Path) -> None:
        tournament_dir = _build_synthetic_tournament(tmp_path)

        analysis = analyze_tournament(tournament_dir, repo_root=tmp_path)

        assert analysis.total_series == 3
        assert analysis.total_games == 3
        assert analysis.champion is not None
        assert analysis.champion.policy_id == "policy/c"

        final_series = next(series for series in analysis.series if series.series_id == "F-1")
        assert final_series.winner is not None
        assert final_series.winner.policy_id == "policy/c"
        assert len(final_series.games) == 1

        policy_c = next(item for item in analysis.policy_summaries if item.policy_id == "policy/c")
        assert policy_c.champion is True
        assert policy_c.series_wins == 2
        assert policy_c.game_wins == 2
        assert policy_c.total_cost_usd == 0.54
        assert policy_c.character_usage["Robot"]["wins"] == 2

        policy_a = next(item for item in analysis.policy_summaries if item.policy_id == "policy/a")
        assert policy_a.eliminated_in_round == "Final"
        assert policy_a.game_losses == 1
        assert policy_a.character_usage["Wizard"]["games"] == 2

    def test_summarize_tournament(self, tmp_path: Path) -> None:
        tournament_dir = _build_synthetic_tournament(tmp_path)
        analysis = analyze_tournament(tournament_dir, repo_root=tmp_path)

        summary = summarize_tournament(analysis)

        assert summary.champion is not None
        assert summary.champion.policy_id == "policy/c"
        assert summary.overview.total_games == 3
        assert summary.overview.total_game_turns == 158
        assert summary.overview.total_model_turns == 316
        assert summary.overview.average_game_turns_per_game == 158 / 3
        assert summary.overview.average_cost_usd_per_model_turn == 1.22 / 316
        assert summary.overview.average_output_tokens_per_model_turn == 245 / 316

        policy_c = next(item for item in summary.model_summaries if item.policy_id == "policy/c")
        assert policy_c.model == "policy/c"
        assert policy_c.total_games == 2
        assert policy_c.average_turns_per_game == 58.0
        assert policy_c.average_cost_usd_per_turn == 0.54 / 116
        assert policy_c.average_output_tokens_per_turn == 110 / 116
        assert policy_c.game_win_rate == 1.0
        assert policy_c.average_remaining_hp_on_wins == 105.0
        assert policy_c.average_remaining_hp_on_losses is None
        assert policy_c.characters[0].character == "Robot"
        assert policy_c.characters[0].pick_rate == 1.0
        assert policy_c.characters[0].win_rate == 1.0

        wizard = next(item for item in summary.character_summaries if item.character == "Wizard")
        assert wizard.games == 2
        assert wizard.wins == 1
        assert wizard.losses == 1
        assert wizard.pick_rate == 2 / 6
        assert wizard.win_rate == 0.5

    def test_write_tournament_analysis(self, tmp_path: Path) -> None:
        tournament_dir = _build_synthetic_tournament(tmp_path)
        analysis = analyze_tournament(tournament_dir, repo_root=tmp_path)

        output_path = write_tournament_analysis(
            analysis,
            output_root=tmp_path / "analysis-outputs",
        )

        assert output_path == (
            tmp_path / "analysis-outputs" / "synthetic_bracket" / "tournament-analysis.json"
        )
        written = json.loads(output_path.read_text(encoding="utf-8"))
        assert written["champion"]["policy_id"] == "policy/c"
        assert written["total_games"] == 3

    def test_write_tournament_summary(self, tmp_path: Path) -> None:
        tournament_dir = _build_synthetic_tournament(tmp_path)
        analysis = analyze_tournament(tournament_dir, repo_root=tmp_path)
        summary = summarize_tournament(analysis)

        output_path = write_tournament_summary(
            summary,
            output_root=tmp_path / "analysis-outputs",
        )

        assert output_path == (
            tmp_path / "analysis-outputs" / "synthetic_bracket" / "tournament-summary.json"
        )
        written = json.loads(output_path.read_text(encoding="utf-8"))
        assert written["champion"]["policy_id"] == "policy/c"
        assert written["overview"]["total_game_turns"] == 158
        assert written["model_summaries"][2]["policy_id"] == "policy/c"

    def test_render_and_write_tournament_summary_report(self, tmp_path: Path) -> None:
        tournament_dir = _build_synthetic_tournament(tmp_path)
        analysis = analyze_tournament(tournament_dir, repo_root=tmp_path)
        summary = summarize_tournament(analysis)

        html = render_tournament_summary_html(summary)
        assert "Times New Roman" in html
        assert "Tournament Summary Report" in html
        assert "policy/c" in html
        assert "Character Patterns" in html

        output_path = write_tournament_summary_report(
            summary,
            output_root=tmp_path / "analysis-outputs",
        )
        assert output_path == (
            tmp_path / "analysis-outputs" / "synthetic_bracket" / "tournament-summary.html"
        )
        written = output_path.read_text(encoding="utf-8")
        assert "<html" in written
        assert "model turn" in written

    def test_build_and_write_bracket_frames(self, tmp_path: Path) -> None:
        tournament_dir = _build_synthetic_tournament(tmp_path)
        analysis = analyze_tournament(tournament_dir, repo_root=tmp_path)

        frames = build_bracket_frames(analysis)
        assert len(frames) == 4
        assert frames[0].revealed_series_id is None
        assert frames[0].label == "Initial bracket"
        assert "TBD" in frames[0].svg
        assert "G1" not in frames[0].svg

        final_frame = frames[-1]
        assert final_frame.revealed_series_id == "F-1"
        assert "G1" in final_frame.svg
        assert "policy/c Robot 120" in final_frame.svg
        assert "policy/a Wizard 0" in final_frame.svg

        manifest_path = write_tournament_bracket_frames(
            analysis,
            output_root=tmp_path / "analysis-outputs",
        )
        assert manifest_path == (
            tmp_path / "analysis-outputs" / "synthetic_bracket" / "bracket-frames" / "index.json"
        )

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["frame_count"] == 4
        assert manifest["frames"][0]["svg_filename"] == "00-initial.svg"
        assert manifest["frames"][0]["png_filename"] == "00-initial.png"
        assert manifest["frames"][-1]["svg_filename"] == "03-after-f-1.svg"
        assert manifest["frames"][-1]["png_filename"] == "03-after-f-1.png"
        assert (
            tmp_path
            / "analysis-outputs"
            / "synthetic_bracket"
            / "bracket-frames"
            / "03-after-f-1.svg"
        ).is_file()
        assert (
            tmp_path
            / "analysis-outputs"
            / "synthetic_bracket"
            / "bracket-frames"
            / "03-after-f-1.png"
        ).is_file()
