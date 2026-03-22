#!/usr/bin/env bash
# Run a live local match: start the daemon, wait for the game mod to connect,
# play one match, and report artifacts.
#
# Usage:
#   scripts/run_live_match.sh [OPTIONS]
#
# Options:
#   --game-dir PATH     Path to YOMI Hustle installation (for pre-flight check).
#   --config PATH       Path to a daemon runtime config JSON file.
#   --p1-policy ID      Policy ID for player 1 (default: baseline/random).
#   --p2-policy ID      Policy ID for player 2 (default: baseline/random).
#   --host HOST         WebSocket listen host (default: 127.0.0.1).
#   --port PORT         WebSocket listen port (default: 8765).
#   --trace-seed INT    Reproducibility seed.
#   --log-level LVL     Log verbosity (default: INFO).
#   --skip-install      Skip mod installation check.
#
# Example (baseline vs baseline):
#   scripts/run_live_match.sh --game-dir ~/Library/Application\ Support/Steam/steamapps/common/YomiHustle
#
# Example (provider-backed):
#   ANTHROPIC_API_KEY=sk-ant-... scripts/run_live_match.sh \
#     --p1-policy anthropic/claude --p2-policy baseline/random
#
# Prerequisites:
#   - uv must be installed
#   - Run from the repository root
#   - The mod must be packaged (scripts/package_mod.sh) and installed
#   - For provider-backed policies, set the corresponding API key env vars

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# --- Defaults ---

GAME_DIR=""
SKIP_INSTALL=false
DAEMON_ARGS=()

# --- Parse arguments ---

while [[ $# -gt 0 ]]; do
    case "$1" in
        --game-dir)
            GAME_DIR="$2"
            shift 2
            ;;
        --game-dir=*)
            GAME_DIR="${1#*=}"
            shift
            ;;
        --skip-install)
            SKIP_INSTALL=true
            shift
            ;;
        --config|--p1-policy|--p2-policy|--host|--port|--trace-seed|--log-level)
            DAEMON_ARGS+=("$1" "$2")
            shift 2
            ;;
        -h|--help)
            head -30 "$0" | tail -28
            exit 0
            ;;
        *)
            DAEMON_ARGS+=("$1")
            shift
            ;;
    esac
done

# --- Pre-flight checks ---

# Check uv
if ! command -v uv &>/dev/null; then
    printf 'ERROR: uv is not installed. Install it from https://docs.astral.sh/uv/\n' >&2
    exit 1
fi

# Check mod is packaged
MOD_ZIP="$REPO_ROOT/dist/YomiLLMBridge.zip"
if [ ! -f "$MOD_ZIP" ]; then
    printf 'ERROR: Mod zip not found at %s\n' "$MOD_ZIP" >&2
    printf '  Run: scripts/package_mod.sh\n' >&2
    exit 1
fi

# Check game directory and mod installation if provided
if [ -n "$GAME_DIR" ] && [ "$SKIP_INSTALL" = false ]; then
    if [ ! -d "$GAME_DIR" ]; then
        printf 'ERROR: Game directory does not exist: %s\n' "$GAME_DIR" >&2
        exit 1
    fi
    if [ ! -f "$GAME_DIR/project.godot" ] && [ ! -f "$GAME_DIR/project.binary" ]; then
        printf 'ERROR: Directory does not look like a YOMI Hustle installation: %s\n' "$GAME_DIR" >&2
        exit 1
    fi
    if [ ! -d "$GAME_DIR/mods/YomiLLMBridge" ] && [ ! -f "$GAME_DIR/mods/YomiLLMBridge.zip" ]; then
        printf 'WARNING: Mod not installed in game directory.\n' >&2
        printf '  Run: scripts/install_mod.sh --game-dir "%s"\n' "$GAME_DIR" >&2
        printf '  Continuing anyway — the daemon will start but the game needs the mod to connect.\n' >&2
    fi
fi

# Check provider credentials if provider policies are requested
for arg in "${DAEMON_ARGS[@]+"${DAEMON_ARGS[@]}"}"; do
    case "$arg" in
        anthropic/*)
            if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
                printf 'WARNING: ANTHROPIC_API_KEY not set but an Anthropic policy is configured.\n' >&2
            fi
            ;;
        openai/*)
            if [ -z "${OPENAI_API_KEY:-}" ]; then
                printf 'WARNING: OPENAI_API_KEY not set but an OpenAI policy is configured.\n' >&2
            fi
            ;;
        openrouter/*)
            if [ -z "${OPENROUTER_API_KEY:-}" ]; then
                printf 'WARNING: OPENROUTER_API_KEY not set but an OpenRouter policy is configured.\n' >&2
            fi
            ;;
    esac
done

# --- Start daemon ---

printf '=== budok-ai — Live Match ===\n'
printf 'Starting daemon...\n'

# Determine the port for status messages
DAEMON_PORT=8765
for i in "${!DAEMON_ARGS[@]}"; do
    if [ "${DAEMON_ARGS[$i]}" = "--port" ] 2>/dev/null; then
        DAEMON_PORT="${DAEMON_ARGS[$((i+1))]}"
    fi
done

DAEMON_PID=""
cleanup() {
    if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        printf '\nStopping daemon (pid %s)...\n' "$DAEMON_PID"
        kill "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

uv run --project daemon yomi-daemon "${DAEMON_ARGS[@]+"${DAEMON_ARGS[@]}"}" &
DAEMON_PID=$!

# Wait for the daemon to start listening
MAX_WAIT=10
for i in $(seq 1 $MAX_WAIT); do
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
        printf 'ERROR: Daemon exited unexpectedly.\n' >&2
        exit 1
    fi
    if python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.settimeout(0.5)
    s.connect(('127.0.0.1', int(sys.argv[1])))
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" "$DAEMON_PORT" 2>/dev/null; then
        break
    fi
    sleep 1
done

if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    printf 'ERROR: Daemon exited before accepting connections.\n' >&2
    exit 1
fi

printf 'Daemon listening on ws://127.0.0.1:%s\n' "$DAEMON_PORT"
printf '\n'
printf '--- Next steps ---\n'
printf '1. Launch YOMI Hustle with the mod loader enabled.\n'
printf '2. The mod will auto-connect to the daemon and begin playing.\n'
printf '3. When the match ends, the daemon will write artifacts to runs/.\n'
printf '\n'
printf 'Waiting for match to complete (Ctrl+C to stop)...\n'

# Wait for the daemon to exit (it exits after match_ended or disconnect)
wait "$DAEMON_PID"
EXIT_CODE=$?
DAEMON_PID=""

if [ $EXIT_CODE -eq 0 ]; then
    printf '\nMatch completed successfully.\n'
    # Find the most recent run directory
    LATEST_RUN=$(ls -td runs/*/ 2>/dev/null | head -1)
    if [ -n "$LATEST_RUN" ]; then
        printf 'Artifacts: %s\n' "$LATEST_RUN"
        if [ -f "${LATEST_RUN}result.json" ]; then
            printf '\nResult:\n'
            python3 -c "
import json, sys
r = json.load(open(sys.argv[1]))
print(f'  Status:  {r.get(\"status\", \"unknown\")}')
print(f'  Winner:  {r.get(\"winner\", \"unknown\")}')
print(f'  Reason:  {r.get(\"end_reason\", \"unknown\")}')
print(f'  Turns:   {r.get(\"total_turns\", \"unknown\")}')
" "${LATEST_RUN}result.json" 2>/dev/null || true
        fi
    fi
else
    printf '\nDaemon exited with code %d.\n' "$EXIT_CODE" >&2
    exit $EXIT_CODE
fi
