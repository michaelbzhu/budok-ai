# Prompt Engineering

Prompt templates live under `prompts/`. The daemon renders them at decision time via `daemon/src/yomi_daemon/prompt.py`.

## Template Versions

Four templates are currently available:

| File | Variant | Description |
|---|---|---|
| `prompts/minimal_v1.md` | `minimal` | Compact instructions, minimal context |
| `prompts/strategic_v1.md` | `strategic` | Expanded strategic reasoning guidance |
| `prompts/few_shot_v1.md` | `few_shot` | Includes worked examples of legal decisions |
| `prompts/reasoning_v1.md` | `reasoning_enabled` | Encourages chain-of-thought reasoning |

The default template is `minimal_v1`. Policies select a template via `prompt_version` in their config entry.

An alias map redirects legacy names: `reasoning_enabled_v1` resolves to `reasoning_v1`.

## Rendered Prompt Structure

`render_prompt()` assembles a complete prompt from these sections appended to the template body:

1. **Template body** — the raw markdown from the selected `prompts/<version>.md` file.
2. **Output contract** — a concise natural-language description of the expected JSON output with two examples. Replaces the previous full JSON schema (~1200 tokens saved).
3. **Turn context** — a compact JSON block with `match_id`, `turn_id`, `player_id`, `deadline_ms`, `decision_type`. Null fields are stripped.
4. **Situation summary** — a pre-computed tactical digest: player identity and character name, both fighters' HP, distance with range label (CLOSE/MID/FAR), current states, meter, and burst. When the player has repeated the same action 3+ turns in a row, a repetition warning is appended.
5. **Tactical cheat sheet** — contextual suggestions based on opponent state (attacking → block, blocking → grab, grabbing → attack), opponent history patterns (repetition/alternation detection), and outcome feedback (attacks landing vs blocked). Only present when actionable suggestions exist.
6. **Observation** — the serialized observation with compact history (last 5 entries with game_tick, actions, HP, HP deltas, and outcomes).
7. **Legal actions** — grouped by category (Offense, Defense, Grab, Movement, Special, Super, Utility). Each action includes `description`, `category`, `speed`, `damage`, `range`, `beats`, `weakness` from the move catalog. Zero-range payload_specs and all-false supports flags are omitted.

## Move Catalog

`prompts/move_catalog.json` provides static tactical metadata for every known move. The catalog is organized by character name (`Cowboy`, `Ninja`, `Wizard`, `Robot`, `Mutant`) with a `_universal` section for moves shared across all characters.

Each entry includes:
- `category` (offense, defense, grab, movement, utility, special, super)
- `description` — what the move does
- `speed`, `damage`, `range` — tactical stats
- `beats` — what situations/opponent actions the move is good against
- `weakness` — what beats the move or when it's punishable
- `stance_unlocks` (optional) — moves unlocked by entering this stance (e.g., Brandish unlocks Shoot2, PointBlank)

During prompt rendering, `_legal_actions_payload()` resolves the active character name from the observation, looks up each legal action in the catalog (character-specific first, then universal), and injects the catalog metadata into the prompt.

## Response Parsing

`daemon/src/yomi_daemon/response_parser.py` parses raw provider responses into validated `ActionDecision` objects. It supports three parse sources:

- **Structured** — the response is already a mapping or `ProtocolModel`.
- **JSON** — a JSON object is extracted from a text response.
- **Text** — key-value pairs are extracted from freeform text using `key: value` or `key=value` syntax.

Parsing normalizes defaults (fills `match_id`, `turn_id`, `data`, and `extra` when missing), silently strips `extra.prediction` when the chosen action does not support it, auto-fills payload data from `payload_spec` defaults when the LLM omits trivial payloads, and validates the result against the original `DecisionRequest` for action legality, payload conformance, and extra bounds.

## Auto-Fill Payload Defaults

Many legal actions have payload_specs with trivial required fields (boolean checkboxes defaulting to false, zero-range XY plots). When the LLM omits `data` for these actions, the response parser auto-fills from the spec's default values. This prevents `illegal_output` fallbacks for actions like Grab (which has required Direction, Dash, Jump fields that always default to zero/false).

## Correction Retry

Provider-backed policies can enable a single correction retry via `options.response_parser.enable_correction_retry` in the policy config. When enabled:

- If the first parse fails with `malformed_output` or `illegal_output`, the adapter sends a bounded correction prompt describing the failure and the legal action set.
- The model's second response is parsed normally. If it also fails, the turn falls back.
- At most one correction retry is allowed (`max_correction_retries` must be 0 or 1).

## Provider Adapters

Three provider adapters are fully implemented:

| Adapter | Provider | Module |
|---|---|---|
| `anthropic` | Anthropic (Claude) | `daemon/src/yomi_daemon/adapters/anthropic.py` |
| `openai` | OpenAI | `daemon/src/yomi_daemon/adapters/openai.py` |
| `openrouter` | OpenRouter | `daemon/src/yomi_daemon/adapters/openrouter.py` |

Four deterministic baseline adapters require no prompting:

- `baseline/random`
- `baseline/block_always`
- `baseline/greedy_damage`
- `baseline/scripted_safe`

Placeholder stubs exist for `google`, `local`, and `ollama` but are not registered or functional.

## Iteration Log

### Iteration 1: Initial LLM v LLM (pre-debug plan)

**Config**: Claude Sonnet 4, strategic_v1, temperature 0.3, Cowboy mirror, 100 HP.

**Problem**: Both agents converged on a deterministic 2-action oscillation (LightningSliceNeutral and Grab) with 100/100 HP after 107 turns. No net damage dealt. History entries didn't pair both players' actions, no outcome feedback, move catalog had no tradeoffs, and the prompt wasted ~12K tokens on JSON schema.

