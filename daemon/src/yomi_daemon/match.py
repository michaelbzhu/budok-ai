"""Daemon-side session and match metadata state."""

from __future__ import annotations

from dataclasses import dataclass

from yomi_daemon.protocol import ConfigPayload, Hello, PlayerPolicyMapping, ProtocolVersion


@dataclass(slots=True)
class MatchMetadata:
    game_version: str
    mod_version: str
    schema_version: str
    match_id: str | None = None


@dataclass(slots=True)
class MatchSession:
    session_id: str
    remote_address: str | None
    accepted_protocol_version: ProtocolVersion
    daemon_version: str
    policy_mapping: PlayerPolicyMapping
    config_snapshot: ConfigPayload | None
    metadata: MatchMetadata

    @classmethod
    def from_hello(
        cls,
        *,
        session_id: str,
        remote_address: str | None,
        hello: Hello,
        accepted_protocol_version: ProtocolVersion,
        daemon_version: str,
        policy_mapping: PlayerPolicyMapping,
        config_snapshot: ConfigPayload | None,
    ) -> "MatchSession":
        return cls(
            session_id=session_id,
            remote_address=remote_address,
            accepted_protocol_version=accepted_protocol_version,
            daemon_version=daemon_version,
            policy_mapping=policy_mapping,
            config_snapshot=config_snapshot,
            metadata=MatchMetadata(
                game_version=hello.game_version,
                mod_version=hello.mod_version,
                schema_version=hello.schema_version,
            ),
        )
