"""End-to-end integration tests for single-match orchestration (WU-012)."""

from __future__ import annotations

import asyncio
import json

from typing import Any, cast

from websockets.asyncio.client import connect

from yomi_daemon.protocol import (
    ActionDecision,
    EventName,
    MessageType,
)
from yomi_daemon.storage.writer import RUNS_DIR
from yomi_daemon.validation import parse_envelope

from tests.daemon.conftest import (
    baseline_runtime_config,
    decision_request_envelope,
    handshake,
    hello_envelope,
    match_ended_envelope,
    running_match_server,
    unique_match_id,
)

_INTEGRATION = True

# Re-export for files that still import from here
_baseline_runtime_config = baseline_runtime_config
_hello_envelope = hello_envelope
_handshake = handshake
_match_ended_envelope = match_ended_envelope


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_full_match_baseline_vs_baseline() -> None:
    """Run a complete multi-turn match with baseline policies and verify artifacts."""

    async def scenario() -> None:
        mid = unique_match_id()
        config = baseline_runtime_config()
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                ack = await handshake(ws)
                assert ack.policy_mapping.p1 == "baseline/random"

                responses = []
                for turn in range(1, 6):
                    player = "p1" if turn % 2 == 1 else "p2"
                    req = decision_request_envelope(
                        match_id=mid, turn_id=turn, player_id=player
                    )
                    await ws.send(json.dumps(req))
                    raw_resp = await ws.recv()
                    resp_envelope = parse_envelope(json.loads(raw_resp))
                    assert resp_envelope.type is MessageType.ACTION_DECISION
                    responses.append(resp_envelope.payload)

                assert len(responses) == 5
                for resp in responses:
                    assert resp.action in {"block", "attack_a", "move_forward"}

                await ws.send(
                    json.dumps(match_ended_envelope(match_id=mid, total_turns=5))
                )

        await asyncio.sleep(0.1)

    asyncio.run(scenario())


def test_disconnect_during_match_finalizes_artifacts() -> None:
    """Verify artifacts are finalized when client disconnects mid-match."""

    async def scenario() -> None:
        mid = unique_match_id()
        config = baseline_runtime_config()
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await handshake(ws)

                await ws.send(
                    json.dumps(
                        decision_request_envelope(
                            match_id=mid, turn_id=1, player_id="p1"
                        )
                    )
                )
                raw_resp = await ws.recv()
                resp = parse_envelope(json.loads(raw_resp))
                assert resp.type is MessageType.ACTION_DECISION

            await asyncio.sleep(0.2)

    asyncio.run(scenario())


def test_invalid_json_during_match_does_not_crash() -> None:
    """Malformed JSON in the match loop should be logged and skipped."""

    async def scenario() -> None:
        mid = unique_match_id()
        config = baseline_runtime_config()
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await handshake(ws)

                await ws.send("not valid json{{{")

                await ws.send(
                    json.dumps(
                        decision_request_envelope(
                            match_id=mid, turn_id=1, player_id="p1"
                        )
                    )
                )
                raw_resp = await ws.recv()
                resp = parse_envelope(json.loads(raw_resp))
                assert resp.type is MessageType.ACTION_DECISION

                await ws.send(
                    json.dumps(match_ended_envelope(match_id=mid, total_turns=1))
                )

    asyncio.run(scenario())


def test_invalid_envelope_during_match_does_not_crash() -> None:
    """Invalid envelope structure in the match loop should be logged and skipped."""

    async def scenario() -> None:
        from yomi_daemon.protocol import CURRENT_PROTOCOL_VERSION

        mid = unique_match_id()
        config = baseline_runtime_config()
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await handshake(ws)

                await ws.send(
                    json.dumps(
                        {
                            "type": "bogus",
                            "version": CURRENT_PROTOCOL_VERSION.value,
                            "ts": "now",
                        }
                    )
                )

                await ws.send(
                    json.dumps(
                        decision_request_envelope(
                            match_id=mid, turn_id=1, player_id="p2"
                        )
                    )
                )
                raw_resp = await ws.recv()
                resp = parse_envelope(json.loads(raw_resp))
                assert resp.type is MessageType.ACTION_DECISION

                await ws.send(
                    json.dumps(match_ended_envelope(match_id=mid, total_turns=1))
                )

    asyncio.run(scenario())


def test_bare_decision_request_is_rejected_but_enveloped_request_succeeds() -> None:
    """Bare turn payloads are ignored after handshake; only v2 envelopes are routed."""

    async def scenario() -> None:
        mid = unique_match_id()
        config = baseline_runtime_config()
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await handshake(ws)

                bare_payload = decision_request_envelope(
                    match_id=mid, turn_id=1, player_id="p1"
                )["payload"]
                await ws.send(json.dumps(bare_payload))

                try:
                    await asyncio.wait_for(ws.recv(), timeout=0.05)
                except TimeoutError:
                    pass
                else:
                    raise AssertionError(
                        "Bare decision_request payload should not receive a response"
                    )

                await ws.send(
                    json.dumps(
                        decision_request_envelope(
                            match_id=mid, turn_id=2, player_id="p1"
                        )
                    )
                )
                raw_resp = await ws.recv()
                resp = parse_envelope(json.loads(raw_resp))
                assert resp.type is MessageType.ACTION_DECISION

                await ws.send(
                    json.dumps(match_ended_envelope(match_id=mid, total_turns=2))
                )

    asyncio.run(scenario())


