from __future__ import annotations

from dataclasses import replace

from tests.daemon._decision_fixtures import build_action, build_request
from yomi_daemon.prompt import (
    PromptTemplateVariant,
    available_prompt_versions,
    render_prompt,
)


def test_render_prompt_is_deterministic_for_same_request() -> None:
    request = build_request(
        (
            build_action("guard", description="Block safely."),
            build_action(
                "slash",
                payload_spec={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["target"],
                    "properties": {
                        "target": {
                            "type": "string",
                            "enum": ["enemy", "self"],
                            "semantic_hint": "throw_target",
                        }
                    },
                },
                prediction=True,
                prediction_spec={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["horizon"],
                    "properties": {
                        "horizon": {"type": "integer", "minimum": 1, "maximum": 3},
                        "confidence": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                    },
                },
                damage=80.0,
                startup_frames=5,
            ),
        )
    )

    first = render_prompt(
        request,
        configured_prompt_version="strategic_v1",
        policy_id="provider/openai-main",
    )
    second = render_prompt(
        request,
        configured_prompt_version="strategic_v1",
        policy_id="provider/openai-main",
    )

    assert first == second
    assert first.prompt_version == "strategic_v1"
    assert first.variant is PromptTemplateVariant.STRATEGIC
    assert "## Observation" in first.prompt_text
    assert '"policy_id": "provider/openai-main"' in first.prompt_text
    assert '"minimum": -100' in first.prompt_text
    assert '"prediction_spec"' in first.prompt_text


def test_render_prompt_prefers_request_prompt_version_over_policy_default() -> None:
    request = replace(
        build_request((build_action("guard"),)),
        prompt_version="few_shot_v1",
    )

    rendered = render_prompt(
        request,
        configured_prompt_version="minimal_v1",
        policy_id="provider/openai-main",
    )

    assert rendered.prompt_version == "few_shot_v1"
    assert rendered.variant is PromptTemplateVariant.FEW_SHOT
    assert "Follow these examples" in rendered.prompt_text


def test_available_prompt_versions_lists_current_template_inventory() -> None:
    assert available_prompt_versions() == (
        "few_shot_v1",
        "minimal_v1",
        "reasoning_v1",
        "strategic_v1",
    )
