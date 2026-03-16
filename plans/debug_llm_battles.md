# Debug LLM Battles — Improving Agent Play Quality

Status: **Draft**
Created: 2026-03-16
Context: After fixing character name resolution, move catalog injection, and prediction stripping, LLM agents now choose character-specific moves and use basic yomi reasoning. However, matches stall at 100/100 HP because both agents converge on a deterministic 2-action oscillation (LightningSliceNeutral ↔ Grab) that results in simultaneous trades with no net damage.

## Root Cause Analysis

Based on full transcript audit of match `5377cda5` (107 decisions, Cowboy mirror, 100 HP):

1. **History entries don't pair both players' actions per game turn.** P1 and P2 get separate `turn_id`s, so ~50% of history entries show only one player's action. The LLM can't see "last turn I attacked and they blocked."
2. **No outcome feedback.** The LLM sees what actions were taken but not whether they hit, whiffed, clashed, or were blocked. It has no signal to adapt.
3. **Move catalog has no tradeoffs.** LightningSliceNeutral is listed as fast/high/long — strictly dominates all other attacks on paper. The LLM has no reason to pick anything else.
4. **Positions never change.** Both fighters stay at (-50, 0) and (50, 0) for all 107 turns. Simultaneous attacks at 100 units appear to mutually reset.
5. **Prompt wastes tokens on structural noise.** ~12K of 23K prompt chars are output schema JSON, payload_specs with zero-range fields, and verbose turn context.
6. **No randomization guidance.** The LLM converges on a deterministic pattern because nothing tells it that predictability is the core vulnerability in yomi.
7. **Key moves never appear in legal set.** Cowboy's Shoot2/PointBlank/PistolWhip/ShootDodge2 are absent despite `bullets_left: 6`. Likely requires Brandish (Quick Draw) stance first, but the LLM doesn't know that.

---

## WU-DBG-01: Fix history pairing so both players' actions appear per game turn

**Priority:** Critical
**Blocked by:** Nothing
**Files:** `mod/YomiLLMBridge/bridge/TurnHook.gd`

The current system increments `_turn_id` per decision request (per player per actionable moment). Both players acting in the same game tick get different turn_ids, so `_record_turn_decision` rarely merges both into one history entry.

### Implementation

- [x] Change history keying from `turn_id` (per-player) to game tick (`game.current_tick`) so both players' decisions in the same tick always merge into one entry
- [x] Ensure the history entry is only finalized (appended to `_turn_history`) once both players have acted for that tick, or after a short grace window
- [x] Keep `turn_id` in the history entry for ordering, but use tick as the merge key
- [ ] Verify in a test match that mid-game history entries consistently show both `p1_action` and `p2_action`

### Acceptance criteria

- [ ] History entries from a Cowboy mirror match show both `p1_action` and `p2_action` in >80% of entries
- [x] History is ordered by game tick, not by arrival order of decision responses

### Execution notes for future agents

- `_turn_decisions` dict is now keyed by `str(game.current_tick)` instead of `str(turn_id)`. Both P1 and P2 decisions in the same tick merge into the same staging entry.
- `_sort_history_by_tick()` custom sort added to maintain tick ordering after updates.
- The first `turn_id` seen for a given tick is preserved in the history entry for backwards compatibility.
- `game_tick` field added to history entries in both mod and protocol/schema.

---

## WU-DBG-02: Add outcome feedback to history entries

**Priority:** Critical
**Blocked by:** WU-DBG-01
**Files:** `mod/YomiLLMBridge/bridge/TurnHook.gd`, `mod/YomiLLMBridge/bridge/ObservationBuilder.gd`, `daemon/src/yomi_daemon/protocol.py`, `schemas/decision-request.v2.json`

The LLM can see actions taken but not results. It needs to know whether its Lightning Slice hit, whiffed, clashed, or was blocked to learn and adapt.

### Implementation

- [x] After both players act and the game resolves a tick, capture the **delta** between pre-action and post-action state:
  - `p1_hp_delta` / `p2_hp_delta` (damage dealt, negative = took damage)
  - `outcome` per player: `hit`, `blocked`, `whiffed`, `clashed`, `grabbed`, `dodged`, `combo`
- [x] The outcome can be inferred from HP changes and state transitions:
  - HP decreased → the player was hit
  - Both HPs unchanged + both attacked → clashed or both whiffed
  - One HP decreased + attacker in attack state → the attack landed
  - Opponent in blockstun → attack was blocked
- [x] Add `p1_hp_delta`, `p2_hp_delta`, and `p1_outcome`/`p2_outcome` to `HistoryEntry` in `protocol.py`
- [x] Update `decision-request.v2.json` schema with the new optional fields
- [x] Emit the enriched history entries from `TurnHook.gd` after tick resolution

