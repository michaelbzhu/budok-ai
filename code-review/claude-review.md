# budok-ai Comprehensive Code Review

**Date:** 2026-03-13
**Reviewer:** Claude Opus 4.6
**Scope:** All source code in `daemon/`, `mod/`, `schemas/`, `prompts/`, `scripts/`, `tests/`. Excludes `specs/` and `docs/`.
**Test suite:** 267 tests, all passing. Ruff check clean.

---

## Executive Summary

budok-ai is a well-engineered system for connecting LLMs to the turn-based fighting game YOMI Hustle (Your Only Move Is Hustle). The architecture — a Godot mod communicating over WebSocket to a Python daemon that orchestrates LLM calls — is sound and appropriate for the game's simultaneous-turn structure.

**However, the system cannot currently run an actual LLM-vs-LLM match in the live game.** While the individual components (protocol, daemon, adapters, fallback engine, artifact storage, tournament system) are thoroughly built and tested, several integration gaps and a critical protocol mismatch prevent end-to-end operation. The project is roughly **85% complete** for a functional MVP.

---

## Architecture Assessment

### What Works Well

1. **Dual validation** — Daemon validates advisory; mod validates with final authority. This defense-in-depth ensures no illegal action reaches the game engine regardless of daemon bugs.

2. **Schema-first protocol** — 9 JSON Schema files (Draft 2020-12) define the wire contract. Typed Python dataclasses mirror the schemas. Validation is enforced at every boundary.

3. **Graceful degradation** — Three-tier fallback chain (`last_valid_replayable` → `heuristic_guard` → `safe_continue`) implemented in both Python and GDScript, ensuring the game never deadlocks waiting for an LLM.

4. **Auditable artifacts** — Every turn's full request, prompt, decision, and telemetry events persist to `runs/` directories with atomic writes and replay indices.

5. **Provider abstraction** — Anthropic, OpenAI, and OpenRouter adapters share a common interface. Four deterministic baselines (random, block-always, greedy-damage, scripted-safe) enable testing without API keys.

6. **Tournament infrastructure** — Elo ratings, round-robin scheduling, side-swapped pairings, and report generation are complete.

### Architecture Diagram

```
Game (Godot 3.5.1 / GDScript)
  └─ YomiLLMBridge mod (.pck)
       ├─ ModMain.gd          ─ loads config, connects bridge
       ├─ BridgeClient.gd     ─ WebSocket state machine
       ├─ TurnHook.gd         ─ intercepts player_actionable signal
       ├─ ObservationBuilder   ─ extracts game state
       ├─ LegalActionBuilder   ─ enumerates legal moves
       ├─ DecisionValidator    ─ final legality check
       ├─ ActionApplier        ─ writes queued_action/data/extra
       ├─ FallbackHandler      ─ GDScript fallback mirror
       └─ Telemetry            ─ event emission
              │
              │ WebSocket (ws://127.0.0.1:8765)
              ▼
       Python Daemon
       ├─ DaemonServer        ─ handshake + match loop
       ├─ Orchestrator         ─ timeout/error wrapping
       ├─ PolicyAdapter        ─ LLM API calls
       ├─ Prompt Renderer      ─ template + observation + legal actions
       ├─ Response Parser      ─ JSON extraction + correction retry
       ├─ Fallback Engine      ─ daemon-side fallback
       ├─ Validation           ─ schema + request-relative checks
       ├─ Artifact Writer      ─ JSONL persistence
       └─ Tournament Runner    ─ multi-match orchestration
```

---

## Critical Bugs

### 1. DI Range Mismatch Between Prompt Schema and Protocol (SEVERITY: HIGH)

The LLM-facing output schema in `prompt.py:114-116` constrains DI to:
```json
"x": {"type": "integer", "minimum": -1, "maximum": 1}
"y": {"type": "integer", "minimum": -1, "maximum": 1}
```

But the actual protocol (`schemas/action-decision.v1.json`, `DecisionValidator.gd:7-8`) uses **-100 to 100** percentage integers, matching the game's native DI system.

**Impact:** LLMs will only ever produce DI values of -1, 0, or 1. While these won't fail validation (they're within [-100, 100]), this eliminates 99.97% of the DI space. The LLM effectively cannot use directional influence — a core defensive mechanic — making it dramatically weaker in combos and on defense.

