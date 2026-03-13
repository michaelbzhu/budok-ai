"""WU-023: Transport, credential, and operator-safety hardening tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from yomi_daemon.protocol import (
    CURRENT_PROTOCOL_VERSION,
    CURRENT_SCHEMA_VERSION,
    HelloAck,
    MessageType,
)
from yomi_daemon.redact import redact_secrets, sanitize_provider_error
from yomi_daemon.server import DaemonServer
from yomi_daemon.validation import parse_envelope


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _build_hello_envelope(*, auth_token: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "game_version": "1.0.0",
        "mod_version": "0.1.0",
        "schema_version": CURRENT_SCHEMA_VERSION,
        "supported_protocol_versions": [CURRENT_PROTOCOL_VERSION.value],
    }
    if auth_token is not None:
        payload["auth_token"] = auth_token
    return {
        "type": MessageType.HELLO.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-13T00:00:00Z",
        "payload": payload,
    }


@asynccontextmanager
async def _running_server(
    *, auth_secret: str | None = None
) -> AsyncIterator[DaemonServer]:
    server = DaemonServer(port=0, auth_secret=auth_secret)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


# --- Handshake auth tests ---


def test_auth_succeeds_with_correct_token() -> None:
    async def scenario() -> None:
        async with _running_server(auth_secret="test-secret-42") as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await ws.send(
                    json.dumps(_build_hello_envelope(auth_token="test-secret-42"))
                )
                response = await ws.recv()
                assert isinstance(response, str)
                envelope = parse_envelope(json.loads(response))
                assert isinstance(envelope.payload, HelloAck)
                assert envelope.type is MessageType.HELLO_ACK

    asyncio.run(scenario())


def test_auth_rejects_wrong_token() -> None:
    async def scenario() -> None:
        async with _running_server(auth_secret="correct-secret") as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await ws.send(
                    json.dumps(_build_hello_envelope(auth_token="wrong-secret"))
                )
                try:
                    await ws.recv()
                except ConnectionClosedError as exc:
                    assert exc.rcvd is not None
                    assert exc.rcvd.code == 1008
                else:
                    raise AssertionError("Expected connection to be closed with 1008")

    asyncio.run(scenario())


def test_auth_rejects_missing_token() -> None:
    async def scenario() -> None:
        async with _running_server(auth_secret="my-secret") as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await ws.send(json.dumps(_build_hello_envelope()))
                try:
                    await ws.recv()
                except ConnectionClosedError as exc:
                    assert exc.rcvd is not None
                    assert exc.rcvd.code == 1008
                else:
                    raise AssertionError("Expected connection to be closed with 1008")

    asyncio.run(scenario())


def test_no_auth_required_when_secret_not_configured() -> None:
    async def scenario() -> None:
        async with _running_server(auth_secret=None) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await ws.send(json.dumps(_build_hello_envelope()))
                response = await ws.recv()
                assert isinstance(response, str)
                envelope = parse_envelope(json.loads(response))
                assert isinstance(envelope.payload, HelloAck)

    asyncio.run(scenario())


def test_auth_token_ignored_when_secret_not_configured() -> None:
    """Sending an auth_token when the server doesn't require auth should work fine."""

    async def scenario() -> None:
        async with _running_server(auth_secret=None) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await ws.send(
                    json.dumps(_build_hello_envelope(auth_token="bonus-token"))
                )
                response = await ws.recv()
                assert isinstance(response, str)
                envelope = parse_envelope(json.loads(response))
                assert isinstance(envelope.payload, HelloAck)

    asyncio.run(scenario())


# --- Credential file gitignore tests ---


def test_env_files_are_gitignored() -> None:
    gitignore_path = REPO_ROOT / ".gitignore"
    content = gitignore_path.read_text()
    assert ".env" in content
    assert "*.env" in content
    assert "!.env.example" in content


def test_env_example_exists_without_real_keys() -> None:
    env_example = REPO_ROOT / ".env.example"
    assert env_example.exists()
    content = env_example.read_text()
    # All API key lines should be commented out
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            # Only blank lines should be uncommented
            assert stripped == "", (
                f"Uncommented non-blank line in .env.example: {stripped!r}"
            )


# --- Secret redaction tests ---


def test_redact_anthropic_api_key() -> None:
    text = "Error: sk-ant-api03-abcdef1234567890-xyz received 401"
    redacted = redact_secrets(text)
    assert "sk-ant" not in redacted
    assert "[REDACTED]" in redacted
    assert "received 401" in redacted


def test_redact_openai_api_key() -> None:
    text = "Failed with sk-proj-abcdef1234567890abcdef in body"
    redacted = redact_secrets(text)
    assert "sk-proj" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_openrouter_api_key() -> None:
    text = "Header: sk-or-v1-abc123def456 unauthorized"
    redacted = redact_secrets(text)
    assert "sk-or" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_authorization_header() -> None:
    text = "Authorization: Bearer sk-ant-secret123"
    redacted = redact_secrets(text)
    assert "sk-ant" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_api_key_equals_pattern() -> None:
    text = "api_key=sk-something-secret"
    redacted = redact_secrets(text)
    assert "sk-something" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_preserves_safe_text() -> None:
    text = "Connection refused on port 8765"
    redacted = redact_secrets(text)
    assert redacted == text


def test_sanitize_provider_error_strips_response_repr() -> None:
    class FakeExc(Exception):
        pass

    exc = FakeExc(
        "Anthropic request failed with HTTP 401: <Response [401 Unauthorized]> "
        "sk-ant-api03-secret"
    )
    sanitized = sanitize_provider_error(exc)
    assert "sk-ant" not in sanitized
    assert "<Response" not in sanitized


def test_sanitize_provider_error_handles_clean_message() -> None:
    class FakeExc(Exception):
        pass

    exc = FakeExc("Connection timeout after 5000ms")
    sanitized = sanitize_provider_error(exc)
    assert sanitized == "Connection timeout after 5000ms"
