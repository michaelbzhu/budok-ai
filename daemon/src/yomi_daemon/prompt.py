"""Deterministic prompt rendering utilities for provider-backed policies."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from yomi_daemon.protocol import DecisionRequest, JsonObject
from yomi_daemon.validation import REPO_ROOT


PROMPTS_DIR = REPO_ROOT / "prompts"
DEFAULT_PROMPT_VERSION = "minimal_v1"
PROMPT_VERSION_ALIASES = {
    "reasoning_enabled_v1": "reasoning_v1",
}


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
    sections = [
        template_path.read_text(encoding="utf-8").strip(),
        "## Output Contract\n"
        "Return exactly one JSON object. Never include Markdown fences.\n"
        "Choose `action` from the legal actions listed below. Only include `data` when the chosen "
        "action requires payload fields. Only include `extra` fields that the chosen action "
        "supports; otherwise omit them and the daemon will apply safe defaults.\n"
        "If multiple actions look equivalent, prefer the lower-commitment option.\n"
        f"Target schema:\n```json\n{_json_dump(decision_output_json_schema())}\n```",
        f"## Turn Context\n```json\n{_json_dump(_turn_context(request, policy_id=policy_id))}\n```",
        f"## Observation\n```json\n{_json_dump(request.observation.to_dict())}\n```",
        f"## Legal Actions\n```json\n{_json_dump(_legal_actions_payload(request))}\n```",
    ]

    return RenderedPrompt(
        prompt_version=prompt_version,
        variant=variant,
        prompt_text="\n\n".join(sections).strip() + "\n",
        template_path=template_path,
    )


def decision_output_json_schema() -> JsonObject:
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
                            "x": {"type": "integer", "minimum": -1, "maximum": 1},
                            "y": {"type": "integer", "minimum": -1, "maximum": 1},
                        },
                    },
                    "feint": {"type": "boolean"},
                    "reverse": {"type": "boolean"},
                    "prediction": {
                        "type": ["object", "null"],
                        "additionalProperties": True,
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
    return {
        "match_id": request.match_id,
        "turn_id": request.turn_id,
        "player_id": request.player_id,
        "deadline_ms": request.deadline_ms,
        "decision_type": request.decision_type.value,
        "trace_seed": request.trace_seed,
        "policy_id": policy_id,
        "state_hash": request.state_hash,
        "legal_actions_hash": request.legal_actions_hash,
        "game_version": request.game_version,
        "mod_version": request.mod_version,
        "schema_version": request.schema_version,
        "ruleset_id": request.ruleset_id,
        "prompt_version": request.prompt_version,
    }


def _legal_actions_payload(request: DecisionRequest) -> list[JsonObject]:
    return [
        {
            "action": action.action,
            "label": action.label,
            "description": action.description,
            "damage": action.damage,
            "startup_frames": action.startup_frames,
            "range": action.range,
            "meter_cost": action.meter_cost,
            "payload_spec": action.payload_spec,
            "supports": action.supports.to_dict(),
        }
        for action in request.legal_actions
    ]


def _json_dump(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)
