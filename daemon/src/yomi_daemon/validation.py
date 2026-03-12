"""Schema-backed validation helpers for protocol payloads and envelopes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError, best_match

from yomi_daemon.protocol import (
    CURRENT_PROTOCOL_VERSION,
    PAYLOAD_TYPE_BY_MESSAGE_TYPE,
    Envelope,
    MessageType,
    ProtocolModel,
    ProtocolVersion,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "schemas"

ENVELOPE_SCHEMA_FILE = "envelope.json"
SCHEMA_FILE_BY_MESSAGE_TYPE: dict[MessageType, str] = {
    MessageType.HELLO: "hello.v1.json",
    MessageType.HELLO_ACK: "hello-ack.v1.json",
    MessageType.DECISION_REQUEST: "decision-request.v1.json",
    MessageType.ACTION_DECISION: "action-decision.v1.json",
    MessageType.EVENT: "event.v1.json",
    MessageType.MATCH_ENDED: "match-ended.v1.json",
    MessageType.CONFIG: "config.v1.json",
}


class ProtocolValidationError(ValueError):
    """Raised when a protocol payload or envelope fails validation."""

    def __init__(
        self,
        message: str,
        *,
        schema_name: str,
        location: tuple[str | int, ...] = (),
    ) -> None:
        super().__init__(message)
        self.schema_name = schema_name
        self.location = location


def _schema_path(filename: str) -> Path:
    return SCHEMA_DIR / filename


@cache
def load_schema(filename: str) -> dict[str, Any]:
    with _schema_path(filename).open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return schema


@cache
def load_all_schemas() -> dict[str, dict[str, Any]]:
    return {path.name: load_schema(path.name) for path in sorted(SCHEMA_DIR.glob("*.json"))}


def _validator_for(filename: str) -> Any:
    return Draft202012Validator(load_schema(filename), format_checker=FormatChecker())


def _error_path(error: ValidationError) -> tuple[str | int, ...]:
    return tuple(error.absolute_path)


def _format_validation_error(
    error: ValidationError, *, schema_name: str
) -> ProtocolValidationError:
    location = _error_path(error)
    location_text = ".".join(str(part) for part in location) if location else "<root>"
    return ProtocolValidationError(
        f"{schema_name} validation failed at {location_text}: {error.message}",
        schema_name=schema_name,
        location=location,
    )


def _best_error(validator: Any, instance: Any) -> ValidationError | None:
    errors = list(validator.iter_errors(instance))
    if not errors:
        return None
    return best_match(errors)


def ensure_supported_protocol_version(version: str | ProtocolVersion) -> ProtocolVersion:
    try:
        normalized = ProtocolVersion(version)
    except ValueError as exc:
        supported = ", ".join(item.value for item in ProtocolVersion)
        raise ProtocolValidationError(
            f"Unsupported protocol version {version!r}. Supported versions: {supported}",
            schema_name=ENVELOPE_SCHEMA_FILE,
            location=("version",),
        ) from exc
    return normalized


def validate_payload(
    message_type: str | MessageType,
    payload: Mapping[str, object],
    *,
    version: str | ProtocolVersion = CURRENT_PROTOCOL_VERSION,
) -> None:
    normalized_version = ensure_supported_protocol_version(version)
    if normalized_version is not CURRENT_PROTOCOL_VERSION:
        raise ProtocolValidationError(
            f"Unsupported protocol version {normalized_version.value!r}",
            schema_name=ENVELOPE_SCHEMA_FILE,
            location=("version",),
        )

    normalized_type = MessageType(message_type)
    schema_name = SCHEMA_FILE_BY_MESSAGE_TYPE[normalized_type]
    validator = _validator_for(schema_name)
    if error := _best_error(validator, payload):
        raise _format_validation_error(error, schema_name=schema_name)


def validate_envelope(envelope: Mapping[str, object]) -> None:
    envelope_validator = _validator_for(ENVELOPE_SCHEMA_FILE)
    if error := _best_error(envelope_validator, envelope):
        raise _format_validation_error(error, schema_name=ENVELOPE_SCHEMA_FILE)

    normalized_type = MessageType(str(envelope["type"]))
    validate_payload(
        normalized_type,
        envelope["payload"],  # type: ignore[arg-type]
        version=str(envelope["version"]),
    )


def validate_model(
    model: ProtocolModel, *, version: str | ProtocolVersion = CURRENT_PROTOCOL_VERSION
) -> None:
    if isinstance(model, Envelope):
        validate_envelope(model.to_dict())
        return

    for message_type, payload_type in PAYLOAD_TYPE_BY_MESSAGE_TYPE.items():
        if isinstance(model, payload_type):
            validate_payload(message_type, model.to_dict(), version=version)
            return

    raise TypeError(f"Unsupported protocol model: {type(model)!r}")


def parse_envelope(envelope: Mapping[str, object]) -> Envelope:
    validate_envelope(envelope)
    return Envelope.from_dict(envelope)