**Location:** `daemon/src/yomi_daemon/prompt.py:114-116`

### 2. TurnHook Sends Bare Decision Requests Without Envelope Wrapping (SEVERITY: HIGH)

`TurnHook.gd:99` calls:
```gdscript
_bridge_client._send_json_message(decision_request)
```

This sends a raw `DecisionRequest` dict. But `server.py:213-215` calls `parse_envelope(raw_data)` which requires the standard envelope wrapper `{type, version, ts, payload}`.

**Impact:** Every decision request from the mod will fail envelope validation on the daemon side with `ProtocolValidationError`, meaning **no turns will ever be processed in a live game session**. The daemon will log warnings and skip every message.

**Location:** `mod/YomiLLMBridge/bridge/TurnHook.gd:99` — needs to wrap the decision request in an envelope like `BridgeClient.gd:92-102` does for the hello message.

### 3. TurnHook Sends Telemetry Events Without Envelope Wrapping (SEVERITY: MEDIUM)

The `Telemetry.gd` likely has the same issue — events sent as bare payloads won't parse on the daemon. Without reading `Telemetry.gd` in full, this follows from the same pattern: `_send_json_message` on bare dicts.

---

## Functional Gaps

### 4. No Game-Version Lock or Runtime Verification

The mod relies on specific engine script paths (`res://game.gd`, `P1ActionButtons`, `P2ActionButtons`), fighter property names (`hp`, `super_meter`, `bursts_available`, etc.), and signal names (`player_actionable`). There is no runtime check that these exist.

**Risk:** If the game updates (currently v1.9.20), any renamed property or restructured scene tree will cause silent failures or crashes. The `ObservationBuilder` uses `"X" in fighter` guards for character-specific data, but core fields like `hp`, `get_pos()`, `state_interruptable` have no such guards.

**Recommendation:** Add a lightweight runtime compatibility check in `ModMain.gd` that verifies critical paths exist before attaching the turn hook.

### 5. History Field Is Always Empty

`ObservationBuilder.gd:16` hardcodes `"history": []`. The LLM receives zero context about prior turns.

**Impact:** Without turn history, the LLM cannot:
- Recognize patterns in opponent behavior
- Adapt strategy based on what worked/failed
- Understand the flow of a combo or neutral exchange
- Make predictions (the game rewards correct `prediction` in extras)

This is the single largest strategic handicap the LLM faces. Even a 3-5 turn sliding window of `{action, result, damage_dealt, state_after}` would substantially improve decision quality.

### 6. Prediction Extra Not Exposed

YOMI Hustle awards super meter for correct predictions (`PREDICTION_CORRECT_SUPER_GAIN = 30`). The game's `extra` dict supports a `prediction` field. Neither the prompt, legal action builder, nor the protocol models expose this.

**Impact:** LLMs cannot earn prediction bonuses, putting them at a resource disadvantage against any player (human or AI) that uses predictions.

### 7. Character-Specific Extras Not Exposed to LLM

Several characters have unique extra parameters that affect gameplay:
- **Ninja:** `explode` (trigger sticky bomb), `pull` (grappling hook), `release`/`release_dir`/`store` (momentum system)
- **Mutant:** `juke_dir` (directional juke), `spike_enabled`
- **Robot:** Various unique parameters

`LegalActionBuilder.gd` builds `payload_spec` from UI data nodes but only classifies them by widget type (`slider`, `option`, `check`, `8way`, `xy_plot`). The LLM sees:
```json
{"Block Height": "option", "Melee Parry Timing": "slider"}
```
but gets no semantic description of what these mean, what the valid ranges are, or how they affect gameplay.

**Impact:** The LLM must guess at payload semantics. It will likely submit incorrect or suboptimal payload data for complex moves, triggering fallbacks to simpler actions.

### 8. No Mod Packaging/Installation Automation

`scripts/package_mod.sh` exists but there's no documented process for:
- Building the `.pck` file
- Installing it into the game's `user://mods/` directory
- Verifying the mod loads correctly
- Testing against the actual game binary

