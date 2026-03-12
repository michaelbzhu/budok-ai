# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosedError

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.mod_bridge_harness import (
    build_hello_envelope,
    load_mod_config,
    load_mod_metadata,
    perform_handshake,
)
from yomi_daemon.protocol import HelloAck, MessageType
from yomi_daemon.server import DaemonServer


@asynccontextmanager
async def running_server() -> AsyncIterator[DaemonServer]:
    server = DaemonServer(port=0)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def test_mod_default_config_exposes_local_bridge_settings() -> None:
    config = load_mod_config()

    assert config["transport"] == {
        "host": "127.0.0.1",
        "port": 8765,
        "connect_on_ready": True,
    }
    assert config["protocol"] == {
        "schema_version": "v1",
        "supported_versions": ["v1"],
    }
    assert config["timeout_profile"] == "strict_local"
    assert config["decision_timeout_ms"] == 2500
    assert config["fallback_mode"] == "safe_continue"
    assert config["logging"] == {
        "events": True,
        "bridge_state": True,
        "raw_messages": False,
    }


def test_bridge_harness_builds_schema_valid_hello_from_mod_assets() -> None:
    envelope = build_hello_envelope(
        config=load_mod_config(), metadata=load_mod_metadata()
    )

    assert envelope["type"] == MessageType.HELLO.value
    assert envelope["version"] == "v1"
    assert envelope["payload"]["mod_version"] == "0.0.1"
    assert envelope["payload"]["schema_version"] == "v1"
    assert envelope["payload"]["supported_protocol_versions"] == ["v1"]


def test_bridge_harness_completes_handshake_against_daemon_server() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            envelope = await perform_handshake(
                f"ws://127.0.0.1:{server.listening_port}"
            )

            assert envelope.type is MessageType.HELLO_ACK
            assert isinstance(envelope.payload, HelloAck)
            assert envelope.payload.policy_mapping.p1 == "baseline/random"
            assert envelope.payload.policy_mapping.p2 == "baseline/random"

    asyncio.run(scenario())


def test_bridge_harness_surfaces_unsupported_version_rejection() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            config = load_mod_config()
            config["protocol"]["supported_versions"] = ["v99"]

            async with connect(f"ws://127.0.0.1:{server.listening_port}") as websocket:
                await websocket.send(
                    json.dumps(
                        build_hello_envelope(
                            config=config, metadata=load_mod_metadata()
                        )
                    )
                )
                with pytest.raises(ConnectionClosedError) as exc_info:
                    await websocket.recv()

                assert exc_info.value.rcvd is not None
                assert exc_info.value.rcvd.code == 1002

    asyncio.run(scenario())


def test_bridge_harness_surfaces_disconnect_before_hello_ack() -> None:
    async def closing_handler(connection: ServerConnection) -> None:
        await connection.recv()
        await connection.close(code=1011, reason="intentional test disconnect")

    async def scenario() -> None:
        server = await serve(closing_handler, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        try:
            with pytest.raises(ConnectionClosedError, match="received 1011"):
                await perform_handshake(f"ws://127.0.0.1:{port}")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
