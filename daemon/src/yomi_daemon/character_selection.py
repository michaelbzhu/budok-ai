"""LLM-driven character selection for llm_choice mode."""

from __future__ import annotations

import asyncio
import json
import logging
from hashlib import sha256
from random import Random
from typing import TYPE_CHECKING, Any, cast

from yomi_daemon.prompt import (
    VALID_CHARACTERS,
    MatchHistoryEntry,
    character_select_output_json_schema,
    render_character_select_prompt,
)
from yomi_daemon.protocol import CharacterAssignments, PlayerSlot

if TYPE_CHECKING:
    from yomi_daemon.config import DaemonRuntimeConfig, PolicyConfig


logger = logging.getLogger("yomi_daemon.character_selection")

_TOOL_NAME = "submit_character_choice"


async def resolve_character_assignments(
    config: "DaemonRuntimeConfig",
    *,
    match_history: dict[str, list[MatchHistoryEntry]] | None = None,
) -> CharacterAssignments:
    """Resolve character assignments for llm_choice mode.

    For provider-backed policies, calls the LLM with the character selection prompt.
    For baseline policies, picks a random character seeded by trace_seed.
    Both players are resolved concurrently.

    Args:
        config: The daemon runtime config.
        match_history: Per-player match history keyed by player slot ("p1", "p2").
    """
    p1_policy_id = config.policy_mapping.p1
    p2_policy_id = config.policy_mapping.p2
    p1_config = config.policies[p1_policy_id]
    p2_config = config.policies[p2_policy_id]

    history = match_history or {}

    p1_coro = _resolve_for_player(
        player_slot=PlayerSlot.P1,
        policy_id=p1_policy_id,
        policy_config=p1_config,
        opponent_policy_id=p2_policy_id,
        trace_seed=config.trace_seed,
        match_history=history.get("p1"),
        timeout_ms=config.decision_timeout_ms,
    )
    p2_coro = _resolve_for_player(
        player_slot=PlayerSlot.P2,
        policy_id=p2_policy_id,
        policy_config=p2_config,
        opponent_policy_id=p1_policy_id,
        trace_seed=config.trace_seed,
        match_history=history.get("p2"),
        timeout_ms=config.decision_timeout_ms,
    )

    p1_char, p2_char = await asyncio.gather(p1_coro, p2_coro)

    logger.info(
        "LLM character selection: P1 (%s) -> %s, P2 (%s) -> %s",
        p1_policy_id,
        p1_char,
        p2_policy_id,
        p2_char,
    )

    return CharacterAssignments(p1=p1_char, p2=p2_char)


async def _resolve_for_player(
    *,
    player_slot: PlayerSlot,
    policy_id: str,
    policy_config: "PolicyConfig",
    opponent_policy_id: str,
    trace_seed: int,
    match_history: list[MatchHistoryEntry] | None,
    timeout_ms: int,
) -> str:
    """Resolve a character for a single player."""
    if policy_config.provider == "baseline":
        return _random_character(trace_seed=trace_seed, salt=f"char_select:{player_slot.value}")

    try:
        return await _llm_character_select(
            player_slot=player_slot,
            policy_id=policy_id,
            policy_config=policy_config,
            opponent_policy_id=opponent_policy_id,
            match_history=match_history,
            timeout_ms=timeout_ms,
        )
    except Exception:
        logger.exception(
            "Character selection failed for %s (%s), falling back to random",
            player_slot.value,
            policy_id,
        )
        return _random_character(trace_seed=trace_seed, salt=f"char_select:{player_slot.value}")


def _random_character(*, trace_seed: int, salt: str) -> str:
    """Pick a random character deterministically from trace_seed + salt."""
    seed_material = f"character_select|{trace_seed}|{salt}"
    digest = sha256(seed_material.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], byteorder="big", signed=False)
    rng = Random(seed)
    return rng.choice(VALID_CHARACTERS)


async def _llm_character_select(
    *,
    player_slot: PlayerSlot,
    policy_id: str,
    policy_config: "PolicyConfig",
    opponent_policy_id: str,
    match_history: list[MatchHistoryEntry] | None,
    timeout_ms: int,
) -> str:
    """Call the LLM to choose a character."""
    prompt_text = render_character_select_prompt(
        player_id=player_slot.value,
        opponent_policy_id=opponent_policy_id,
        match_history=match_history,
    )

    provider = policy_config.provider
    if provider == "anthropic":
        raw = await _call_anthropic(
            policy_config=policy_config,
            prompt_text=prompt_text,
            timeout_ms=timeout_ms,
        )
    elif provider in ("openai", "openrouter"):
        raw = await _call_openai_compat(
            policy_config=policy_config,
            prompt_text=prompt_text,
            timeout_ms=timeout_ms,
            provider=provider,
        )
    else:
        raise ValueError(f"Unsupported provider for character selection: {provider}")

    return _parse_character_choice(raw)


