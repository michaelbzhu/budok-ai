#!/usr/bin/env python3
"""Validate that every JSON schema file in the repository parses cleanly."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "schemas"


def parse_schemas(schema_dir: Path = SCHEMA_DIR) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for schema_path in sorted(schema_dir.glob("*.json")):
        with schema_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        results.append(
            {
                "path": str(schema_path.relative_to(REPO_ROOT)),
                "title": payload.get("title"),
            }
        )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable output.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    results = parse_schemas()
    if args.json:
        print(json.dumps({"schema_count": len(results), "schemas": results}, indent=2))
        return 0

    print(f"Parsed {len(results)} schema file(s) from {SCHEMA_DIR}.")
    for result in results:
        print(f"- {result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
