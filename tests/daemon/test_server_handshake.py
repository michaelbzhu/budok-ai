from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from yomi_daemon.protocol import (
    CURRENT_PROTOCOL_VERSION,
    HelloAck,
    MessageType,
    PlayerPolicyMapping,
)
from yomi_daemon.server import DEFAULT_HOST, DaemonServer
from yomi_daemon.validation import parse_envelope


def build_hello_envelope(
    *,
    supported_versions: list[str] | None = None,
    schema_version: str = "v1",
) -> dict[str, object]:
    return {
        "type": MessageType.HELLO.value,
        "version": CURRENT_PROTOCOL_VERSION.value,
        "ts": "2026-03-12T00:00:00Z",
        "payload": {
            "game_version": "1.0.0",
            "mod_version": "0.1.0",
            "schema_version": schema_version,
            "supported_protocol_versions": supported_versions
            or [CURRENT_PROTOCOL_VERSION.value],
        },
    }


@asynccontextmanager
async def running_server(
    *,
    policy_mapping: PlayerPolicyMapping | None = None,
) -> AsyncIterator[DaemonServer]:
    server = DaemonServer(port=0, policy_mapping=policy_mapping)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def test_successful_handshake_returns_hello_ack() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as websocket:
                await websocket.send(json.dumps(build_hello_envelope()))
                response = await websocket.recv()
                assert isinstance(response, str)
                envelope = parse_envelope(json.loads(response))
                assert isinstance(envelope.payload, HelloAck)

                assert envelope.type is MessageType.HELLO_ACK
                assert envelope.version is CURRENT_PROTOCOL_VERSION
                assert envelope.payload.policy_mapping.p1 == "baseline/random"
                assert envelope.payload.policy_mapping.p2 == "baseline/random"
                assert server.active_sessions

    asyncio.run(scenario())


def test_unsupported_protocol_version_is_rejected_cleanly() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as websocket:
                await websocket.send(
                    json.dumps(build_hello_envelope(supported_versions=["v99"]))
                )
                try:
                    await websocket.recv()
                except ConnectionClosedError as exc:
                    assert exc.rcvd is not None
                    assert exc.rcvd.code == 1002
                else:
                    raise AssertionError("Expected the daemon to close the connection")

            await asyncio.sleep(0)
            assert server.active_sessions == {}

    asyncio.run(scenario())


def test_daemon_defaults_to_localhost_binding() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            assert server.host == DEFAULT_HOST
            assert server.listening_port > 0

    asyncio.run(scenario())


def test_hello_ack_uses_configured_player_slot_mapping() -> None:
    async def scenario() -> None:
        async with running_server(
            policy_mapping=PlayerPolicyMapping(
                p1="baseline/greedy_damage",
                p2="baseline/block_always",
            )
        ) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as websocket:
                await websocket.send(json.dumps(build_hello_envelope()))
                response = await websocket.recv()
                assert isinstance(response, str)
                envelope = parse_envelope(json.loads(response))
                assert isinstance(envelope.payload, HelloAck)

                assert envelope.payload.policy_mapping == PlayerPolicyMapping(
                    p1="baseline/greedy_damage",
                    p2="baseline/block_always",
                )

    asyncio.run(scenario())
