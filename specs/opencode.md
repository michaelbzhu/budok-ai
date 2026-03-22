# Budok-AI Harness Mod Spec

## 1) Purpose

Define a production-quality mod + external harness interface for running deterministic LLM-vs-LLM matches in **Your Only Move Is HUSTLE**.

This spec covers:
- Mod behavior and lifecycle
- IPC contract between game and daemon
- JSON schemas (observation/action/events/config)
- Repo structure and module ownership
- Reliability, reproducibility, and tournament requirements

---

## 2) Goals and Non-Goals

### Goals
- Expose a stable, typed API from the game at decision points.
- Let external agents (LLMs or baselines) return valid actions.
- Enforce legality and strict timeout handling in mod-side gatekeeping.
- Produce replayable, auditable logs for every match.
- Support scalable automated tournaments.

### Non-Goals
- Replacing the game engine with a standalone simulator (phase 2+ only).
- Bypassing game legality checks with synthetic actions.
- Public matchmaking integration in MVP.

---

## 3) High-Level Architecture

```text
YOMI Game Process
  └─ Opencode Bridge Mod
       ├─ Hooks actionable turn events
       ├─ Builds observation + legal action set
       ├─ Sends request to localhost daemon
       ├─ Receives decision
       ├─ Validates legality + timing
       ├─ Injects queued action/extra + lock-in
       └─ Emits telemetry events

Local Daemon Process
  ├─ Session/match orchestration
  ├─ Policy adapters (LLM A, LLM B, baselines)
  ├─ Schema validation + fallback policies
  ├─ Tournament scheduler + rating engine
  └─ Log/replay/artifact writer
```

Transport (MVP): `localhost` WebSocket
- Mod is client, daemon is server.
- Single persistent connection per game process.

---

## 4) Mod Spec

## 4.1 Mod Identity
- Name: `OpencodeBridge`
- Entrypoint: `ModMain.gd`
- Required files: `_metadata`, `ModMain.gd`
- Optional dependency: Mod Options menu library for in-game config UI

## 4.2 Runtime Responsibilities
1. Detect current game + players + match state.
2. Subscribe to turn-actionable signal.
3. Build `DecisionRequest`:
   - Full observation snapshot (compact, deterministic)
   - Legal actions and payload constraints
   - Deadline metadata
4. Send request to daemon and wait.
5. Validate returned `ActionDecision`:
   - Schema-valid
   - Action legal in current state
   - Payload legal for action
6. Apply decision to queued fields (`action`, `data`, `extra`), then lock in.
7. Emit `DecisionApplied` or `DecisionFallback` event to daemon.

## 4.3 Turn Deadline and Fallback
- Configurable timeout, default `2500ms`.
- If timeout/invalid decision/disconnect:
  - Apply fallback policy in this order:
    1. `safe_continue`
    2. `heuristic_guard` (optional)
    3. `last_valid_replayable` (optional)
- Always log fallback reason.

## 4.4 Determinism and Traceability
- Each match has immutable `match_id` and `trace_seed`.
- Each turn has `turn_id` + `state_hash` + `legal_actions_hash`.
- Every applied decision records:
  - request hash
  - response hash
  - latency
  - legality verdict

## 4.5 Safety Rules
- Only connect to `127.0.0.1` / `::1` by default.
- Reject responses for stale `turn_id`.
- Never execute arbitrary scripts from daemon payload.
- Do not allow daemon to bypass legality checks.

---

## 5) Daemon Spec

## 5.1 Responsibilities
- Host IPC server.
- Route each `DecisionRequest` to the correct policy.
- Enforce strict schema validation for request/response.
- Track per-model latency and token usage.
- Persist logs/artifacts.
- Run tournaments and compute ratings.

## 5.2 Policy Adapter Contract

```ts
interface PolicyAdapter {
  id: string;
  decide(request: DecisionRequest): Promise<ActionDecision>;
}
```

Adapters:
- `openai/*`
- `anthropic/*`
- `local/*`
- `baseline/*` (random, greedy, scripted)

---

## 6) JSON Schemas