The mod has **never been tested against the live game** as far as the codebase indicates. All mod tests use Python-side harnesses that simulate the GDScript environment.

### 9. Observation Missing Key Game State

The `ObservationBuilder` captures position, HP, meter, burst, facing, current state, combo count, hitstun, and hitlag. Missing:

| Field | Importance | Why |
|---|---|---|
| `air_movements_left` | High | Determines aerial options remaining |
| `feints` remaining | High | Core resource for mixups |
| `initiative` | High | Affects startup frames and strategy |
| `is_grounded` | Medium | Determines available action categories |
| `penalty` / sadness | Medium | Affects meter gain, indicates repetitive play |
| `wakeup_throw_immunity` | Medium | Affects oki decisions |
| `combo_proration` | Medium | Determines combo viability |
| `blockstun_ticks` vs actual hitstun | Medium | `hitstun` field reads `blockstun_ticks` which is only relevant during block — not the same as actual hitstun |

### 10. `hitstun` Field Reads Wrong Property

`ObservationBuilder.gd:35`:
```gdscript
"hitstun": int(fighter.blockstun_ticks),
```

This reads `blockstun_ticks`, which counts remaining blockstun frames. This is **not** hitstun — hitstun is tracked per-state on the `CharacterHurtState`. The field name is misleading and the value is only meaningful when the fighter is blocking.

---

## Code Quality Issues

### 11. Hitlag Read May Crash

`ObservationBuilder.gd:36`:
```gdscript
"hitlag": int(fighter.hitlag_ticks),
```

`hitlag_ticks` is a property on `CharacterState`, not on `Fighter`. The code reads it from the fighter directly, which may fail depending on how the game exposes it. If it's not a direct fighter property, this will produce a runtime error in GDScript.

### 12. Object Type Classification Is Unreliable

`ObservationBuilder.gd:123`:
```gdscript
"type": str(obj.get_class()),
```

`get_class()` in Godot returns the base engine class name (e.g., `"Node2D"`, `"Area2D"`), not the game-specific class name. Projectiles, bombs, orbs, etc. will all report their engine base class, not their gameplay identity. The LLM will see `"Node2D"` for everything, losing critical information about what objects are on screen.

Should use `obj.get_script().resource_path` or a custom identification method.

### 13. UUID Generation Has Bias

`TurnHook.gd:308-321`: The UUID v4 generator calls `randomize()` on every invocation (reseeding the RNG from system time), which is both unnecessary and slightly biases the output. More importantly, the algorithm hardcodes position 12 as `"4"` (correct) and position 16 as `8|9|a|b` (correct), but the loop indices don't account for the hyphens, so the variant/version bits may land in wrong positions.

This is cosmetic — match IDs don't need to be RFC-compliant — but it's worth noting.

### 14. State Hash Determinism Is Not Cross-Language

The mod hashes observations using GDScript's `to_json()`, while the daemon uses Python's `json.dumps()`. These serializers produce different key ordering and whitespace, so `state_hash` values from the mod will never match what the daemon would compute for the same logical state. The codebase acknowledges this ("accepted by design") but it means hash-based deduplication or replay matching across the wire boundary is impossible.

---

## Testing Gaps

### 15. No Live Game Integration Tests

All 267 tests run in Python. The mod GDScript has **zero in-game tests**. The Python-side harnesses (`mod_bridge_harness.py`, `mod_observation_harness.py`, `mod_decision_harness.py`) simulate the mod's behavior in Python, but:
- They cannot verify GDScript runtime behavior
- They cannot verify scene tree node discovery
- They cannot verify the actual game's signal timing
- They cannot verify WebSocket frame encoding from Godot's `WebSocketClient`

### 16. No End-to-End Smoke Test With Real LLM

There's no test or script that:
1. Starts the daemon with real API keys
2. Sends a realistic decision request
3. Verifies the LLM returns a parseable, legal action

The adapter tests all use mocks. A single real-provider smoke test would catch prompt/schema issues.

### 17. Baseline Adapters Don't Test Character-Specific Payloads

The baseline adapter tests use generic fixtures. No test exercises payload synthesis for character-specific moves (Jump direction vectors, Block height, Ninja momentum, etc.).

---

## Security Considerations

