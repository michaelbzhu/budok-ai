"""CLI entry points for the daemon package."""

from __future__ import annotations

from yomi_daemon import __version__


def main() -> int:
    print(f"yomi-daemon {__version__} (scaffold)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
