from __future__ import annotations

import pytest

from yomi_daemon.ids import (
    SessionIdGenerator,
    new_benchmark_id,
    new_match_id,
    prefixed_identifier,
    random_hex_token,
)


def test_session_id_generator_is_deterministic() -> None:
    generator = SessionIdGenerator()

    assert generator.next() == "session-0001"
    assert generator.next() == "session-0002"
    assert generator.next() == "session-0003"


def test_match_and_benchmark_ids_use_injected_tokens() -> None:
    assert new_match_id(token_factory=lambda: "ABC123") == "match-abc123"
    assert new_benchmark_id(token_factory=lambda: "feedbeef") == "bench-feedbeef"


def test_random_hex_token_accepts_deterministic_byte_source() -> None:
    assert random_hex_token(8, token_bytes=lambda _: b"\x01\x02\x03\x04") == "01020304"


def test_prefixed_identifier_rejects_empty_inputs() -> None:
    with pytest.raises(ValueError, match="prefix"):
        prefixed_identifier("", "abc")
    with pytest.raises(ValueError, match="token"):
        prefixed_identifier("match", "")


def test_random_hex_token_requires_even_length() -> None:
    with pytest.raises(ValueError, match="even"):
        random_hex_token(3)
