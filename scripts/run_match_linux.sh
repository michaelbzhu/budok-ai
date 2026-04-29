#!/usr/bin/env bash
# End-to-end match runner for budok-ai on native Linux.
#
# Assumes the YOMI Hustle game files live in ./yomi-steam by default.
# Expected environment, based on the README setup:
#   - uv and Python 3.12+
#   - daemon deps installed with: uv sync --project daemon
#   - YOMI Hustle Steam files in ./yomi-steam
#   - Linux packages equivalent to the README VM deps, including:
#     xvfb, x11-utils, zip, unzip, mesa/libgl, pulse/alsa runtime libs
#   - API keys in .env when using provider-backed policies
# Replay recording is disabled by default because the daemon replay recorder
# currently shells out to OrbStack (`orb run` / `orb pull`) instead of local
# Linux ffmpeg. Use --record-replay only if that daemon path is supported.
#
# Usage:
#   scripts/run_match_linux.sh [CONFIG_FILE] [OPTIONS]
#
# Arguments:
#   CONFIG_FILE              Path to a match.conf file (default: match.conf)
#
# Options (override config file values):
#   --game-dir PATH          YOMI Hustle install directory (default: ./yomi-steam)
#   --daemon-config PATH     Daemon runtime config JSON
#   --display DISPLAY        X display used to launch the game (default: $DISPLAY or :99)
#   --resolution WxH         Xvfb resolution when starting a virtual display
#   --log-level LVL          Log verbosity
#   --record-replay          Enable daemon replay recording
#   --no-replay              Disable daemon replay recording (default)
#   --skip-mod-install       Skip mod packaging and installation
#   --dry-run                Print what would be done, don't execute
#   -h, --help               Show this help
#
# Examples:
#   scripts/run_match_linux.sh
#   scripts/run_match_linux.sh --game-dir ./yomi-steam
#   scripts/run_match_linux.sh --daemon-config daemon/config/llm_first_test.json
#   scripts/run_match_linux.sh --skip-mod-install

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# --- Defaults ---

GAME_DIR="$REPO_ROOT/yomi-steam"
GAME_EXE="YourOnlyMoveIsHUSTLE.x86_64"
GAME_DISPLAY="${DISPLAY:-:99}"
GAME_RESOLUTION="1280x720"
DAEMON_CONFIG="daemon/config/llm_v_llm.json"
DAEMON_PORT=""
LOG_LEVEL="INFO"
RECORD_REPLAY=false
ENV_FILE=".env"
SKIP_MOD_INSTALL=false
DRY_RUN=false

EXTRA_DAEMON_ARGS=()
CONF_FILE=""
XVFB_PID=""
DAEMON_PID=""
GAME_PID=""
declare -A CLI_OVERRIDES=()

log() { printf '[run_match_linux] %s\n' "$*"; }
err() { printf '[run_match_linux] ERROR: %s\n' "$*" >&2; }
warn() { printf '[run_match_linux] WARNING: %s\n' "$*" >&2; }

usage() {
    head -31 "$0" | tail -29
}

# --- Parse arguments ---

