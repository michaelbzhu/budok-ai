"""Match manifest foundations for reproducible daemon runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from yomi_daemon import __version__
from yomi_daemon.config import DaemonRuntimeConfig
from yomi_daemon.match import MatchMetadata
from yomi_daemon.protocol import CURRENT_PROTOCOL_VERSION, ConfigPayload, JsonObject, ProtocolModel


@dataclass(frozen=True, slots=True)
class ManifestPolicyEntry(ProtocolModel):
    provider: str
    model: str | None = None
    prompt_version: str | None = None
    credential_env_var: str | None = None
    credential_configured: bool = False


@dataclass(frozen=True, slots=True)
class MatchManifest(ProtocolModel):
    match_id: str
    created_at: str
    daemon_version: str
    protocol_version: str
    schema_version: str
    trace_seed: int
    game_version: str | None = field(metadata={"serialize_null": True})
    mod_version: str | None = field(metadata={"serialize_null": True})
    stage_id: str | None = field(metadata={"serialize_null": True})
    prompt_version: str | None = field(metadata={"serialize_null": True})
    policy_mapping: JsonObject
    policies: JsonObject
    transport: JsonObject
    tournament: JsonObject
    config_snapshot: ConfigPayload


def build_match_manifest(
    *,
    match_id: str,
    runtime_config: DaemonRuntimeConfig,
    metadata: MatchMetadata | None = None,
    created_at: datetime | None = None,
) -> MatchManifest:
    manifest_time = created_at or datetime.now(tz=UTC)
    prompt_versions = {
        policy.prompt_version
        for policy in runtime_config.policies.values()
        if policy.prompt_version is not None
    }
    pinned_prompt_version = prompt_versions.pop() if len(prompt_versions) == 1 else None

    policies: JsonObject = {}
    for policy_id, policy in runtime_config.policies.items():
        policies[policy_id] = ManifestPolicyEntry(
            provider=policy.provider,
            model=policy.model,
            prompt_version=policy.prompt_version,
            credential_env_var=policy.credential.env_var,
            credential_configured=policy.credential.is_configured,
        ).to_dict()

    return MatchManifest(
        match_id=match_id,
        created_at=manifest_time.isoformat().replace("+00:00", "Z"),
        daemon_version=__version__,
        protocol_version=CURRENT_PROTOCOL_VERSION.value,
        schema_version=metadata.schema_version if metadata is not None else "v1",
        trace_seed=runtime_config.trace_seed,
        game_version=metadata.game_version if metadata is not None else None,
        mod_version=metadata.mod_version if metadata is not None else None,
        stage_id=runtime_config.effective_stage_id,
        prompt_version=pinned_prompt_version,
        policy_mapping=runtime_config.policy_mapping.to_dict(),
        policies=policies,
        transport={
            "host": runtime_config.transport.host,
            "port": runtime_config.transport.port,
        },
        tournament={
            "format": runtime_config.tournament.format,
            "mirror_matches_first": runtime_config.tournament.mirror_matches_first,
            "side_swap": runtime_config.tournament.side_swap,
            "games_per_pair": runtime_config.tournament.games_per_pair,
            "fixed_stage": runtime_config.tournament.fixed_stage,
        },
        config_snapshot=runtime_config.to_config_payload(),
    )