All schemas are Draft 2020-12.

## 6.1 Envelope

```json
{
  "$id": "https://opencode.yomi/schemas/envelope.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["type", "version", "ts", "payload"],
  "properties": {
    "type": {"type": "string"},
    "version": {"type": "string", "pattern": "^v[0-9]+$"},
    "ts": {"type": "string", "format": "date-time"},
    "payload": {"type": "object"}
  },
  "additionalProperties": false
}
```

## 6.2 DecisionRequest

```json
{
  "$id": "https://opencode.yomi/schemas/decision-request.v1.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": [
    "match_id",
    "turn_id",
    "player_id",
    "deadline_ms",
    "observation",
    "legal_actions",
    "state_hash"
  ],
  "properties": {
    "match_id": {"type": "string", "minLength": 8},
    "turn_id": {"type": "integer", "minimum": 0},
    "player_id": {"type": "integer", "minimum": 1},
    "deadline_ms": {"type": "integer", "minimum": 50, "maximum": 30000},
    "state_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
    "legal_actions_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
    "observation": {"$ref": "#/definitions/Observation"},
    "legal_actions": {
      "type": "array",
      "minItems": 1,
      "items": {"$ref": "#/definitions/LegalAction"}
    },
    "meta": {
      "type": "object",
      "properties": {
        "game_version": {"type": "string"},
        "mod_version": {"type": "string"},
        "ruleset_id": {"type": "string"},
        "seed": {"type": "integer"}
      },
      "additionalProperties": false
    }
  },
  "definitions": {
    "Vec2": {
      "type": "object",
      "required": ["x", "y"],
      "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"}
      },
      "additionalProperties": false
    },
    "DI": {
      "type": "object",
      "required": ["x", "y"],
      "properties": {
        "x": {"type": "integer", "minimum": -100, "maximum": 100},
        "y": {"type": "integer", "minimum": -100, "maximum": 100}
      },
      "additionalProperties": false
    },
    "FighterState": {
      "type": "object",
      "required": [
        "id",
        "name",
        "hp",
        "meter",
        "burst",
        "position",
        "velocity",
        "facing",
        "combo_count",
        "current_state"
      ],
      "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "hp": {"type": "number"},
        "meter": {"type": "number"},
        "burst": {"type": "integer"},
        "position": {"$ref": "#/definitions/Vec2"},
        "velocity": {"$ref": "#/definitions/Vec2"},
        "facing": {"type": "integer", "enum": [-1, 1]},
        "combo_count": {"type": "integer", "minimum": 0},
        "current_state": {"type": "string"},
        "hitstun": {"type": "integer", "minimum": 0},
        "hitlag": {"type": "integer", "minimum": 0}
      },
      "additionalProperties": false
    },
    "Observation": {
      "type": "object",
      "required": ["tick", "frame", "active_player", "fighters"],
      "properties": {
        "tick": {"type": "integer", "minimum": 0},
        "frame": {"type": "integer", "minimum": 0},
        "active_player": {"type": "integer", "minimum": 1},
        "fighters": {
          "type": "array",
          "minItems": 2,
          "items": {"$ref": "#/definitions/FighterState"}
        },
        "global_flags": {
          "type": "object",
          "properties": {
            "multihustle": {"type": "boolean"}
          },
          "additionalProperties": false
        }
      },
      "additionalProperties": false
    },
    "DataFieldSpec": {
      "type": "object",
      "required": ["name", "kind"],
      "properties": {
        "name": {"type": "string"},
        "kind": {
          "type": "string",
          "enum": ["bool", "int", "float", "enum", "vec2", "object"]
        },
        "required": {"type": "boolean", "default": true},
        "enum_values": {"type": "array", "items": {"type": "string"}},
        "min": {"type": "number"},
        "max": {"type": "number"}
      },
      "additionalProperties": false
    },
    "LegalAction": {
      "type": "object",
      "required": ["action", "payload_spec", "supports"],
      "properties": {
        "action": {"type": "string"},
        "payload_spec": {
          "type": "array",
          "items": {"$ref": "#/definitions/DataFieldSpec"}
        },
        "supports": {
          "type": "object",
          "required": ["di", "feint", "reverse"],
          "properties": {
            "di": {"type": "boolean"},
            "feint": {"type": "boolean"},
            "reverse": {"type": "boolean"}
          },
          "additionalProperties": false
        }
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false
}
```