### 18. WebSocket Has No Authentication

The daemon binds to `127.0.0.1:8765` with no authentication. Any local process can connect and inject game decisions. For local development this is acceptable, but:
- If the port is accidentally exposed (e.g., Docker port forwarding), anyone on the network can control the game
- A malicious local process could interfere with tournament results

### 19. API Keys in Config Files

`config.py` supports API keys via environment variables (`ANTHROPIC_API_KEY`, etc.) and also via JSON config files. If a user puts API keys in `daemon-config.json` and commits it, they're exposed. The `.gitignore` should explicitly exclude config files containing credentials.

---

## Performance Considerations

### 20. Prompt Size May Exceed Token Budgets

The rendered prompt includes the full observation (all fighter state, all objects, all legal actions with payload specs). For a complex game state with many legal actions (20-30 moves available), the prompt could be 2000-4000 tokens. With `max_tokens: 256` for the response, this is fine for most models but should be monitored.

### 21. Tournament Runner Is Sequential

`tournament/runner.py` processes matches one at a time (`concurrency=1`). For a 5-policy round-robin (20 matches), this could take hours with LLM latency. Parallel execution is noted as deferred but is a significant practical limitation.

### 22. No Prompt Caching

Each turn renders the full prompt from scratch. The system prompt, output contract, and most of the template are identical across turns. Using Anthropic's prompt caching or OpenAI's cached system messages would significantly reduce token costs and latency.

---

## Strategic Risks

### 23. Game Version Drift

YOMI Hustle is actively maintained (currently v1.9.20). Any game update could:
- Rename internal classes/properties
- Change the action-button UI tree structure
- Modify the `player_actionable` signal signature
- Alter character move data

The mod has no version guard, no graceful degradation on API mismatch, and no mechanism to detect incompatible game updates.

### 24. Payload Spec Semantics Are Opaque

The LLM sees payload specs like `{"Block Height": "option"}` but has no way to know:
- What values "option" accepts
- What "Block Height" means tactically
- Whether high or low block is appropriate in the current situation

Without richer payload descriptions (at minimum: valid values, brief description), the LLM is flying blind on any move with a payload.

### 25. No Learning/Adaptation Loop

The system runs each turn in isolation. There's no mechanism to:
- Feed match results back into prompt selection
- Adjust strategy based on opponent patterns
- Use artifact data to improve prompts between matches
- Fine-tune or adapt model behavior

This is likely intentional for v0 but is the most impactful improvement for competitive viability.

---

## Summary: What's Needed for LLMs to Actually Play

| Priority | Issue | Effort |
|---|---|---|
| **P0** | Fix envelope wrapping in TurnHook.gd (#2) | Small |
| **P0** | Fix DI range in prompt schema (#1) | Small |
| **P1** | Add missing observation fields (#9, #10) | Medium |
| **P1** | Fix object type classification (#12) | Small |
| **P1** | Test against live game (#15) | Large |
| **P1** | Enrich payload specs with semantic info (#24) | Medium |
| **P2** | Add turn history to observations (#5) | Medium |
| **P2** | Expose prediction extra (#6) | Small |
| **P2** | Expose character-specific extras (#7) | Medium |
| **P2** | Add game-version runtime check (#4) | Small |
| **P3** | Prompt caching (#22) | Medium |
| **P3** | Parallel tournament execution (#21) | Medium |
| **P3** | Learning/adaptation loop (#25) | Large |

---

## Conclusion

The codebase demonstrates strong engineering fundamentals: clean separation of concerns, thorough test coverage, schema-first protocol design, and defense-in-depth validation. The daemon side in particular is production-quality code.

The two P0 bugs (envelope wrapping and DI range) are straightforward fixes that would unblock basic functionality. After those, the biggest wins are enriching the observation data the LLM receives (turn history, missing game state fields, semantic payload descriptions) — the current information diet is too sparse for an LLM to play strategically.

The project is well-positioned to deliver on its vision. The architecture is correct, the hard integration problems (game hooking, legal action enumeration, action injection) are solved in design, and the infrastructure for running and evaluating matches is already built. What remains is the "last mile" of integration testing against the live game and feeding the LLM enough context to make intelligent decisions.
