from __future__ import annotations

import asyncio

import pytest

from tests.daemon._decision_fixtures import build_action, build_request
from yomi_daemon.protocol import FallbackReason
from yomi_daemon.response_parser import (
    ParseSource,
    ResponseParsingConfig,
    ResponseParsingError,
    parse_action_decision_response,
    parse_action_decision_with_correction,
)


def test_structured_parse_succeeds_and_defaults_request_ids() -> None:
    request = build_request(
        (
            build_action(
                "slash",
                payload_spec={"target": {"type": "enemy"}},
                reverse=True,
            ),
        )
    )

    parsed = parse_action_decision_response(
        {
            "action": "slash",
            "data": {"target": "enemy"},
            "extra": {"reverse": True},
        },
        request,
    )

    assert parsed.source is ParseSource.STRUCTURED
    assert parsed.decision.match_id == request.match_id
    assert parsed.decision.turn_id == request.turn_id
    assert parsed.decision.extra.reverse is True
    assert parsed.decision.extra.feint is False
    assert parsed.decision.extra.di is None


def test_json_parse_extracts_first_json_object_from_wrapped_text() -> None:
    request = build_request((build_action("guard", description="Block safely."),))

    parsed = parse_action_decision_response(
        """
        I recommend guarding here.

        ```json
        {"action":"guard","data":{},"extra":{"feint":false,"reverse":false,"di":null}}
        ```
        """,
        request,
    )

    assert parsed.source is ParseSource.JSON
    assert parsed.decision.action == "guard"


def test_text_parse_extracts_constrained_fields() -> None:
    request = build_request(
        (
            build_action(
                "slash",
                payload_spec={"target": {"type": "enemy"}},
                reverse=True,
            ),
        )
    )

    parsed = parse_action_decision_response(
        'action: slash\ndata: {"target":"enemy"}\nreverse: true',
        request,
    )

    assert parsed.source is ParseSource.TEXT
    assert parsed.decision.action == "slash"
    assert parsed.decision.data == {"target": "enemy"}
    assert parsed.decision.extra.reverse is True


def test_correction_retry_is_bounded_and_uses_callback() -> None:
    request = build_request((build_action("guard", description="Block safely."),))
    prompts: list[str] = []

    async def correction_callback(prompt: str) -> object:
        prompts.append(prompt)
        return {"action": "guard"}

    parsed = asyncio.run(
        parse_action_decision_with_correction(
            '{"action":"teleport"}',
            request,
            correction_callback=correction_callback,
            config=ResponseParsingConfig(
                enable_correction_retry=True, max_correction_retries=1
            ),
        )
    )

    assert parsed.correction_attempts == 1
    assert parsed.decision.action == "guard"
    assert len(prompts) == 1
    assert "Return ONLY a JSON object" in prompts[0]
    assert 'Choose "action" from: guard.' in prompts[0]


def test_parse_rejects_stale_match_and_turn_ids() -> None:
    request = build_request((build_action("guard"),))

    with pytest.raises(ResponseParsingError) as exc_info:
        parse_action_decision_response(
            {
                "match_id": "match-other",
                "turn_id": request.turn_id + 1,
                "action": "guard",
                "data": {},
                "extra": {"di": None, "feint": False, "reverse": False},
            },
            request,
        )

    assert exc_info.value.fallback_reason is FallbackReason.STALE_RESPONSE


def test_parse_rejects_unsupported_extras_and_malformed_payloads() -> None:
    request = build_request(
        (
            build_action(
                "slash",
                payload_spec={"target": {"type": "enemy"}},
                reverse=False,
            ),
        )
    )

    with pytest.raises(ResponseParsingError) as extras_error:
        parse_action_decision_response(
            {
                "action": "slash",
                "data": {"target": "enemy"},
                "extra": {"di": None, "feint": False, "reverse": True},
            },
            request,
        )
    assert extras_error.value.fallback_reason is FallbackReason.ILLEGAL_OUTPUT

    with pytest.raises(ResponseParsingError) as payload_error:
        parse_action_decision_response(
            {
                "action": "slash",
                "data": {"unknown": "enemy"},
                "extra": {"di": None, "feint": False, "reverse": False},
            },
            request,
        )
    assert payload_error.value.fallback_reason is FallbackReason.ILLEGAL_OUTPUT


def test_parse_rejects_out_of_bounds_di_vectors() -> None:
    request = build_request((build_action("throw", di=True),))

    with pytest.raises(ResponseParsingError) as exc_info:
        parse_action_decision_response(
            {
                "action": "throw",
                "data": {},
                "extra": {"di": {"x": 101, "y": 0}, "feint": False, "reverse": False},
            },
            request,
        )

    assert exc_info.value.fallback_reason is FallbackReason.MALFORMED_OUTPUT


def test_parser_config_from_policy_options_enforces_single_retry_bound() -> None:
    config = ResponseParsingConfig.from_policy_options(
        {
            "response_parser": {
                "enable_correction_retry": True,
                "max_correction_retries": 1,
            }
        }
    )

    assert config.enable_correction_retry is True
    assert config.max_correction_retries == 1

    with pytest.raises(ValueError, match="0 or 1"):
        ResponseParsingConfig.from_policy_options(
            {
                "response_parser": {
                    "enable_correction_retry": True,
                    "max_correction_retries": 2,
                }
            }
        )