## 6.3 ActionDecision

```json
{
  "$id": "https://opencode.yomi/schemas/action-decision.v1.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["match_id", "turn_id", "action", "data", "extra"],
  "properties": {
    "match_id": {"type": "string"},
    "turn_id": {"type": "integer", "minimum": 0},
    "action": {"type": "string"},
    "data": {
      "description": "Action payload. Null if action has no payload.",
      "type": ["object", "null"]
    },
    "extra": {
      "type": "object",
      "required": ["di", "feint", "reverse"],
      "properties": {
        "di": {
          "type": "object",
          "required": ["x", "y"],
          "properties": {
            "x": {"type": "integer", "minimum": -100, "maximum": 100},
            "y": {"type": "integer", "minimum": -100, "maximum": 100}
          },
          "additionalProperties": false
        },
        "feint": {"type": "boolean"},
        "reverse": {"type": "boolean"}
      },
      "additionalProperties": false
    },
    "debug": {
      "type": "object",
      "properties": {
        "policy_id": {"type": "string"},
        "latency_ms": {"type": "number"},
        "tokens_in": {"type": "integer", "minimum": 0},
        "tokens_out": {"type": "integer", "minimum": 0},
        "notes": {"type": "string"}
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false
}
```

## 6.4 Event (telemetry)

```json
{
  "$id": "https://opencode.yomi/schemas/event.v1.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["event", "match_id", "turn_id", "ts", "payload"],
  "properties": {
    "event": {
      "type": "string",
      "enum": [
        "MatchStarted",
        "TurnRequested",
        "DecisionReceived",
        "DecisionApplied",
        "DecisionFallback",
        "MatchEnded",
        "Error"
      ]
    },
    "match_id": {"type": "string"},
    "turn_id": {"type": "integer", "minimum": 0},
    "ts": {"type": "string", "format": "date-time"},
    "payload": {"type": "object"}
  },
  "additionalProperties": false
}
```

## 6.5 Config

```json
{
  "$id": "https://opencode.yomi/schemas/config.v1.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["ipc", "timeouts", "fallback", "logging"],
  "properties": {
    "ipc": {
      "type": "object",
      "required": ["host", "port", "protocol"],
      "properties": {
        "host": {"type": "string", "default": "127.0.0.1"},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535, "default": 8765},
        "protocol": {"type": "string", "enum": ["ws", "http"], "default": "ws"}
      },
      "additionalProperties": false
    },
    "timeouts": {
      "type": "object",
      "required": ["decision_ms"],
      "properties": {
        "decision_ms": {"type": "integer", "minimum": 50, "maximum": 30000, "default": 2500}
      },
      "additionalProperties": false
    },
    "fallback": {
      "type": "object",
      "required": ["mode"],
      "properties": {
        "mode": {
          "type": "string",
          "enum": ["safe_continue", "heuristic_guard", "last_valid_replayable"]
        }
      },
      "additionalProperties": false
    },
    "logging": {
      "type": "object",
      "required": ["jsonl", "replay_index"],
      "properties": {
        "jsonl": {"type": "boolean", "default": true},
        "replay_index": {"type": "boolean", "default": true}
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false
}
```

---

## 7) Message Flow

## 7.1 Handshake
1. Mod connects to daemon.
2. Mod sends `Hello` with game/mod version and schema version.
3. Daemon returns `HelloAck` with accepted versions and policy mapping.

## 7.2 Turn Loop
1. Mod emits `TurnRequested` + `DecisionRequest`.
2. Daemon asks policy adapter for action.
3. Daemon returns `ActionDecision`.
4. Mod validates and applies.
5. Mod emits `DecisionApplied` or `DecisionFallback`.

## 7.3 Match End
- Mod emits `MatchEnded` with winner, end tick, replay pointer, errors.

