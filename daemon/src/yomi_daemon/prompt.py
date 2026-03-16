"""Deterministic prompt rendering utilities for provider-backed policies."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from yomi_daemon.protocol import DecisionRequest, JsonObject, default_prediction_spec
from yomi_daemon.validation import REPO_ROOT


PROMPTS_DIR = REPO_ROOT / "prompts"
MOVE_CATALOG_PATH = PROMPTS_DIR / "move_catalog.json"
DEFAULT_PROMPT_VERSION = "minimal_v1"
PROMPT_VERSION_ALIASES = {
    "reasoning_enabled_v1": "reasoning_v1",
}

_move_catalog_cache: JsonObject | None = None


class PromptTemplateError(ValueError):
    """Raised when a configured prompt template cannot be rendered."""


class PromptTemplateVariant(StrEnum):
    MINIMAL = "minimal"
    STRATEGIC = "strategic"
    FEW_SHOT = "few_shot"
    REASONING_ENABLED = "reasoning_enabled"


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    prompt_version: str
    variant: PromptTemplateVariant
    prompt_text: str
    template_path: Path


def resolve_prompt_version(
    request: DecisionRequest,
    *,
    configured_prompt_version: str | None = None,
) -> str:
    selected = request.prompt_version or configured_prompt_version or DEFAULT_PROMPT_VERSION
    return PROMPT_VERSION_ALIASES.get(selected, selected)


def render_prompt(
    request: DecisionRequest,
    *,
    configured_prompt_version: str | None = None,
    policy_id: str | None = None,
) -> RenderedPrompt:
    prompt_version = resolve_prompt_version(
        request,
        configured_prompt_version=configured_prompt_version,
    )
    template_path = PROMPTS_DIR / f"{prompt_version}.md"
    if not template_path.is_file():
        raise PromptTemplateError(
            f"prompt template {prompt_version!r} was not found at {template_path}"
        )

    variant = _variant_for_prompt_version(prompt_version)
    cheat_sheet = _tactical_cheat_sheet(request)
    sections = [
        template_path.read_text(encoding="utf-8").strip(),
        _compact_output_contract(),
        f"## Turn Context\n```json\n{_json_dump(_turn_context(request, policy_id=policy_id))}\n```",
        _situation_summary(request),
        *([cheat_sheet] if cheat_sheet else []),
        f"## Observation\n```json\n{_json_dump(_compact_observation(request))}\n```",
        _grouped_legal_actions_section(request),
    ]

    return RenderedPrompt(
        prompt_version=prompt_version,
        variant=variant,
        prompt_text="\n\n".join(sections).strip() + "\n",
        template_path=template_path,
    )


def _compact_output_contract() -> str:
    """Concise output contract replacing the full JSON schema to save ~1200 tokens."""
    return (
        "## Output Contract\n"
        "Return exactly one JSON object (no Markdown fences). Required field: `action` (string "
        "from the legal actions list). Optional fields: `data` (object, only when action has "
        "payload_spec), `extra` (object with `di: {x,y}` integers -100..100, `feint`: bool, "
        "`reverse`: bool — only include if the action supports them), `reasoning` (string), "
        "`notes` (string).\n"
        'Example: `{"action": "HSlash2", "reasoning": "opponent is blocking, switching to grab next"}`\n'
        'Example with payload: `{"action": "ParryHigh", "data": {"Melee Parry Timing": 10}}`'
    )


def decision_output_json_schema() -> JsonObject:
    """Full schema kept for validation/reference use, no longer rendered in prompts."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "description": "Exact legal action identifier chosen for this turn.",
            },
            "data": {
                "type": "object",
                "description": (
                    "Optional action payload object. Only include keys allowed by the chosen "
                    "legal action's payload_spec."
                ),
                "additionalProperties": True,
            },
            "extra": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "di": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["x", "y"],
                        "properties": {
                            "x": {"type": "integer", "minimum": -100, "maximum": 100},
                            "y": {"type": "integer", "minimum": -100, "maximum": 100},
                        },
                    },
                    "feint": {"type": "boolean"},
                    "reverse": {"type": "boolean"},
                    "prediction": {
                        "anyOf": [
                            default_prediction_spec(),
                            {"type": "null"},
                        ]
                    },
                },
            },
            "notes": {
                "type": "string",
                "description": "Optional brief note about the tactical choice.",
            },
            "reasoning": {
                "type": "string",
                "description": "Optional concise strategic rationale.",
            },
        },
    }


