"""CLI entry points for the daemon package."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

from yomi_daemon import __version__
from yomi_daemon.config import ConfigError, RuntimeConfigOverrides, load_runtime_config
from yomi_daemon.server import DaemonServer


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yomi-daemon",
        description="Run the local YOMI Hustle arena daemon.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to a daemon runtime config JSON file.",
    )
    parser.add_argument("--host", default=None, help="Override the WebSocket listen host.")
    parser.add_argument(
        "--port", type=int, default=None, help="Override the WebSocket listen port."
    )
    parser.add_argument(
        "--p1-policy",
        default=None,
        help="Override the policy ID assigned to player slot p1.",
    )
    parser.add_argument(
        "--p2-policy",
        default=None,
        help="Override the policy ID assigned to player slot p2.",
    )
    parser.add_argument(
        "--trace-seed",
        type=int,
        default=None,
        help="Override the manifest trace seed used for reproducibility.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Daemon log verbosity.",
    )
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    runtime_config = load_runtime_config(
        args.config,
        overrides=RuntimeConfigOverrides(
            host=args.host,
            port=args.port,
            p1_policy=args.p1_policy,
            p2_policy=args.p2_policy,
            trace_seed=args.trace_seed,
        ),
    )
    server = DaemonServer(
        host=runtime_config.transport.host,
        port=runtime_config.transport.port,
        policy_mapping=runtime_config.policy_mapping,
        config_snapshot=runtime_config.to_config_payload(),
    )
    await server.start()
    try:
        await server.serve_forever()
    finally:
        await server.stop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        return asyncio.run(_run_async(args))
    except ConfigError as exc:
        logging.getLogger("yomi_daemon.cli").error(str(exc))
        return 2
    except KeyboardInterrupt:
        logging.getLogger("yomi_daemon.cli").info(
            "Stopped daemon %s from CLI interrupt",
            __version__,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
