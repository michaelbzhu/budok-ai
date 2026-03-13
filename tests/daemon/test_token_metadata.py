"""Tests for token and cost metadata capture across provider adapters."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tests.daemon._decision_fixtures import build_action, build_request
from yomi_daemon.adapters.anthropic import AnthropicTransport, build_anthropic_adapter
from yomi_daemon.adapters.openai import OpenAITransport, build_openai_adapter
from yomi_daemon.config import (
    PolicyConfig,
    ProviderCredential,
    parse_runtime_config_document,
)
from yomi_daemon.manifest import build_match_manifest
from yomi_daemon.match import MatchMetadata
from yomi_daemon.orchestrator import resolve_adapter_decision
from yomi_daemon.protocol import (
    CURRENT_SCHEMA_VERSION,
    FallbackMode,
    JsonObject,
    LoggingConfig,
)
from yomi_daemon.storage.writer import MatchArtifactWriter


class _StubAnthropicTransport(AnthropicTransport):
    def __init__(self, *, responses: list[JsonObject]) -> None:
        self._responses = list(responses)

    async def create_message(
        self, *, api_key: str, payload: JsonObject, timeout_ms: int
    ) -> JsonObject:
        return self._responses.pop(0)


class _StubOpenAITransport(OpenAITransport):
    def __init__(self, *, responses: list[JsonObject]) -> None:
        self._responses = list(responses)

    async def create_response(
        self, *, api_key: str, payload: JsonObject, timeout_ms: int
    ) -> JsonObject:
        return self._responses.pop(0)


def _anthropic_response(
    action: str, *, input_tokens: int, output_tokens: int
) -> JsonObject:
    return {
        "id": "msg_tok_test",
        "stop_reason": "tool_use",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "content": [
            {
                "type": "tool_use",
                "name": "submit_action_decision",
                "id": "toolu_tok",
                "input": {"action": action},
            }
        ],
    }


def _openai_response(text: str, *, input_tokens: int, output_tokens: int) -> JsonObject:
    return {
        "id": "resp_tok_test",
        "status": "completed",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def _manifest(match_id: str) -> Any:
    runtime_config = parse_runtime_config_document(
        {
            "version": "v1",
            "trace_seed": 7,
            "fallback_mode": "safe_continue",
            "logging": {
                "events": True,
                "prompts": True,
                "raw_provider_payloads": True,
            },
            "policy_mapping": {"p1": "test-policy", "p2": "test-policy"},
            "policies": {
                "test-policy": {"provider": "baseline"},
            },
        },
    )
    return build_match_manifest(
        match_id=match_id,
        runtime_config=runtime_config,
        metadata=MatchMetadata(
            game_version="1.9.20-steam",
            mod_version="0.2.0",
            schema_version=CURRENT_SCHEMA_VERSION,
            match_id=match_id,
        ),
        created_at=datetime(2026, 3, 13, 12, 0, tzinfo=UTC),
    )


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_anthropic_token_metadata_captured_in_decision() -> None:
    """Anthropic adapter populates tokens_in and tokens_out from usage."""
    transport = _StubAnthropicTransport(
        responses=[_anthropic_response("guard", input_tokens=200, output_tokens=30)]
    )
    adapter = build_anthropic_adapter(
        "test/anthropic",
        PolicyConfig(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            prompt_version="minimal_v1",
            credential=ProviderCredential(
                env_var="ANTHROPIC_API_KEY", value="test-key"
            ),
        ),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        transport=transport,
    )

    request = build_request((build_action("guard"),))
    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.tokens_in == 200
    assert result.decision.tokens_out == 30


def test_openai_token_metadata_captured_in_decision() -> None:
    """OpenAI adapter populates tokens_in and tokens_out from usage."""
    transport = _StubOpenAITransport(
        responses=[
            _openai_response('{"action":"guard"}', input_tokens=150, output_tokens=20)
        ]
    )
    adapter = build_openai_adapter(
        "test/openai",
        PolicyConfig(
            provider="openai",
            model="gpt-4.1-mini",
            prompt_version="minimal_v1",
            credential=ProviderCredential(env_var="OPENAI_API_KEY", value="test-key"),
        ),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        transport=transport,
    )

    request = build_request((build_action("guard"),))
    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.tokens_in == 150
    assert result.decision.tokens_out == 20


def test_token_metadata_persisted_through_artifact_writer(tmp_path: Path) -> None:
    """Token metadata flows through resolve_adapter_decision into decisions.jsonl."""
    transport = _StubOpenAITransport(
        responses=[
            _openai_response('{"action":"guard"}', input_tokens=300, output_tokens=45)
        ]
    )
    adapter = build_openai_adapter(
        "test/openai",
        PolicyConfig(
            provider="openai",
            model="gpt-4.1-mini",
            prompt_version="minimal_v1",
            credential=ProviderCredential(env_var="OPENAI_API_KEY", value="test-key"),
        ),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        transport=transport,
    )

    match_id = "match-token-persist"
    request = build_request((build_action("guard"),), match_id=match_id)
    writer = MatchArtifactWriter.create(
        match_id=match_id,
        manifest=_manifest(match_id),
        runs_root=tmp_path,
        started_at=datetime(2026, 3, 13, 12, 0, tzinfo=UTC),
    )

    asyncio.run(
        resolve_adapter_decision(
            request,
            adapter=adapter,
            artifact_writer=writer,
            logging_config=LoggingConfig(
                events=True, prompts=True, raw_provider_payloads=True
            ),
        )
    )

    decisions = _load_jsonl(writer.decisions_path)
    assert len(decisions) == 1
    decision_payload = cast(dict[str, object], decisions[0]["decision_payload"])
    assert decision_payload["tokens_in"] == 300
    assert decision_payload["tokens_out"] == 45

    # Also verify prompt trace was written with provider payloads
    prompts = _load_jsonl(writer.prompts_path)
    assert len(prompts) == 1
    assert prompts[0]["provider_request"] is not None
    assert prompts[0]["provider_response"] is not None
    provider_response = cast(dict[str, object], prompts[0]["provider_response"])
    response_attempts = cast(list[object], provider_response["attempts"])
    assert len(response_attempts) == 1
    first_response = cast(dict[str, object], response_attempts[0])
    usage = cast(dict[str, object], first_response["usage"])
    assert usage["input_tokens"] == 300
    assert usage["output_tokens"] == 45


def test_baseline_adapter_produces_no_token_metadata() -> None:
    """Baseline adapters do not produce token metadata since they never call a provider."""
    from yomi_daemon.adapters.baseline import build_baseline_adapter

    adapter = build_baseline_adapter(
        "baseline/random",
        PolicyConfig(provider="baseline"),
    )

    request = build_request((build_action("guard"),))
    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.tokens_in is None
    assert result.decision.tokens_out is None
    assert result.prompt_trace is None
