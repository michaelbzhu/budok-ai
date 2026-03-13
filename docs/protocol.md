# Protocol

Versioned transport schemas live under `schemas/`, and the daemon-side typed models live in `daemon/src/yomi_daemon/protocol.py`.

The live bridge contract is now `v2`. `v1` schema files remain in the repository only as historical references.

## Envelope

Every live daemon/mod message is wrapped in the common `v2` envelope:

```json
{
  "type": "decision_request",
  "version": "v2",
  "ts": "2026-03-12T00:00:00Z",
  "payload": {}
}
```

- `type` selects the payload schema.
- `version` is the negotiated protocol version for the envelope and payload family.
- `ts` is an RFC 3339 / ISO 8601 UTC timestamp.
- `payload` contains the message-specific body.

The `v2` message-type enum is:

- `hello`
- `hello_ack`
- `decision_request`
- `action_decision`
- `event`
- `match_ended`
- `config`

## Payloads

The live schema set is:

- `schemas/hello.v2.json`
- `schemas/hello-ack.v2.json`
- `schemas/decision-request.v2.json`
- `schemas/action-decision.v2.json`
- `schemas/event.v2.json`
- `schemas/match-ended.v2.json`
- `schemas/config.v2.json`
- `schemas/envelope.v2.json`

### Handshake

`Hello` carries the mod build metadata and the list of supported protocol versions:

- `game_version`
- `mod_version`
- `schema_version`
- `supported_protocol_versions`

`HelloAck` confirms the negotiated version and pins the daemon-side match mapping:

- `accepted_protocol_version`
- `accepted_schema_version`
- `daemon_version`
- `policy_mapping`
- optional `config` snapshot

### Decision Request

`DecisionRequest` is the canonical turn input to a policy. The required fields are:

- `match_id`
- `turn_id`
- `player_id`
- `deadline_ms`
- `state_hash`
- `legal_actions_hash`
- `decision_type`
- `observation`
- `legal_actions`

The `decision_type` enum is intentionally narrow in `v2`: `turn_action`.

Recommended metadata fields are optional but versioned in-schema already:

- `trace_seed`
- `game_version`
- `mod_version`
- `schema_version`
- `ruleset_id`
- `prompt_version`

`observation` is deterministic and compact. The schema requires the shared fields from the spec:

- `tick`
- `frame`
- `active_player`
- `fighters`
- `objects`
- `stage`
- `history`

Each fighter entry is typed with the common strategic fields from the spec, including position, velocity, meter, burst, and current state.

Each `legal_actions` entry includes:

- `action`
- optional `label`
- `payload_spec`
- optional `prediction_spec`
- optional `payload_schema`
- `supports.di`
- `supports.feint`
- `supports.reverse`
- `supports.prediction`
- optional tactical metadata such as `damage`, `startup_frames`, `range`, `meter_cost`, and `description`

`payload_spec` is now a structured object-schema contract, not a flat widget-tag map. Actions with payload parameters use the JSON-schema-like shape below:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["target"],
  "properties": {
    "target": {
      "type": "string",
      "enum": ["enemy", "self"],
      "ui_kind": "enum",
      "semantic_hint": "throw_target"
    }
  }
}
```

Field descriptors may include `type`, `enum`/`choices`, `minimum`, `maximum`, `default`, `required`, `properties`, `items`, `ui_kind`, and `semantic_hint`. This keeps slider, enum, checkbox, directional, and XY payloads expressible without leaking raw Godot widget classes into the wire format.

### Action Decision

`ActionDecision` is the structured response returned by an adapter or daemon fallback path. Required fields:

- `match_id`
- `turn_id`
- `action`
- `data`
- `extra`

`extra.di` is a percentage-int vector bounded to `[-100, 100]` on both axes. `extra.feint` and `extra.reverse` are explicit booleans even when false. `extra.prediction` is a structured object when supported, and `null` otherwise.

The default prediction-extra contract is:

```json
{
  "horizon": 1,
  "opponent_action": "",
  "confidence": "medium"
}
```

`prediction_spec` on the corresponding legal action can narrow or restate that contract for a specific move, but unsupported prediction payloads are rejected before action application.

Optional debug metadata is schema-recognized:

- `policy_id`
- `latency_ms`
- `tokens_in`
- `tokens_out`
- `reasoning`
- `notes`
- `fallback_reason`

The fallback-reason enum is:

- `timeout`
- `disconnect`
- `malformed_output`
- `illegal_output`
- `stale_response`

### Events And Match End

`Event` is the shared telemetry payload. The standard event enum is:

- `MatchStarted`
- `TurnRequested`
- `DecisionReceived`
- `DecisionApplied`
- `DecisionFallback`
- `MatchEnded`
- `Error`

`MatchEnded` carries the final match summary fields called out in the spec:

- `match_id`
- `winner`
- `end_reason`
- `total_turns`
- `end_tick`
- `end_frame`
- optional `replay_path`
- `errors`

### Config Snapshot

`config.v2.json` is the wire-safe config snapshot used during handshake and run pinning. It is intentionally narrower than later daemon file-loading concerns and currently includes:

- `timeout_profile`
- `decision_timeout_ms`
- `fallback_mode`
- `logging`
- `policy_mapping`
- `character_selection`
- optional `stage_id`

The `v2` enums here align with the unified spec:

- timeout profiles: `strict_local`, `llm_tournament`
- fallback modes: `safe_continue`, `heuristic_guard`, `last_valid_replayable`
- character modes: `mirror`, `assigned`, `random_from_pool`

## Compatibility Policy

Protocol versioning is append-only.

- Non-breaking additions to an existing version may add optional fields or new enum members only when older readers can ignore them safely.
- Breaking changes require a new protocol version and a new schema file version.
- The mod proposes `supported_protocol_versions` during `Hello`.
- The daemon must respond with exactly one accepted version in `HelloAck`.
- `v2` validators reject unknown or stale envelope versions rather than silently coercing them.

## Envelope-Only Runtime Contract

After `hello` / `hello_ack`, live traffic must always use the canonical envelope shape.

- Bare `decision_request` payloads are obsolete and rejected on the live daemon path.
- Bare `action_decision` payloads are obsolete and rejected on the live mod path.
- `event` and `match_ended` messages use the same `v2` envelope contract as turn requests and decisions.

## Canonical Serialization And Hashing

`state_hash` and `legal_actions_hash` are SHA-256 digests of canonical JSON.

- Objects are serialized with lexicographically sorted keys at every nesting level.
- Arrays preserve input order.
- JSON is compact: no insignificant whitespace.
- Strings are UTF-8 encoded before hashing.
- The daemon reference implementation is `canonical_json()` / `canonical_sha256()` in `daemon/src/yomi_daemon/protocol.py`.
- The mod mirrors the same rules in `mod/YomiLLMBridge/bridge/ProtocolCodec.gd`.

## Validation Boundaries

Schema validation and legality validation are separate on purpose.

- Schema validation checks structure, enum values, scalar bounds, and timestamp formatting.
- Daemon-side validation ensures a payload is well-formed enough to route, log, or reject safely.
- Mod-side legality validation remains authoritative for live action application.

Examples:

- A schema-valid `ActionDecision` can still be illegal if `action` is no longer present in the current legal set.
- A schema-valid `DecisionRequest` can still represent stale game state if `turn_id` no longer matches the live turn when the response returns.

That split is deliberate: the protocol layer owns structural correctness, while the game remains the authority on legality.
