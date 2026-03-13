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

`render_prompt()` assembles a complete prompt from four sections appended to the template body:

1. **Template body** — the raw markdown from the selected `prompts/<version>.md` file.
2. **Output contract** — a fixed section describing the expected JSON output shape, including the decision JSON schema with `action`, optional `data`, optional `extra` (DI, feint, reverse, prediction), and optional `notes`/`reasoning` fields.
3. **Turn context** — a JSON block with `match_id`, `turn_id`, `player_id`, `deadline_ms`, `decision_type`, `trace_seed`, `policy_id`, hashes, and version metadata.
4. **Observation** — the full serialized observation for the current turn.
5. **Legal actions** — the list of legal actions with `action`, `label`, `description`, tactical metadata, `payload_spec`, `prediction_spec`, and `supports` flags.

The output contract instructs the model to return exactly one JSON object (no Markdown fences), choose `action` from the legal set, only include `data` when required by the action's `payload_spec`, and prefer lower-commitment options when multiple actions look equivalent.

## Response Parsing

`daemon/src/yomi_daemon/response_parser.py` parses raw provider responses into validated `ActionDecision` objects. It supports three parse sources:

- **Structured** — the response is already a mapping or `ProtocolModel`.
- **JSON** — a JSON object is extracted from a text response.
- **Text** — key-value pairs are extracted from freeform text using `key: value` or `key=value` syntax.

Parsing normalizes defaults (fills `match_id`, `turn_id`, `data`, and `extra` when missing) and validates the result against the original `DecisionRequest` for action legality, payload conformance, and extra bounds.

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