while [[ $# -gt 0 ]]; do
    case "$1" in
        --game-dir)         GAME_DIR="$2"; CLI_OVERRIDES[GAME_DIR]=1; shift 2 ;;
        --game-dir=*)       GAME_DIR="${1#*=}"; CLI_OVERRIDES[GAME_DIR]=1; shift ;;
        --daemon-config)    DAEMON_CONFIG="$2"; CLI_OVERRIDES[DAEMON_CONFIG]=1; shift 2 ;;
        --daemon-config=*)  DAEMON_CONFIG="${1#*=}"; CLI_OVERRIDES[DAEMON_CONFIG]=1; shift ;;
        --display)          GAME_DISPLAY="$2"; CLI_OVERRIDES[GAME_DISPLAY]=1; shift 2 ;;
        --display=*)        GAME_DISPLAY="${1#*=}"; CLI_OVERRIDES[GAME_DISPLAY]=1; shift ;;
        --resolution)       GAME_RESOLUTION="$2"; CLI_OVERRIDES[GAME_RESOLUTION]=1; shift 2 ;;
        --resolution=*)     GAME_RESOLUTION="${1#*=}"; CLI_OVERRIDES[GAME_RESOLUTION]=1; shift ;;
        --log-level)        LOG_LEVEL="$2"; CLI_OVERRIDES[LOG_LEVEL]=1; shift 2 ;;
        --log-level=*)      LOG_LEVEL="${1#*=}"; CLI_OVERRIDES[LOG_LEVEL]=1; shift ;;
        --record-replay)    RECORD_REPLAY=true; CLI_OVERRIDES[RECORD_REPLAY]=1; shift ;;
        --no-replay)        RECORD_REPLAY=false; CLI_OVERRIDES[RECORD_REPLAY]=1; shift ;;
        --skip-mod-install) SKIP_MOD_INSTALL=true; shift ;;
        --dry-run)          DRY_RUN=true; shift ;;
        --match-history)    EXTRA_DAEMON_ARGS+=("--match-history" "$2"); shift 2 ;;
        --match-history=*)  EXTRA_DAEMON_ARGS+=("--match-history" "${1#*=}"); shift ;;
        -h|--help)          usage; exit 0 ;;
        -*)
            EXTRA_DAEMON_ARGS+=("$1")
            shift
            ;;
        *)
            if [ -z "$CONF_FILE" ]; then
                CONF_FILE="$1"
            else
                EXTRA_DAEMON_ARGS+=("$1")
            fi
            shift
            ;;
    esac
done

# --- Load config file ---

if [ -z "$CONF_FILE" ] && [ -f "$REPO_ROOT/match.conf" ]; then
    CONF_FILE="$REPO_ROOT/match.conf"
fi

if [ -n "$CONF_FILE" ]; then
    if [ ! -f "$CONF_FILE" ]; then
        err "Config file not found: $CONF_FILE"
        exit 1
    fi
    log "Loading config: $CONF_FILE"
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$key" ]] && continue
        key="$(echo "$key" | xargs)"
        value="$(echo "$value" | xargs)"
        [ -z "$key" ] && continue
        case "$key" in
            GAME_DIR|GAME_EXE|GAME_DISPLAY|GAME_RESOLUTION|DAEMON_CONFIG|DAEMON_PORT|LOG_LEVEL|ENV_FILE)
                if [ -z "${CLI_OVERRIDES[$key]:-}" ]; then
                    printf -v "$key" '%s' "$value"
                fi
                ;;
            RECORD_REPLAY)
                # Native Linux replay support is not implemented yet; the daemon
                # replay path still shells out to OrbStack. Keep it explicit.
                ;;
            VM_DISPLAY)
                # Accept the macOS/OrbStack config name for convenience.
                if [ -z "${CLI_OVERRIDES[GAME_DISPLAY]:-}" ]; then
                    GAME_DISPLAY="$value"
                fi
                ;;
            VM_RESOLUTION)
                if [ -z "${CLI_OVERRIDES[GAME_RESOLUTION]:-}" ]; then
                    GAME_RESOLUTION="$value"
                fi
                ;;
        esac
    done < "$CONF_FILE"
fi

# --- Resolve paths ---

