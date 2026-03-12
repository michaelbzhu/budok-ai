"""WebSocket daemon server and handshake/session lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import count
from typing import Any, cast

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from yomi_daemon import __version__
from yomi_daemon.match import MatchSession
from yomi_daemon.protocol import (
    ConfigPayload,
    Envelope,
    Hello,
    HelloAck,
    MessageType,
    PlayerPolicyMapping,
    ProtocolVersion,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from yomi_daemon.validation import ProtocolValidationError, parse_envelope


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SUPPORTED_SCHEMA_VERSION = "v1"


class HandshakeRejectedError(ValueError):
    """Raised when the first client message cannot complete a handshake."""


@dataclass(frozen=True, slots=True)
class ServerRuntimeConfig:
    policy_mapping: PlayerPolicyMapping
    config_snapshot: ConfigPayload | None = None


class DaemonServer:
    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        policy_mapping: PlayerPolicyMapping | None = None,
        config_snapshot: ConfigPayload | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.runtime_config = ServerRuntimeConfig(
            policy_mapping=policy_mapping
            if policy_mapping is not None
            else PlayerPolicyMapping(
                p1="baseline/random",
                p2="baseline/random",
            ),
            config_snapshot=config_snapshot,
        )
        self.logger = logger or logging.getLogger("yomi_daemon.server")
        self._server: Server | None = None
        self._session_counter = count(1)
        self._active_sessions: dict[str, MatchSession] = {}
        self._stopped = asyncio.Event()
        self._stopped.set()

    @property
    def listening_port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("Server is not listening")
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def active_sessions(self) -> dict[str, MatchSession]:
        return dict(self._active_sessions)

    async def start(self) -> None:
        if self._server is not None:
            return

        self._server = await serve(self._handle_connection, self.host, self.port)
        self._stopped.clear()
        self.logger.info(
            "Daemon server listening on ws://%s:%d",
            self.host,
            self.listening_port,
        )

    async def stop(self) -> None:
        if self._server is None:
            return

        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self._stopped.set()
        self.logger.info("Daemon server stopped")

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("Server must be started before serving forever")
        await self._stopped.wait()

    async def _handle_connection(self, connection: ServerConnection) -> None:
        session_id = f"session-{next(self._session_counter):04d}"
        remote_address = self._format_remote_address(connection.remote_address)
        self.logger.info("Accepted connection %s from %s", session_id, remote_address)

        try:
            session = await self._perform_handshake(session_id, remote_address, connection)
        except HandshakeRejectedError as exc:
            self.logger.warning(
                "Rejected handshake for %s from %s: %s",
                session_id,
                remote_address,
                exc,
            )
            return
        except ConnectionClosed as exc:
            self.logger.info(
                "Connection %s closed during handshake from %s: %s",
                session_id,
                remote_address,
                exc,
            )
            return

        self._active_sessions[session.session_id] = session
        self.logger.info(
            "Handshake complete for %s using protocol=%s schema=%s",
            session.session_id,
            session.accepted_protocol_version.value,
            session.metadata.schema_version,
        )

        try:
            await connection.wait_closed()
        finally:
            self._active_sessions.pop(session.session_id, None)
            self.logger.info("Session %s closed", session.session_id)

    async def _perform_handshake(
        self,
        session_id: str,
        remote_address: str | None,
        connection: ServerConnection,
    ) -> MatchSession:
        raw_message = await connection.recv()
        if not isinstance(raw_message, str):
            await self._close_for_handshake_error(
                connection,
                code=1003,
                reason="expected text handshake frame",
            )
            raise HandshakeRejectedError("expected a text frame")

        try:
            raw_envelope = self._load_json_object(raw_message)
            self._preflight_handshake(raw_envelope)
        except HandshakeRejectedError as exc:
            await self._close_for_handshake_error(connection, code=1002, reason=str(exc))
            raise

        try:
            envelope = parse_envelope(raw_envelope)
        except ProtocolValidationError as exc:
            await self._close_for_handshake_error(connection, code=1007, reason=str(exc))
            raise HandshakeRejectedError(str(exc)) from exc

        if envelope.type is not MessageType.HELLO:
            await self._close_for_handshake_error(
                connection,
                code=1002,
                reason="expected hello envelope",
            )
            raise HandshakeRejectedError("expected hello envelope")

        hello = envelope.payload
        if not isinstance(hello, Hello):
            await self._close_for_handshake_error(
                connection,
                code=1002,
                reason="hello payload did not decode correctly",
            )
            raise HandshakeRejectedError("hello payload did not decode correctly")

        accepted_version = negotiate_protocol_version(hello.supported_protocol_versions)
        if accepted_version is None:
            await self._close_for_handshake_error(
                connection,
                code=1002,
                reason="unsupported protocol version",
            )
            raise HandshakeRejectedError("unsupported protocol version")
        if hello.schema_version != SUPPORTED_SCHEMA_VERSION:
            await self._close_for_handshake_error(
                connection,
                code=1002,
                reason="unsupported schema version",
            )
            raise HandshakeRejectedError("unsupported schema version")

        session = MatchSession.from_hello(
            session_id=session_id,
            remote_address=remote_address,
            hello=hello,
            accepted_protocol_version=accepted_version,
            daemon_version=__version__,
            policy_mapping=self.runtime_config.policy_mapping,
            config_snapshot=self.runtime_config.config_snapshot,
        )
        await connection.send(json.dumps(self._build_hello_ack_envelope(session).to_dict()))
        return session

    async def _close_for_handshake_error(
        self,
        connection: ServerConnection,
        *,
        code: int,
        reason: str,
    ) -> None:
        await connection.close(code=code, reason=reason[:123])

    def _load_json_object(self, raw_message: str) -> Mapping[str, object]:
        try:
            decoded = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise HandshakeRejectedError(f"invalid JSON: {exc.msg}") from exc
        if not isinstance(decoded, Mapping):
            raise HandshakeRejectedError("handshake frame must decode to an object")
        return decoded

    def _preflight_handshake(self, raw_envelope: Mapping[str, object]) -> None:
        message_type = raw_envelope.get("type")
        if message_type != MessageType.HELLO.value:
            raise HandshakeRejectedError("expected hello envelope")

        raw_version = raw_envelope.get("version")
        if raw_version not in {item.value for item in SUPPORTED_PROTOCOL_VERSIONS}:
            raise HandshakeRejectedError(f"unsupported envelope version: {raw_version!r}")

        payload = raw_envelope.get("payload")
        if not isinstance(payload, Mapping):
            raise HandshakeRejectedError("hello payload must be an object")
        payload_mapping = cast(Mapping[str, object], payload)

        raw_schema_version = payload_mapping.get("schema_version")
        if not isinstance(raw_schema_version, str):
            raise HandshakeRejectedError("hello.schema_version must be a string")
        if raw_schema_version != SUPPORTED_SCHEMA_VERSION:
            raise HandshakeRejectedError(f"unsupported schema version: {raw_schema_version!r}")

        raw_supported_versions = payload_mapping.get("supported_protocol_versions")
        if not isinstance(raw_supported_versions, Sequence) or isinstance(
            raw_supported_versions, str | bytes | bytearray
        ):
            raise HandshakeRejectedError("hello.supported_protocol_versions must be an array")
        if not any(
            version in {item.value for item in SUPPORTED_PROTOCOL_VERSIONS}
            for version in raw_supported_versions
            if isinstance(version, str)
        ):
            raise HandshakeRejectedError(
                f"unsupported protocol versions: {list(raw_supported_versions)!r}"
            )

    def _build_hello_ack_envelope(self, session: MatchSession) -> Envelope:
        return Envelope(
            type=MessageType.HELLO_ACK,
            version=session.accepted_protocol_version,
            ts=_utc_now(),
            payload=HelloAck(
                accepted_protocol_version=session.accepted_protocol_version,
                accepted_schema_version=SUPPORTED_SCHEMA_VERSION,
                daemon_version=session.daemon_version,
                policy_mapping=session.policy_mapping,
                config=session.config_snapshot,
            ),
        )

    @staticmethod
    def _format_remote_address(remote_address: Any) -> str | None:
        if remote_address is None:
            return None
        if isinstance(remote_address, tuple):
            return ":".join(str(part) for part in remote_address[:2])
        return str(remote_address)


def negotiate_protocol_version(
    supported_versions: Sequence[ProtocolVersion],
) -> ProtocolVersion | None:
    client_supported = set(supported_versions)
    for version in SUPPORTED_PROTOCOL_VERSIONS:
        if version in client_supported:
            return version
    return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
