# YOMI Hustle LLM Arena - Unified Specification

## 1. Project Overview

### Purpose
Build a production-quality local system that allows LLMs and scripted policies to play **Your Only Move Is HUSTLE** autonomously, safely, and repeatably.

The system combines:
- a Godot mod running inside YOMI Hustle
- a local daemon that routes decisions to model providers or baselines
- a schema-defined IPC protocol between game and daemon
- a tournament runner that automates repeated matches and rating/report generation

### Why YOMI Hustle
YOMI Hustle is unusually well suited for LLM agents because it is a simultaneous-turn, perfect-information fighting game.

This means:
- the game waits for decisions instead of rewarding the fastest API
- structured state can be extracted from the engine directly through mods
- the core gameplay loop rewards prediction, adaptation, and opponent modeling
- legality can be enforced by the game before any LLM output is applied

### Primary Outcomes
- Run a single autonomous match between any two configured policies
- Run reproducible tournaments across many models and baselines
- Log every turn in an auditable, replayable format
- Provide a stable experimental platform for prompt engineering and strategic evaluation

---

## 2. Goals and Non-Goals

### Goals
- Expose a stable, typed API from the game at decision points
- Allow external policies to return legal, structured actions
- Enforce legality, stale-turn rejection, and timeout handling in the mod
- Produce deterministic, traceable match artifacts
- Support LLM providers, local models, and scripted baselines behind one adapter interface
- Support scalable automated tournaments with ratings and summary reports

### Non-Goals
- Replacing the game engine with a standalone simulator in MVP
- Bypassing in-game legality checks with synthetic or privileged actions
- Online matchmaking or remote daemon control in MVP
- Solving optimal simultaneous-move strategy in the initial release

---

## 3. Prior Art and Inputs

The project is informed by:
- existing YOMI Hustle AI mods such as Goon AI and `_AIOpponents`
- character and scripting mods that demonstrate broad state access in-game
- LLM-vs-game projects such as LLM Colosseum
- Godot decompilation and modding workflows used by the YOMI modding community

These references establish that:
- game scripts can be overridden via the mod loader
- move interception and legal-move enumeration are feasible
- programmatic move injection is feasible
- external decision systems can be coupled to the game via local IPC

---

## 4. High-Level Architecture

```text
YOMI Hustle Game Process
  └─ LLM Bridge Mod
       ├─ Hooks decision points
       ├─ Builds observation snapshot
       ├─ Enumerates legal actions
       ├─ Sends DecisionRequest to localhost daemon
       ├─ Receives ActionDecision
       ├─ Validates legality and turn freshness
       ├─ Injects action and extras into the live game
       └─ Emits telemetry events

Local Daemon Process
  ├─ Hosts IPC server
  ├─ Maintains match/session state
  ├─ Routes requests to policy adapters
  ├─ Performs response parsing and schema validation
  ├─ Applies daemon-side fallback/retry policy
  ├─ Writes logs and match artifacts
  └─ Runs tournaments and rating updates
```

### Transport
- MVP transport: `localhost` WebSocket
- Mod is the client
- Daemon is the server
- One persistent connection per game process

### Design Principles
- The game remains the authority on legal state and legal actions
- The daemon remains the authority on policy routing, logging, and orchestration
- The protocol is explicit, versioned, and schema validated
- All applied decisions must be auditable after the fact

---

## 5. Runtime Behavior

### Lifecycle
1. The daemon starts and listens on localhost.
2. The game launches with the bridge mod enabled.
3. The mod connects to the daemon and performs a versioned handshake.
4. The daemon assigns policy IDs to player slots for the upcoming match.
5. At each decision point, the mod sends a typed decision request.
6. The daemon obtains a decision from the configured policy.
7. The mod validates and applies the decision or falls back.
8. The game resolves the turn.
9. The mod emits telemetry for the turn result.
10. At match end, the daemon finalizes artifacts and updates any ratings or summaries.

### Decision Flow
At every actionable turn, the bridge mod must collect:
- current match and turn identifiers
- active player identifier
- observation snapshot of both fighters and relevant world state
- legal actions for the current player
- decision deadline metadata
- state and legality hashes for traceability

The daemon must:
- map the request to the correct policy adapter
- construct the policy input
- collect and parse the response
- validate the structured response against the request
- return a typed decision or an explicit fallback decision

