"""Identifier helpers shared across daemon runtime paths."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from itertools import count


TokenFactory = Callable[[], str]


def random_hex_token(length: int = 16, *, token_bytes: Callable[[int], bytes] | None = None) -> str:
    """Return a hex token with a deterministic injection point for tests."""

    if length <= 0:
        raise ValueError("length must be positive")
    if length % 2 != 0:
        raise ValueError("length must be even so the hex token is whole-byte aligned")
    token_source = token_bytes or secrets.token_bytes
    return token_source(length // 2).hex()


def prefixed_identifier(prefix: str, token: str) -> str:
    normalized_prefix = prefix.strip().lower()
    normalized_token = token.strip().lower()
    if not normalized_prefix:
        raise ValueError("prefix must not be empty")
    if not normalized_token:
        raise ValueError("token must not be empty")
    return f"{normalized_prefix}-{normalized_token}"


def new_match_id(*, token_factory: TokenFactory | None = None) -> str:
    return prefixed_identifier("match", (token_factory or random_hex_token)())


def new_benchmark_id(*, token_factory: TokenFactory | None = None) -> str:
    return prefixed_identifier("bench", (token_factory or random_hex_token)())


@dataclass(slots=True)
class SessionIdGenerator:
    """Generate stable session identifiers for a single daemon process."""

    prefix: str = "session"
    width: int = 4
    _counter: Iterator[int] = field(default_factory=lambda: count(1), repr=False)

    def next(self) -> str:
        if self.width <= 0:
            raise ValueError("width must be positive")
        return f"{self.prefix}-{next(self._counter):0{self.width}d}"