[[ "$GAME_DIR" != /* ]] && GAME_DIR="$REPO_ROOT/$GAME_DIR"
[[ "$DAEMON_CONFIG" != /* ]] && DAEMON_CONFIG="$REPO_ROOT/$DAEMON_CONFIG"
[[ "$ENV_FILE" != /* ]] && ENV_FILE="$REPO_ROOT/$ENV_FILE"

GAME_PATH="$GAME_DIR/$GAME_EXE"

# --- Cleanup ---

cleanup() {
    if [ -n "$GAME_PID" ] && kill -0 "$GAME_PID" 2>/dev/null; then
        log "Stopping game (pid $GAME_PID)..."
        kill "$GAME_PID" 2>/dev/null || true
        sleep 1
        kill -9 "$GAME_PID" 2>/dev/null || true
        wait "$GAME_PID" 2>/dev/null || true
    fi
    if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        log "Stopping daemon (pid $DAEMON_PID)..."
        kill "$DAEMON_PID" 2>/dev/null || true
        sleep 1
        kill -9 "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
    fi
    if [ -n "$XVFB_PID" ] && kill -0 "$XVFB_PID" 2>/dev/null; then
        log "Stopping Xvfb (pid $XVFB_PID)..."
        kill "$XVFB_PID" 2>/dev/null || true
        wait "$XVFB_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# --- Pre-flight checks ---

log "Pre-flight checks..."

for cmd in uv python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        err "$cmd is not installed"
        exit 1
    fi
done

if [ ! -f "$DAEMON_CONFIG" ]; then
    err "Daemon config not found: $DAEMON_CONFIG"
    exit 1
fi

if [ "$DRY_RUN" = false ]; then
    if [ ! -d "$GAME_DIR" ]; then
        err "Game directory not found: $GAME_DIR"
        err "Add the Steam game files there, or pass --game-dir PATH."
        exit 1
    fi
    if [ ! -f "$GAME_PATH" ]; then
        err "Game executable not found: $GAME_PATH"
        exit 1
    fi
fi

if [ -f "$ENV_FILE" ]; then
    log "Loading env from $ENV_FILE"
    set -a
    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$line" ]] && continue
        eval "export $line" 2>/dev/null || true
    done < "$ENV_FILE"
    set +a
else
    warn "No .env file found at $ENV_FILE"
fi

if [ "$DRY_RUN" = true ]; then
    log "DRY RUN - would execute the following steps:"
    log "  1. Ensure display $GAME_DISPLAY is available"
    log "  2. Package and install YomiLLMBridge into $GAME_DIR/mods"
    log "  3. Start daemon with config $DAEMON_CONFIG"
    log "  4. Launch $GAME_PATH"
    log "  5. Poll runs/ for match result"
    exit 0
fi

# --- Step 1: Ensure display ---

log "Ensuring display $GAME_DISPLAY..."
if command -v xdpyinfo >/dev/null 2>&1 && DISPLAY="$GAME_DISPLAY" xdpyinfo >/dev/null 2>&1; then
    log "Using existing display $GAME_DISPLAY"
else
    if ! command -v Xvfb >/dev/null 2>&1; then
        err "No working display at $GAME_DISPLAY and Xvfb is not installed"
        exit 1
    fi
    log "Starting Xvfb on $GAME_DISPLAY..."
    rm -f "/tmp/.X${GAME_DISPLAY#:}-lock" "/tmp/.X11-unix/X${GAME_DISPLAY#:}" 2>/dev/null || true
    Xvfb "$GAME_DISPLAY" -screen 0 "${GAME_RESOLUTION}x24" -nocursor >/tmp/yomi_xvfb.log 2>&1 &
    XVFB_PID=$!
    sleep 1
    if command -v xdpyinfo >/dev/null 2>&1 && ! DISPLAY="$GAME_DISPLAY" xdpyinfo >/dev/null 2>&1; then
        err "Xvfb did not start on $GAME_DISPLAY. See /tmp/yomi_xvfb.log"
        exit 1
    fi
fi

# --- Step 2: Package and install mod ---

if [ "$SKIP_MOD_INSTALL" = false ]; then
    log "Packaging mod for localhost bridge..."
    MOD_CONFIG="$REPO_ROOT/mod/YomiLLMBridge/config/default_config.json"
    ORIGINAL_MOD_CONFIG="$(mktemp)"
    cp "$MOD_CONFIG" "$ORIGINAL_MOD_CONFIG"
    restore_mod_config() {
        cp "$ORIGINAL_MOD_CONFIG" "$MOD_CONFIG" 2>/dev/null || true
        rm -f "$ORIGINAL_MOD_CONFIG"
    }
    trap 'restore_mod_config; cleanup' EXIT

    python3 -c "
import json
from pathlib import Path
path = Path('$MOD_CONFIG')
cfg = json.loads(path.read_text())
cfg['transport']['host'] = '127.0.0.1'
path.write_text(json.dumps(cfg, indent=2) + '\n')
"
    scripts/package_mod.sh
    restore_mod_config
    trap cleanup EXIT

    log "Installing mod into $GAME_DIR..."
    scripts/install_mod.sh --game-dir "$GAME_DIR"
else
    log "Skipping mod install (--skip-mod-install)"
fi

# --- Step 3: Start daemon ---

log "Starting daemon..."

DAEMON_ARGS=("--config" "$DAEMON_CONFIG" "--host" "127.0.0.1" "--log-level" "$LOG_LEVEL")

if [ -n "$DAEMON_PORT" ]; then
    DAEMON_ARGS+=("--port" "$DAEMON_PORT")
fi

if [ "$RECORD_REPLAY" = "true" ]; then
    warn "Replay capture currently uses OrbStack-specific daemon hooks."
    DAEMON_ARGS+=("--record-replay" "--replay-display" "$GAME_DISPLAY")
else
    DAEMON_ARGS+=("--no-record-replay")
fi

DAEMON_ARGS+=("${EXTRA_DAEMON_ARGS[@]+"${EXTRA_DAEMON_ARGS[@]}"}")

MATCH_START_EPOCH=$(date +%s)
uv run --project daemon yomi-daemon "${DAEMON_ARGS[@]}" &
DAEMON_PID=$!

LISTEN_PORT="${DAEMON_PORT:-8765}"
log "Waiting for daemon to listen on port $LISTEN_PORT..."
for _ in $(seq 1 15); do
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
        err "Daemon exited unexpectedly"
        exit 1
    fi
    if python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(0.5)
s.connect(('127.0.0.1', int(sys.argv[1])))
s.close()
" "$LISTEN_PORT" 2>/dev/null; then
        break
    fi
    sleep 1
done

if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    err "Daemon exited before accepting connections"
    exit 1
fi

log "Daemon listening on ws://127.0.0.1:$LISTEN_PORT"

# --- Step 4: Launch game ---

if [ ! -x "$GAME_PATH" ]; then
    chmod +x "$GAME_PATH" 2>/dev/null || true
fi

log "Launching game..."
(
    cd "$GAME_DIR"
    export DISPLAY="$GAME_DISPLAY"
    export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
    export LD_LIBRARY_PATH="$GAME_DIR:${LD_LIBRARY_PATH:-}"
    "./$GAME_EXE" >/tmp/yomi_game.log 2>&1
) &
GAME_PID=$!

log "Game launched. Waiting for match to complete (Ctrl+C to stop)..."

# --- Step 5: Poll for match completion ---

RESULT_FILE=""
LATEST_RUN=""
while true; do
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
        break
    fi
    if [ -n "$GAME_PID" ] && ! kill -0 "$GAME_PID" 2>/dev/null; then
        warn "Game process exited before a result was found. See /tmp/yomi_game.log"
        break
    fi

    for d in $(ls -td runs/*/ 2>/dev/null); do
        dir_epoch=$(stat -c %Y "$d" 2>/dev/null || echo 0)
        if [ "$dir_epoch" -ge "$MATCH_START_EPOCH" ]; then
            LATEST_RUN="$d"
            break
        fi
    done

    if [ -n "$LATEST_RUN" ] && [ -f "${LATEST_RUN}result.json" ] && \
       python3 -c "import json,sys; r=json.load(open(sys.argv[1])); sys.exit(0 if r.get('status') in ('completed','failed') else 1)" "${LATEST_RUN}result.json" 2>/dev/null; then
        RESULT_FILE="${LATEST_RUN}result.json"
        break
    fi
    sleep 2
done

if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
    log "Stopping daemon..."
    kill "$DAEMON_PID" 2>/dev/null || true
    sleep 2
    kill -9 "$DAEMON_PID" 2>/dev/null || true
fi
wait "$DAEMON_PID" 2>/dev/null || true
DAEMON_PID=""

if [ -n "$RESULT_FILE" ]; then
    MATCH_STATUS=$(python3 -c "import json; print(json.load(open('$RESULT_FILE')).get('status','?'))" 2>/dev/null || echo "?")
    if [ "$MATCH_STATUS" = "failed" ]; then
        log "Match failed"
    else
        log "Match completed successfully"
    fi
    log "Artifacts: $LATEST_RUN"
    printf '\n'
    printf 'MATCH RESULT\n'
    python3 -c "
import json, sys
r = json.load(open(sys.argv[1]))
print(f'  Status: {r.get(\"status\", \"unknown\")}')
print(f'  Winner: {r.get(\"winner\", \"unknown\")}')
print(f'  Reason: {r.get(\"end_reason\", \"unknown\")}')
print(f'  Turns:  {r.get(\"total_turns\", \"unknown\")}')
" "$RESULT_FILE" 2>/dev/null || true
else
    err "Match did not complete - no result.json found"
    exit 1
fi