The mod must then:
- reject stale, malformed, or illegal responses
- apply the action to the game's queued decision fields
- lock in the choice through the game path used by normal play
- emit `DecisionApplied` or `DecisionFallback`

---

## 6. Mod Specification

### Identity
- Mod name: `YomiLLMBridge`
- Entrypoint: `ModMain.gd`
- Required files: `_metadata`, `ModMain.gd`

### Responsibilities
The mod is responsible for:
1. Detecting match state and actionable decision points
2. Building deterministic observation snapshots
3. Enumerating legal actions and action payload constraints
4. Sending requests to the daemon over WebSocket
5. Validating returned decisions against live game legality
6. Injecting valid actions into the game's native decision pipeline
7. Falling back safely on timeout, disconnect, or invalid response
8. Emitting telemetry events for all important lifecycle actions

### Safety Rules
- Only connect to `127.0.0.1` or `::1` by default
- Never execute arbitrary scripts or code from daemon payloads
- Never allow daemon responses to bypass legality checks
- Reject responses with stale or mismatched `match_id` or `turn_id`
- Reject actions or extras not present in the current legal set

### Turn Timeout and Fallback
Timeouts must be configuration driven.

Two standard profiles are defined:
- `strict_local`: default `2500ms`, used for protocol testing and fast baseline runs
- `llm_tournament`: default `10000ms`, configurable up to `15000ms` for remote/provider-backed models

Fallback policy is layered:
1. `safe_continue`
2. `heuristic_guard` if available
3. `last_valid_replayable` if enabled and legal

Fallback behavior must always:
- preserve legality
- be logged with a reason code
- record whether the daemon timed out, disconnected, or returned invalid output

### Determinism and Traceability
Each match must have:
- immutable `match_id`
- `trace_seed`
- pinned policy IDs
- pinned game/mod/schema versions

Each request should include:
- `state_hash`
- `legal_actions_hash`
- request timestamp and deadline

Each applied decision should record:
- response hash
- latency
- legality verdict
- fallback reason when applicable

---

## 7. Daemon Specification

### Responsibilities
The daemon is responsible for:
- hosting the IPC server
- managing sessions and match configuration
- routing requests to policy adapters
- formatting policy inputs and parsing outputs
- performing schema validation and retry/correction behavior
- writing artifacts and metrics
- orchestrating single matches and tournaments

### Policy Adapter Contract

```python
class PolicyAdapter(Protocol):
    @property
    def id(self) -> str:
        ...

    async def decide(self, request: "DecisionRequest") -> "ActionDecision":
        ...
```

### Supported Adapter Categories
- `anthropic/*`
- `openai/*`
- `google/*`
- `local/*`
- `ollama/*`
- `baseline/*`

### Baseline Policies
At minimum, the daemon should ship with:
- `baseline/random`
- `baseline/block_always`
- `baseline/greedy_damage`
- `baseline/scripted_safe`

### Retry and Parsing Policy
For LLM-backed adapters:
- parse structured output first when provider supports it
- otherwise extract the first valid action representation from the response
- if invalid, optionally retry once with a correction prompt
- if still invalid or late, emit a fallback decision

The daemon may assist with validation, but the mod remains the final legality gate.

---

## 8. Protocol and Message Model

### Protocol Principles
- All messages are versioned
- All core messages are JSON schema validated
- The protocol is append-only by version; breaking changes require a new version
- The mod and daemon must negotiate protocol compatibility during handshake

### Message Types
Core message families:
- `Hello`
- `HelloAck`
- `DecisionRequest`
- `ActionDecision`
- telemetry `Event`
- `MatchEnded`
- optional daemon control/config messages

### Envelope
All daemon-mod messages should support a common envelope:

```json
{
  "type": "decision_request",
  "version": "v1",
  "ts": "2026-03-12T00:00:00Z",
  "payload": {}
}
```

The payload contains the message-specific schema.

---

## 9. Canonical Data Schemas

This unified spec adopts the stricter schema-first approach while preserving the richer gameplay context from the broader design.

### 9.1 DecisionRequest
Required fields:
- `match_id`
- `turn_id`
- `player_id`
- `deadline_ms`
- `state_hash`
- `legal_actions_hash`
- `decision_type`
- `observation`
- `legal_actions`

Recommended metadata:
- `trace_seed`
- `game_version`
- `mod_version`
- `schema_version`
- `ruleset_id`
- `prompt_version`

### 9.2 Observation
Observation should be compact, deterministic, and sufficient for strategic play.

