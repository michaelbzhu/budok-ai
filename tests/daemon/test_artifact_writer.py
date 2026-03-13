from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from yomi_daemon.config import parse_runtime_config_document
from yomi_daemon.logging.schemas import ArtifactStatus
from yomi_daemon.manifest import build_match_manifest
from yomi_daemon.match import MatchMetadata
from yomi_daemon.protocol import (
    ActionDecision,
    CURRENT_SCHEMA_VERSION,
    DIVector,
    DecisionExtras,
    DecisionRequest,
    DecisionType,
    Event,
    EventName,
    FighterObservation,
    FallbackReason,
    LegalAction,
    LegalActionSupports,
    MatchEnded,
    Observation,
    Vector2,
)
from yomi_daemon.storage.writer import MatchArtifactWriter


def _runtime_config() -> Any:
    return parse_runtime_config_document(
        {
            "version": "v1",
            "trace_seed": 17,
            "policy_mapping": {
                "p1": "baseline/random",
                "p2": "baseline/block_always",
            },
            "policies": {
                "baseline/random": {
                    "provider": "baseline",
                    "prompt_version": "minimal_v1",
                },
                "baseline/block_always": {
                    "provider": "baseline",
                    "prompt_version": "minimal_v1",
                },
            },
        }
    )


def _manifest(match_id: str) -> Any:
    return build_match_manifest(
        match_id=match_id,
        runtime_config=_runtime_config(),
        metadata=MatchMetadata(
            game_version="1.9.11",
            mod_version="0.2.0",
            schema_version=CURRENT_SCHEMA_VERSION,
            match_id=match_id,
        ),
        created_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
    )


def _request(
    turn_id: int, *, state_hash: str, legal_actions_hash: str
) -> DecisionRequest:
    return DecisionRequest(
        match_id="match-005",
        turn_id=turn_id,
        player_id="p1",
        deadline_ms=2500,
        state_hash=state_hash,
        legal_actions_hash=legal_actions_hash,
        decision_type=DecisionType.TURN_ACTION,
        observation=Observation(
            tick=1000 + turn_id,
            frame=80 + turn_id,
            active_player="p1",
            fighters=(
                FighterObservation(
                    id="p1",
                    character="Cowboy",
                    hp=920,
                    max_hp=1000,
                    meter=2,
                    burst=1,
                    position=Vector2(x=-10.0, y=0.0),
                    velocity=Vector2(x=1.0, y=0.0),
                    facing="right",
                    current_state="neutral",
                    combo_count=0,
                    hitstun=0,
                    hitlag=0,
                ),
                FighterObservation(
                    id="p2",
                    character="Ninja",
                    hp=870,
                    max_hp=1000,
                    meter=3,
                    burst=1,
                    position=Vector2(x=10.0, y=0.0),
                    velocity=Vector2(x=-1.0, y=0.0),
                    facing="left",
                    current_state="neutral",
                    combo_count=0,
                    hitstun=0,
                    hitlag=0,
                ),
            ),
            objects=(),
            stage={"id": "training_room"},
            history=({"turn_id": turn_id - 1, "player_id": "p2", "action": "jump"},),
        ),
        legal_actions=(
            LegalAction(
                action="slash",
                label="Slash",
                payload_spec={"target": {"type": "enemy"}},
                supports=LegalActionSupports(
                    di=True,
                    feint=False,
                    reverse=True,
                    prediction=False,
                ),
                damage=120.0,
                startup_frames=5,
                range=18.5,
                meter_cost=1,
                description="Mid-range punish.",
            ),
        ),
        trace_seed=17,
        game_version="1.9.11",
        mod_version="0.2.0",
        schema_version=CURRENT_SCHEMA_VERSION,
        ruleset_id="default-ruleset",
        prompt_version="minimal_v1",
    )


