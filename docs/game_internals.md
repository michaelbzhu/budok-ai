# Game Internals

`WU-001` owns the concrete YOMI Hustle bridge mapping: how we inspect the game, which native scripts appear to own decision flow, how legal actions are exposed, and how later mod files should divide responsibility.

## Supported Build Assumption

The repository does not include a decompiled YOMI Hustle project. This document is therefore split into:

- confirmed modding patterns from public reference mods cited in the spec
- concrete hook targets and validation steps that must be rechecked against the supported game build after a local decompile run

Treat anything labeled `Candidate` as a target that is strongly suggested by reference-mod behavior but still requires confirmation against the decompiled build under `docs/decompile-output/project/`.

Confirmed public-build fixture:

- `tests/fixtures/decompile/yomi_hustle_supported_build_16151810.json` records the first supported-build recovery captured in-repo.
- That fixture was recovered from app `2212330`, depot `2232859`, public build `16151810`, and detected engine version `3.5.1`.

## Canonical Decompilation Workflow

Use [`scripts/decompile.sh`](../scripts/decompile.sh) as the only supported entry point for decompilation work.

Expected output layout:

- `docs/decompile-output/manifest.json`: run metadata, local operator inputs, and required next steps
- `docs/decompile-output/reference-hooks.json`: hook inventory captured during `WU-001`
- `docs/decompile-output/project/`: the decompiled Godot project for the supported game build
- `docs/decompile-output/reports/`: symbol inventory, script hashes, and manual validation notes

Workflow:

1. Install GDRETools or `gdsdecomp` locally.
2. Locate the exact `.pck` for the supported YOMI Hustle build.
3. Run `scripts/decompile.sh` once to prepare the output tree.
4. Re-run it with local inputs recorded in the manifest, then write the extracted project into `docs/decompile-output/project/`.
5. Open the extracted project in Godot `3.5.1`, not a newer editor.
6. Validate the candidate hook points below and record the confirmed script names or hashes under `docs/decompile-output/reports/`.

## Reference Evidence Used In WU-001

The concrete mapping below is grounded in the public mod sources already referenced by the spec:

- `_AIOpponents` demonstrates that AI control is achieved by extending `res://game.gd`, subscribing to the game-level `player_actionable` signal, reading visible action-button state, and writing player `queued_*` fields.
- `char_loader` demonstrates that `installScriptExtension()` can take over vanilla script paths such as `res://ui/CSS/CharacterSelect.gd`, `res://main.gd`, `res://char_loader/SteamLobby.gd`, and `res://char_loader/Network.gd`.

These references are sufficient to scope the bridge file ownership even before the supported game build is decompiled locally.

## Hook Map

### 1. Decision Interception

Candidate native owner:

- `res://game.gd`

Observed pattern from `_AIOpponents`:

- `AILoader.gd` extends `res://game.gd`.
- In `_ready()`, it attaches an AI controller node to the live game scene.
- `AIController.gd` calls `game.connect("player_actionable", self, "_start_decision_thread")`.

Implication for this repository:

- [`TurnHook.gd`](../mod/YomiLLMBridge/bridge/TurnHook.gd) should target the same game-level seam first.
- The bridge should prefer subscribing to the native actionable-turn signal instead of polling UI state.
- `TurnHook.gd` should own:
  - attaching to the live game instance
  - deciding whether P1 or P2 is AI-controlled
  - minting `match_id` and `turn_id`
  - calling `ObservationBuilder`, `LegalActionBuilder`, `BridgeClient`, and `ActionApplier`

Validation steps after decompile:

1. Confirm the live script path is still `res://game.gd` or locate its replacement.
2. Confirm the signal name is still `player_actionable`.
3. Record the method or signal signature in `docs/decompile-output/reports/`.

### 2. Live Player Selection And Lock-In

Candidate native owner:

- player/fighter node methods and signals

Observed pattern from `_AIOpponents`:

- The AI controller resolves a player with `game.get_player(id)`.
- It subscribes to `target_player.connect("action_selected", self, "_edit_queue")`.
- It writes:
  - `target_player.queued_action`
  - `target_player.queued_data`
  - `target_player.queued_extra`

Implication for this repository:

- The supported-build decompile fixture confirms the fighter-side action-application hook is `characters/BaseChar.gd::on_action_selected(action, data, extra)`.
- [`ActionApplier.gd`](../mod/YomiLLMBridge/bridge/ActionApplier.gd) should prefer calling that native fighter hook so ready-state changes and downstream commit behavior stay on the game's own path.
- Direct `queued_*` mutation is retained only as a compatibility fallback when the native hook is unavailable in a harness.
- [`DecisionValidator.gd`](../mod/YomiLLMBridge/bridge/DecisionValidator.gd) must run before those writes and must reject stale or illegal payloads.
- `TurnHook.gd` should still be responsible for feeding the current fighter into `ActionApplier.gd`; `ActionApplier.gd` owns the final native call that locks in the choice.

Fields to preserve in the bridge payload:

- `action`: legal action identifier
- `data`: move-specific payload, when the chosen move requires one
- `extra`:
  - `DI`
  - `feint`
  - `reverse`
  - any future extras we explicitly whitelist

Validation steps after decompile:

1. Confirm the signal `action_selected` still exists on the player object.
2. Confirm queued fields are still the native source of truth before turn resolution.
3. Confirm `on_action_selected(action, data, extra)` remains the authoritative fighter method that finalizes the queued choice.

### 3. Legal Action Enumeration

Candidate native owners:

- main scene action-button containers
- action-state metadata on the button objects
- per-move `ActionUIData` scenes

Observed pattern from `_AIOpponents`:

- The mod resolves `main.find_node("P1ActionButtons")` or `main.find_node("P2ActionButtons")`.
- It iterates `action_buttons.buttons`.
- For each visible button it reads:
  - `button.action_name`
  - `button.state`
  - `button.state.type`
  - `button.state.data_ui_scene`
- It expands legal payloads by instantiating `data_ui_scene` and traversing controls that behave like:
  - `ActionUIData`
  - `XYPlot`
  - `8Way`
  - `Slider`
  - `CountOption`
  - `OptionButton`
  - `CheckButton`

Implication for this repository:

- [`LegalActionBuilder.gd`](../mod/YomiLLMBridge/bridge/LegalActionBuilder.gd) should enumerate legality from the visible UI-backed action source first, because that path is already battle-tested by an existing AI mod.
- `LegalActionBuilder.gd` should produce:
  - stable `action_id`
  - move label and category when available
  - canonicalized `data` choices or compact constraints
  - allowed extras such as `feint`, `reverse`, and DI metadata
- The builder should preserve the game's ordering or explicitly sort with a documented rule so seeded baselines remain deterministic.

Known move-specific payload shapes inferred from `_AIOpponents`:

- DI is a percentage-int vector: `{"x": int, "y": int}`
- parry/block payloads include nested keys such as `"Block Height"` and `"Melee Parry Timing"`
- some actions expose structured dictionaries rather than scalar values

Validation steps after decompile:

1. Confirm action-button node names and whether they are still `P1ActionButtons` and `P2ActionButtons`.
2. Inventory all data UI scene types used by the supported build.
3. Record any character-specific payload UIs that need special handling.

### 4. Observation Construction

Candidate native owners:

- game root
- player/fighter nodes
- projectile or world-state collections
- stage metadata nodes

Observed pattern from `_AIOpponents`:

- The mod reads player positions via `get_pos()`.
- It accesses fields such as `hp`, `combo_count`, `bursts_available`, `feints`, `opponent`, `state_machine`, `current_state()`, and various interruptibility flags.
- It copies the current game into a ghost scene with `game.copy_to(ghost_game)` and simulates with `simulate_one_tick()`.

