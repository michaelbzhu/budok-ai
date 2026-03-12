"""Local harness for the mod bridge handshake contract.

This mirrors the handshake envelope shape expected from `mod/YomiLLMBridge/bridge/BridgeClient.gd`
so repository tests can validate the mod-side contract against the live daemon protocol without
requiring a Godot runtime in CI-like environments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from yomi_daemon.protocol import Envelope, MessageType
from yomi_daemon.validation import parse_envelope


REPO_ROOT = Path(__file__).resolve().parent.parent
MOD_ROOT = REPO_ROOT / "mod" / "YomiLLMBridge"
DEFAULT_CONFIG_PATH = MOD_ROOT / "config" / "default_config.json"
DEFAULT_METADATA_PATH = MOD_ROOT / "_metadata"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_SCHEMA_VERSION = "v1"
DEFAULT_SUPPORTED_VERSIONS = ("v1",)
DEFAULT_TIMEOUT_PROFILE = "strict_local"
DEFAULT_DECISION_TIMEOUT_MS = 2500
DEFAULT_FALLBACK_MODE = "safe_continue"
DEFAULT_GAME_VERSION = "supported-build-16151810"


def load_json_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return document


def load_mod_metadata(path: Path = DEFAULT_METADATA_PATH) -> dict[str, Any]:
    return load_json_object(path)


def load_mod_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    raw = load_json_object(path)

    transport_raw = raw.get("transport")
    transport = transport_raw if isinstance(transport_raw, Mapping) else {}
    protocol_raw = raw.get("protocol")
    protocol = protocol_raw if isinstance(protocol_raw, Mapping) else {}
    logging_raw = raw.get("logging")
    logging = logging_raw if isinstance(logging_raw, Mapping) else {}

    supported_versions_raw = protocol.get("supported_versions")
    supported_versions = (
        tuple(str(version) for version in supported_versions_raw)
        if isinstance(supported_versions_raw, list | tuple) and supported_versions_raw
        else DEFAULT_SUPPORTED_VERSIONS
    )

    return {
        "transport": {
            "host": str(transport.get("host", DEFAULT_HOST)),
            "port": int(transport.get("port", DEFAULT_PORT)),
            "connect_on_ready": bool(transport.get("connect_on_ready", True)),
        },
        "protocol": {
            "schema_version": str(
                protocol.get("schema_version", DEFAULT_SCHEMA_VERSION)
            ),
            "supported_versions": list(supported_versions),
        },
        "game_version": str(raw.get("game_version", DEFAULT_GAME_VERSION)),
        "timeout_profile": str(raw.get("timeout_profile", DEFAULT_TIMEOUT_PROFILE)),
        "decision_timeout_ms": int(
            raw.get("decision_timeout_ms", DEFAULT_DECISION_TIMEOUT_MS)
        ),
        "fallback_mode": str(raw.get("fallback_mode", DEFAULT_FALLBACK_MODE)),
        "logging": {
            "events": bool(logging.get("events", True)),
            "bridge_state": bool(logging.get("bridge_state", True)),
            "raw_messages": bool(logging.get("raw_messages", False)),
        },
    }


def build_hello_envelope(
    *,
    config: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    ts: str = "2026-03-12T00:00:00Z",
) -> dict[str, Any]:
    normalized_config = dict(load_mod_config() if config is None else config)
    normalized_metadata = dict(load_mod_metadata() if metadata is None else metadata)

    protocol_config = normalized_config["protocol"]
    supported_versions = list(protocol_config["supported_versions"])

    return {
        "type": MessageType.HELLO.value,
        "version": supported_versions[0],
        "ts": ts,
        "payload": {
            "game_version": str(
                normalized_metadata.get(
                    "game_version",
                    normalized_config.get("game_version", DEFAULT_GAME_VERSION),
                )
            ),
            "mod_version": str(normalized_metadata.get("version", "0.0.0")),
            "schema_version": str(protocol_config["schema_version"]),
            "supported_protocol_versions": supported_versions,
        },
    }


async def perform_handshake(
    url: str,
    *,
    config: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Envelope:
    hello_envelope = build_hello_envelope(config=config, metadata=metadata)

    async with connect(url) as websocket:
        await websocket.send(json.dumps(hello_envelope))
        response = await websocket.recv()
        if not isinstance(response, str):
            raise TypeError("expected text hello_ack response")
        return parse_envelope(json.loads(response))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("print-hello", "handshake"),
        default="print-hello",
        help="Either print the canonical hello envelope or perform a live handshake.",
    )
    parser.add_argument(
        "--url",
        default=f"ws://{DEFAULT_HOST}:{DEFAULT_PORT}",
        help="WebSocket URL to target when --mode=handshake.",
    )
    args = parser.parse_args()

    if args.mode == "print-hello":
        print(json.dumps(build_hello_envelope(), indent=2, sort_keys=True))
        return

    envelope = asyncio.run(perform_handshake(args.url))
    print(json.dumps(envelope.to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
