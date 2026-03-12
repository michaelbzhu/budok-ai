"""Telemetry and artifact logging record models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeAlias, cast

from yomi_daemon.protocol import JsonObject, JsonValue, ProtocolModel


RawJsonObject: TypeAlias = Mapping[str, object] | ProtocolModel


class ArtifactStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


def utc_timestamp(value: datetime | None = None) -> str:
    timestamp = value or datetime.now(tz=UTC)
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _coerce_mapping(raw: Mapping[object, object], *, context: str) -> JsonObject:
    result: JsonObject = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise TypeError(f"{context} keys must be strings")
        result[key] = _coerce_json_value(value, context=f"{context}.{key}")
    return result


def _coerce_json_value(raw: object, *, context: str) -> JsonValue:
    if isinstance(raw, ProtocolModel):
        return raw.to_dict()
    if raw is None or isinstance(raw, str | bool | int | float):
        return raw
    if isinstance(raw, Mapping):
        return _coerce_mapping(cast(Mapping[object, object], raw), context=context)
    if isinstance(raw, Sequence) and not isinstance(raw, str | bytes | bytearray):
        return [_coerce_json_value(item, context=f"{context}[]") for item in raw]
    raise TypeError(f"{context} contains a non-JSON value: {type(raw)!r}")


def coerce_json_object(raw: RawJsonObject, *, context: str) -> JsonObject:
    if isinstance(raw, ProtocolModel):
        return raw.to_dict()
    return _coerce_mapping(cast(Mapping[object, object], raw), context=context)


@dataclass(frozen=True, slots=True)
class ArtifactPaths(ProtocolModel):
    manifest: str
    events: str
    decisions: str
    prompts: str
    metrics: str
    result: str
    replay_index: str
    stderr: str


@dataclass(frozen=True, slots=True)
class EventLogRecord(ProtocolModel):
    recorded_at: str
    match_id: str
    payload: JsonObject


@dataclass(frozen=True, slots=True)
class PromptLogRecord(ProtocolModel):
    recorded_at: str
    match_id: str
    turn_id: int
    player_id: str
    prompt_text: str
    request_payload: JsonObject
    policy_id: str | None = field(metadata={"serialize_null": True})
    prompt_version: str | None = field(metadata={"serialize_null": True})
    provider_request: JsonObject | None = field(metadata={"serialize_null": True})
    provider_response: JsonObject | None = field(metadata={"serialize_null": True})


@dataclass(frozen=True, slots=True)
class DecisionLogRecord(ProtocolModel):
    recorded_at: str
    match_id: str
    turn_id: int
    player_id: str
    request_payload: JsonObject
    decision_payload: JsonObject | None = field(metadata={"serialize_null": True})


@dataclass(frozen=True, slots=True)
class MetricsSnapshot(ProtocolModel):
    match_id: str
    status: ArtifactStatus
    started_at: str
    updated_at: str
    completed_at: str | None = field(metadata={"serialize_null": True})
    event_count: int = 0
    prompt_count: int = 0
    decision_count: int = 0
    fallback_count: int = 0
    error_count: int = 0
    latency_sample_count: int = 0
    average_latency_ms: float | None = field(
        default=None,
        metadata={"serialize_null": True},
    )
    total_latency_ms: int = 0
    tokens_in_total: int = 0
    tokens_out_total: int = 0

    def with_updates(
        self,
        *,
        status: ArtifactStatus | None = None,
        updated_at: str | None = None,
        completed_at: str | None = None,
        event_delta: int = 0,
        prompt_delta: int = 0,
        decision_delta: int = 0,
        fallback_delta: int = 0,
        error_delta: int = 0,
        latency_ms: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        error_count_floor: int | None = None,
    ) -> "MetricsSnapshot":
        latency_sample_count = self.latency_sample_count
        total_latency_ms = self.total_latency_ms
        if latency_ms is not None:
            latency_sample_count += 1
            total_latency_ms += latency_ms

        average_latency = total_latency_ms / latency_sample_count if latency_sample_count else None
        error_count = self.error_count + error_delta
        if error_count_floor is not None:
            error_count = max(error_count, error_count_floor)

        return MetricsSnapshot(
            match_id=self.match_id,
            status=status or self.status,
            started_at=self.started_at,
            updated_at=updated_at or self.updated_at,
            completed_at=completed_at,
            event_count=self.event_count + event_delta,
            prompt_count=self.prompt_count + prompt_delta,
            decision_count=self.decision_count + decision_delta,
            fallback_count=self.fallback_count + fallback_delta,
            error_count=error_count,
            latency_sample_count=latency_sample_count,
            average_latency_ms=average_latency,
            total_latency_ms=total_latency_ms,
            tokens_in_total=self.tokens_in_total + (tokens_in or 0),
            tokens_out_total=self.tokens_out_total + (tokens_out or 0),
        )


@dataclass(frozen=True, slots=True)
class ResultSummary(ProtocolModel):
    match_id: str
    status: ArtifactStatus
    started_at: str
    completed_at: str | None = field(metadata={"serialize_null": True})
    winner: str | None = field(metadata={"serialize_null": True})
    end_reason: str | None = field(metadata={"serialize_null": True})
    total_turns: int | None = field(metadata={"serialize_null": True})
    end_tick: int | None = field(metadata={"serialize_null": True})
    end_frame: int | None = field(metadata={"serialize_null": True})
    replay_path: str | None = field(metadata={"serialize_null": True})
    errors: tuple[str, ...] = ()
    metrics: JsonObject = field(default_factory=dict)
    artifacts: JsonObject = field(default_factory=dict)
    match_ended_payload: JsonObject | None = field(
        default=None,
        metadata={"serialize_null": True},
    )
    details: JsonObject = field(default_factory=dict)


def artifact_paths_to_json(paths: ArtifactPaths) -> JsonObject:
    return cast(JsonObject, paths.to_dict())
