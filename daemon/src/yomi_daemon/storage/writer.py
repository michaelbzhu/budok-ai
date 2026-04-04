"""Artifact writer for per-match logs and summaries."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import cast

from yomi_daemon.logging.schemas import (
    ArtifactPaths,
    ArtifactStatus,
    DecisionLogRecord,
    EventLogRecord,
    MetricsSnapshot,
    PromptLogRecord,
    RawJsonObject,
    ResultSummary,
    artifact_paths_to_json,
    coerce_json_object,
    utc_timestamp,
)
from yomi_daemon.storage.replay_index import ReplayIndexState
from yomi_daemon.validation import REPO_ROOT


RUNS_DIR = REPO_ROOT / "runs"
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def _timestamp_directory_prefix(value: datetime) -> str:
    return value.astimezone(UTC).strftime(_TIMESTAMP_FORMAT)


def _require_string(raw: object, *, context: str) -> str:
    if not isinstance(raw, str):
        raise TypeError(f"{context} must be a string")
    if not raw:
        raise ValueError(f"{context} must not be empty")
    return raw


def _require_integer(raw: object, *, context: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(f"{context} must be an integer")
    return raw


def _optional_string(raw: object, *, context: str) -> str | None:
    if raw is None:
        return None
    return _require_string(raw, context=context)


def _optional_integer(raw: object, *, context: str) -> int | None:
    if raw is None:
        return None
    return _require_integer(raw, context=context)


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> int:
    existing_lines = 0
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            existing_lines = sum(1 for _ in handle)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    return existing_lines + 1


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    temp_path.replace(path)


@dataclass(slots=True)
class MatchArtifactWriter:
    match_id: str
    run_dir: Path
    manifest_path: Path
    events_path: Path
    decisions_path: Path
    prompts_path: Path
    metrics_path: Path
    result_path: Path
    replay_index_path: Path
    stderr_path: Path
    started_at: str
    _artifact_paths: ArtifactPaths
    _metrics: MetricsSnapshot
    _result: ResultSummary
    _replay_index: ReplayIndexState
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    @classmethod
    def create(
        cls,
        *,
        match_id: str,
        manifest: RawJsonObject,
        runs_root: Path | None = None,
        started_at: datetime | None = None,
    ) -> "MatchArtifactWriter":
        manifest_payload = coerce_json_object(manifest, context="manifest")
        manifest_match_id = _require_string(
            manifest_payload.get("match_id"),
            context="manifest.match_id",
        )
        if manifest_match_id != match_id:
            raise ValueError(f"manifest.match_id {manifest_match_id!r} does not match {match_id!r}")

        start_time = started_at or datetime.now(tz=UTC)
        started_at_text = utc_timestamp(start_time)
        root = runs_root or RUNS_DIR
        run_dir = root / f"{_timestamp_directory_prefix(start_time)}_{match_id}"
        run_dir.mkdir(parents=True, exist_ok=False)

        artifact_paths = ArtifactPaths(
            manifest="manifest.json",
            events="events.jsonl",
            decisions="decisions.jsonl",
            prompts="prompts.jsonl",
            metrics="metrics.json",
            result="result.json",
            replay_index="replay_index.json",
            stderr="stderr.log",
        )
        manifest_path = run_dir / artifact_paths.manifest
        events_path = run_dir / artifact_paths.events
        decisions_path = run_dir / artifact_paths.decisions
        prompts_path = run_dir / artifact_paths.prompts
        metrics_path = run_dir / artifact_paths.metrics
        result_path = run_dir / artifact_paths.result
        replay_index_path = run_dir / artifact_paths.replay_index
        stderr_path = run_dir / artifact_paths.stderr

        _write_json_atomic(manifest_path, manifest_payload)
        events_path.touch()
        decisions_path.touch()
        prompts_path.touch()
        stderr_path.touch()

        metrics = MetricsSnapshot(
            match_id=match_id,
            status=ArtifactStatus.IN_PROGRESS,
            started_at=started_at_text,
            updated_at=started_at_text,
            completed_at=None,
        )
        replay_index = ReplayIndexState(
            match_id=match_id,
            artifact_paths=artifact_paths,
            created_at=started_at_text,
            updated_at=started_at_text,
        )
        result = ResultSummary(
            match_id=match_id,
            status=ArtifactStatus.IN_PROGRESS,
            started_at=started_at_text,
            completed_at=None,
            winner=None,
            end_reason=None,
            total_turns=None,
            end_tick=None,
            end_frame=None,
            replay_path=None,
            errors=(),
            metrics=metrics.to_dict(),
            artifacts=artifact_paths_to_json(artifact_paths),
            match_ended_payload=None,
            details={},
        )

        _write_json_atomic(metrics_path, metrics.to_dict())
        _write_json_atomic(result_path, result.to_dict())
        _write_json_atomic(replay_index_path, replay_index.snapshot().to_dict())

        return cls(
            match_id=match_id,
            run_dir=run_dir,
            manifest_path=manifest_path,
            events_path=events_path,
            decisions_path=decisions_path,
            prompts_path=prompts_path,
            metrics_path=metrics_path,
            result_path=result_path,
            replay_index_path=replay_index_path,
            stderr_path=stderr_path,
            started_at=started_at_text,
            _artifact_paths=artifact_paths,
            _metrics=metrics,
            _result=result,
            _replay_index=replay_index,
        )

    @property
    def metrics(self) -> MetricsSnapshot:
        return self._metrics

    @property
    def result(self) -> ResultSummary:
        return self._result

    def append_event(
        self,
        event: RawJsonObject,
        *,
        recorded_at: datetime | None = None,
    ) -> EventLogRecord:
        payload = coerce_json_object(event, context="event")
        self._validate_match_id(payload, context="event")
        record = EventLogRecord(
            recorded_at=utc_timestamp(recorded_at),
            match_id=self.match_id,
            payload=payload,
        )

        with self._lock:
            _append_jsonl(self.events_path, record.to_dict())
            event_name = _optional_string(payload.get("event"), context="event.event")
            error_delta = 1 if event_name == "Error" else 0
            self._metrics = self._metrics.with_updates(
                updated_at=record.recorded_at,
                event_delta=1,
                error_delta=error_delta,
                completed_at=self._metrics.completed_at,
            )
            self._persist_metrics()

        return record

    def append_prompt(
        self,
        *,
        prompt_text: str,
        request_payload: RawJsonObject,
        recorded_at: datetime | None = None,
        policy_id: str | None = None,
        prompt_version: str | None = None,
        provider_request: RawJsonObject | None = None,
        provider_response: RawJsonObject | None = None,
    ) -> PromptLogRecord:
        request_json = coerce_json_object(request_payload, context="request_payload")
        self._validate_match_id(request_json, context="request_payload")
        recorded_at_text = utc_timestamp(recorded_at)
        turn_id = _require_integer(
            request_json.get("turn_id"),
            context="request_payload.turn_id",
        )
        player_id = _require_string(
            request_json.get("player_id"),
            context="request_payload.player_id",
        )
        record = PromptLogRecord(
            recorded_at=recorded_at_text,
            match_id=self.match_id,
            turn_id=turn_id,
            player_id=player_id,
            prompt_text=prompt_text,
            request_payload=request_json,
            policy_id=policy_id,
            prompt_version=prompt_version,
            provider_request=(
                coerce_json_object(provider_request, context="provider_request")
                if provider_request is not None
                else None
            ),
            provider_response=(
                coerce_json_object(provider_response, context="provider_response")
                if provider_response is not None
                else None
            ),
        )

        with self._lock:
            line_number = _append_jsonl(self.prompts_path, record.to_dict())
            self._metrics = self._metrics.with_updates(
                updated_at=record.recorded_at,
                prompt_delta=1,
                completed_at=self._metrics.completed_at,
            )
            self._replay_index.record_prompt(
                turn_id=turn_id,
                player_id=player_id,
                state_hash=_require_string(
                    request_json.get("state_hash"),
                    context="request_payload.state_hash",
                ),
                legal_actions_hash=_require_string(
                    request_json.get("legal_actions_hash"),
                    context="request_payload.legal_actions_hash",
                ),
                line_number=line_number,
                updated_at=record.recorded_at,
            )
            self._persist_metrics()
            self._persist_replay_index()

        return record

    def append_decision(
        self,
        *,
        request_payload: RawJsonObject,
        decision_payload: RawJsonObject | None,
        recorded_at: datetime | None = None,
    ) -> DecisionLogRecord:
        request_json = coerce_json_object(request_payload, context="request_payload")
        self._validate_match_id(request_json, context="request_payload")
        decision_json = (
            coerce_json_object(decision_payload, context="decision_payload")
            if decision_payload is not None
            else None
        )
        if decision_json is not None:
            self._validate_match_id(decision_json, context="decision_payload")

        recorded_at_text = utc_timestamp(recorded_at)
        turn_id = _require_integer(
            request_json.get("turn_id"),
            context="request_payload.turn_id",
        )
        player_id = _require_string(
            request_json.get("player_id"),
            context="request_payload.player_id",
        )
        record = DecisionLogRecord(
            recorded_at=recorded_at_text,
            match_id=self.match_id,
            turn_id=turn_id,
            player_id=player_id,
            request_payload=request_json,
            decision_payload=decision_json,
        )

        with self._lock:
            line_number = _append_jsonl(self.decisions_path, record.to_dict())
            self._metrics = self._metrics.with_updates(
                updated_at=record.recorded_at,
                decision_delta=1,
                fallback_delta=(
                    1
                    if decision_json is not None
                    and decision_json.get("fallback_reason") is not None
                    else 0
                ),
                latency_ms=(
                    _optional_integer(
                        decision_json.get("latency_ms"),
                        context="decision_payload.latency_ms",
                    )
                    if decision_json is not None
                    else None
                ),
                tokens_in=(
                    _optional_integer(
                        decision_json.get("tokens_in"),
                        context="decision_payload.tokens_in",
                    )
                    if decision_json is not None
                    else None
                ),
                tokens_out=(
                    _optional_integer(
                        decision_json.get("tokens_out"),
                        context="decision_payload.tokens_out",
                    )
                    if decision_json is not None
                    else None
                ),
                completed_at=self._metrics.completed_at,
            )
            self._replay_index.record_decision(
                turn_id=turn_id,
                player_id=player_id,
                state_hash=_require_string(
                    request_json.get("state_hash"),
                    context="request_payload.state_hash",
                ),
                legal_actions_hash=_require_string(
                    request_json.get("legal_actions_hash"),
                    context="request_payload.legal_actions_hash",
                ),
                line_number=line_number,
                updated_at=record.recorded_at,
                action=(
                    _optional_string(
                        decision_json.get("action"),
                        context="decision_payload.action",
                    )
                    if decision_json is not None
                    else None
                ),
            )
            self._persist_metrics()
            self._persist_replay_index()

        return record

    def append_stderr(self, text: str) -> None:
        with self._lock:
            with self.stderr_path.open("a", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())

    def finalize(
        self,
        *,
        completed_at: datetime | None = None,
        match_ended: RawJsonObject | None = None,
        status: ArtifactStatus | str | None = None,
        end_reason: str | None = None,
        errors: Sequence[str] = (),
        details: Mapping[str, object] | None = None,
        replay_path: str | None = None,
    ) -> ResultSummary:
        completed_at_text = utc_timestamp(completed_at)
        match_ended_payload = (
            coerce_json_object(match_ended, context="match_ended")
            if match_ended is not None
            else None
        )
        if match_ended_payload is not None:
            self._validate_match_id(match_ended_payload, context="match_ended")

        winner = (
            _optional_string(match_ended_payload.get("winner"), context="match_ended.winner")
            if match_ended_payload is not None
            else None
        )
        final_end_reason = end_reason
        if final_end_reason is None and match_ended_payload is not None:
            final_end_reason = _require_string(
                match_ended_payload.get("end_reason"),
                context="match_ended.end_reason",
            )

        final_replay_path = replay_path
        if final_replay_path is None and match_ended_payload is not None:
            final_replay_path = _optional_string(
                match_ended_payload.get("replay_path"),
                context="match_ended.replay_path",
            )

        merged_errors: list[str] = [str(item) for item in errors]
        if match_ended_payload is not None:
            raw_errors = cast(list[object], match_ended_payload.get("errors", []))
            merged_errors.extend(
                _require_string(item, context="match_ended.errors[]") for item in raw_errors
            )
        deduped_errors = tuple(dict.fromkeys(merged_errors))

        if status is None:
            final_status = ArtifactStatus.FAILED if deduped_errors else ArtifactStatus.COMPLETED
        else:
            final_status = ArtifactStatus(status)

        with self._lock:
            self._metrics = self._metrics.with_updates(
                status=final_status,
                updated_at=completed_at_text,
                completed_at=completed_at_text,
                error_count_floor=len(deduped_errors),
            )
            self._replay_index.finalize(
                updated_at=completed_at_text,
                replay_path=final_replay_path,
            )
            self._result = ResultSummary(
                match_id=self.match_id,
                status=final_status,
                started_at=self.started_at,
                completed_at=completed_at_text,
                winner=winner,
                end_reason=final_end_reason,
                total_turns=(
                    _optional_integer(
                        match_ended_payload.get("total_turns"),
                        context="match_ended.total_turns",
                    )
                    if match_ended_payload is not None
                    else None
                ),
                end_tick=(
                    _optional_integer(
                        match_ended_payload.get("end_tick"),
                        context="match_ended.end_tick",
                    )
                    if match_ended_payload is not None
                    else None
                ),
                end_frame=(
                    _optional_integer(
                        match_ended_payload.get("end_frame"),
                        context="match_ended.end_frame",
                    )
                    if match_ended_payload is not None
                    else None
                ),
                replay_path=final_replay_path,
                errors=deduped_errors,
                metrics=self._metrics.to_dict(),
                artifacts=artifact_paths_to_json(self._artifact_paths),
                match_ended_payload=match_ended_payload,
                details=(
                    coerce_json_object(details, context="result.details")
                    if details is not None
                    else {}
                ),
            )
            self._persist_metrics()
            self._persist_replay_index()
            self._persist_result()

        return self._result

    def update_replay_path(
        self,
        replay_path: str,
        *,
        updated_at: datetime | None = None,
    ) -> ResultSummary:
        normalized_replay_path = _require_string(replay_path, context="replay_path")
        updated_at_text = utc_timestamp(updated_at)

        with self._lock:
            self._replay_index.finalize(
                updated_at=updated_at_text,
                replay_path=normalized_replay_path,
            )
            self._result = ResultSummary(
                match_id=self._result.match_id,
                status=self._result.status,
                started_at=self._result.started_at,
                completed_at=self._result.completed_at,
                winner=self._result.winner,
                end_reason=self._result.end_reason,
                total_turns=self._result.total_turns,
                end_tick=self._result.end_tick,
                end_frame=self._result.end_frame,
                replay_path=normalized_replay_path,
                errors=self._result.errors,
                metrics=self._result.metrics,
                artifacts=self._result.artifacts,
                match_ended_payload=self._result.match_ended_payload,
                details=self._result.details,
            )
            self._persist_replay_index()
            self._persist_result()

        return self._result

    def _persist_metrics(self) -> None:
        _write_json_atomic(self.metrics_path, self._metrics.to_dict())

    def _persist_replay_index(self) -> None:
        _write_json_atomic(self.replay_index_path, self._replay_index.snapshot().to_dict())

    def _persist_result(self) -> None:
        _write_json_atomic(self.result_path, self._result.to_dict())

    def _validate_match_id(self, payload: Mapping[str, object], *, context: str) -> None:
        payload_match_id = payload.get("match_id")
        if payload_match_id is None:
            return
        normalized_match_id = _require_string(payload_match_id, context=f"{context}.match_id")
        if normalized_match_id != self.match_id:
            raise ValueError(
                f"{context}.match_id {normalized_match_id!r} does not match {self.match_id!r}"
            )
