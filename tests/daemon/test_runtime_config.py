from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from yomi_daemon.config import (
    ConfigError,
    load_runtime_config,
    parse_runtime_config_document,
)
from yomi_daemon.manifest import build_match_manifest
from yomi_daemon.match import MatchMetadata
from yomi_daemon.protocol import CharacterSelectionMode, FallbackMode, TimeoutProfile


def test_load_runtime_config_defaults_and_env_credentials() -> None:
    config = parse_runtime_config_document(
        {
            "version": "v1",
            "policy_mapping": {
                "p1": "provider/openai-main",
                "p2": "baseline/random",
            },
            "policies": {
                "provider/openai-main": {
                    "provider": "openai",
                    "model": "gpt-5-mini",
                    "prompt_version": "strategic_v1",
                    "credential_env_var": "OPENAI_API_KEY",
                    "temperature": 0.1,
                },
                "baseline/random": {
                    "provider": "baseline",
                    "prompt_version": "none",
                },
            },
        },
        env={"OPENAI_API_KEY": "test-key"},
    )

    assert config.transport.host == "127.0.0.1"
    assert config.transport.port == 8765
    assert config.timeout_profile is TimeoutProfile.STRICT_LOCAL
    assert config.decision_timeout_ms == 2500
    assert config.fallback_mode is FallbackMode.SAFE_CONTINUE
    assert config.character_selection.mode is CharacterSelectionMode.MIRROR
    assert (
        config.policies["provider/openai-main"].credential.env_var == "OPENAI_API_KEY"
    )
    assert config.policies["provider/openai-main"].credential.value == "test-key"
    assert config.policies["provider/openai-main"].credential.is_configured is True


def test_load_runtime_config_from_default_file() -> None:
    config = load_runtime_config()

    assert config.policy_mapping.p1 == "baseline/random"
    assert config.policy_mapping.p2 == "baseline/random"
    assert config.effective_stage_id == "training_room"
    assert "baseline/block_always" in config.policies
    assert "baseline/scripted_safe" in config.policies


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (
            {
                "version": "v1",
                "timeout_profile": "arcade",
                "policy_mapping": {"p1": "baseline/random", "p2": "baseline/random"},
                "policies": {"baseline/random": {"provider": "baseline"}},
            },
            "runtime config validation failed at timeout_profile",
        ),
        (
            {
                "version": "v1",
                "policies": {"baseline/random": {"provider": "baseline"}},
            },
            "runtime_config.policy_mapping must be an object",
        ),
        (
            {
                "version": "v1",
                "policy_mapping": {"p1": "baseline/random", "p2": "baseline/random"},
                "policies": {"baseline/random": {"provider": "baseline"}},
                "character_selection": {"mode": "assigned"},
            },
            "runtime_config.character_selection.assignments must provide non-empty p1 and p2 values",
        ),
    ],
)
def test_invalid_runtime_config_values_fail_fast(
    document: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_runtime_config_document(document)


def test_manifest_generation_pins_required_metadata_fields() -> None:
    config = parse_runtime_config_document(
        {
            "version": "v1",
            "trace_seed": 77,
            "policy_mapping": {
                "p1": "provider/openai-main",
                "p2": "baseline/random",
            },
            "policies": {
                "provider/openai-main": {
                    "provider": "openai",
                    "model": "gpt-5-mini",
                    "prompt_version": "strategic_v1",
                    "credential_env_var": "OPENAI_API_KEY",
                },
                "baseline/random": {
                    "provider": "baseline",
                    "prompt_version": "none",
                },
            },
            "character_selection": {
                "mode": "assigned",
                "assignments": {"p1": "Cowboy", "p2": "Ninja"},
            },
        },
        env={"OPENAI_API_KEY": "test-key"},
    )

    manifest = build_match_manifest(
        match_id="match-004",
        runtime_config=config,
        metadata=MatchMetadata(
            game_version="1.9.11",
            mod_version="0.2.0",
            schema_version="v1",
            match_id="match-004",
        ),
        created_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
    )

    serialized = manifest.to_dict()

    assert serialized["match_id"] == "match-004"
    assert serialized["created_at"] == "2026-03-12T12:00:00Z"
    assert serialized["trace_seed"] == 77
    assert serialized["protocol_version"] == "v1"
    assert serialized["schema_version"] == "v1"
    assert serialized["daemon_version"] == "0.0.1"
    assert serialized["prompt_version"] is None
    assert serialized["policy_mapping"] == {
        "p1": "provider/openai-main",
        "p2": "baseline/random",
    }
    policies = cast(dict[str, object], serialized["policies"])
    provider_policy = cast(dict[str, object], policies["provider/openai-main"])
    config_snapshot = cast(dict[str, object], serialized["config_snapshot"])

    assert provider_policy == {
        "provider": "openai",
        "model": "gpt-5-mini",
        "prompt_version": "strategic_v1",
        "credential_env_var": "OPENAI_API_KEY",
        "credential_configured": True,
    }
    assert config_snapshot["decision_timeout_ms"] == 2500
    assert config_snapshot["stage_id"] == "training_room"
