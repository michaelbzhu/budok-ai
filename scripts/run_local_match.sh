#!/usr/bin/env bash
# Run a single autonomous match between two policies.
#
# Usage:
#   scripts/run_local_match.sh [OPTIONS]
#
# Options:
#   --config PATH     Path to a daemon runtime config JSON file.
#   --p1-policy ID    Policy ID for player 1 (default: baseline/random).
#   --p2-policy ID    Policy ID for player 2 (default: baseline/random).
#   --trace-seed INT  Reproducibility seed.
#   --log-level LVL   Log verbosity: DEBUG, INFO, WARNING, ERROR (default: INFO).
#   --host HOST       WebSocket listen host (default: 127.0.0.1).
#   --port PORT       WebSocket listen port (default: 8765).
#
# Example (baseline vs baseline):
#   scripts/run_local_match.sh --p1-policy baseline/random --p2-policy baseline/block_always
#
# Example (with custom config):
#   scripts/run_local_match.sh --config daemon/config/default_config.json --trace-seed 42
#
# The daemon starts, waits for the mod to connect and play a match, then
# writes auditable artifacts under runs/<timestamp>_<match_id>/.
#
# Prerequisites:
#   - uv must be installed
#   - Run from the repository root
#   - For provider-backed policies, set the corresponding API key env vars
#     (OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

exec uv run --project daemon yomi-daemon "$@"