def available_prompt_versions() -> tuple[str, ...]:
    return tuple(path.stem for path in sorted(PROMPTS_DIR.glob("*.md")))


def _variant_for_prompt_version(prompt_version: str) -> PromptTemplateVariant:
    if prompt_version.startswith("minimal_"):
        return PromptTemplateVariant.MINIMAL
    if prompt_version.startswith("strategic_"):
        return PromptTemplateVariant.STRATEGIC
    if prompt_version.startswith("few_shot_"):
        return PromptTemplateVariant.FEW_SHOT
    if prompt_version.startswith("reasoning_"):
        return PromptTemplateVariant.REASONING_ENABLED
    raise PromptTemplateError(f"prompt version {prompt_version!r} does not map to a known variant")


def _turn_context(request: DecisionRequest, *, policy_id: str | None) -> JsonObject:
    ctx: JsonObject = {
        "match_id": request.match_id,
        "turn_id": request.turn_id,
        "player_id": request.player_id,
        "deadline_ms": request.deadline_ms,
        "decision_type": request.decision_type.value,
    }
    # Only include non-null optional fields to reduce prompt size
    if request.trace_seed is not None:
        ctx["trace_seed"] = request.trace_seed
    if policy_id is not None:
        ctx["policy_id"] = policy_id
    return ctx


def _situation_summary(request: DecisionRequest) -> str:
    """Pre-compute a tactical situation summary so the LLM doesn't need to do arithmetic."""
    obs = request.observation
    fighters = obs.fighters
    if len(fighters) < 2:
        return ""

    me = next((f for f in fighters if f.id == request.player_id), fighters[0])
    opp = next((f for f in fighters if f.id != request.player_id), fighters[1])
    distance = abs(me.position.x - opp.position.x)
    if distance < 200:
        range_label = "CLOSE"
    elif distance < 400:
        range_label = "MID"
    else:
        range_label = "FAR"

    # Detect repetition in recent history
    history = obs.history
    my_key = f"{request.player_id}_action"
    recent_actions: list[str] = []
    for entry in history[-5:]:
        d = entry.to_dict()
        action = d.get(my_key)
        if action:
            recent_actions.append(action)
    repetition_warning = ""
    if len(recent_actions) >= 3:
        last_3 = recent_actions[-3:]
        if len(set(last_3)) == 1:
            repetition_warning = (
                f"\n**WARNING: You have used {last_3[0]} for 3+ turns in a row. "
                f"Choose something DIFFERENT this turn.**"
            )

    lines = [
        "## Situation",
        f"- You are **{request.player_id}** ({me.character})",
        f"- Your HP: {me.hp}/{me.max_hp} | Opponent HP: {opp.hp}/{opp.max_hp}",
        f"- Distance: **{int(distance)} units ({range_label} range)**",
        f"- Your state: {me.current_state} | Opponent state: {opp.current_state}",
        f"- Meter: {me.meter} | Burst: {me.burst}",
    ]
    if repetition_warning:
        lines.append(repetition_warning)
    return "\n".join(lines)


