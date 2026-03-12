"""CLI entry points for the daemon package."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence

from yomi_daemon import __version__
from yomi_daemon.protocol import PlayerPolicyMapping
from yomi_daemon.server import DEFAULT_HOST, DEFAULT_PORT, DaemonServer


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yomi-daemon",
        description="Run the local YOMI Hustle arena daemon.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="WebSocket listen host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="WebSocket listen port.")
    parser.add_argument(
        "--p1-policy",
        default="baseline/random",
        help="Policy ID assigned to player slot p1.",
    )
    parser.add_argument(
        "--p2-policy",
        default="baseline/random",
        help="Policy ID assigned to player slot p2.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Daemon log verbosity.",
    )
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    server = DaemonServer(
        host=args.host,
        port=args.port,
        policy_mapping=PlayerPolicyMapping(p1=args.p1_policy, p2=args.p2_policy),
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
    except KeyboardInterrupt:
        logging.getLogger("yomi_daemon.cli").info(
            "Stopped daemon %s from CLI interrupt",
            __version__,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