Minimum contents:
- `tick`
- `frame`
- `active_player`
- `fighters`
- `objects` for active projectiles or other relevant entities
- `stage`
- `history` slice for recent turns

Each fighter should include at least:
- `id`
- `character`
- `hp`
- `max_hp`
- `meter`
- `burst`
- `position`
- `velocity`
- `facing`
- `current_state`
- `combo_count`
- `hitstun`
- `hitlag`

### 9.3 LegalAction
The unified action model is structured rather than move-name-only.

Each legal action should include:
- `action`: canonical action ID
- `label`: human-readable name if useful
- `payload_spec`: structured object-schema description of action-specific payload fields, including requiredness, numeric bounds, enum choices, defaults, and semantic hints when known
- `supports.di`: boolean
- `supports.feint`: boolean
- `supports.reverse`: boolean
- `supports.prediction`: boolean
- optional `prediction_spec`: structured contract for prediction extras when the action supports them
- optional tactical metadata such as `damage`, `startup_frames`, `range`, `meter_cost`, `description`

This allows the system to support both simple actions and actions with move-specific parameters.

### 9.4 ActionDecision
Required fields:
- `match_id`
- `turn_id`
- `action`
- `data`
- `extra`

Where:
- `action` is the selected legal action ID
- `data` is the action payload object or `null`
- `extra.di` is a bounded vector when supported
- `extra.feint` is boolean
- `extra.reverse` is boolean
- `extra.prediction` is either `null` or a structured prediction object validated against the legal action contract

Optional debug fields:
- `policy_id`
- `latency_ms`
- `tokens_in`
- `tokens_out`
- `reasoning` or `notes`

### 9.5 Event
Standard telemetry events should include:
- `MatchStarted`
- `TurnRequested`
- `DecisionReceived`
- `DecisionApplied`
- `DecisionFallback`
- `MatchEnded`
- `Error`

### 9.6 Match Result
Each completed match should produce a result artifact containing:
- winner and end reason
- policy IDs and characters for both players
- final HP and aggregate damage metrics
- fallback counts
- average latency
- total turns and duration
- token and cost totals where available
- pointers to decision logs and replay index

---

## 10. Handshake and Message Flow

### Handshake
1. Mod connects to daemon.
2. Mod sends `Hello` with:
   - game version
   - mod version
   - schema version
   - supported protocol versions
3. Daemon returns `HelloAck` with:
   - accepted schema/protocol version
   - daemon version
   - policy mapping for player slots
   - optional match configuration snapshot

### Turn Loop
1. Mod emits `TurnRequested` and sends `DecisionRequest`.
2. Daemon routes the request to the configured adapter.
3. Adapter returns an `ActionDecision` or the daemon constructs a fallback decision.
4. Mod validates the response.
5. Mod applies the action or uses fallback.
6. Mod emits `DecisionApplied` or `DecisionFallback`.
7. Game resolves the turn and advances.

### Match End
At end of match, the mod emits `MatchEnded` with:
- `match_id`
- winner
- end reason
- total turns
- end tick/frame
- replay pointer if available
- errors encountered during match

---

## 11. Prompting and Policy Input

Prompting is not part of the mod-daemon wire contract, but it is a first-class part of the research and evaluation layer.

### Prompting Goals
- convey enough state for strategic decision-making
- preserve deterministic formatting for auditing
- make legal actions explicit
- support opponent modeling and history when desired

### Prompt Variants
The daemon should support multiple prompt templates, including:
- `minimal`
- `strategic`
- `few_shot`
- `reasoning_enabled`
- `character_specific`

### Recommended Prompt Contents
- player identity and character
- current fighter and opponent state
- distance, meter, burst, and state
- recent move history
- optional opponent tendency summary
- full legal actions list with compact metadata
- strict output instructions targeting the canonical structured action format

### Output Strategy
Preferred order:
1. provider-native structured output / tool output
2. JSON response matching the action schema
3. constrained text fallback that is reparsed into the action schema

---

## 12. Tournament System

### Supported Match Modes
- single match
- side-swapped pair
- round robin
- double round robin
- optional swiss or elimination in later versions

### Recommended MVP Tournament Policy
- mirror matches first to reduce character-balance confounds
- side swap enabled
- fixed stage
- 10 games per pair for smoke and iteration
- 50 or more games per pair for published reports

### Rating System
- MVP rating: Elo
- optional later upgrade: Glicko-2
- ratings should be accompanied by raw win rates and match counts