### Acceptance criteria

- [ ] History entries in a live match include `p1_hp_delta` and `p2_hp_delta`
- [ ] At least one history entry in a match where damage occurs shows a non-zero delta
- [x] Schema validates with the new optional fields

### Execution notes for future agents

- `_enrich_last_history_entry()` runs at the START of `_on_player_actionable`, before capturing the new observation. At this point the game has resolved the previous tick, so HP/position reflect post-resolution state.
- HP deltas are computed as `current_hp - snapshot_hp`. Negative = took damage.
- Post-resolution HP and position overwrite the pre-resolution snapshot in the history entry, so the LLM sees the actual result state.
- Outcome inference uses heuristics: `_is_attack_action()` and `_is_defense_action()` match on action name patterns. These are best-effort and may need tuning for edge cases.
- The outcome enum includes `neutral` as a catch-all when neither player took damage and no clear interaction occurred.
- ObservationBuilder was NOT modified — it already captures state at the right time (post-previous-resolution). Only TurnHook and protocol needed changes.

---

## WU-DBG-03: Rebalance move catalog to include tradeoffs and situational guidance

**Priority:** High
**Blocked by:** Nothing
**Files:** `prompts/move_catalog.json`

LightningSliceNeutral is listed as `speed: fast, damage: high, range: long` with no downside. The LLM rationally picks it every time. Every move needs tradeoffs and situational context.

### Implementation

- [x] Add `weakness` or `risk` field to move catalog entries describing what beats the move or when it's punishable
- [x] Add `beats` field listing what situations/opponent actions the move is good against
- [x] Rewrite LightningSliceNeutral: add "loses to blocking, punishable on whiff" to description
- [x] Add combo/sequence context: "Pommel — fast, safe on block, **combos into 3Combo or HSlash2**"
- [x] Add stance context: "Brandish — Quick Draw stance entry. **Unlocks Shoot, PointBlank, and ShootDodge next turn.**"
- [x] Audit all 5 character sections for strict-dominance moves and add counterbalancing weaknesses
- [x] Add a `matchup_notes` field for moves that are particularly good/bad in specific situations (e.g., "UpwardSwipe — anti-air, **use when opponent is airborne**")

### Acceptance criteria

- [x] No move in the catalog has speed=fast + damage=high + range=long without a listed weakness
- [x] Brandish description mentions it unlocks gun moves
- [x] At least 50% of offensive moves have a `beats` or `weakness` field

### Execution notes for future agents

- Used `beats` and `weakness` fields instead of separate `matchup_notes` — simpler, accomplishes same goal.
- `stance_unlocks` array added to Brandish, QuickerDraw (Cowboy) and Drive (Robot) to signal which moves they gate.
- LightningSliceNeutral now has explicit weakness: "loses to blocking and parry, punishable on whiff, predictable if overused".
- prompt.py now injects `beats` and `weakness` from catalog into legal action entries shown to the LLM.
- All five character sections audited. Every fast+high+long move now has a weakness listed.

---

## WU-DBG-04: Add per-turn "what beats what" cheat sheet to the prompt

**Priority:** High
**Blocked by:** Nothing
**Files:** `daemon/src/yomi_daemon/prompt.py`

Instead of making the LLM infer yomi from abstract rules, give it a concrete action-reaction matrix each turn based on the opponent's current state and recent history.

### Implementation

- [x] Add a `_tactical_cheat_sheet()` function in `prompt.py` that generates a short markdown section
- [x] Based on opponent's `current_state`, suggest:
  - If opponent is attacking → "Block (ParryHigh), SpotDodge, or Roll to counter"
  - If opponent is blocking/parrying → "Grab or Lasso to throw through their guard"
  - If opponent is grabbing → "Any attack beats a grab — use a fast attack"
  - If opponent is in recovery/whiff → "Punish with a high-damage move: Stinger, VSlash, 3Combo"
  - If opponent is neutral/starting → "Mix unpredictably between attack, grab, and block"
- [x] Based on opponent's recent history pattern (last 3 actions), suggest what to exploit:
  - "Opponent has attacked 3 times in a row → they may continue attacking, BLOCK to punish"
  - "Opponent alternates attack/grab → break the pattern with a DODGE or WAIT"
- [x] Keep the section short (5-8 lines max) to stay within token budget
- [x] Insert between Situation and Observation in the prompt

### Acceptance criteria

- [x] Cheat sheet appears in rendered prompts
- [x] Suggestions change based on opponent state and history
- [ ] LLM decisions reference the cheat sheet suggestions in reasoning (spot-check 5+ turns)