def _tactical_cheat_sheet(request: DecisionRequest) -> str:
    """Generate a short tactical suggestion based on opponent state and recent history."""
    obs = request.observation
    fighters = obs.fighters
    if len(fighters) < 2:
        return ""

    opp = next((f for f in fighters if f.id != request.player_id), fighters[1])

    lines = ["## Tactical Cheat Sheet"]

    # Suggestion based on opponent's current state
    opp_state = opp.current_state.lower()
    if any(kw in opp_state for kw in ("attack", "slash", "kick", "punch", "combo", "shoot")):
        lines.append(
            "- Opponent is ATTACKING: **Block (ParryHigh), SpotDodge, or Roll to counter**"
        )
    elif any(kw in opp_state for kw in ("block", "parry", "guard")):
        lines.append(
            "- Opponent is BLOCKING: **Grab or command grab to throw through their guard**"
        )
    elif any(kw in opp_state for kw in ("grab", "throw", "lasso")):
        lines.append("- Opponent is GRABBING: **Any attack beats a grab — use a fast attack**")
    elif any(kw in opp_state for kw in ("whiff", "recovery", "landing", "endlag")):
        lines.append(
            "- Opponent is in RECOVERY: **Punish with a high-damage move (Stinger, VSlash, 3Combo)**"
        )
    else:
        lines.append("- Opponent is NEUTRAL: **Mix unpredictably between attack, grab, and block**")

    # Suggestion based on opponent's recent history pattern
    opp_key = "p1_action" if request.player_id == "p2" else "p2_action"
    recent_opp_actions: list[str] = []
    for entry in obs.history[-5:]:
        d = entry.to_dict()
        action = d.get(opp_key)
        if action:
            recent_opp_actions.append(action)

    if len(recent_opp_actions) >= 3:
        last_3 = recent_opp_actions[-3:]
        if len(set(last_3)) == 1:
            lines.append(
                f"- Opponent used **{last_3[0]}** 3x in a row — they are predictable. "
                f"Choose the counter (block beats attacks, attack beats grabs, grab beats blocks)."
            )
        elif len(recent_opp_actions) >= 4:
            # Check for alternating pattern
            last_4 = recent_opp_actions[-4:]
            if last_4[0] == last_4[2] and last_4[1] == last_4[3] and last_4[0] != last_4[1]:
                lines.append(
                    f"- Opponent alternates **{last_4[0]}** / **{last_4[1]}** — "
                    f"break the pattern with a DODGE, ROLL, or unexpected option."
                )

    # Outcome-based suggestion from recent history
    my_key = f"{request.player_id}_outcome"
    recent_outcomes: list[str] = []
    for entry in obs.history[-3:]:
        d = entry.to_dict()
        outcome = d.get(my_key)
        if outcome:
            recent_outcomes.append(outcome)

    if recent_outcomes:
        hit_count = sum(1 for o in recent_outcomes if o == "hit" and o != "")
        blocked_count = sum(1 for o in recent_outcomes if o in ("blocked", "clashed"))
        if hit_count >= 2:
            lines.append(
                "- Your attacks are LANDING — keep up the pressure but stay unpredictable."
            )
        elif blocked_count >= 2:
            lines.append(
                "- Your attacks are being BLOCKED — switch to grabs or try a different timing."
            )

    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def _compact_observation(request: DecisionRequest) -> JsonObject:
    """Produce a compact observation dict that strips redundant/verbose fields."""
    obs = request.observation.to_dict()
    # Strip history from observation — it's already summarized in Situation/Cheat Sheet
    # and the full entries are verbose. Keep last 5 entries with compact format.
    if "history" in obs and isinstance(obs["history"], list):
        compact_history = []
        for entry in obs["history"][-5:]:
            if not isinstance(entry, dict):
                continue
            compact: JsonObject = {}
            if "game_tick" in entry:
                compact["tick"] = entry["game_tick"]
            elif "turn_id" in entry:
                compact["turn_id"] = entry["turn_id"]
            for key in (
                "p1_action",
                "p2_action",
                "p1_hp",
                "p2_hp",
                "p1_hp_delta",
                "p2_hp_delta",
                "p1_outcome",
                "p2_outcome",
            ):
                if key in entry and entry[key] is not None:
                    compact[key] = entry[key]
            compact_history.append(compact)
        obs["history"] = compact_history
    return obs


def _grouped_legal_actions_section(request: DecisionRequest) -> str:
    """Render legal actions grouped by category instead of a flat list."""
    actions = _legal_actions_payload(request)
    groups: dict[str, list[JsonObject]] = {}
    for action in actions:
        cat = str(action.get("category", "other"))
        if cat not in groups:
            groups[cat] = []
        groups[cat].append(action)

    # Preferred category order
    order = ["offense", "defense", "grab", "movement", "special", "super", "utility", "other"]
    lines = ["## Legal Actions"]
    for cat in order:
        if cat not in groups:
            continue
        lines.append(f"\n### {cat.title()}")
        lines.append(f"```json\n{_json_dump(groups[cat])}\n```")
        del groups[cat]
    # Any remaining categories
    for cat, acts in groups.items():
        lines.append(f"\n### {cat.title()}")
        lines.append(f"```json\n{_json_dump(acts)}\n```")
    return "\n".join(lines)