### Tournament Runner Responsibilities
- generate scheduled pairings
- configure policy assignments
- start or attach to game instances
- collect match-end artifacts
- update ratings and aggregate reports
- enforce cooldowns or concurrency limits for provider rate limits

---

## 13. Logging, Artifacts, and Reproducibility

### Match Artifact Directory
Each match should write a directory under `runs/` such as:

```text
runs/<timestamp>_<match_id>/
  manifest.json
  events.jsonl
  decisions.jsonl
  prompts.jsonl
  metrics.json
  result.json
  replay_index.json
  stderr.log
```

### Required Manifest Fields
- game build/version
- mod version and mod hash if available
- daemon commit hash or version
- schema/protocol version
- policy IDs and model/provider IDs
- prompt version
- config snapshot
- trace seed

### Reproducibility Requirements
- log the exact request payload sent for each turn
- log the exact decision payload returned by daemon
- record hashes for state and legal actions
- pin versions wherever possible
- separate reproducible metadata from non-deterministic provider behavior

---

## 14. Validation and Testing

### Unit Tests
- schema validation for all message types
- decision validation against synthetic legal action sets
- fallback selection logic
- response parsing logic
- ratings calculations

### Integration Tests
- mock daemon with live mod request/response loop
- timeout during decision
- daemon disconnect during match
- stale `turn_id` response
- malformed payload handling

### Determinism Tests
- same build, same seed, same baseline policy => identical decision logs
- hash mismatches fail tests
- artifact manifests must capture enough metadata to explain non-determinism for provider-backed runs

### Performance Budgets
Two reference budgets are defined:
- `strict_local`: p95 round-trip <= `1200ms`, fallback rate <= `2%` over 100 games
- `llm_tournament`: timeout configurable, target fallback rate < `5%` under expected provider latency

### Reliability Requirements
- malformed daemon payloads must never crash the mod
- invalid actions must never be applied to live game state
- disconnects must degrade into safe fallback rather than deadlock

---

## 15. Repository Structure

```text
yomi-ai/
  specs/
    claude_spec.md
    opencode.md
    unified_spec.md

  docs/
    architecture.md
    protocol.md
    game_internals.md
    prompt_engineering.md
    operations.md

  schemas/
    envelope.json
    decision-request.v1.json
    action-decision.v1.json
    event.v1.json
    config.v1.json

  mod/
    YomiLLMBridge/
      _metadata
      ModMain.gd
      bridge/
        BridgeClient.gd
        TurnHook.gd
        ObservationBuilder.gd
        LegalActionBuilder.gd
        DecisionValidator.gd
        ActionApplier.gd
        FallbackHandler.gd
        Telemetry.gd
      ui/
        ModOptions.gd
      config/
        default_config.json

  daemon/
    pyproject.toml
    src/yomi_daemon/
      __init__.py
      cli.py
      server.py
      protocol.py
      validation.py
      prompt.py
      response_parser.py
      orchestrator.py
      fallback.py
      match.py
      adapters/
        base.py
        anthropic.py
        openai.py
        google.py
        ollama.py
        local.py
        baseline.py
      tournament/
        runner.py
        scheduler.py
        ratings.py
        reporter.py
      storage/
        writer.py
        replay_index.py
      logging/
        schemas.py

  prompts/
    minimal_v1.md
    strategic_v1.md
    few_shot_v1.md
    reasoning_v1.md

  runs/
    .gitkeep

  scripts/
    decompile.sh
    run_local_match.sh
    run_round_robin.sh
    package_mod.sh

  tests/
    fixtures/

  .github/workflows/
    ci.yaml

  README.md
  LICENSE
```

---

## 16. Configuration Model

### Core Config Areas
- IPC transport and host/port
- timeout profile
- fallback mode
- logging and prompt persistence
- policy assignments
- tournament format
- character and stage selection

### Timeout Defaults
- `strict_local.decision_ms = 2500`
- `llm_tournament.decision_ms = 10000`

### Character Selection Modes
- `mirror`
- `assigned`
- `random_from_pool`

### Policy Configuration
Each configured policy should include:
- logical policy ID
- provider
- model
- temperature or provider-specific generation controls
- prompt template version
- optional provider credentials via environment variables

---

## 17. Implementation Plan

### Phase 1: Game Mapping
- decompile and inspect the game
- identify move selection hooks and legal action generation
- document live game internals and mod insertion points