### Iteration 2: After debug plan implementation

**Config**: Claude Sonnet 4.6, strategic_v1, temperature 0.7, Wizard mirror, 100 HP.

**Changes applied**: History pairing by game tick (WU-DBG-01), outcome feedback (WU-DBG-02), move catalog rebalancing with beats/weakness (WU-DBG-03), tactical cheat sheet (WU-DBG-04), randomization guidance (WU-DBG-05), prompt compression (WU-DBG-06).

**Problem**: 68/73 decisions were fallbacks (93% fallback rate!). Root cause: the `Grab` action has required payload fields (`Direction`, `Dash`, `Jump`) but the LLM doesn't include them because the compressed prompt omits the payload_spec (zero-range detection was too aggressive). The LLM's tool_use response is valid JSON with the right action, but validation rejects it for missing payload data.

**Fix**: Auto-fill payload defaults in `response_parser.py`. When the LLM omits `data` and the action has trivial payload_spec defaults (boolean fields defaulting to false, zero-range XY plots), the parser auto-fills them. Also fixed `_is_zero_range_payload` to correctly handle boolean fields.

### Iteration 3: With auto-fill fix (Ninja mirror)

**Config**: Claude Sonnet 4.6, strategic_v1, temperature 0.7, random character (got Ninja mirror), 100 HP.

**Result**: 8 turns, P1 wins by KO, **0 fallbacks**. Both players use NunChukLight and Grab. The LLM shows good yomi reasoning: "grab beats block", "attack beats grab", reads the tactical cheat sheet. HP went 100→12 from simultaneous NunChukLight, then both tried to read each other.

**Issue**: Only 2 distinct actions per player. The match was too short (8 turns) to show variety.

### Iteration 4: Cowboy mirror, 200 HP

**Config**: Claude Sonnet 4.6, strategic_v1, temperature 0.7, Cowboy mirror, 200 HP.

**Result**: 162 turns, P2 wins by KO, **19 fallbacks (11.7%)**. Excellent variety and quality:

| Metric | Value |
|---|---|
| Total decisions | 162 |
| Fallback rate | 11.7% |
| P1 distinct actions | 13 |
| P2 distinct actions | 15 |
| Category distribution | Offense 29%, Grab 26%, Utility 22%, Defense 19% |

**What's working**:
- Good action diversity: Pommel, Grab, Lasso, ParryHigh, LightningSliceNeutral, VSlash, 3Combo, HSlash2, Stinger, SpotDodge, UpwardSwipe, AnkleCutter, LassoReel
- Sensible yomi reads: "opponent is in Grab state — attacks beat grabs, use Pommel"
- Strategic reasoning: references HP, distance, opponent state, and cheat sheet suggestions
- Defensive play: ParryHigh (23 uses), SpotDodge, ParrySuper used proactively
- Movement to combat ratio is good: no DashForward spam
- Real damage dealing: match progressed from 200/200 to KO

**Remaining issues**:
- `Continue` action accounts for ~20% of decisions — this is the game requesting a decision when the fighter is in an ongoing state (grab animation, combo). Ideally the LLM should always Continue here since there's no real choice.
- 11.7% fallback rate — still some actions the LLM picks that don't match the legal set or have payload issues.
- Rate limiting is a significant bottleneck with concurrent API calls (429 responses ~30% of requests).

### Iteration 5: Robot vs Ninja cross-character match

**Config**: Claude Sonnet 4.6, strategic_v1, temperature 0.7, Robot (P1) vs Ninja (P2), 200 HP.

**Result**: 16 turns, Ninja wins by KO, **0 fallbacks** (0%).

| Player | Character | Distinct Actions | Top Actions |
|---|---|---|---|
| P1 | Robot | 6 | Vacuum (3), Burst, CommandGrab, Slap, Continue, ParryHigh |
| P2 | Ninja | 4 | NunChukLight (3), NunChukHeavy (2), Grab (2), Uppercut |

**What's working**:
- Character-specific play: Robot uses Vacuum (its best grab), CommandGrab, Slap. Ninja uses NunChukLight/Heavy, Uppercut.
- Combo awareness: "opponent is in HurtGrounded state with 121 HP — they're knocked down and vulnerable"
- Defensive adaptation: Robot uses Burst to escape combo when at 121/200 HP
- Anti-repetition: "I've used NunChukHeavy twice in a row - I need to switch it up"
- HP tracking: "I'm in HurtGrounded state with only 121/200 HP while the opponent has full HP"
- Yomi reads: "Opponent just used Burst, which means they likely..."

### Key Lessons

1. **Auto-fill payload defaults is essential**. Many game actions have required payload fields that are trivial (zero-range XY, boolean checkboxes). Without auto-fill, nearly every action with any payload_spec causes fallbacks.

2. **The tactical cheat sheet works**. The LLM directly references it in reasoning ("The cheat sheet says opponent is GRABBING — use fast attack"). It provides concrete, actionable guidance that breaks decision paralysis.

3. **Move catalog beats/weakness fields matter**. The LLM uses them to reason about tradeoffs ("Pommel is fast, safe on block" vs "Stinger has long range but is punishable on block"). Without these, it defaulted to the statistically best move every time.

4. **Temperature 0.7 produces good variety**. At 0.3, the LLM converged on deterministic patterns. At 0.7, it naturally varies between multiple good options.

5. **Prompt compression saves tokens without losing quality**. Replacing the full JSON schema with a 4-line description + examples, stripping null turn context fields, and grouping legal actions by category reduced prompt size by ~40% with no loss in decision quality.

6. **History pairing + outcome feedback enables adaptation**. The LLM can now see what happened last turn (e.g., "both attacked, both took damage → clashed") and adjust. Pre-fix, it had no feedback signal.
