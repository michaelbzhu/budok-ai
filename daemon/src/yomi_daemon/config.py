"""Daemon runtime config loading and normalization."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match

from yomi_daemon.protocol import (
    CharacterSelectionConfig,
    CharacterSelectionMode,
    ConfigPayload,
    FallbackMode,
    JsonObject,
    LoggingConfig,
    MatchOptions,
    PlayerPolicyMapping,
)
from yomi_daemon.validation import REPO_ROOT, load_schema


CONFIG_SCHEMA_FILE = "daemon-config.v1.json"
DEFAULT_RUNTIME_CONFIG_PATH = REPO_ROOT / "daemon" / "config" / "default_config.json"

_BASE_CONFIG: dict[str, object] = {
    "version": "v1",
    "transport": {"host": "127.0.0.1", "port": 8765},
    "decision_timeout_ms": 10000,
    "fallback_mode": FallbackMode.SAFE_CONTINUE.value,
    "logging": {
        "events": True,
        "prompts": True,
        "raw_provider_payloads": False,
    },
    "character_selection": {"mode": CharacterSelectionMode.MIRROR.value},
    "tournament": {
        "format": "round_robin",
        "mirror_matches_first": True,
        "side_swap": True,
        "games_per_pair": 10,
        "fixed_stage": "training_room",
    },
    "trace_seed": 0,
}


class ConfigError(ValueError):
    """Raised when a runtime config file fails validation or normalization."""


@dataclass(frozen=True, slots=True)
class TransportConfig:
    host: str
    port: int
    auth_secret: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCredential:
    env_var: str | None = None
    value: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.env_var and self.value)


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    provider: str
    model: str | None = None
    prompt_version: str | None = None
    credential: ProviderCredential = field(default_factory=ProviderCredential)
    temperature: float | None = None
    max_tokens: int | None = None
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TournamentDefaults:
    format: str
    mirror_matches_first: bool
    side_swap: bool
    games_per_pair: int
    fixed_stage: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeConfigOverrides:
    host: str | None = None
    port: int | None = None
    p1_policy: str | None = None
    p2_policy: str | None = None
    trace_seed: int | None = None


@dataclass(frozen=True, slots=True)
class DaemonRuntimeConfig:
    version: str
    transport: TransportConfig
    decision_timeout_ms: int
    fallback_mode: FallbackMode
    logging: LoggingConfig
    policy_mapping: PlayerPolicyMapping
    policies: Mapping[str, PolicyConfig]
    character_selection: CharacterSelectionConfig
    tournament: TournamentDefaults
    trace_seed: int
    stage_id: str | None = None
    match_options: MatchOptions | None = None

    def to_config_payload(self) -> ConfigPayload:
        return ConfigPayload(
            decision_timeout_ms=self.decision_timeout_ms,
            fallback_mode=self.fallback_mode,
            logging=self.logging,
            policy_mapping=self.policy_mapping,
            character_selection=self.character_selection,
            stage_id=self.effective_stage_id,
            match_options=self.match_options,
        )

    @property
    def effective_stage_id(self) -> str | None:
        return self.stage_id or self.tournament.fixed_stage


def _config_validator() -> Any:
    return Draft202012Validator(load_schema(CONFIG_SCHEMA_FILE))


def _format_validation_error(error: ValidationError) -> ConfigError:
    path = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return ConfigError(f"runtime config validation failed at {path}: {error.message}")


def _require_mapping(raw: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{context} must be an object")
    return cast(Mapping[str, object], raw)


def _require_string(raw: object, *, context: str) -> str:
    if not isinstance(raw, str):
        raise ConfigError(f"{context} must be a string")
    if not raw:
        raise ConfigError(f"{context} must not be empty")
    return raw


def _optional_string(raw: object, *, context: str) -> str | None:
    if raw is None:
        return None
    return _require_string(raw, context=context)


def _require_bool(raw: object, *, context: str) -> bool:
    if not isinstance(raw, bool):
        raise ConfigError(f"{context} must be a boolean")
    return raw


def _require_int(raw: object, *, context: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(f"{context} must be an integer")
    return raw


def _optional_float(raw: object, *, context: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise ConfigError(f"{context} must be numeric")
    return float(raw)


def _optional_int(raw: object, *, context: str) -> int | None:
    if raw is None:
        return None
    return _require_int(raw, context=context)


def _merge_dicts(base: Mapping[str, object], overlay: Mapping[str, object]) -> dict[str, object]:
    merged: dict[str, object] = dict(base)
    for key, value in overlay.items():
        base_value = merged.get(key)
        if isinstance(base_value, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge_dicts(
                cast(Mapping[str, object], base_value),
                cast(Mapping[str, object], value),
            )
            continue
        merged[key] = value
    return merged


def _apply_overrides(
    document: Mapping[str, object], overrides: RuntimeConfigOverrides | None
) -> dict[str, object]:
    if overrides is None:
        return dict(document)

    result = _merge_dicts(document, {})
    transport = cast(dict[str, object], result.setdefault("transport", {}))
    policy_mapping = cast(dict[str, object], result.setdefault("policy_mapping", {}))

    if overrides.host is not None:
        transport["host"] = overrides.host
    if overrides.port is not None:
        transport["port"] = overrides.port
    if overrides.p1_policy is not None:
        policy_mapping["p1"] = overrides.p1_policy
    if overrides.p2_policy is not None:
        policy_mapping["p2"] = overrides.p2_policy
    if overrides.trace_seed is not None:
        result["trace_seed"] = overrides.trace_seed

    return result


def _load_config_document(path: Path) -> Mapping[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"runtime config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"runtime config file {path} is not valid JSON: {exc.msg}") from exc

    return _require_mapping(raw, context=f"runtime config file {path}")


def _resolve_timeout(document: Mapping[str, object], *, context: str) -> int:
    raw_timeout = document.get("decision_timeout_ms")
    timeout_ms = (
        10000
        if raw_timeout is None
        else _require_int(raw_timeout, context=f"{context}.decision_timeout_ms")
    )
    if timeout_ms < 1:
        raise ConfigError(f"{context}.decision_timeout_ms must be at least 1")
    return timeout_ms


def _resolve_character_selection(raw: object, *, context: str) -> CharacterSelectionConfig:
    selection = CharacterSelectionConfig.from_dict(raw, context=context)
    if selection.mode is CharacterSelectionMode.ASSIGNED:
        assignments = selection.assignments
        if assignments is None or not assignments.p1 or not assignments.p2:
            raise ConfigError(
                f"{context}.assignments must provide non-empty p1 and p2 values for mode 'assigned'"
            )
    if selection.mode is CharacterSelectionMode.RANDOM_FROM_POOL and not selection.pool:
        raise ConfigError(f"{context}.pool must contain at least one character")
    return selection


def _resolve_policies(
    raw: object, *, context: str, env: Mapping[str, str]
) -> Mapping[str, PolicyConfig]:
    policies_raw = _require_mapping(raw, context=context)
    if not policies_raw:
        raise ConfigError(f"{context} must define at least one policy")

    policies: dict[str, PolicyConfig] = {}
    for policy_id, definition_raw in policies_raw.items():
        definition = _require_mapping(definition_raw, context=f"{context}.{policy_id}")
        env_var = _optional_string(
            definition.get("credential_env_var"),
            context=f"{context}.{policy_id}.credential_env_var",
        )
        options_raw = definition.get("options", {})
        options = _require_mapping(options_raw, context=f"{context}.{policy_id}.options")
        policies[policy_id] = PolicyConfig(
            provider=_require_string(
                definition.get("provider"),
                context=f"{context}.{policy_id}.provider",
            ),
            model=_optional_string(definition.get("model"), context=f"{context}.{policy_id}.model"),
            prompt_version=_optional_string(
                definition.get("prompt_version"),
                context=f"{context}.{policy_id}.prompt_version",
            ),
            credential=ProviderCredential(
                env_var=env_var, value=env.get(env_var) if env_var else None
            ),
            temperature=_optional_float(
                definition.get("temperature"),
                context=f"{context}.{policy_id}.temperature",
            ),
            max_tokens=_optional_int(
                definition.get("max_tokens"),
                context=f"{context}.{policy_id}.max_tokens",
            ),
            options=MappingProxyType(dict(options)),
        )
    return MappingProxyType(policies)


def _resolve_policy_mapping(
    raw: object, *, context: str, policies: Mapping[str, PolicyConfig]
) -> PlayerPolicyMapping:
    try:
        mapping = PlayerPolicyMapping.from_dict(raw, context=context)
    except (TypeError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc
    for slot, policy_id in (("p1", mapping.p1), ("p2", mapping.p2)):
        if policy_id not in policies:
            raise ConfigError(
                f"{context}.{slot} references undefined policy {policy_id!r}; "
                f"define it under policies.{policy_id}"
            )
    return mapping


def _resolve_tournament_defaults(raw: object, *, context: str) -> TournamentDefaults:
    mapping = _require_mapping(raw, context=context)
    games_per_pair = _require_int(
        mapping.get("games_per_pair"),
        context=f"{context}.games_per_pair",
    )
    if games_per_pair < 1:
        raise ConfigError(f"{context}.games_per_pair must be at least 1")

    return TournamentDefaults(
        format=_require_string(mapping.get("format"), context=f"{context}.format"),
        mirror_matches_first=_require_bool(
            mapping.get("mirror_matches_first"),
            context=f"{context}.mirror_matches_first",
        ),
        side_swap=_require_bool(mapping.get("side_swap"), context=f"{context}.side_swap"),
        games_per_pair=games_per_pair,
        fixed_stage=_optional_string(mapping.get("fixed_stage"), context=f"{context}.fixed_stage"),
    )


def _resolve_match_options(raw: object, *, context: str) -> MatchOptions | None:
    if raw is None:
        return None
    return MatchOptions.from_dict(raw, context=context)


def _validate_document(document: Mapping[str, object]) -> None:
    error = best_match(_config_validator().iter_errors(document))
    if error is not None:
        raise _format_validation_error(error)


def parse_runtime_config_document(
    raw: Mapping[str, object],
    *,
    overrides: RuntimeConfigOverrides | None = None,
    env: Mapping[str, str] | None = None,
) -> DaemonRuntimeConfig:
    resolved_env = env if env is not None else os.environ
    merged = _apply_overrides(_merge_dicts(_BASE_CONFIG, raw), overrides)
    _validate_document(merged)

    context = "runtime_config"
    policies = _resolve_policies(
        merged.get("policies"), context=f"{context}.policies", env=resolved_env
    )
    transport = _require_mapping(merged.get("transport"), context=f"{context}.transport")
    auth_secret_env_var = _optional_string(
        transport.get("auth_secret_env_var"), context=f"{context}.transport.auth_secret_env_var"
    )
    auth_secret = resolved_env.get(auth_secret_env_var) if auth_secret_env_var else None

    return DaemonRuntimeConfig(
        version=_require_string(merged.get("version"), context=f"{context}.version"),
        transport=TransportConfig(
            host=_require_string(transport.get("host"), context=f"{context}.transport.host"),
            port=_require_int(transport.get("port"), context=f"{context}.transport.port"),
            auth_secret=auth_secret,
        ),
        decision_timeout_ms=_resolve_timeout(merged, context=context),
        fallback_mode=FallbackMode(
            _require_string(merged.get("fallback_mode"), context=f"{context}.fallback_mode")
        ),
        logging=LoggingConfig.from_dict(merged.get("logging"), context=f"{context}.logging"),
        policy_mapping=_resolve_policy_mapping(
            merged.get("policy_mapping"),
            context=f"{context}.policy_mapping",
            policies=policies,
        ),
        policies=policies,
        character_selection=_resolve_character_selection(
            merged.get("character_selection"),
            context=f"{context}.character_selection",
        ),
        tournament=_resolve_tournament_defaults(
            merged.get("tournament"),
            context=f"{context}.tournament",
        ),
        trace_seed=_require_int(merged.get("trace_seed"), context=f"{context}.trace_seed"),
        stage_id=_optional_string(merged.get("stage_id"), context=f"{context}.stage_id"),
        match_options=_resolve_match_options(
            merged.get("match_options"), context=f"{context}.match_options"
        ),
    )


def load_runtime_config(
    path: Path | None = None,
    *,
    overrides: RuntimeConfigOverrides | None = None,
    env: Mapping[str, str] | None = None,
) -> DaemonRuntimeConfig:
    selected_path = path or DEFAULT_RUNTIME_CONFIG_PATH
    document = _load_config_document(selected_path)
    return parse_runtime_config_document(document, overrides=overrides, env=env)


def config_to_json_object(config: DaemonRuntimeConfig) -> JsonObject:
    policies: JsonObject = {}
    for policy_id, policy in config.policies.items():
        entry: JsonObject = {
            "provider": policy.provider,
            "credential_env_var": policy.credential.env_var,
            "credential_resolved": policy.credential.is_configured,
        }
        if policy.model is not None:
            entry["model"] = policy.model
        if policy.prompt_version is not None:
            entry["prompt_version"] = policy.prompt_version
        if policy.temperature is not None:
            entry["temperature"] = policy.temperature
        if policy.max_tokens is not None:
            entry["max_tokens"] = policy.max_tokens
        if policy.options:
            entry["options"] = dict(policy.options)
        policies[policy_id] = entry

    return {
        "version": config.version,
        "transport": {
            "host": config.transport.host,
            "port": config.transport.port,
            "auth_configured": config.transport.auth_secret is not None,
        },
        "decision_timeout_ms": config.decision_timeout_ms,
        "fallback_mode": config.fallback_mode.value,
        "logging": config.logging.to_dict(),
        "policy_mapping": config.policy_mapping.to_dict(),
        "policies": policies,
        "character_selection": config.character_selection.to_dict(),
        "tournament": {
            "format": config.tournament.format,
            "mirror_matches_first": config.tournament.mirror_matches_first,
            "side_swap": config.tournament.side_swap,
            "games_per_pair": config.tournament.games_per_pair,
            "fixed_stage": config.tournament.fixed_stage,
        },
        "trace_seed": config.trace_seed,
        "stage_id": config.stage_id,
        "match_options": config.match_options.to_dict() if config.match_options else None,
    }