### Phase 2: Bridge Mod MVP
- build handshake and WebSocket client
- construct observation and legal action builders
- validate and apply decisions in the live game
- implement fallback behavior and telemetry

### Phase 3: Daemon MVP
- define schemas and validation
- implement server, adapters, and response parsing
- support one LLM adapter and baseline adapters
- write per-match artifacts

### Phase 4: End-to-End Match Runner
- run autonomous head-to-head matches
- verify legality, logging, and resilience
- tune timeout and fallback policies

### Phase 5: Tournament and Reporting
- implement scheduler and ratings
- add side swaps and mirror-match workflows
- generate summaries, leaderboards, and matchup reports

### Phase 6: Advanced Research Features
- opponent modeling summaries in prompts
- multimodal screenshot augmentation
- multi-game memory
- replay export improvements
- hybrid LLM plus simulation or game-theoretic policies

---

## 18. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Game updates break hooks | Mod stops functioning | Pin supported game versions and maintain smoke tests |
| Interception point is harder than expected | Delays mod MVP | Study existing AI mods and decompiled code paths first |
| LLM outputs invalid actions | High fallback rate | Strict schema validation, correction retry, mod-side legality gate |
| Provider latency is high | Match slowdown or fallback spikes | Use timeout profiles, per-provider tuning, and baseline fallbacks |
| Mod crashes on bad payloads | Unusable system | Defensive parsing and malformed-payload tests |
| Reproducibility is weak | Hard to compare runs | Hash state/action sets and persist manifests/config snapshots |
| API cost is too high | Limited tournament scale | Track token and cost usage, support cheaper baselines and local models |

---

## 19. Design Decisions

### Why a Mod Instead of Screen Capture
- structured state is accurate and deterministic
- legality remains grounded in the actual game engine
- existing modding patterns show this is feasible

### Why WebSocket Instead of Per-Turn HTTP
- persistent low-overhead localhost connection
- cleaner lifecycle management
- straightforward support for telemetry and control messages

### Why the Mod Is the Client
- the daemon can start first and wait for connections
- orchestration stays outside the game process
- future multi-instance or tournament scaling is easier

### Why Structured Actions Instead of Move-ID-Only Responses
- some actions may require payloads or parameterization
- legality becomes easier to specify and validate generically
- the protocol remains future-proof as support deepens

### Why Elo for MVP
- simple and interpretable
- sufficient for initial comparisons
- can be extended later without redesigning the match pipeline

---

## 20. Success Criteria

### MVP
- Two configured policies can complete a full match with no manual intervention
- No invalid action is ever applied to the live game state
- Match logs include complete per-turn request/decision records
- Fallback behavior handles timeout, disconnect, and invalid output safely
- At least one LLM policy consistently beats a random baseline

### V1 Tournament
- Automated side-swapped round robin runs without manual intervention
- Ratings, win rates, and matchup summaries are generated from artifacts
- Cost and latency are tracked per policy
- Runs are auditable via manifests, hashes, and decision logs

### Stretch Goals
- measurable strategic adaptation to opponent tendencies
- competitive performance against existing scripted YOMI bots
- support for multiple characters and curated modded rosters
- multimodal or hybrid decision systems

---

## 21. How a User Runs a Match

### Single Match
1. Install the bridge mod into YOMI Hustle.
2. Configure daemon policies, for example:
   - P1: `anthropic/claude-sonnet`
   - P2: `openai/gpt-4o`
3. Start the daemon with a match config.
4. Launch the game with the mod enabled, or launch through a helper script.
5. The daemon and mod handshake.
6. The match plays autonomously until completion.
7. Inspect the match directory under `runs/`.

### Tournament
1. Prepare a tournament config with policy list, format, timeouts, and character mode.
2. Start the tournament runner.
3. The runner schedules matches, assigns policies, collects artifacts, and writes reports.

---

## 22. Acceptance Criteria for This Unified Spec

This unified spec is considered successful if implementation can proceed with no unresolved ambiguity in these areas:
- mod vs daemon responsibilities
- canonical action/request/response format
- timeout and fallback model
- handshake and lifecycle
- artifact and reproducibility requirements
- repository/module ownership boundaries

Normative choices made by this document:
- transport is localhost WebSocket for MVP
- mod is client, daemon is server
- action format is structured: `action` + `data` + `extra`
- legality is always enforced mod-side before application
- protocol is schema-first and versioned
- timeout behavior is profile-driven rather than globally fixed
