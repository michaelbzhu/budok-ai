# Yomi-AI Code Review

## Bottom line

No: this repository does not currently prove, and in one critical place does not even implement, a working end-to-end system where LLMs can fully play YOMI Hustle against each other in the live game.

What it does have is a fairly strong MVP foundation:

- a typed daemon protocol,
- a handshake path,
- baseline and provider adapters,
- artifact writing,
- a large simulated test suite,
- Godot-side bridge components for observation, legality extraction, validation, fallback, and application.

But the live integration still has major gaps. The biggest one is an actual wire-protocol mismatch between the mod and the daemon after handshake.

## External research context

I reviewed external sources first because correctness here depends on how YOMI Hustle actually works:

- Official game page: https://ivysly.itch.io/your-only-move-is-hustle
- Mizuumi / wiki.gbl.gg basics: https://wiki.gbl.gg/w/YomiHustle/Basics
- Mizuumi actionability: https://wiki.gbl.gg/w/YomiHustle/Actionability
- Mizuumi HUD/how-to-play: https://wiki.gbl.gg/w/YomiHustle/How_to_play
- Community loader docs: https://gitlab.com/ZT2wo/YomiModding/-/raw/main/MODDING.md
- Godot Modding org: https://github.com/GodotModding
- Godot Mod Loader wiki: https://wiki.godotmodding.com/
- YOMI Hustle Mod Wiki: https://tiggerbiggo.github.io/YomiHustleModWiki/
- Community AI mod example: https://steamcommunity.com/sharedfiles/filedetails/?id=3577160476

Those sources matter because they establish that:

- YOMI Hustle is a simultaneous-decision, lock-in-based fighter. A bot needs to do more than choose an action name; it needs to participate in the same actionable/ready/commit flow as the in-game UI.
- Prediction/actionability windows and move modifiers matter to correctness.
- Modding is possible, but it is version-fragile and based on internal game structures rather than a stable official automation API.
- Community AI mods demonstrate that in-game automation is possible in principle, but they also explicitly call out simultaneous-choice handling and thinking time as real practical constraints.

That makes live hook correctness more important than the repo’s internal schema/test quality.

## What is working

- The daemon-side protocol, validation, fallback, adapters, and artifact plumbing are well structured.
- The repository test suite is broad and currently green. I ran `uv run --project daemon pytest tests`, and all 267 tests passed.
- The handshake path is covered reasonably well.
- The daemon can orchestrate simulated matches when the client sends correctly enveloped protocol messages.

## Findings

### 1. Critical: the live mod sends bare decision requests, but the daemon only accepts enveloped requests

This is the most important defect in the repository.

- `mod/YomiLLMBridge/bridge/TurnHook.gd:82-99` builds a bare `decision_request` payload and sends it with `_bridge_client._send_json_message(decision_request)`.
- `daemon/src/yomi_daemon/server.py:203-223` parses every post-handshake message with `parse_envelope(raw_data)` and only routes it when `envelope.type is MessageType.DECISION_REQUEST`.

That means the expected live flow is:

1. Handshake succeeds.
2. The mod sends a bare payload.
3. The daemon rejects it as an invalid envelope and ignores it.

The simulated tests miss this because they do not use the live GDScript turn path:

- `tests/mod/test_bridge_harness.py:21-128` only exercises handshake behavior through `scripts/mod_bridge_harness.py`.
- `tests/daemon/test_match_orchestration.py:96-177` uses handcrafted, correctly enveloped `decision_request` messages.

Impact:

- As written, the daemon and mod do not actually interoperate for turns after handshake.
- This alone is enough to answer the main question with “not currently”.

### 2. High: the mod does not send match-end results to the daemon

- `mod/YomiLLMBridge/bridge/Telemetry.gd:89-103` defines `emit_match_ended(...)`.
- `mod/YomiLLMBridge/bridge/TurnHook.gd:27-60` only connects to daemon events and the game’s `player_actionable` signal. There is no hook for game-over, winner, replay path, or match-end emission.
- `daemon/src/yomi_daemon/server.py:277-313` expects a `match_ended` message to finalize a match with winner/end-reason details; otherwise it falls back to disconnect finalization.

Impact:

- Even if turn exchange worked, the daemon would not receive authoritative match completion data from the live mod.
- Real runs would likely finalize as disconnects or partial matches rather than completed audited games.

### 3. High: action payload modeling is too weak for “full play”

The legal-action extraction path is not rich enough to support many real YOMI actions that need parameters.

- `mod/YomiLLMBridge/bridge/LegalActionBuilder.gd:61-86` reduces payload metadata to strings like `"slider"`, `"option"`, `"check"`, `"8way"`, and `"xy_plot"`.
- It does not expose ranges, defaults, option lists, required fields, or value semantics.
- `mod/YomiLLMBridge/bridge/DecisionValidator.gd:134-148` accepts any dictionary whenever `payload_spec` is non-empty; it does not validate keys or values against the spec.

Impact:

- The daemon/LLM does not get enough structured information to reliably synthesize valid payloads for parameterized moves.
- The live mod can apply structurally invalid payload dictionaries anyway.
- The system may work for trivial no-payload actions, but that is not “fully playing the game”.