def _decision(
    turn_id: int,
    *,
    fallback_reason: FallbackReason | None = None,
    latency_ms: int = 111,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> ActionDecision:
    return ActionDecision(
        match_id="match-005",
        turn_id=turn_id,
        action="slash",
        data={"target": "enemy"},
        extra=DecisionExtras(
            di=DIVector(x=25, y=-10),
            feint=False,
            reverse=True,
            prediction=None,
        ),
        policy_id="baseline/random",
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        notes="Deterministic baseline",
        fallback_reason=fallback_reason,
    )


def _event(name: EventName, *, turn_id: int | None = None) -> Event:
    return Event(
        match_id="match-005",
        event=name,
        turn_id=turn_id,
        player_id="p1" if turn_id is not None else None,
        details={"source": "test"},
    )


def _load_json(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_match_artifact_writer_creates_full_skeleton(tmp_path: Path) -> None:
    writer = MatchArtifactWriter.create(
        match_id="match-005",
        manifest=_manifest("match-005"),
        runs_root=tmp_path,
        started_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
    )

    assert writer.run_dir.name == "20260312T120000Z_match-005"
    assert writer.manifest_path.exists()
    assert writer.events_path.exists()
    assert writer.decisions_path.exists()
    assert writer.prompts_path.exists()
    assert writer.metrics_path.exists()
    assert writer.result_path.exists()
    assert writer.replay_index_path.exists()
    assert writer.stderr_path.exists()

    assert _load_json(writer.manifest_path) == _manifest("match-005").to_dict()
    assert _load_jsonl(writer.events_path) == []
    assert _load_jsonl(writer.decisions_path) == []
    assert _load_jsonl(writer.prompts_path) == []
    assert writer.stderr_path.read_text(encoding="utf-8") == ""

    assert _load_json(writer.metrics_path) == {
        "match_id": "match-005",
        "status": "in_progress",
        "started_at": "2026-03-12T12:00:00Z",
        "updated_at": "2026-03-12T12:00:00Z",
        "completed_at": None,
        "event_count": 0,
        "prompt_count": 0,
        "decision_count": 0,
        "fallback_count": 0,
        "error_count": 0,
        "latency_sample_count": 0,
        "average_latency_ms": None,
        "total_latency_ms": 0,
        "tokens_in_total": 0,
        "tokens_out_total": 0,
    }
    assert _load_json(writer.result_path) == {
        "match_id": "match-005",
        "status": "in_progress",
        "started_at": "2026-03-12T12:00:00Z",
        "completed_at": None,
        "winner": None,
        "end_reason": None,
        "total_turns": None,
        "end_tick": None,
        "end_frame": None,
        "replay_path": None,
        "errors": [],
        "metrics": _load_json(writer.metrics_path),
        "artifacts": {
            "manifest": "manifest.json",
            "events": "events.jsonl",
            "decisions": "decisions.jsonl",
            "prompts": "prompts.jsonl",
            "metrics": "metrics.json",
            "result": "result.json",
            "replay_index": "replay_index.json",
            "stderr": "stderr.log",
        },
        "match_ended_payload": None,
        "details": {},
    }
    assert _load_json(writer.replay_index_path) == {
        "match_id": "match-005",
        "created_at": "2026-03-12T12:00:00Z",
        "updated_at": "2026-03-12T12:00:00Z",
        "replay_path": None,
        "artifacts": {
            "manifest": "manifest.json",
            "events": "events.jsonl",
            "decisions": "decisions.jsonl",
            "prompts": "prompts.jsonl",
            "metrics": "metrics.json",
            "result": "result.json",
            "replay_index": "replay_index.json",
            "stderr": "stderr.log",
        },
        "turns": [],
    }


def test_match_artifact_writer_appends_records_in_order_and_preserves_payloads(
    tmp_path: Path,
) -> None:
    writer = MatchArtifactWriter.create(
        match_id="match-005",
        manifest=_manifest("match-005"),
        runs_root=tmp_path,
        started_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
    )
    request_one = _request(7, state_hash="state-007", legal_actions_hash="legal-007")
    request_two = _request(8, state_hash="state-008", legal_actions_hash="legal-008")
    decision_one = _decision(7, latency_ms=111, tokens_in=12, tokens_out=5)
    decision_two = _decision(
        8,
        fallback_reason=FallbackReason.TIMEOUT,
        latency_ms=222,
        tokens_in=3,
        tokens_out=1,
    )

    writer.append_event(
        _event(EventName.MATCH_STARTED),
        recorded_at=datetime(2026, 3, 12, 12, 0, 1, tzinfo=UTC),
    )
    writer.append_prompt(
        prompt_text="Choose a safe punish.",
        request_payload=request_one,
        recorded_at=datetime(2026, 3, 12, 12, 0, 2, tzinfo=UTC),
        policy_id="baseline/random",
        prompt_version="minimal_v1",
        provider_request={
            "messages": [{"role": "user", "content": "Choose a safe punish."}]
        },
        provider_response={"raw": {"choice": "slash"}},
    )
    writer.append_decision(
        request_payload=request_one,
        decision_payload=decision_one,
        recorded_at=datetime(2026, 3, 12, 12, 0, 3, tzinfo=UTC),
    )
    writer.append_prompt(
        prompt_text="Fallback if needed.",
        request_payload=request_two,
        recorded_at=datetime(2026, 3, 12, 12, 0, 4, tzinfo=UTC),
        policy_id="baseline/random",
        prompt_version="minimal_v1",
    )
    writer.append_decision(
        request_payload=request_two,
        decision_payload=decision_two,
        recorded_at=datetime(2026, 3, 12, 12, 0, 5, tzinfo=UTC),
    )
    writer.append_event(
        _event(EventName.DECISION_FALLBACK, turn_id=8),
        recorded_at=datetime(2026, 3, 12, 12, 0, 6, tzinfo=UTC),
    )

    events = _load_jsonl(writer.events_path)
    prompts = _load_jsonl(writer.prompts_path)
    decisions = _load_jsonl(writer.decisions_path)
    metrics = _load_json(writer.metrics_path)
    replay_index = _load_json(writer.replay_index_path)

    assert events == [
        {
            "recorded_at": "2026-03-12T12:00:01Z",
            "match_id": "match-005",
            "payload": _event(EventName.MATCH_STARTED).to_dict(),
        },
        {
            "recorded_at": "2026-03-12T12:00:06Z",
            "match_id": "match-005",
            "payload": _event(EventName.DECISION_FALLBACK, turn_id=8).to_dict(),
        },
    ]
    assert prompts == [
        {
            "recorded_at": "2026-03-12T12:00:02Z",
            "match_id": "match-005",
            "turn_id": 7,
            "player_id": "p1",
            "prompt_text": "Choose a safe punish.",
            "request_payload": request_one.to_dict(),
            "policy_id": "baseline/random",
            "prompt_version": "minimal_v1",
            "provider_request": {
                "messages": [{"role": "user", "content": "Choose a safe punish."}]
            },
            "provider_response": {"raw": {"choice": "slash"}},
        },
        {
            "recorded_at": "2026-03-12T12:00:04Z",
            "match_id": "match-005",
            "turn_id": 8,
            "player_id": "p1",
            "prompt_text": "Fallback if needed.",
            "request_payload": request_two.to_dict(),
            "policy_id": "baseline/random",
            "prompt_version": "minimal_v1",
            "provider_request": None,
            "provider_response": None,
        },
    ]
    assert decisions == [
        {
            "recorded_at": "2026-03-12T12:00:03Z",
            "match_id": "match-005",
            "turn_id": 7,
            "player_id": "p1",
            "request_payload": request_one.to_dict(),
            "decision_payload": decision_one.to_dict(),
        },
        {
            "recorded_at": "2026-03-12T12:00:05Z",
            "match_id": "match-005",
            "turn_id": 8,
            "player_id": "p1",
            "request_payload": request_two.to_dict(),
            "decision_payload": decision_two.to_dict(),
        },
    ]
    assert metrics == {
        "match_id": "match-005",
        "status": "in_progress",
        "started_at": "2026-03-12T12:00:00Z",
        "updated_at": "2026-03-12T12:00:06Z",
        "completed_at": None,
        "event_count": 2,
        "prompt_count": 2,
        "decision_count": 2,
        "fallback_count": 1,
        "error_count": 0,
        "latency_sample_count": 2,
        "average_latency_ms": 166.5,
        "total_latency_ms": 333,
        "tokens_in_total": 15,
        "tokens_out_total": 6,
    }
    assert replay_index == {
        "match_id": "match-005",
        "created_at": "2026-03-12T12:00:00Z",
        "updated_at": "2026-03-12T12:00:05Z",
        "replay_path": None,
        "artifacts": {
            "manifest": "manifest.json",
            "events": "events.jsonl",
            "decisions": "decisions.jsonl",
            "prompts": "prompts.jsonl",
            "metrics": "metrics.json",
            "result": "result.json",
            "replay_index": "replay_index.json",
            "stderr": "stderr.log",
        },
        "turns": [
            {
                "turn_id": 7,
                "player_id": "p1",
                "state_hash": "state-007",
                "legal_actions_hash": "legal-007",
                "prompt_line": 1,
                "decision_line": 1,
                "action": "slash",
            },
            {
                "turn_id": 8,
                "player_id": "p1",
                "state_hash": "state-008",
                "legal_actions_hash": "legal-008",
                "prompt_line": 2,
                "decision_line": 2,
                "action": "slash",
            },
        ],
    }


def test_match_artifact_writer_finalizes_success_and_failure(tmp_path: Path) -> None:
    writer = MatchArtifactWriter.create(
        match_id="match-005",
        manifest=_manifest("match-005"),
        runs_root=tmp_path,
        started_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
    )
    request = _request(7, state_hash="state-007", legal_actions_hash="legal-007")
    decision = _decision(7, latency_ms=120, tokens_in=8, tokens_out=4)
    writer.append_decision(
        request_payload=request,
        decision_payload=decision,
        recorded_at=datetime(2026, 3, 12, 12, 0, 3, tzinfo=UTC),
    )
    result = writer.finalize(
        completed_at=datetime(2026, 3, 12, 12, 0, 10, tzinfo=UTC),
        match_ended=MatchEnded(
            match_id="match-005",
            winner="p1",
            end_reason="ko",
            total_turns=12,
            end_tick=5150,
            end_frame=129,
            replay_path="runs/20260312T120000Z_match-005/replay.yomi",
            errors=(),
        ),
    )

    assert result.status is ArtifactStatus.COMPLETED
    assert _load_json(writer.metrics_path) == {
        "match_id": "match-005",
        "status": "completed",
        "started_at": "2026-03-12T12:00:00Z",
        "updated_at": "2026-03-12T12:00:10Z",
        "completed_at": "2026-03-12T12:00:10Z",
        "event_count": 0,
        "prompt_count": 0,
        "decision_count": 1,
        "fallback_count": 0,
        "error_count": 0,
        "latency_sample_count": 1,
        "average_latency_ms": 120.0,
        "total_latency_ms": 120,
        "tokens_in_total": 8,
        "tokens_out_total": 4,
    }
    assert _load_json(writer.result_path) == {
        "match_id": "match-005",
        "status": "completed",
        "started_at": "2026-03-12T12:00:00Z",
        "completed_at": "2026-03-12T12:00:10Z",
        "winner": "p1",
        "end_reason": "ko",
        "total_turns": 12,
        "end_tick": 5150,
        "end_frame": 129,
        "replay_path": "runs/20260312T120000Z_match-005/replay.yomi",
        "errors": [],
        "metrics": _load_json(writer.metrics_path),
        "artifacts": {
            "manifest": "manifest.json",
            "events": "events.jsonl",
            "decisions": "decisions.jsonl",
            "prompts": "prompts.jsonl",
            "metrics": "metrics.json",
            "result": "result.json",
            "replay_index": "replay_index.json",
            "stderr": "stderr.log",
        },
        "match_ended_payload": {
            "match_id": "match-005",
            "winner": "p1",
            "end_reason": "ko",
            "total_turns": 12,
            "end_tick": 5150,
            "end_frame": 129,
            "replay_path": "runs/20260312T120000Z_match-005/replay.yomi",
            "errors": [],
        },
        "details": {},
    }
    assert _load_json(writer.replay_index_path)["replay_path"] == (
        "runs/20260312T120000Z_match-005/replay.yomi"
    )

    failed_writer = MatchArtifactWriter.create(
        match_id="match-005",
        manifest=_manifest("match-005"),
        runs_root=tmp_path / "failed",
        started_at=datetime(2026, 3, 12, 13, 0, tzinfo=UTC),
    )
    failed_writer.append_event(
        _event(EventName.ERROR),
        recorded_at=datetime(2026, 3, 12, 13, 0, 5, tzinfo=UTC),
    )
    failed_result = failed_writer.finalize(
        completed_at=datetime(2026, 3, 12, 13, 0, 8, tzinfo=UTC),
        status=ArtifactStatus.FAILED,
        end_reason="disconnect",
        errors=("socket closed",),
        details={"recovered": False},
    )

    assert failed_result.status is ArtifactStatus.FAILED
    assert _load_json(failed_writer.metrics_path) == {
        "match_id": "match-005",
        "status": "failed",
        "started_at": "2026-03-12T13:00:00Z",
        "updated_at": "2026-03-12T13:00:08Z",
        "completed_at": "2026-03-12T13:00:08Z",
        "event_count": 1,
        "prompt_count": 0,
        "decision_count": 0,
        "fallback_count": 0,
        "error_count": 1,
        "latency_sample_count": 0,
        "average_latency_ms": None,
        "total_latency_ms": 0,
        "tokens_in_total": 0,
        "tokens_out_total": 0,
    }
    assert _load_json(failed_writer.result_path) == {
        "match_id": "match-005",
        "status": "failed",
        "started_at": "2026-03-12T13:00:00Z",
        "completed_at": "2026-03-12T13:00:08Z",
        "winner": None,
        "end_reason": "disconnect",
        "total_turns": None,
        "end_tick": None,
        "end_frame": None,
        "replay_path": None,
        "errors": ["socket closed"],
        "metrics": _load_json(failed_writer.metrics_path),
        "artifacts": {
            "manifest": "manifest.json",
            "events": "events.jsonl",
            "decisions": "decisions.jsonl",
            "prompts": "prompts.jsonl",
            "metrics": "metrics.json",
            "result": "result.json",
            "replay_index": "replay_index.json",
            "stderr": "stderr.log",
        },
        "match_ended_payload": None,
        "details": {"recovered": False},
    }
