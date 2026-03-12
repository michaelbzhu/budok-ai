#!/usr/bin/env bash
set -euo pipefail

# Run a round-robin tournament using the daemon's tournament runner.
# Usage: scripts/run_round_robin.sh [--config CONFIG] [POLICY_IDS...]
#
# Example:
#   scripts/run_round_robin.sh baseline/random baseline/block_always baseline/greedy_damage
#
# The runner generates pairings, executes matches via the daemon server
# (requires a game instance to connect), and outputs a tournament report.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

exec uv run --project "$REPO_ROOT/daemon" python -m yomi_daemon.tournament.cli "$@"
