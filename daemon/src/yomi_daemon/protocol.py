"""Typed protocol models for the YOMI daemon <-> mod contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from typing import TypeAlias, cast


JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class ProtocolVersion(StrEnum):
    V1 = "v1"
    V2 = "v2"


class MessageType(StrEnum):
    HELLO = "hello"
    HELLO_ACK = "hello_ack"
    DECISION_REQUEST = "decision_request"
    ACTION_DECISION = "action_decision"
    EVENT = "event"
    MATCH_ENDED = "match_ended"
    CONFIG = "config"


class DecisionType(StrEnum):
    TURN_ACTION = "turn_action"


class FallbackReason(StrEnum):
    TIMEOUT = "timeout"
    DISCONNECT = "disconnect"
    MALFORMED_OUTPUT = "malformed_output"
    ILLEGAL_OUTPUT = "illegal_output"
    STALE_RESPONSE = "stale_response"


class EventName(StrEnum):
    MATCH_STARTED = "MatchStarted"
    TURN_REQUESTED = "TurnRequested"
    DECISION_RECEIVED = "DecisionReceived"
    DECISION_APPLIED = "DecisionApplied"
    DECISION_FALLBACK = "DecisionFallback"
    MATCH_ENDED = "MatchEnded"
    REPLAY_SAVED = "ReplaySaved"
    REPLAY_STARTED = "ReplayStarted"
    REPLAY_ENDED = "ReplayEnded"
    ERROR = "Error"


class TimeoutProfile(StrEnum):
    STRICT_LOCAL = "strict_local"
    LLM_TOURNAMENT = "llm_tournament"


class FallbackMode(StrEnum):
    SAFE_CONTINUE = "safe_continue"
    HEURISTIC_GUARD = "heuristic_guard"
    LAST_VALID_REPLAYABLE = "last_valid_replayable"


class CharacterSelectionMode(StrEnum):
    MIRROR = "mirror"
    ASSIGNED = "assigned"
    RANDOM_FROM_POOL = "random_from_pool"


class PlayerSlot(StrEnum):
    P1 = "p1"
    P2 = "p2"


SUPPORTED_PROTOCOL_VERSIONS: tuple[ProtocolVersion, ...] = (ProtocolVersion.V2,)
CURRENT_PROTOCOL_VERSION = ProtocolVersion.V2
CURRENT_SCHEMA_VERSION = CURRENT_PROTOCOL_VERSION.value


def default_prediction_spec() -> JsonObject:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["horizon"],
        "properties": {
            "horizon": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
                "default": 1,
                "semantic_hint": "prediction_horizon_turns",
            },
            "opponent_action": {
                "type": "string",
                "default": "",
                "semantic_hint": "predicted_opponent_action",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "medium",
                "semantic_hint": "prediction_confidence",
            },
        },
    }


def canonical_json(value: JsonValue | ProtocolModel) -> str:
    """Serialize protocol payloads with stable key ordering for cross-language hashing."""

    normalized = _serialize(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_sha256(value: JsonValue | ProtocolModel) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _serialize(value: object) -> JsonValue:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        serialized: JsonObject = {}
        for field_definition in fields(value):
            item = getattr(value, field_definition.name)
            if item is None and not field_definition.metadata.get("serialize_null", False):
                continue
            serialized[field_definition.name] = _serialize(item)
        return serialized
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_serialize(item) for item in value]
    if value is None or isinstance(value, str | bool | int | float):
        return value
    raise TypeError(f"Unsupported protocol value: {type(value)!r}")


def _require_mapping(raw: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"{context} must be an object")
    return cast(Mapping[str, object], raw)


def _require_sequence(raw: object, *, context: str) -> Sequence[object]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes | bytearray):
        raise TypeError(f"{context} must be an array")
    return raw


def _require_string(raw: object, *, context: str) -> str:
    if not isinstance(raw, str):
        raise TypeError(f"{context} must be a string")
    if not raw:
        raise ValueError(f"{context} must not be empty")
    return raw


def _optional_string(raw: object, *, context: str) -> str | None:
    if raw is None:
        return None
    return _require_string(raw, context=context)


def _require_integer(raw: object, *, context: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(f"{context} must be an integer")
    return raw


def _optional_integer(raw: object, *, context: str) -> int | None:
    if raw is None:
        return None
    return _require_integer(raw, context=context)


def _require_bool(raw: object, *, context: str) -> bool:
    if not isinstance(raw, bool):
        raise TypeError(f"{context} must be a boolean")
    return raw


def _require_number(raw: object, *, context: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise TypeError(f"{context} must be numeric")
    return float(raw)


def _optional_mapping(raw: object, *, context: str) -> Mapping[str, object] | None:
    if raw is None:
        return None
    return _require_mapping(raw, context=context)


def _coerce_str_dict(raw: object, *, context: str) -> JsonObject:
    mapping = _require_mapping(raw, context=context)
    result: JsonObject = {}
    for key, value in mapping.items():
        if not isinstance(key, str):
            raise TypeError(f"{context} keys must be strings")
        result[key] = _coerce_json_value(value, context=f"{context}.{key}")
    return result


def _coerce_json_value(raw: object, *, context: str) -> JsonValue:
    if raw is None or isinstance(raw, str | bool | int | float):
        return raw
    if isinstance(raw, Mapping):
        return _coerce_str_dict(raw, context=context)
    if isinstance(raw, Sequence) and not isinstance(raw, str | bytes | bytearray):
        return [_coerce_json_value(item, context=f"{context}[]") for item in raw]
    raise TypeError(f"{context} contains a non-JSON value")


@dataclass(frozen=True, slots=True)
class ProtocolModel:
    def to_dict(self) -> JsonObject:
        serialized = _serialize(self)
        if not isinstance(serialized, dict):
            raise TypeError(f"{self.__class__.__name__} did not serialize to an object")
        return serialized


@dataclass(frozen=True, slots=True)
class Vector2(ProtocolModel):
    x: float
    y: float

    @classmethod
    def from_dict(cls, raw: object, *, context: str) -> "Vector2":
        mapping = _require_mapping(raw, context=context)
        return cls(
            x=_require_number(mapping.get("x"), context=f"{context}.x"),
            y=_require_number(mapping.get("y"), context=f"{context}.y"),
        )


@dataclass(frozen=True, slots=True)
class DIVector(ProtocolModel):
    x: int
    y: int

    def __post_init__(self) -> None:
        for axis_name, axis_value in (("x", self.x), ("y", self.y)):
            if axis_value < -100 or axis_value > 100:
                raise ValueError(f"DI {axis_name} must be between -100 and 100")

    @classmethod
    def from_dict(cls, raw: object, *, context: str) -> "DIVector":
        mapping = _require_mapping(raw, context=context)
        return cls(
            x=_require_integer(mapping.get("x"), context=f"{context}.x"),
            y=_require_integer(mapping.get("y"), context=f"{context}.y"),
        )


@dataclass(frozen=True, slots=True)
class PlayerPolicyMapping(ProtocolModel):
    p1: str
    p2: str

    @classmethod
    def from_dict(cls, raw: object, *, context: str) -> "PlayerPolicyMapping":
        mapping = _require_mapping(raw, context=context)
        return cls(
            p1=_require_string(mapping.get(PlayerSlot.P1.value), context=f"{context}.p1"),
            p2=_require_string(mapping.get(PlayerSlot.P2.value), context=f"{context}.p2"),
        )


@dataclass(frozen=True, slots=True)
class CharacterAssignments(ProtocolModel):
    p1: str | None = None
    p2: str | None = None

    @classmethod
    def from_dict(cls, raw: object, *, context: str) -> "CharacterAssignments":
        mapping = _require_mapping(raw, context=context)
        return cls(
            p1=_optional_string(mapping.get(PlayerSlot.P1.value), context=f"{context}.p1"),
            p2=_optional_string(mapping.get(PlayerSlot.P2.value), context=f"{context}.p2"),
        )


@dataclass(frozen=True, slots=True)
class LoggingConfig(ProtocolModel):
    events: bool
    prompts: bool
    raw_provider_payloads: bool

    @classmethod
    def from_dict(cls, raw: object, *, context: str) -> "LoggingConfig":
        mapping = _require_mapping(raw, context=context)
        return cls(
            events=_require_bool(mapping.get("events"), context=f"{context}.events"),
            prompts=_require_bool(mapping.get("prompts"), context=f"{context}.prompts"),
            raw_provider_payloads=_require_bool(
                mapping.get("raw_provider_payloads"),
                context=f"{context}.raw_provider_payloads",
            ),
        )


@dataclass(frozen=True, slots=True)
class CharacterSelectionConfig(ProtocolModel):
    mode: CharacterSelectionMode
    assignments: CharacterAssignments | None = None
    pool: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: object, *, context: str) -> "CharacterSelectionConfig":
        mapping = _require_mapping(raw, context=context)
        pool_raw = mapping.get("pool", [])
        pool_values = tuple(
            _require_string(item, context=f"{context}.pool[]")
            for item in _require_sequence(pool_raw, context=f"{context}.pool")
        )
        return cls(
            mode=CharacterSelectionMode(
                _require_string(mapping.get("mode"), context=f"{context}.mode")
            ),
            assignments=(
                CharacterAssignments.from_dict(assignments_raw, context=f"{context}.assignments")
                if (assignments_raw := mapping.get("assignments")) is not None
                else None
            ),
            pool=pool_values,
        )


@dataclass(frozen=True, slots=True)
class ConfigPayload(ProtocolModel):
    timeout_profile: TimeoutProfile
    decision_timeout_ms: int
    fallback_mode: FallbackMode
    logging: LoggingConfig
    policy_mapping: PlayerPolicyMapping
    character_selection: CharacterSelectionConfig
    stage_id: str | None = None

    @classmethod
    def from_dict(cls, raw: object, *, context: str = "config") -> "ConfigPayload":
        mapping = _require_mapping(raw, context=context)
        return cls(
            timeout_profile=TimeoutProfile(
                _require_string(
                    mapping.get("timeout_profile"), context=f"{context}.timeout_profile"
                )
            ),
            decision_timeout_ms=_require_integer(
                mapping.get("decision_timeout_ms"),
                context=f"{context}.decision_timeout_ms",
            ),
            fallback_mode=FallbackMode(
                _require_string(mapping.get("fallback_mode"), context=f"{context}.fallback_mode")
            ),
            logging=LoggingConfig.from_dict(mapping.get("logging"), context=f"{context}.logging"),
            policy_mapping=PlayerPolicyMapping.from_dict(
                mapping.get("policy_mapping"),
                context=f"{context}.policy_mapping",
            ),
            character_selection=CharacterSelectionConfig.from_dict(
                mapping.get("character_selection"),
                context=f"{context}.character_selection",
            ),
            stage_id=_optional_string(mapping.get("stage_id"), context=f"{context}.stage_id"),
        )


@dataclass(frozen=True, slots=True)
class Hello(ProtocolModel):
    game_version: str
    mod_version: str
    schema_version: str
    supported_protocol_versions: tuple[ProtocolVersion, ...]
    auth_token: str | None = None

    @classmethod
    def from_dict(cls, raw: object, *, context: str = "hello") -> "Hello":
        mapping = _require_mapping(raw, context=context)
        versions = tuple(
            ProtocolVersion(
                _require_string(item, context=f"{context}.supported_protocol_versions[]")
            )
            for item in _require_sequence(
                mapping.get("supported_protocol_versions"),
                context=f"{context}.supported_protocol_versions",
            )
        )
        return cls(
            game_version=_require_string(
                mapping.get("game_version"), context=f"{context}.game_version"
            ),
            mod_version=_require_string(
                mapping.get("mod_version"), context=f"{context}.mod_version"
            ),
            schema_version=_require_string(
                mapping.get("schema_version"), context=f"{context}.schema_version"
            ),
            supported_protocol_versions=versions,
            auth_token=_optional_string(mapping.get("auth_token"), context=f"{context}.auth_token"),
        )


@dataclass(frozen=True, slots=True)
class HelloAck(ProtocolModel):
    accepted_protocol_version: ProtocolVersion
    accepted_schema_version: str
    daemon_version: str
    policy_mapping: PlayerPolicyMapping
    config: ConfigPayload | None = None

    @classmethod
    def from_dict(cls, raw: object, *, context: str = "hello_ack") -> "HelloAck":
        mapping = _require_mapping(raw, context=context)
        return cls(
            accepted_protocol_version=ProtocolVersion(
                _require_string(
                    mapping.get("accepted_protocol_version"),
                    context=f"{context}.accepted_protocol_version",
                )
            ),
            accepted_schema_version=_require_string(
                mapping.get("accepted_schema_version"),
                context=f"{context}.accepted_schema_version",
            ),
            daemon_version=_require_string(
                mapping.get("daemon_version"),
                context=f"{context}.daemon_version",
            ),
            policy_mapping=PlayerPolicyMapping.from_dict(
                mapping.get("policy_mapping"),
                context=f"{context}.policy_mapping",
            ),
            config=(
                ConfigPayload.from_dict(config_raw, context=f"{context}.config")
                if (config_raw := mapping.get("config")) is not None
                else None
            ),
        )


MAX_HISTORY_ENTRIES = 10


OBJECT_CATEGORY_MAP: dict[str, str] = {
    "Bullet": "projectile",
    "Arrow": "projectile",
    "StickyBomb": "projectile",
    "Shuriken": "projectile",
    "LoicBeam": "projectile",
    "Zap": "projectile",
    "Fireball": "projectile",
    "WindSlash": "projectile",
    "Geyser": "install",
    "Storm": "install",
    "Trap": "install",
    "Mine": "install",
    "Shield": "effect",
    "Aura": "effect",
}


def classify_object_type(raw_type: str) -> str:
    """Map a raw object type to a gameplay-meaningful category."""
    return OBJECT_CATEGORY_MAP.get(raw_type, "unknown")


@dataclass(frozen=True, slots=True)
class HistoryEntry(ProtocolModel):
    turn_id: int
    player_id: str
    action: str
    was_fallback: bool = False

    @classmethod
    def from_dict(cls, raw: object, *, context: str) -> "HistoryEntry":
        mapping = _require_mapping(raw, context=context)
        return cls(
            turn_id=_require_integer(mapping.get("turn_id"), context=f"{context}.turn_id"),
            player_id=_require_string(mapping.get("player_id"), context=f"{context}.player_id"),
            action=_require_string(mapping.get("action"), context=f"{context}.action"),
            was_fallback=_require_bool(
                mapping.get("was_fallback", False), context=f"{context}.was_fallback"
            ),
        )


@dataclass(frozen=True, slots=True)
class FighterObservation(ProtocolModel):
    id: str
    character: str
    hp: int
    max_hp: int
    meter: int
    burst: int
    position: Vector2
    velocity: Vector2
    facing: str
    current_state: str
    combo_count: int
    blockstun: int
    hitlag: int
    state_interruptable: bool
    can_feint: bool
    grounded: bool
    air_actions_remaining: int | None = None
    feints_remaining: int | None = None
    initiative: bool | None = None
    sadness: int | None = None
    wakeup_throw_immune: bool | None = None
    combo_proration: float | None = None
    character_data: JsonObject | None = None

    @classmethod
    def from_dict(cls, raw: object, *, context: str) -> "FighterObservation":
        mapping = _require_mapping(raw, context=context)
        raw_character_data = mapping.get("character_data")
        return cls(
            id=_require_string(mapping.get("id"), context=f"{context}.id"),
            character=_require_string(mapping.get("character"), context=f"{context}.character"),
            hp=_require_integer(mapping.get("hp"), context=f"{context}.hp"),
            max_hp=_require_integer(mapping.get("max_hp"), context=f"{context}.max_hp"),
            meter=_require_integer(mapping.get("meter"), context=f"{context}.meter"),
            burst=_require_integer(mapping.get("burst"), context=f"{context}.burst"),
            position=Vector2.from_dict(mapping.get("position"), context=f"{context}.position"),
            velocity=Vector2.from_dict(mapping.get("velocity"), context=f"{context}.velocity"),
            facing=_require_string(mapping.get("facing"), context=f"{context}.facing"),
            current_state=_require_string(
                mapping.get("current_state"),
                context=f"{context}.current_state",
            ),
            combo_count=_require_integer(
                mapping.get("combo_count"),
                context=f"{context}.combo_count",
            ),
            blockstun=_require_integer(mapping.get("blockstun"), context=f"{context}.blockstun"),
            hitlag=_require_integer(mapping.get("hitlag"), context=f"{context}.hitlag"),
            state_interruptable=_require_bool(
                mapping.get("state_interruptable"),
                context=f"{context}.state_interruptable",
            ),
            can_feint=_require_bool(mapping.get("can_feint"), context=f"{context}.can_feint"),
            grounded=_require_bool(mapping.get("grounded"), context=f"{context}.grounded"),
            air_actions_remaining=_optional_integer(
                mapping.get("air_actions_remaining"),
                context=f"{context}.air_actions_remaining",
            ),
            feints_remaining=_optional_integer(
                mapping.get("feints_remaining"),
                context=f"{context}.feints_remaining",
            ),
            initiative=(
                _require_bool(initiative_raw, context=f"{context}.initiative")
                if (initiative_raw := mapping.get("initiative")) is not None
                else None
            ),
            sadness=_optional_integer(mapping.get("sadness"), context=f"{context}.sadness"),
            wakeup_throw_immune=(
                _require_bool(wti_raw, context=f"{context}.wakeup_throw_immune")
                if (wti_raw := mapping.get("wakeup_throw_immune")) is not None
                else None
            ),
            combo_proration=(
                _require_number(cp_raw, context=f"{context}.combo_proration")
                if (cp_raw := mapping.get("combo_proration")) is not None
                else None
            ),
            character_data=(
                _coerce_str_dict(raw_character_data, context=f"{context}.character_data")
                if raw_character_data is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class Observation(ProtocolModel):
    tick: int
    frame: int
    active_player: str
    fighters: tuple[FighterObservation, ...]
    objects: tuple[JsonObject, ...]
    stage: JsonObject
    history: tuple[HistoryEntry, ...]

    @classmethod
    def from_dict(cls, raw: object, *, context: str = "observation") -> "Observation":
        mapping = _require_mapping(raw, context=context)
        fighters_raw = _require_sequence(mapping.get("fighters"), context=f"{context}.fighters")
        objects_raw = _require_sequence(mapping.get("objects"), context=f"{context}.objects")
        history_raw = _require_sequence(mapping.get("history"), context=f"{context}.history")
        return cls(
            tick=_require_integer(mapping.get("tick"), context=f"{context}.tick"),
            frame=_require_integer(mapping.get("frame"), context=f"{context}.frame"),
            active_player=_require_string(
                mapping.get("active_player"),
                context=f"{context}.active_player",
            ),
            fighters=tuple(
                FighterObservation.from_dict(item, context=f"{context}.fighters[{index}]")
                for index, item in enumerate(fighters_raw)
            ),
            objects=tuple(
                _coerce_str_dict(item, context=f"{context}.objects[{index}]")
                for index, item in enumerate(objects_raw)
            ),
            stage=_coerce_str_dict(mapping.get("stage"), context=f"{context}.stage"),
            history=tuple(
                HistoryEntry.from_dict(item, context=f"{context}.history[{index}]")
                for index, item in enumerate(history_raw)
            ),
        )


@dataclass(frozen=True, slots=True)
class LegalActionSupports(ProtocolModel):
    di: bool
    feint: bool
    reverse: bool
    prediction: bool = False

    @classmethod
    def from_dict(cls, raw: object, *, context: str) -> "LegalActionSupports":
        mapping = _require_mapping(raw, context=context)
        return cls(
            di=_require_bool(mapping.get("di"), context=f"{context}.di"),
            feint=_require_bool(mapping.get("feint"), context=f"{context}.feint"),
            reverse=_require_bool(mapping.get("reverse"), context=f"{context}.reverse"),
            prediction=_require_bool(
                mapping.get("prediction", False),
                context=f"{context}.prediction",
            ),
        )


@dataclass(frozen=True, slots=True)
class LegalAction(ProtocolModel):
    action: str
    payload_spec: JsonObject
    supports: LegalActionSupports
    prediction_spec: JsonObject | None = None
    payload_schema: JsonObject | None = None
    label: str | None = None
    damage: float | None = None
    startup_frames: int | None = None
    range: float | None = None
    meter_cost: int | None = None
    description: str | None = None

    @classmethod
    def from_dict(cls, raw: object, *, context: str) -> "LegalAction":
        mapping = _require_mapping(raw, context=context)
        return cls(
            action=_require_string(mapping.get("action"), context=f"{context}.action"),
            payload_spec=_coerce_str_dict(
                mapping.get("payload_spec"),
                context=f"{context}.payload_spec",
            ),
            supports=LegalActionSupports.from_dict(
                mapping.get("supports"),
                context=f"{context}.supports",
            ),
            prediction_spec=(
                _coerce_str_dict(prediction_spec_raw, context=f"{context}.prediction_spec")
                if (prediction_spec_raw := mapping.get("prediction_spec")) is not None
                else None
            ),
            payload_schema=(
                _coerce_str_dict(payload_schema_raw, context=f"{context}.payload_schema")
                if (payload_schema_raw := mapping.get("payload_schema")) is not None
                else None
            ),
            label=_optional_string(mapping.get("label"), context=f"{context}.label"),
            damage=(
                _require_number(damage_raw, context=f"{context}.damage")
                if (damage_raw := mapping.get("damage")) is not None
                else None
            ),
            startup_frames=_optional_integer(
                mapping.get("startup_frames"),
                context=f"{context}.startup_frames",
            ),
            range=(
                _require_number(range_raw, context=f"{context}.range")
                if (range_raw := mapping.get("range")) is not None
                else None
            ),
            meter_cost=_optional_integer(
                mapping.get("meter_cost"),
                context=f"{context}.meter_cost",
            ),
            description=_optional_string(
                mapping.get("description"),
                context=f"{context}.description",
            ),
        )


@dataclass(frozen=True, slots=True)
class DecisionRequest(ProtocolModel):
    match_id: str
    turn_id: int
    player_id: str
    deadline_ms: int
    state_hash: str
    legal_actions_hash: str
    decision_type: DecisionType
    observation: Observation
    legal_actions: tuple[LegalAction, ...]
    trace_seed: int | None = None
    game_version: str | None = None
    mod_version: str | None = None
    schema_version: str | None = None
    ruleset_id: str | None = None
    prompt_version: str | None = None

    @classmethod
    def from_dict(cls, raw: object, *, context: str = "decision_request") -> "DecisionRequest":
        mapping = _require_mapping(raw, context=context)
        legal_actions_raw = _require_sequence(
            mapping.get("legal_actions"),
            context=f"{context}.legal_actions",
        )
        return cls(
            match_id=_require_string(mapping.get("match_id"), context=f"{context}.match_id"),
            turn_id=_require_integer(mapping.get("turn_id"), context=f"{context}.turn_id"),
            player_id=_require_string(mapping.get("player_id"), context=f"{context}.player_id"),
            deadline_ms=_require_integer(
                mapping.get("deadline_ms"),
                context=f"{context}.deadline_ms",
            ),
            state_hash=_require_string(mapping.get("state_hash"), context=f"{context}.state_hash"),
            legal_actions_hash=_require_string(
                mapping.get("legal_actions_hash"),
                context=f"{context}.legal_actions_hash",
            ),
            decision_type=DecisionType(
                _require_string(mapping.get("decision_type"), context=f"{context}.decision_type")
            ),
            observation=Observation.from_dict(
                mapping.get("observation"),
                context=f"{context}.observation",
            ),
            legal_actions=tuple(
                LegalAction.from_dict(item, context=f"{context}.legal_actions[{index}]")
                for index, item in enumerate(legal_actions_raw)
            ),
            trace_seed=_optional_integer(
                mapping.get("trace_seed"), context=f"{context}.trace_seed"
            ),
            game_version=_optional_string(
                mapping.get("game_version"),
                context=f"{context}.game_version",
            ),
            mod_version=_optional_string(
                mapping.get("mod_version"),
                context=f"{context}.mod_version",
            ),
            schema_version=_optional_string(
                mapping.get("schema_version"),
                context=f"{context}.schema_version",
            ),
            ruleset_id=_optional_string(
                mapping.get("ruleset_id"),
                context=f"{context}.ruleset_id",
            ),
            prompt_version=_optional_string(
                mapping.get("prompt_version"),
                context=f"{context}.prompt_version",
            ),
        )


@dataclass(frozen=True, slots=True)
class DecisionExtras(ProtocolModel):
    di: DIVector | None = field(metadata={"serialize_null": True})
    feint: bool
    reverse: bool
    prediction: JsonObject | None = field(metadata={"serialize_null": True}, default=None)

    @classmethod
    def from_dict(cls, raw: object, *, context: str = "extra") -> "DecisionExtras":
        mapping = _require_mapping(raw, context=context)
        return cls(
            di=(
                DIVector.from_dict(di_raw, context=f"{context}.di")
                if (di_raw := mapping.get("di")) is not None
                else None
            ),
            feint=_require_bool(mapping.get("feint"), context=f"{context}.feint"),
            reverse=_require_bool(mapping.get("reverse"), context=f"{context}.reverse"),
            prediction=(
                _coerce_str_dict(prediction_raw, context=f"{context}.prediction")
                if (prediction_raw := mapping.get("prediction")) is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ActionDecision(ProtocolModel):
    match_id: str
    turn_id: int
    action: str
    data: JsonObject | None = field(metadata={"serialize_null": True})
    extra: DecisionExtras
    policy_id: str | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    reasoning: str | None = None
    notes: str | None = None
    fallback_reason: FallbackReason | None = None

    @classmethod
    def from_dict(cls, raw: object, *, context: str = "action_decision") -> "ActionDecision":
        mapping = _require_mapping(raw, context=context)
        data_mapping = _optional_mapping(mapping.get("data"), context=f"{context}.data")
        return cls(
            match_id=_require_string(mapping.get("match_id"), context=f"{context}.match_id"),
            turn_id=_require_integer(mapping.get("turn_id"), context=f"{context}.turn_id"),
            action=_require_string(mapping.get("action"), context=f"{context}.action"),
            data=(
                _coerce_str_dict(data_mapping, context=f"{context}.data")
                if data_mapping is not None
                else None
            ),
            extra=DecisionExtras.from_dict(mapping.get("extra"), context=f"{context}.extra"),
            policy_id=_optional_string(mapping.get("policy_id"), context=f"{context}.policy_id"),
            latency_ms=_optional_integer(
                mapping.get("latency_ms"),
                context=f"{context}.latency_ms",
            ),
            tokens_in=_optional_integer(mapping.get("tokens_in"), context=f"{context}.tokens_in"),
            tokens_out=_optional_integer(
                mapping.get("tokens_out"),
                context=f"{context}.tokens_out",
            ),
            reasoning=_optional_string(
                mapping.get("reasoning"),
                context=f"{context}.reasoning",
            ),
            notes=_optional_string(mapping.get("notes"), context=f"{context}.notes"),
            fallback_reason=(
                FallbackReason(
                    _require_string(
                        fallback_raw,
                        context=f"{context}.fallback_reason",
                    )
                )
                if (fallback_raw := mapping.get("fallback_reason")) is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class Event(ProtocolModel):
    match_id: str
    event: EventName
    turn_id: int | None = None
    player_id: str | None = None
    fallback_reason: FallbackReason | None = None
    latency_ms: int | None = None
    details: JsonObject = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: object, *, context: str = "event") -> "Event":
        mapping = _require_mapping(raw, context=context)
        details_mapping = _optional_mapping(mapping.get("details"), context=f"{context}.details")
        return cls(
            match_id=_require_string(mapping.get("match_id"), context=f"{context}.match_id"),
            event=EventName(_require_string(mapping.get("event"), context=f"{context}.event")),
            turn_id=_optional_integer(mapping.get("turn_id"), context=f"{context}.turn_id"),
            player_id=_optional_string(mapping.get("player_id"), context=f"{context}.player_id"),
            fallback_reason=(
                FallbackReason(
                    _require_string(
                        fallback_raw,
                        context=f"{context}.fallback_reason",
                    )
                )
                if (fallback_raw := mapping.get("fallback_reason")) is not None
                else None
            ),
            latency_ms=_optional_integer(
                mapping.get("latency_ms"),
                context=f"{context}.latency_ms",
            ),
            details=(
                _coerce_str_dict(details_mapping, context=f"{context}.details")
                if details_mapping is not None
                else {}
            ),
        )


@dataclass(frozen=True, slots=True)
class MatchEnded(ProtocolModel):
    match_id: str
    winner: str | None = field(metadata={"serialize_null": True})
    end_reason: str
    total_turns: int
    end_tick: int
    end_frame: int
    replay_path: str | None = None
    errors: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: object, *, context: str = "match_ended") -> "MatchEnded":
        mapping = _require_mapping(raw, context=context)
        errors_raw = _require_sequence(mapping.get("errors", []), context=f"{context}.errors")
        return cls(
            match_id=_require_string(mapping.get("match_id"), context=f"{context}.match_id"),
            winner=_optional_string(mapping.get("winner"), context=f"{context}.winner"),
            end_reason=_require_string(mapping.get("end_reason"), context=f"{context}.end_reason"),
            total_turns=_require_integer(
                mapping.get("total_turns"),
                context=f"{context}.total_turns",
            ),
            end_tick=_require_integer(mapping.get("end_tick"), context=f"{context}.end_tick"),
            end_frame=_require_integer(mapping.get("end_frame"), context=f"{context}.end_frame"),
            replay_path=_optional_string(
                mapping.get("replay_path"),
                context=f"{context}.replay_path",
            ),
            errors=tuple(
                _require_string(item, context=f"{context}.errors[{index}]")
                for index, item in enumerate(errors_raw)
            ),
        )


ProtocolPayload: TypeAlias = (
    Hello | HelloAck | DecisionRequest | ActionDecision | Event | MatchEnded | ConfigPayload
)


PAYLOAD_TYPE_BY_MESSAGE_TYPE: dict[MessageType, type[ProtocolPayload]] = {
    MessageType.HELLO: Hello,
    MessageType.HELLO_ACK: HelloAck,
    MessageType.DECISION_REQUEST: DecisionRequest,
    MessageType.ACTION_DECISION: ActionDecision,
    MessageType.EVENT: Event,
    MessageType.MATCH_ENDED: MatchEnded,
    MessageType.CONFIG: ConfigPayload,
}


@dataclass(frozen=True, slots=True)
class Envelope(ProtocolModel):
    type: MessageType
    version: ProtocolVersion
    ts: str
    payload: ProtocolPayload

    @classmethod
    def from_dict(cls, raw: object) -> "Envelope":
        mapping = _require_mapping(raw, context="envelope")
        message_type = MessageType(_require_string(mapping.get("type"), context="envelope.type"))
        version = ProtocolVersion(
            _require_string(mapping.get("version"), context="envelope.version")
        )
        payload_type = PAYLOAD_TYPE_BY_MESSAGE_TYPE[message_type]
        payload = payload_type.from_dict(
            mapping.get("payload"), context=f"payload[{message_type.value}]"
        )
        return cls(
            type=message_type,
            version=version,
            ts=_require_string(mapping.get("ts"), context="envelope.ts"),
            payload=payload,
        )
