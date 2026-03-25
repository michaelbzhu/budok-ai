"""Game-level analysis helpers for tournament artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from yomi_daemon.analysis.pricing import estimate_cost_from_public_pricing
from yomi_daemon.protocol import ProtocolModel
from yomi_daemon.validation import REPO_ROOT

_RUN_DIR_PATTERN = re.compile(r"Artifacts:\s+(runs/[^/\s]+/)")


@dataclass(frozen=True, slots=True)
class GameParticipantAnalysis(ProtocolModel):
    """Per-player summary for a completed game artifact."""

    player_slot: str
    policy_id: str
    provider: str | None
    model: str | None
    character: str | None
    won: bool | None
    last_observed_hp: int | None
    prompt_count: int
    tokens_in: int
    tokens_out: int
    reasoning_tokens: int
    cost_usd: float
    cost_source: str


@dataclass(frozen=True, slots=True)
class GameAnalysis(ProtocolModel):
    """Joined analysis row for one bracket game."""

    series_id: str
    game_index: int
    round_name: str | None
    log_path: str
    run_dir: str
    match_id: str
    status: str
    started_at: str | None
    completed_at: str | None
    winner_slot: str | None
    winner_policy: str | None
    loser_policy: str | None
    end_reason: str | None
    total_turns: int | None
    fallback_count: int
    error_count: int
    replay_path: str | None
    p1: GameParticipantAnalysis
    p2: GameParticipantAnalysis


@dataclass(slots=True)
class _PromptUsageAccumulator:
    prompt_count: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    cost_source: str = "missing"


@dataclass(frozen=True, slots=True)
class _PolicyMetadata:
    provider: str | None = None
    model: str | None = None


def parse_run_dir_from_log(log_path: Path, *, repo_root: Path | None = None) -> Path:
    """Extract the run artifact directory from a tournament game log."""

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    match = _RUN_DIR_PATTERN.search(log_text)
    if match is None:
        raise ValueError(f"Could not find run artifacts reference in {log_path}")

    root = repo_root or REPO_ROOT
    return root / match.group(1).rstrip("/")


def analyze_game_log(
    log_path: Path,
    *,
    series_id: str,
    game_index: int,
    round_name: str | None = None,
    repo_root: Path | None = None,
) -> GameAnalysis:
    """Join a tournament game log to its persisted run artifacts."""

    root = repo_root or REPO_ROOT
    run_dir = parse_run_dir_from_log(log_path, repo_root=root)

    result = _load_json_object(run_dir / "result.json")
    manifest = _load_json_object(run_dir / "manifest.json")
    metrics = _coerce_object(result.get("metrics"))
    policy_mapping = _coerce_object(manifest.get("policy_mapping"))
    policies = _coerce_object(manifest.get("policies"))

    p1_policy = _expect_string(policy_mapping.get("p1"), context="manifest.policy_mapping.p1")
    p2_policy = _expect_string(policy_mapping.get("p2"), context="manifest.policy_mapping.p2")

    characters = _load_character_assignments(run_dir)
    prompt_usage = _load_prompt_usage(run_dir)
    observed_hp = _load_last_observed_hp(run_dir)
    p1_usage = prompt_usage["p1"]
    p2_usage = prompt_usage["p2"]
    p1_metadata = _load_policy_metadata(policies, p1_policy)
    p2_metadata = _load_policy_metadata(policies, p2_policy)

    winner_slot = _expect_optional_string(result.get("winner"), context="result.winner")
    winner_policy = _policy_for_slot(winner_slot, p1=p1_policy, p2=p2_policy)
    loser_policy = _loser_policy(winner_slot, p1=p1_policy, p2=p2_policy)

    replay_path = _expect_optional_string(result.get("replay_path"), context="result.replay_path")
    if replay_path is None:
        replay_file = run_dir / "replay.mp4"
        if replay_file.is_file():
            replay_path = str(replay_file)

    return GameAnalysis(
        series_id=series_id,
        game_index=game_index,
        round_name=round_name,
        log_path=str(log_path.resolve()),
        run_dir=str(run_dir.resolve()),
        match_id=_expect_string(result.get("match_id"), context="result.match_id"),
        status=_expect_string(result.get("status"), context="result.status"),
        started_at=_expect_optional_string(result.get("started_at"), context="result.started_at"),
        completed_at=_expect_optional_string(
            result.get("completed_at"), context="result.completed_at"
        ),
        winner_slot=winner_slot,
        winner_policy=winner_policy,
        loser_policy=loser_policy,
        end_reason=_expect_optional_string(result.get("end_reason"), context="result.end_reason"),
        total_turns=_expect_optional_int(result.get("total_turns"), context="result.total_turns"),
        fallback_count=_expect_int(
            metrics.get("fallback_count", 0), context="metrics.fallback_count"
        ),
        error_count=_expect_int(metrics.get("error_count", 0), context="metrics.error_count"),
        replay_path=replay_path,
        p1=GameParticipantAnalysis(
            player_slot="p1",
            policy_id=p1_policy,
            provider=p1_metadata.provider,
            model=p1_metadata.model,
            character=characters.get("p1"),
            won=winner_slot == "p1" if winner_slot is not None else None,
            last_observed_hp=observed_hp.get("p1"),
            prompt_count=p1_usage.prompt_count,
            tokens_in=p1_usage.tokens_in,
            tokens_out=p1_usage.tokens_out,
            reasoning_tokens=p1_usage.reasoning_tokens,
            cost_usd=p1_usage.cost_usd,
            cost_source=p1_usage.cost_source,
        ),
        p2=GameParticipantAnalysis(
            player_slot="p2",
            policy_id=p2_policy,
            provider=p2_metadata.provider,
            model=p2_metadata.model,
            character=characters.get("p2"),
            won=winner_slot == "p2" if winner_slot is not None else None,
            last_observed_hp=observed_hp.get("p2"),
            prompt_count=p2_usage.prompt_count,
            tokens_in=p2_usage.tokens_in,
            tokens_out=p2_usage.tokens_out,
            reasoning_tokens=p2_usage.reasoning_tokens,
            cost_usd=p2_usage.cost_usd,
            cost_source=p2_usage.cost_source,
        ),
    )


def _load_character_assignments(run_dir: Path) -> dict[str, str]:
    path = run_dir / "character_selection.json"
    if not path.is_file():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return {}

    characters: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        player_slot = entry.get("player_slot")
        character = entry.get("character")
        if isinstance(player_slot, str) and isinstance(character, str):
            characters[player_slot] = character
    return characters


def _load_prompt_usage(run_dir: Path) -> dict[str, _PromptUsageAccumulator]:
    base: dict[str, _PromptUsageAccumulator] = {
        "p1": _PromptUsageAccumulator(),
        "p2": _PromptUsageAccumulator(),
    }

    prompts_path = run_dir / "prompts.jsonl"
    if not prompts_path.is_file():
        return base

    for line in prompts_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            continue

        player_id = record.get("player_id")
        if player_id not in ("p1", "p2"):
            continue

        usage_bucket = base[player_id]
        usage_bucket.prompt_count += 1

        provider_response = _coerce_object(record.get("provider_response"))
        attempts = provider_response.get("attempts")
        if not isinstance(attempts, list):
            continue

        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            usage = _coerce_object(attempt.get("usage"))
            completion_details = _coerce_object(usage.get("completion_tokens_details"))
            prompt_tokens = _expect_int(
                usage.get("prompt_tokens", usage.get("input_tokens", 0)),
                context="usage.prompt_tokens",
            )
            completion_tokens = _expect_int(
                usage.get("completion_tokens", usage.get("output_tokens", 0)),
                context="usage.completion_tokens",
            )
            usage_bucket.tokens_in += _expect_int(
                prompt_tokens,
                context="usage.prompt_tokens",
            )
            usage_bucket.tokens_out += _expect_int(
                completion_tokens, context="usage.completion_tokens"
            )
            usage_bucket.reasoning_tokens += _expect_int(
                completion_details.get("reasoning_tokens", 0),
                context="usage.completion_tokens_details.reasoning_tokens",
            )
            usage_bucket.cost_usd += _expect_float(usage.get("cost", 0), context="usage.cost")

    manifest = _load_json_object(run_dir / "manifest.json")
    policies = _coerce_object(manifest.get("policies"))
    policy_mapping = _coerce_object(manifest.get("policy_mapping"))
    for player_slot in ("p1", "p2"):
        usage_bucket = base[player_slot]
        if usage_bucket.cost_usd > 0:
            usage_bucket.cost_source = "provider_usage"
            continue

        raw_policy_id = policy_mapping.get(player_slot)
        policy_id = raw_policy_id if isinstance(raw_policy_id, str) else ""
        metadata = _load_policy_metadata(policies, policy_id)
        estimated_cost, cost_source = estimate_cost_from_public_pricing(
            provider=metadata.provider,
            model=metadata.model,
            tokens_in=usage_bucket.tokens_in,
            tokens_out=usage_bucket.tokens_out,
        )
        if estimated_cost is not None and cost_source is not None:
            usage_bucket.cost_usd = estimated_cost
            usage_bucket.cost_source = cost_source

    return base


def _load_last_observed_hp(run_dir: Path) -> dict[str, int | None]:
    decisions_path = run_dir / "decisions.jsonl"
    latest: dict[str, int | None] = {"p1": None, "p2": None}
    if not decisions_path.is_file():
        return latest

    for line in decisions_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            continue
        request_payload = _coerce_object(record.get("request_payload"))
        observation = _coerce_object(request_payload.get("observation"))
        fighters = observation.get("fighters")
        if not isinstance(fighters, list):
            continue
        for fighter in fighters:
            if not isinstance(fighter, dict):
                continue
            fighter_id = fighter.get("id")
            hp = fighter.get("hp")
            if fighter_id in ("p1", "p2") and isinstance(hp, int):
                latest[fighter_id] = hp

    return latest


def _load_json_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{path} did not contain a JSON object")
    return raw


def _coerce_object(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    return {}


def _load_policy_metadata(policies: dict[str, Any], policy_id: str) -> _PolicyMetadata:
    policy_payload = _coerce_object(policies.get(policy_id))
    provider = policy_payload.get("provider")
    model = policy_payload.get("model")
    return _PolicyMetadata(
        provider=provider if isinstance(provider, str) else None,
        model=model if isinstance(model, str) else None,
    )


def _expect_string(raw: object, *, context: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{context} must be a non-empty string")
    return raw


def _expect_optional_string(raw: object, *, context: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"{context} must be a string or null")
    return raw


def _expect_int(raw: object, *, context: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{context} must be an integer")
    return raw


def _expect_optional_int(raw: object, *, context: str) -> int | None:
    if raw is None:
        return None
    return _expect_int(raw, context=context)


def _expect_float(raw: object, *, context: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise ValueError(f"{context} must be numeric")
    return float(raw)


def _policy_for_slot(winner_slot: str | None, *, p1: str, p2: str) -> str | None:
    if winner_slot == "p1":
        return p1
    if winner_slot == "p2":
        return p2
    return None


def _loser_policy(winner_slot: str | None, *, p1: str, p2: str) -> str | None:
    if winner_slot == "p1":
        return p2
    if winner_slot == "p2":
        return p1
    return None
