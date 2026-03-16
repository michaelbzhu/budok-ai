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

`render_prompt()` assembles a complete prompt from six sections appended to the template body:

1. **Template body** — the raw markdown from the selected `prompts/<version>.md` file.
2. **Output contract** — a fixed section describing the expected JSON output shape, including the decision JSON schema with `action`, optional `data`, optional `extra` (DI, feint, reverse, prediction), and optional `notes`/`reasoning` fields.
3. **Turn context** — a JSON block with `match_id`, `turn_id`, `player_id`, `deadline_ms`, `decision_type`, `trace_seed`, `policy_id`, hashes, and version metadata.
4. **Situation summary** — a pre-computed tactical digest: player identity and character name, both fighters' HP, distance with range label (CLOSE/MID/FAR), current states, meter, and burst. When the player has repeated the same action 3+ turns in a row, a repetition warning is appended.
5. **Observation** — the full serialized observation for the current turn.
6. **Legal actions** — the list of legal actions with `action`, `label`, `description`, tactical metadata (category, speed, damage, range from the move catalog), `payload_spec`, `prediction_spec`, and `supports` flags.

The output contract instructs the model to return exactly one JSON object (no Markdown fences), choose `action` from the legal set, only include `data` when required by the action's `payload_spec`, and prefer lower-commitment options when multiple actions look equivalent.

## Move Catalog

`prompts/move_catalog.json` provides static tactical metadata for every known move. The catalog is organized by character name (`Cowboy`, `Ninja`, `Wizard`, `Robot`, `Mutant`) with a `_universal` section for moves shared across all characters. Each entry includes `category` (offense, defense, grab, movement, utility, special, super), `description`, `speed`, and optionally `damage` and `range`.

During prompt rendering, `_legal_actions_payload()` resolves the active character name from the observation, looks up each legal action in the catalog (character-specific first, then universal), and injects the catalog metadata into the prompt. This gives the LLM human-readable context about what each move does without relying on the game to provide descriptions.

## Response Parsing

`daemon/src/yomi_daemon/response_parser.py` parses raw provider responses into validated `ActionDecision` objects. It supports three parse sources:

- **Structured** — the response is already a mapping or `ProtocolModel`.
- **JSON** — a JSON object is extracted from a text response.
- **Text** — key-value pairs are extracted from freeform text using `key: value` or `key=value` syntax.

Parsing normalizes defaults (fills `match_id`, `turn_id`, `data`, and `extra` when missing), silently strips `extra.prediction` when the chosen action does not support it, and validates the result against the original `DecisionRequest` for action legality, payload conformance, and extra bounds.

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