def _parse_character_choice(raw: object) -> str:
    """Extract and validate the character choice from provider response."""
    if isinstance(raw, str):
        # Try to extract JSON from text
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            # Try to find JSON object in text
            text = cast(str, raw)
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    raw = json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass

    if isinstance(raw, dict):
        raw_dict = cast(dict[str, object], raw)
        character = raw_dict.get("character")
        if isinstance(character, str) and character in VALID_CHARACTERS:
            reasoning = raw_dict.get("reasoning", "")
            if reasoning:
                logger.info("Character selection reasoning: %s", reasoning)
            return character

    raise ValueError(f"Could not parse valid character from response: {raw!r}")


async def _call_anthropic(
    *,
    policy_config: "PolicyConfig",
    prompt_text: str,
    timeout_ms: int,
) -> object:
    """Make an Anthropic API call for character selection."""
    from anthropic import AsyncAnthropic

    api_key = policy_config.credential.value
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured for character selection")

    client = AsyncAnthropic(api_key=api_key)
    schema = character_select_output_json_schema()

    payload: dict[str, Any] = {
        "model": cast(str, policy_config.model),
        "max_tokens": 512,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"{prompt_text}\n\nUse the `{_TOOL_NAME}` tool for your answer.",
                    }
                ],
            }
        ],
        "tools": [
            {
                "name": _TOOL_NAME,
                "description": "Submit your character choice with reasoning.",
                "input_schema": schema,
            }
        ],
        "tool_choice": {"type": "tool", "name": _TOOL_NAME},
    }
    if policy_config.temperature is not None:
        payload["temperature"] = policy_config.temperature

    response = await client.messages.create(**payload, timeout=timeout_ms / 1000)
    dumped = response.model_dump(mode="json")

    # Extract tool_use output
    content = dumped.get("content", [])
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            if block.get("name") == _TOOL_NAME:
                return block.get("input", {})

    # Fall back to text
    text_parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(block.get("text", ""))
    if text_parts:
        return "".join(text_parts)

    raise ValueError("No usable output from Anthropic character selection response")


async def _call_openai_compat(
    *,
    policy_config: "PolicyConfig",
    prompt_text: str,
    timeout_ms: int,
    provider: str,
) -> object:
    """Make an OpenAI-compatible API call for character selection."""
    from openai import AsyncOpenAI

    api_key = policy_config.credential.value
    if not api_key:
        raise ValueError(f"API key not configured for {provider} character selection")

    base_url = "https://openrouter.ai/api/v1" if provider == "openrouter" else None
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    schema = character_select_output_json_schema()

    payload: dict[str, Any] = {
        "model": cast(str, policy_config.model),
        "max_tokens": 512,
        "input": [
            {
                "role": "user",
                "content": (
                    f"{prompt_text}\n\nUse the `{_TOOL_NAME}` tool to submit your character choice."
                ),
            }
        ],
        "tools": [
            {
                "type": "function",
                "name": _TOOL_NAME,
                "description": "Submit your character choice with reasoning.",
                "parameters": schema,
            }
        ],
        "tool_choice": {"type": "function", "name": _TOOL_NAME},
    }
    if policy_config.temperature is not None:
        payload["temperature"] = policy_config.temperature

    response = await client.responses.create(**payload, timeout=timeout_ms / 1000)
    dumped = response.model_dump(mode="json")

    # Extract function call output
    output = dumped.get("output", [])
    for item in output:
        if isinstance(item, dict) and item.get("type") == "function_call":
            if item.get("name") == _TOOL_NAME:
                arguments = item.get("arguments", "{}")
                if isinstance(arguments, str):
                    return json.loads(arguments)
                return arguments

    # Fall back to text output
    for item in output:
        if isinstance(item, dict) and item.get("type") == "message":
            content = item.get("content", [])
            for block in content:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    return block.get("text", "")

    raise ValueError(f"No usable output from {provider} character selection response")