---

## 8) Repo Structure

```text
yomi-opencode/
  README.md
  LICENSE
  docs/
    architecture.md
    protocol.md
    operations.md

  schemas/
    envelope.json
    decision-request.v1.json
    action-decision.v1.json
    event.v1.json
    config.v1.json

  mod/
    OpencodeBridge/
      _metadata
      ModMain.gd
      bridge/
        BridgeClient.gd
        TurnHook.gd
        ObservationBuilder.gd
        LegalActionBuilder.gd
        DecisionValidator.gd
        ActionApplier.gd
        Telemetry.gd
      ui/
        ModOptions.gd
      config/
        default_config.json

  daemon/
    pyproject.toml
    src/opencode_daemon/
      __init__.py
      server.py
      protocol.py
      validation.py
      orchestrator.py
      fallback.py
      adapters/
        base.py
        openai_adapter.py
        anthropic_adapter.py
        local_adapter.py
        baseline_adapter.py
      tournament/
        scheduler.py
        ratings.py
      storage/
        writer.py
        replay_index.py
    tests/
      test_protocol.py
      test_schema_validation.py
      test_fallbacks.py

  prompts/
    policy_v1.md
    policy_v2.md

  runs/
    .gitkeep

  scripts/
    run_local_match.sh
    run_round_robin.sh
    package_mod.sh

  .github/workflows/
    ci.yaml
```

---

## 9) Validation and Testing

## 9.1 Unit Tests
- Schema validation for all message types.
- Decision legality validation against synthetic legal-action sets.
- Fallback behavior on timeout/invalid payload.

## 9.2 Integration Tests
- Local mock daemon + live mod request/response loop.
- Simulate disconnect during decision.
- Simulate stale `turn_id` response.

## 9.3 Determinism Tests
- Same seed + same policy + same build => identical decision logs.
- Hash mismatches fail test.

## 9.4 Performance Budgets (MVP)
- p95 decision round-trip <= `1200ms`
- Max timeout fallback rate <= `2%` over 100 games
- Zero crashes caused by malformed daemon payloads

---

## 10) Logging and Artifacts

Per match directory:

```text
runs/<timestamp>_<match_id>/
  manifest.json
  events.jsonl
  decisions.jsonl
  metrics.json
  replay_index.json
  stderr.log
```

`manifest.json` includes:
- game build/version
- mod hash
- daemon commit hash
- adapter/model IDs
- prompt version
- config snapshot

---

## 11) Tournament Spec

- Initial format: double round-robin with side swap.
- Character control:
  - MVP: base cast only
  - Expansion: curated modded cast packs with pinned versions
- Stage control: fixed stage set
- Rating: Elo (MVP), optional Glicko2 later
- Minimum games for report: 50 per policy pair

---

## 12) Failure Modes and Mitigations

- **Daemon unavailable**: local fallback policy, log `Error` + `DecisionFallback`.
- **Invalid model output**: strict parser, reject and fallback.
- **Stale response**: compare `turn_id`, reject.
- **Mod conflicts**: compatibility allowlist and startup warnings.
- **High latency**: adaptive timeout tiers or cheap baseline fallback.
- **Game update breakage**: CI smoke test matrix with pinned builds/mod versions.

---

## 13) Implementation Milestones

## M1: Protocol + Skeleton
- Define schemas, handshake, message bus.
- Basic mod->daemon loop with static legal action mock.

## M2: Real Turn Hook
- Build true observation and legal action extraction.
- Apply real decisions with legality checks.

## M3: Reliability
- Timeout/fallback, reconnection, structured telemetry.
- Integration tests and crash hardening.

## M4: Tournament Runner
- Round-robin, side swaps, rating output.
- Artifact packaging and summary reports.

---

## 14) Acceptance Criteria (MVP)

- Runs 100 local matches headlessly/supervised without manual intervention.
- Produces complete per-turn decision logs.
- Fallback rate < 2% under normal model latency.
- No invalid actions ever applied to game state.
- Replay index and manifest generated for every match.
