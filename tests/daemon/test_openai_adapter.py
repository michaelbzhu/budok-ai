from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tests.daemon._decision_fixtures import build_action, build_request
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
    FallbackMode,
    FallbackReason,
    JsonObject,
    LoggingConfig,
)
from yomi_daemon.storage.writer import MatchArtifactWriter


class StubOpenAITransport(OpenAITransport):
    def __init__(
        self,
        *,
        responses: Sequence[JsonObject] = (),
        delay_seconds: float = 0.0,
    ) -> None:
        self._responses = list(responses)
        self._delay_seconds = delay_seconds
        self.calls: list[JsonObject] = []

    async def create_response(
        self,
        *,
        api_key: str,
        payload: JsonObject,
        timeout_ms: int,
    ) -> JsonObject:
        self.calls.append(
            {
                "api_key": api_key,
                "payload": payload,
                "timeout_ms": timeout_ms,
            }
        )
        if self._delay_seconds > 0:
            await asyncio.sleep(self._delay_seconds)
        if not self._responses:
            raise AssertionError("stub transport ran out of responses")
        return self._responses.pop(0)


def _policy_config(
    *,
    credential_value: str | None = "test-key",
    options: dict[str, object] | None = None,
) -> PolicyConfig:
    return PolicyConfig(
        provider="openai",
        model="gpt-5-mini",
        prompt_version="strategic_v1",
        credential=ProviderCredential(
            env_var="OPENAI_API_KEY",
            value=credential_value,
        ),
        temperature=0.1,
        max_tokens=128,
        options=options or {},
    )


def _response_payload(
    text: str, *, input_tokens: int = 15, output_tokens: int = 4
) -> JsonObject:
    return {
        "id": "resp_test",
        "status": "completed",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                    }
                ],
            }
        ],
    }


def _manifest(match_id: str) -> Any:
    runtime_config = parse_runtime_config_document(
        {
            "version": "v1",
            "trace_seed": 7,
            "fallback_mode": "heuristic_guard",
            "logging": {
                "events": True,
                "prompts": True,
                "raw_provider_payloads": True,
            },
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
                    "prompt_version": "minimal_v1",
                },
            },
        },
        env={"OPENAI_API_KEY": "test-key"},
    )
    return build_match_manifest(
        match_id=match_id,
        runtime_config=runtime_config,
        metadata=MatchMetadata(
            game_version="1.9.11",
            mod_version="0.2.0",
            schema_version="v1",
            match_id=match_id,
        ),
        created_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
    )


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_openai_adapter_returns_schema_valid_decision_on_success() -> None:
    request = build_request(
        (
            build_action("guard", description="Safe block."),
            build_action("slash"),
        )
    )
    transport = StubOpenAITransport(
        responses=[
            _response_payload('{"action":"guard","notes":"Take space safely."}')
        ],
    )
    adapter = build_openai_adapter(
        "provider/openai-main",
        _policy_config(),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.HEURISTIC_GUARD,
        transport=transport,
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.action == "guard"
    assert result.decision.policy_id == "provider/openai-main"
    assert result.decision.tokens_in == 15
    assert result.decision.tokens_out == 4
    assert result.prompt_trace is not None
    assert result.prompt_trace.prompt_version == "strategic_v1"
    assert transport.calls[0]["api_key"] == "test-key"
    provider_request = cast(dict[str, object], transport.calls[0]["payload"])
    metadata = cast(dict[str, object], provider_request["metadata"])
    assert metadata["attempt_kind"] == "initial"


def test_openai_adapter_falls_back_on_malformed_provider_response() -> None:
    request = build_request((build_action("guard"),))
    transport = StubOpenAITransport(
        responses=[_response_payload('{"action":"teleport"}')],
    )
    adapter = build_openai_adapter(
        "provider/openai-main",
        _policy_config(),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.HEURISTIC_GUARD,
        transport=transport,
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.fallback_reason is FallbackReason.MALFORMED_OUTPUT
    assert result.decision.action == "guard"
    assert result.prompt_trace is not None


def test_openai_adapter_falls_back_on_timeout() -> None:
    request = build_request((build_action("guard"),), deadline_ms=10)
    transport = StubOpenAITransport(
        responses=[_response_payload('{"action":"guard"}')],
        delay_seconds=0.05,
    )
    adapter = build_openai_adapter(
        "provider/openai-main",
        _policy_config(),
        decision_timeout_ms=10,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        transport=transport,
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.fallback_reason is FallbackReason.TIMEOUT
    assert result.decision.action == "guard"


def test_openai_adapter_falls_back_when_credentials_are_missing() -> None:
    request = build_request((build_action("guard"),))
    transport = StubOpenAITransport(
        responses=[_response_payload('{"action":"guard"}')],
    )
    adapter = build_openai_adapter(
        "provider/openai-main",
        _policy_config(credential_value=None),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        transport=transport,
    )

    result = asyncio.run(adapter.decide_with_trace(request))

    assert result.decision.fallback_reason is FallbackReason.MALFORMED_OUTPUT
    assert transport.calls == []


def test_resolve_adapter_decision_persists_prompt_and_provider_metadata(
    tmp_path: Path,
) -> None:
    request = build_request((build_action("guard"),), match_id="match-008")
    transport = StubOpenAITransport(
        responses=[
            _response_payload('{"action":"guard","reasoning":"Opponent is too close."}')
        ],
    )
    adapter = build_openai_adapter(
        "provider/openai-main",
        _policy_config(),
        decision_timeout_ms=2500,
        fallback_mode=FallbackMode.SAFE_CONTINUE,
        transport=transport,
    )
    writer = MatchArtifactWriter.create(
        match_id="match-008",
        manifest=_manifest("match-008"),
        runs_root=tmp_path,
        started_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
    )

    result = asyncio.run(
        resolve_adapter_decision(
            request,
            adapter=adapter,
            artifact_writer=writer,
            logging_config=LoggingConfig(
                events=True,
                prompts=True,
                raw_provider_payloads=True,
            ),
        )
    )

    prompts = _load_jsonl(writer.prompts_path)
    decisions = _load_jsonl(writer.decisions_path)

    assert result.decision.action == "guard"
    assert len(prompts) == 1
    assert prompts[0]["policy_id"] == "provider/openai-main"
    assert prompts[0]["prompt_version"] == "strategic_v1"
    provider_request = cast(dict[str, object], prompts[0]["provider_request"])
    provider_response = cast(dict[str, object], prompts[0]["provider_response"])
    request_attempts = cast(list[object], provider_request["attempts"])
    response_attempts = cast(list[object], provider_response["attempts"])
    assert len(request_attempts) == 1
    assert len(response_attempts) == 1
    decision_payload = cast(dict[str, object], decisions[0]["decision_payload"])
    assert decision_payload["tokens_in"] == 15