Implication for this repository:

- [`ObservationBuilder.gd`](../mod/YomiLLMBridge/bridge/ObservationBuilder.gd) should build from live game objects, not from UI text.
- The first-pass observation schema should include at least:
  - match metadata: `match_id`, `turn_id`, `player_id`, `trace_seed`
  - both fighters: position, velocity, health, meter/resources, combo state, facing, current state name, frame/tick counters when available
  - projectiles and spawned entities
  - stage bounds or relevant arena data
  - transient flags that affect legality or strategy: hitstun, actionable state, feint availability, burst availability

Normalization requirements:

- sort dictionary keys before hashing or serialization
- use a stable player ordering independent of camera or viewport order
- sort projectiles by a deterministic key rather than scene-tree order
- avoid raw node paths or instance IDs in the wire payload
- normalize floats to game-stable integer or fixed-precision values before hashing

`ObservationBuilder.gd` should not simulate future states. Ghost simulation belongs to future heuristic helpers or offline analysis, not the transport snapshot.

### 5. Action Application Ownership

File ownership for later work units should be:

- [`TurnHook.gd`](../mod/YomiLLMBridge/bridge/TurnHook.gd): subscribe to the decision seam, coordinate the turn request lifecycle, and guard against stale turns
- [`ObservationBuilder.gd`](../mod/YomiLLMBridge/bridge/ObservationBuilder.gd): serialize deterministic live state only
- [`LegalActionBuilder.gd`](../mod/YomiLLMBridge/bridge/LegalActionBuilder.gd): enumerate visible legal actions plus move-specific payload constraints
- [`DecisionValidator.gd`](../mod/YomiLLMBridge/bridge/DecisionValidator.gd): verify `match_id`, `turn_id`, schema shape, action legality, and extra legality
- [`ActionApplier.gd`](../mod/YomiLLMBridge/bridge/ActionApplier.gd): call `on_action_selected(action, data, extra)` when available, then fall back to direct queued-field writes only for compatibility harnesses
- [`FallbackHandler.gd`](../mod/YomiLLMBridge/bridge/FallbackHandler.gd): choose legal fallback actions for timeout, disconnect, or invalid responses
- [`Telemetry.gd`](../mod/YomiLLMBridge/bridge/Telemetry.gd): emit auditable lifecycle events around request, apply, fallback, and match end

## DI, Feint, Reverse, And Turn Freshness

Reference-mod evidence confirms that these extras are not side channels; they are part of the live queued decision shape.

Current working assumptions:

- DI should be represented as the same percentage-int vector format the game UI uses.
- `feint` should only be sent when the current legal state exposes feint availability.
- `reverse` should remain explicit even if most moves default it to `false`.
- any daemon decision must be tied to the current `match_id` and `turn_id`; stale responses must be rejected before touching `queued_*`.

## Known Version Risks

- `res://game.gd` may move or be renamed in future builds even if the gameplay seam is unchanged.
- signal names such as `player_actionable` and `action_selected` may drift across versions.
- action-button node names and UI scene classes may change between characters or builds.
- online-related scripts like `Network.gd` and `SteamLobby.gd` are modifiable, but they are not the first seam to use for single-process local AI control.
- Godot editor mismatch is a real risk: validate against Godot `3.5.1`, not a newer release.

## Validation Strategy After Local Decompile

For the supported game build, record all of the following under `docs/decompile-output/reports/`:

1. The script path and hash for the confirmed turn-actionable owner.
2. The signal and method names involved in move selection and move lock-in.
3. The node names and class names used to enumerate visible legal actions.
4. The player fields or methods that expose DI, feint, reverse, and state/actionability.
5. Any character-specific exceptions that require builder special cases.

If a candidate seam above is wrong, update this file first before changing bridge implementation code. This document is the authority for `WU-009` and `WU-010`.