### 4. High-risk inference: the apply path bypasses the documented `action_selected` hook

The repo’s own decompile-derived evidence says the native AI path is centered on `action_selected`:

- `scripts/decompile.sh:55-65` says `_AIOpponents/AIController.gd` connects `target_player.action_selected` and rewrites `queued_action`, `queued_data`, and `queued_extra`.

But the implementation only mutates queued fields directly:

- `mod/YomiLLMBridge/bridge/ActionApplier.gd:11-20`.

I did not find live code that emits or hooks `action_selected`, nor any automated in-engine test proving that direct queued-field mutation is sufficient for the full commit/ready path.

Impact:

- This may still work if queued fields are the only thing the game reads.
- But if `action_selected` triggers side effects such as ready-state transitions, UI sync, or downstream bookkeeping, the bridge is incomplete.

This is a risk/inference, not a proven defect on the same level as Finding 1.

### 5. High: the test suite gives false confidence about live readiness

The test suite is good, but most of the mod-side safety comes from Python mirrors, not the actual GDScript running in a live game.

- `scripts/mod_observation_harness.py:1-8` explicitly says it is a “Python mirror”.
- `tests/mod/test_observation_harness.py:14-140` validates that mirror on JSON fixtures.
- `tests/mod/test_decision_validation.py:1-5` explicitly validates `scripts/mod_decision_harness.py`, another mirror.
- `mod/BridgeHandshakeSmoke.gd:21-42` only checks that the mod can reach the `connected` handshake state.

Impact:

- 267 passing tests means “the protocol and mirror logic are consistent”.
- It does not mean “the real Godot mod can drive a live YOMI match from actionable turn to match end”.

### 6. Medium: provider prompt/provider-request traces are implemented but not used in the real server path

- `daemon/src/yomi_daemon/orchestrator.py:146-180` provides `resolve_adapter_decision(...)`, which preserves prompt/provider traces via `decide_with_trace(...)`.
- Provider adapters like `daemon/src/yomi_daemon/adapters/openai.py:113-169` build those traces.
- But `daemon/src/yomi_daemon/server.py:373-388` calls `resolve_policy_decision(... decision_provider=adapter.decide ...)` and only appends decisions, not prompts/provider payloads.

Impact:

- Provider-backed matches lose part of the audit trail the repo otherwise appears designed to capture.
- This does not block play, but it is a real observability gap.

### 7. Medium: observation quality is still MVP-level, not full-game quality

- `mod/YomiLLMBridge/bridge/ObservationBuilder.gd:7-17` always emits `"history": []`.
- `mod/YomiLLMBridge/bridge/ObservationBuilder.gd:44-98` includes character-specific data for Cowboy, Robot, Ninja, Mutant, and Wizard, but not Alien.

Impact:

- This is not the reason the system currently fails end-to-end.
- But it does mean the information available to LLMs is still materially below what a strong or fully robust agent would want in a prediction-heavy game.

### 8. Operational gap: local scripts are not yet turnkey for full live play

- `scripts/run_local_match.sh:22-36` only starts the daemon; it does not launch the game/mod or verify the live loop.
- `scripts/package_mod.sh:1-5` is still a placeholder.

Impact:

- The repo is not yet a complete operator workflow for running live LLM-vs-LLM matches from the command line.

## Biggest risks

### 1. Protocol drift between simulated tests and the real mod

This has already happened once in a critical way: turn messages in the mod are bare payloads, while the daemon requires envelopes.

### 2. Game-hook fragility

Because the integration relies on internal/decompiled game structures, even correct code today is vulnerable to build changes and mod-loader changes.

### 3. Incomplete move parameterization

If the system cannot represent payload-heavy moves correctly, LLM play will collapse toward only simple actions or produce illegal game states.

### 4. False positive confidence from mirror-based tests

The current suite is good at protecting the protocol layer, but weak at proving live game behavior.

### 5. Silent degradation through fallback

The daemon is intentionally resilient and will fall back instead of crashing. That is good operationally, but it also makes it easier to miss that provider output or live game integration is systematically failing.

## Gaps to close before claiming “LLMs can fully play the game”

1. Fix the mod/daemon turn-message contract so the live mod sends full `decision_request` envelopes, not bare payloads.
2. Add a real live-game integration test that covers: handshake, actionable turn detection, `decision_request` emission, `action_decision` response, action application, and `match_ended`.
3. Implement real match-end emission from the mod.
4. Upgrade `payload_spec` from widget labels to structured value constraints that models and validators can actually use.
5. Decide whether direct `queued_*` mutation is sufficient or whether the bridge must participate in the native `action_selected` / ready / lock-in path.
6. Exercise parameterized moves, simultaneous-action situations, and at least one full match in-engine.
7. Wire provider prompt/provider-response tracing into the real server path if auditability is a core goal.

## Verdict

This repository is a credible MVP codebase for a YOMI Hustle AI arena, but it is not yet a complete or proven live gameplay system.

If the question is “is the architecture directionally sound?”, the answer is yes.

If the question is “can I trust this repository today to let LLMs fully play the game against each other in a real YOMI Hustle match?”, the answer is no.