### Execution notes for future agents

- Cheat sheet is inserted between Situation and Observation using `*([cheat_sheet] if cheat_sheet else [])` spread.
- Three suggestion sources: opponent current state keywords, opponent recent history patterns (repetition + alternation), and outcome-based feedback (your attacks landing/blocked).
- Returns empty string (omitted from prompt) if no actionable suggestions — avoids wasting tokens on trivial neutral states.

---

## WU-DBG-05: Add randomization / unpredictability guidance to the prompt

**Priority:** High
**Blocked by:** Nothing
**Files:** `prompts/strategic_v1.md`

The LLM converges on deterministic patterns because it reasons to a single "best" action. In yomi, mixed strategies are optimal.

### Implementation

- [x] Add a "Mixed strategy" section to strategic_v1.md explaining that predictability is the biggest weakness:
  - "Your opponent can see your history. If you always do the same thing, they will counter it."
  - "A good baseline distribution: 35% attack (vary which attack each turn), 25% grab, 25% block/defense, 15% movement/utility."
  - "Adjust weights based on what's working: if your attacks keep landing, increase attack frequency. If you keep getting grabbed, use more attacks (attacks beat grabs)."
- [x] Add explicit instruction: "NEVER use the same action more than 2 turns in a row. When choosing between similar options (e.g., multiple attack moves), pick a DIFFERENT one each time."
- [x] Add instruction: "Distribute your attacks across your full moveset. You have N attack moves — use them all, not just the one with the best stats."
- [x] Remove or weaken the "prefer attacks or grabs over movement" guidance that biases away from defense

### Acceptance criteria

- [ ] In a 50+ turn match, no single action exceeds 35% of total decisions
- [ ] At least 5 distinct action types used per player
- [ ] Defense actions (ParryHigh, SpotDodge, Roll) are chosen proactively, not just from fallbacks

### Execution notes for future agents

- "When unsure, prefer attacks or grabs over movement" replaced with guidance to use variety and the beats/weakness fields.
- New "Mixed strategy" section placed early in the prompt (after core yomi layer) so the LLM reads it before decision factors.
- Defense is now explicitly called out as a proactive choice, not a fallback.

---

## WU-DBG-06: Compress prompt format to reduce token waste

**Priority:** Medium
**Blocked by:** Nothing
**Files:** `daemon/src/yomi_daemon/prompt.py`

The current prompt is ~23K chars. About half is structural JSON that doesn't help the LLM make better decisions.

### Implementation

