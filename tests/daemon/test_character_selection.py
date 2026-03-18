"""Tests for LLM character selection feature."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from yomi_daemon.character_selection import (
    _parse_character_choice,
    _random_character,
    resolve_character_assignments,
)
from yomi_daemon.config import (
    DaemonRuntimeConfig,
    PolicyConfig,
    ProviderCredential,
    TournamentDefaults,
    TransportConfig,
    parse_runtime_config_document,
)
from yomi_daemon.prompt import (
    VALID_CHARACTERS,
    MatchHistoryEntry,
    character_select_output_json_schema,
    render_character_select_prompt,
)
from yomi_daemon.protocol import (
    CharacterSelectionConfig,
    CharacterSelectionMode,
    FallbackMode,
    LoggingConfig,
    PlayerPolicyMapping,
)
from yomi_daemon.replay_capture import ReplayCaptureConfig


# --- Config validation ---


def test_llm_choice_mode_accepted_in_config():
    """Config with llm_choice mode should parse successfully."""
    document = {
        "version": "v1",
        "policy_mapping": {"p1": "baseline/random", "p2": "baseline/random"},
        "policies": {"baseline/random": {"provider": "baseline"}},
        "character_selection": {"mode": "llm_choice"},
    }
    config = parse_runtime_config_document(document)
    assert config.character_selection.mode is CharacterSelectionMode.LLM_CHOICE


def test_llm_choice_in_schema_enum():
    """The JSON schema should accept llm_choice as a valid mode."""
    from yomi_daemon.validation import load_schema

    schema = load_schema("daemon-config.v1.json")
    char_enum = schema["properties"]["character_selection"]["properties"]["mode"][
        "enum"
    ]
    assert "llm_choice" in char_enum


# --- Prompt rendering ---


def test_render_character_select_prompt_basic():
    """Character select prompt renders with all characters."""
    prompt = render_character_select_prompt(player_id="p1")
    assert "Ninja" in prompt
    assert "Cowboy" in prompt
    assert "Wizard" in prompt
    assert "Robot" in prompt
    assert "Mutant" in prompt
    assert "reasoning" in prompt
    assert "character" in prompt


def test_render_character_select_prompt_with_history():
    """Character select prompt includes match history when provided."""
    history = [
        MatchHistoryEntry(
            your_character="Ninja",
            opponent_character="Cowboy",
            result="win",
            your_final_hp=800,
            opponent_final_hp=0,
        ),
        MatchHistoryEntry(
            your_character="Ninja",
            opponent_character="Robot",
            result="loss",
            your_final_hp=0,
            opponent_final_hp=420,
        ),
    ]
    prompt = render_character_select_prompt(
        player_id="p1",
        opponent_policy_id="anthropic/claude-opus",
        match_history=history,
    )
    assert "Tournament Match History" in prompt
    assert "anthropic/claude-opus" in prompt
    assert "Match 1" in prompt
    assert "Match 2" in prompt
    assert "You won" in prompt
    assert "You lost" in prompt
    assert "800 HP" in prompt
    assert "420 HP" in prompt


def test_render_character_select_prompt_no_history_section_when_empty():
    """No history section when match_history is None or empty."""
    prompt = render_character_select_prompt(player_id="p1")
    assert "Tournament Match History" not in prompt

    prompt2 = render_character_select_prompt(player_id="p1", match_history=[])
    assert "Tournament Match History" not in prompt2


# --- Output schema ---


def test_character_select_schema_structure():
    """Character select schema has required fields."""
    schema = character_select_output_json_schema()
    assert schema["required"] == ["reasoning", "character"]
    assert schema["properties"]["character"]["enum"] == list(VALID_CHARACTERS)


# --- Parse character choice ---


def test_parse_valid_tool_output():
    """Parse a valid dict response."""
    result = _parse_character_choice(
        {"reasoning": "strong zoning", "character": "Wizard"}
    )
    assert result == "Wizard"


def test_parse_json_string():
    """Parse a JSON string response."""
    result = _parse_character_choice('{"reasoning": "fast", "character": "Ninja"}')
    assert result == "Ninja"


def test_parse_json_in_text():
    """Parse JSON embedded in text."""
    result = _parse_character_choice(
        'Here is my choice: {"reasoning": "grabs", "character": "Robot"} done.'
    )
    assert result == "Robot"


def test_parse_invalid_character_raises():
    """Invalid character name should raise ValueError."""
    with pytest.raises(ValueError, match="Could not parse"):
        _parse_character_choice({"reasoning": "ok", "character": "InvalidName"})


def test_parse_missing_character_raises():
    """Missing character field should raise ValueError."""
    with pytest.raises(ValueError, match="Could not parse"):
        _parse_character_choice({"reasoning": "ok"})


def test_parse_non_json_string_raises():
    """Non-JSON string should raise ValueError."""
    with pytest.raises(ValueError, match="Could not parse"):
        _parse_character_choice("I choose Ninja")


# --- Random character ---


def test_random_character_deterministic():
    """Random character is deterministic given same seed and salt."""
    c1 = _random_character(trace_seed=42, salt="test")
    c2 = _random_character(trace_seed=42, salt="test")
    assert c1 == c2
    assert c1 in VALID_CHARACTERS


def test_random_character_varies_with_salt():
    """Different salts should eventually produce different characters."""
    chars = {_random_character(trace_seed=42, salt=f"salt_{i}") for i in range(50)}
    # With 50 attempts and 5 characters, we should get more than 1
    assert len(chars) > 1


# --- resolve_character_assignments ---


def _make_config(
    *,
    p1_provider: str = "baseline",
    p2_provider: str = "baseline",
    p1_model: str | None = None,
    p2_model: str | None = None,
) -> DaemonRuntimeConfig:
    """Build a minimal DaemonRuntimeConfig for testing."""
    return DaemonRuntimeConfig(
        version="v1",
        transport=TransportConfig(host="127.0.0.1", port=8765),
        decision_timeout_ms=10000,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        logging=LoggingConfig(events=True, prompts=False, raw_provider_payloads=False),
        policy_mapping=PlayerPolicyMapping(p1="policy_p1", p2="policy_p2"),
        policies={
            "policy_p1": PolicyConfig(
                provider=p1_provider,
                model=p1_model,
                credential=ProviderCredential(env_var="TEST_KEY", value="test-key"),
            ),
            "policy_p2": PolicyConfig(
                provider=p2_provider,
                model=p2_model,
                credential=ProviderCredential(env_var="TEST_KEY", value="test-key"),
            ),
        },
        character_selection=CharacterSelectionConfig(
            mode=CharacterSelectionMode.LLM_CHOICE,
        ),
        tournament=TournamentDefaults(
            format="round_robin",
            mirror_matches_first=True,
            side_swap=True,
            games_per_pair=10,
        ),
        trace_seed=42,
        replay_capture=ReplayCaptureConfig(enabled=False),
    )


def test_baseline_policies_get_random_characters():
    """Baseline policies should get seeded random characters."""
    config = _make_config()
    assignments = asyncio.run(resolve_character_assignments(config))
    assert assignments.p1 in VALID_CHARACTERS
    assert assignments.p2 in VALID_CHARACTERS


def test_baseline_assignments_are_deterministic():
    """Same seed should produce same baseline character assignments."""
    config = _make_config()
    a1 = asyncio.run(resolve_character_assignments(config))
    a2 = asyncio.run(resolve_character_assignments(config))
    assert a1.p1 == a2.p1
    assert a1.p2 == a2.p2


def test_provider_policy_calls_llm():
    """Provider-backed policy should call the LLM for character selection."""
    config = _make_config(
        p1_provider="anthropic",
        p1_model="claude-sonnet-4-6",
    )

    mock_response = {"reasoning": "zoning is strong", "character": "Wizard"}

    with patch(
        "yomi_daemon.character_selection._call_anthropic",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        assignments = asyncio.run(resolve_character_assignments(config))

    assert assignments.p1 == "Wizard"
    assert assignments.p2 in VALID_CHARACTERS  # p2 is baseline, gets random


def test_provider_failure_falls_back_to_random():
    """If LLM call fails, should fall back to random character."""
    config = _make_config(
        p1_provider="anthropic",
        p1_model="claude-sonnet-4-6",
    )

    with patch(
        "yomi_daemon.character_selection._call_anthropic",
        new_callable=AsyncMock,
        side_effect=RuntimeError("API error"),
    ):
        assignments = asyncio.run(resolve_character_assignments(config))

    assert assignments.p1 in VALID_CHARACTERS  # fell back to random
    assert assignments.p2 in VALID_CHARACTERS