def test_artifact_directory_created_for_match() -> None:
    """Verify that the match loop creates an artifact directory under runs/."""

    mid = unique_match_id()

    async def scenario() -> Any:
        config = baseline_runtime_config()
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await handshake(ws)

                await ws.send(
                    json.dumps(
                        decision_request_envelope(
                            match_id=mid, turn_id=1, player_id="p1"
                        )
                    )
                )
                await ws.recv()
                await ws.send(
                    json.dumps(match_ended_envelope(match_id=mid, total_turns=1))
                )

            await asyncio.sleep(0.2)

        run_dirs = [d for d in RUNS_DIR.iterdir() if d.is_dir() and mid in d.name]
        assert run_dirs, f"Expected a run directory containing {mid}"
        return run_dirs[0]

    latest = asyncio.run(scenario())

    assert (latest / "manifest.json").exists()
    assert (latest / "events.jsonl").exists()
    assert (latest / "decisions.jsonl").exists()
    assert (latest / "metrics.json").exists()
    assert (latest / "result.json").exists()
    assert (latest / "replay_index.json").exists()
    assert (latest / "stderr.log").exists()

    result = json.loads((latest / "result.json").read_text())
    assert result["winner"] == "p1"
    assert result["end_reason"] == "ko"
    assert result["total_turns"] == 1
    assert result["status"] == "completed"

    decisions = (latest / "decisions.jsonl").read_text().strip().splitlines()
    assert len(decisions) == 1
    decision_record = json.loads(decisions[0])
    assert "request_payload" in decision_record
    assert "decision_payload" in decision_record

    events = (latest / "events.jsonl").read_text().strip().splitlines()
    assert len(events) >= 3
    event_names = [json.loads(e)["payload"]["event"] for e in events]
    assert EventName.MATCH_STARTED.value in event_names
    assert EventName.TURN_REQUESTED.value in event_names
    assert EventName.DECISION_RECEIVED.value in event_names

    manifest = json.loads((latest / "manifest.json").read_text())
    assert manifest["trace_seed"] == 42
    assert manifest["policy_mapping"]["p1"] == "baseline/random"


def test_both_players_routed_to_correct_policies() -> None:
    """p1 and p2 requests should route to their respective configured policies."""

    async def scenario() -> None:
        mid = unique_match_id()
        config = baseline_runtime_config(
            p1="baseline/random",
            p2="baseline/block_always",
        )
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await handshake(ws)

                for turn in range(1, 4):
                    req = decision_request_envelope(
                        match_id=mid, turn_id=turn, player_id="p2"
                    )
                    await ws.send(json.dumps(req))
                    raw = await ws.recv()
                    resp = parse_envelope(json.loads(raw))
                    decision = cast(ActionDecision, resp.payload)
                    assert decision.action == "block", (
                        f"block_always policy should pick block, got {decision.action}"
                    )

                await ws.send(
                    json.dumps(match_ended_envelope(match_id=mid, total_turns=3))
                )

    asyncio.run(scenario())


def test_event_forwarded_to_artifacts() -> None:
    """Events sent by the mod should be recorded in the artifact event log."""

    mid = unique_match_id()

    async def scenario() -> Any:
        from yomi_daemon.protocol import CURRENT_PROTOCOL_VERSION

        config = baseline_runtime_config()
        async with running_match_server(config) as server:
            async with connect(f"ws://127.0.0.1:{server.listening_port}") as ws:
                await handshake(ws)

                await ws.send(
                    json.dumps(
                        decision_request_envelope(
                            match_id=mid, turn_id=1, player_id="p1"
                        )
                    )
                )
                await ws.recv()

                event_envelope = {
                    "type": MessageType.EVENT.value,
                    "version": CURRENT_PROTOCOL_VERSION.value,
                    "ts": "2026-03-12T00:00:05Z",
                    "payload": {
                        "match_id": mid,
                        "event": EventName.DECISION_APPLIED.value,
                        "turn_id": 1,
                        "player_id": "p1",
                        "details": {"action": "block"},
                    },
                }
                await ws.send(json.dumps(event_envelope))

                await asyncio.sleep(0.05)

                await ws.send(
                    json.dumps(match_ended_envelope(match_id=mid, total_turns=1))
                )

            await asyncio.sleep(0.2)

        run_dirs = [d for d in RUNS_DIR.iterdir() if d.is_dir() and mid in d.name]
        return run_dirs[0]

    latest = asyncio.run(scenario())
    events = (latest / "events.jsonl").read_text().strip().splitlines()
    event_names = [json.loads(e)["payload"]["event"] for e in events]
    assert EventName.DECISION_APPLIED.value in event_names