- [x] Compress the output contract: replace the full JSON schema with a concise natural-language description + one example. The schema is the same every turn — no need to repeat 1200 tokens of it.
- [x] Compress legal actions: omit `payload_spec` for actions where all payload fields have `min == max` (zero-range XY plots like Roll's Direction). Show payload specs inline as one-liners for simple cases.
- [x] Group legal actions by category in the prompt output (Offense, Defense, Grab, Movement, Utility, Special, Super) instead of a flat list
- [x] Omit `supports` flags that are all false — only show the flags that are true (e.g., `"supports": {"di": true, "feint": true}` instead of listing all four)
- [x] Strip null fields from turn context (`game_version: null`, `mod_version: null`, etc.)
- [ ] Target: reduce prompt to <15K chars without losing tactical signal

### Acceptance criteria

- [ ] Prompt length is <15K chars for a typical turn with 10 history entries and 25+ legal actions
- [ ] No regression in parse success rate (fallback rate stays <15%)
- [x] Legal actions are grouped by category in the prompt

### Execution notes for future agents

- `_compact_output_contract()` replaces the full JSON schema (~1200 tokens) with a 4-line natural language description + 2 examples.
- `_turn_context()` now only includes non-null fields — removed game_version, mod_version, schema_version, ruleset_id, prompt_version, state_hash, legal_actions_hash from prompt.
- `_compact_observation()` trims history to last 5 entries with compact fields (omits was_fallback, p1_pos/p2_pos unless helpful).
- `_grouped_legal_actions_section()` groups actions by category with ordered headings.
- `_is_zero_range_payload()` detects payload_specs where all numeric fields have min==max (no real choice) and omits them.
- `decision_output_json_schema()` is preserved for validation/reference use but no longer rendered in prompts.

---

## WU-DBG-07: Investigate and fix missing Cowboy gun moves

**Priority:** Medium
**Blocked by:** Nothing
**Files:** `mod/YomiLLMBridge/bridge/LegalActionBuilder.gd`, `prompts/move_catalog.json`

Cowboy's gun moves (Shoot2, PointBlank, PistolWhip, ShootDodge2) never appear in the legal action set despite `bullets_left: 6` and `has_gun: true`. These are core to the character's kit.

### Implementation

- [x] Check whether Shoot2/PointBlank/etc. require entering Quick Draw stance (Brandish) first by examining the game's action button visibility rules
- [x] If gun moves require Brandish: update the Brandish catalog entry to say "**Unlocks Shoot, PointBlank, ShootDodge next turn**" and verify Brandish is in the legal set
- [ ] If gun moves should be directly available: debug `LegalActionBuilder.gd` to find why the buttons aren't being enumerated
- [ ] Take a screenshot of the game UI during a Cowboy match to verify which action buttons are visible
- [x] If the moves are stance-gated, add a "stance_unlocks" field to the catalog so the LLM knows to use Brandish as a setup move

### Acceptance criteria

- [x] Either gun moves appear in the legal action set, or Brandish's description clearly explains it unlocks gun moves
- [ ] In a 50+ turn match, at least one gun move is used by an LLM agent

### Execution notes for future agents

- Code analysis confirms gun moves are stance-gated: `LegalActionBuilder.gd` reads visible action buttons, and gun buttons only become visible after entering Quick Draw stance via Brandish.
- Brandish catalog entry updated in WU-DBG-03 with `stance_unlocks: ["Shoot2", "ShootDodge2", "PointBlank", "PistolWhip"]` and description explaining it unlocks gun moves.
- `QuickerDraw` also has the same `stance_unlocks` array.
- Remaining: verify in a live match that Brandish appears in the legal set and that choosing it makes gun moves appear next turn. Take screenshots to confirm.

---

## WU-DBG-08: Investigate position stasis and damage resolution

**Priority:** Medium
**Blocked by:** Nothing
**Files:** `mod/YomiLLMBridge/bridge/ObservationBuilder.gd`, `mod/YomiLLMBridge/bridge/TurnHook.gd`

Both fighters stay at starting positions (-50, 50) for the entire match. 100/100 HP after 107 turns.

### Implementation

- [ ] Take screenshots at multiple points during a match to verify the visual game state matches the observation data
- [x] Check whether `ObservationBuilder` captures position at the right moment (before action resolution vs after)
- [ ] Check whether the game resets positions after simultaneous mutual attacks (could be a game mechanic where clashing attacks reset to neutral)
- [ ] If positions are genuinely static, investigate whether the actions are actually being applied via `ActionApplier.gd` by adding telemetry for `on_action_selected` calls
- [x] Compare observation timing vs game tick resolution — are we capturing state before the previous turn's actions resolve?
- [x] If the issue is observation timing, adjust to capture state **after** the previous tick resolves but **before** the next decision is needed

### Acceptance criteria

- [ ] Observation positions change across turns in a match where both players use movement or attack moves
- [ ] At least one player's HP drops below max in a 50+ turn match
- [ ] Screenshot visual state matches observation position data

### Execution notes for future agents

- **Observation timing is correct**: `ObservationBuilder.build_observation()` runs when `player_actionable` fires, which is after the previous tick resolved. Positions and HP reflect post-resolution state.
- **History HP/position timing was wrong (fixed in WU-DBG-01/02)**: `_record_turn_decision` used to snapshot HP/position at decision-application time (pre-resolution). Now `_enrich_last_history_entry()` retroactively updates to post-resolution values when the next `player_actionable` fires.
- **Position stasis root cause is likely gameplay**: at 100-unit distance with both players using LightningSliceNeutral, the attacks may clash (both fighters attack simultaneously, causing a "clash" mechanic that resets to neutral positions). The 100/100 HP after 107 turns suggests clashed attacks deal no net damage.
- **Remaining investigation requires running the game**: take screenshots to verify positions visually, add ActionApplier telemetry to confirm `on_action_selected` is being called, and test whether movement actions (DashForward) actually change position values.
- The prompt improvements in WU-DBG-03/04/05 should break the LightningSliceNeutral oscillation by encouraging variety, which may resolve the position/damage stasis indirectly.

---

## Execution Order

```
Phase 1 (unblock damage): WU-DBG-08 (position/damage investigation)
Phase 2 (information quality): WU-DBG-01 (history pairing), WU-DBG-02 (outcome feedback)
Phase 3 (decision quality): WU-DBG-03 (catalog tradeoffs), WU-DBG-04 (cheat sheet), WU-DBG-05 (randomization)
Phase 4 (efficiency): WU-DBG-06 (prompt compression), WU-DBG-07 (gun moves)
```

Phase 1 should be investigated first because if the game isn't actually resolving actions or applying damage, all the prompt improvements won't matter. Phase 2 gives the LLM the information it needs to adapt. Phase 3 gives it the guidance to use that information well. Phase 4 is optimization.
