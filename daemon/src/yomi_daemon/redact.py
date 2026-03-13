"""Secret redaction utilities for log output and artifact traces."""

from __future__ import annotations

import re

# Patterns that may appear in provider error messages or response bodies.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-or-[A-Za-z0-9_-]+"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*\S+"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"),
)

_REDACTED = "[REDACTED]"


def redact_secrets(text: str) -> str:
    """Remove likely API key patterns from a string."""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(_REDACTED, result)
    return result


def sanitize_provider_error(exc: BaseException) -> str:
    """Return a log-safe summary of a provider exception.

    Strips the full response object and redacts any secret-like patterns,
    keeping only the HTTP status code and sanitized message.
    """
    message = str(exc)
    # Truncate httpx Response repr which may include response body details
    if "Response" in message:
        # Keep text before the Response(...) blob
        idx = message.find("<Response")
        if idx == -1:
            idx = message.find("Response(")
        if idx == -1:
            idx = message.find("Response [")
        if idx > 0:
            message = message[:idx].rstrip(": ")
    return redact_secrets(message)