def _legal_actions_payload(request: DecisionRequest) -> list[JsonObject]:
    # Resolve character name from the active player's fighter observation
    character_name = _resolve_active_character(request)
    catalog = _load_move_catalog()
    universal_moves = cast(JsonObject, catalog.get("_universal", {}))
    character_moves = cast(JsonObject, catalog.get(character_name, {})) if character_name else {}

    result: list[JsonObject] = []
    for action in request.legal_actions:
        # Look up catalog entry: character-specific first, then universal
        catalog_entry = cast(
            JsonObject,
            character_moves.get(action.action, universal_moves.get(action.action, {})),
        )

        entry: JsonObject = {
            "action": action.action,
            "label": action.label,
        }

        # Inject catalog description and tactical metadata
        desc = action.description
        if not desc and isinstance(catalog_entry, dict):
            desc = catalog_entry.get("description")
        if desc:
            entry["description"] = desc

        cat = catalog_entry.get("category") if isinstance(catalog_entry, dict) else None
        if cat:
            entry["category"] = cat
        spd = catalog_entry.get("speed") if isinstance(catalog_entry, dict) else None
        if spd:
            entry["speed"] = spd
        dmg = catalog_entry.get("damage") if isinstance(catalog_entry, dict) else None
        if dmg:
            entry["damage"] = dmg
        rng = catalog_entry.get("range") if isinstance(catalog_entry, dict) else None
        if rng:
            entry["range"] = rng
        beats = catalog_entry.get("beats") if isinstance(catalog_entry, dict) else None
        if beats:
            entry["beats"] = beats
        weakness = catalog_entry.get("weakness") if isinstance(catalog_entry, dict) else None
        if weakness:
            entry["weakness"] = weakness

        # Only include non-trivial payload_specs (omit zero-range XY plots)
        if action.payload_spec and not _is_zero_range_payload(action.payload_spec):
            entry["payload_spec"] = action.payload_spec
        if action.prediction_spec:
            entry["prediction_spec"] = action.prediction_spec
        # Only show supports flags that are true to save tokens
        true_supports = {k: v for k, v in action.supports.to_dict().items() if v}
        if true_supports:
            entry["supports"] = true_supports
        result.append(entry)
    return result


def _is_zero_range_payload(payload_spec: JsonObject) -> bool:
    """Check if all fields in a payload_spec have zero-choice ranges and can be auto-defaulted.

    Returns True only if every field is either:
    - A numeric field with min == max (no real choice)
    - An XY field where both axes have min == max
    - A boolean field (defaults to false)

    When True, the prompt omits the payload_spec and the response parser auto-fills defaults.
    """
    props = payload_spec.get("properties")
    if not isinstance(props, dict) or not props:
        return False
    for field in props.values():
        if not isinstance(field, dict):
            continue
        field_type = field.get("type")
        # Boolean fields always default to false — zero-choice
        if field_type == "boolean":
            continue
        field_min = field.get("minimum")
        field_max = field.get("maximum")
        if field_min is not None and field_max is not None and field_min == field_max:
            continue  # Zero range numeric
        # Check nested xy properties
        nested_props = field.get("properties")
        if isinstance(nested_props, dict):
            all_zero = True
            for nested in nested_props.values():
                if not isinstance(nested, dict):
                    continue
                n_min = nested.get("minimum")
                n_max = nested.get("maximum")
                if n_min is None or n_max is None or n_min != n_max:
                    all_zero = False
                    break
            if all_zero:
                continue
        # Has a real choice — not zero-range
        return False
    return True


def _resolve_active_character(request: DecisionRequest) -> str | None:
    """Resolve the character name for the active player from the observation."""
    for fighter in request.observation.fighters:
        if fighter.id == request.player_id:
            # Character name from observation (e.g. "P1", "Ninja", "Mutant")
            name = fighter.character
            # The game uses generic names like "P1"/"P2" when character_selection
            # is mirror mode; try to match against known catalog characters
            if name and name not in ("P1", "P2"):
                return name
    return None


def _load_move_catalog() -> JsonObject:
    """Load and cache the move catalog from prompts/move_catalog.json."""
    global _move_catalog_cache  # noqa: PLW0603
    if _move_catalog_cache is not None:
        return _move_catalog_cache
    if not MOVE_CATALOG_PATH.is_file():
        _move_catalog_cache = {}
        return _move_catalog_cache
    raw = json.loads(MOVE_CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        _move_catalog_cache = {}
        return _move_catalog_cache
    _move_catalog_cache = cast(JsonObject, raw)
    return _move_catalog_cache


def _json_dump(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)
