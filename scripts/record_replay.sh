#!/usr/bin/env bash
# Record an existing .replay file by launching the engine in replay mode
# inside the OrbStack VM and capturing the X display with ffmpeg.
#
# Usage:
#   scripts/record_replay.sh PATH/TO/match.replay [OPTIONS]
#
# Options:
#   --output PATH         Write the derived video here
#   --skip-mod-push       Reuse the existing mod already installed in the VM
#   --dry-run             Print the planned actions and exit
#   -h, --help            Show this help

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VM_NAME="ubuntu"
VM_GAME_DIR="/home/$USER/games/yomi"
VM_DISPLAY=":99"
VM_RESOLUTION="1280x720"
FRAMERATE=30
VIDEO_CODEC="libx264"
PRESET="fast"
MAX_RECORD_SECONDS=180
END_GRACE_SECONDS=5
PLAYBACK_SPEED_MOD=2
SKIP_MOD_PUSH=false
DRY_RUN=false

REPLAY_FILE=""
OUTPUT_FILE=""

log() { printf '[record_replay] %s\n' "$*"; }
err() { printf '[record_replay] ERROR: %s\n' "$*" >&2; }

run_vm() {
    orb run -m "$VM_NAME" bash -c "$1"
}

usage() {
    head -18 "$0" | tail -16
}

next_available_output_path() {
    local requested="$1"
    local dir stem ext candidate
    dir="$(dirname "$requested")"
    ext=".${requested##*.}"
    if [ "$ext" = ".$requested" ]; then
        ext=""
    fi
    stem="$(basename "$requested" "$ext")"
    candidate="$dir/$stem$ext"
    if [ ! -e "$candidate" ]; then
        printf '%s\n' "$candidate"
        return
    fi
    local version=2
    while true; do
        candidate="$dir/${stem}_v${version}${ext}"
        if [ ! -e "$candidate" ]; then
            printf '%s\n' "$candidate"
            return
        fi
        version=$((version + 1))
    done
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output) OUTPUT_FILE="$2"; shift 2 ;;
        --output=*) OUTPUT_FILE="${1#*=}"; shift ;;
        --skip-mod-push) SKIP_MOD_PUSH=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage; exit 0 ;;
        -*)
            err "Unknown option: $1"
            exit 1
            ;;
        *)
            if [ -z "$REPLAY_FILE" ]; then
                REPLAY_FILE="$1"
            else
                err "Unexpected extra argument: $1"
                exit 1
            fi
            shift
            ;;
    esac
done

if [ -z "$REPLAY_FILE" ]; then
    usage >&2
    exit 1
fi

if [[ "$REPLAY_FILE" != /* ]]; then
    REPLAY_FILE="$REPO_ROOT/$REPLAY_FILE"
fi

if [ ! -f "$REPLAY_FILE" ]; then
    err "Replay file not found: $REPLAY_FILE"
    exit 1
fi

if [ "${REPLAY_FILE##*.}" != "replay" ]; then
    err "Replay file must end with .replay: $REPLAY_FILE"
    exit 1
fi

if [ -z "$OUTPUT_FILE" ]; then
    OUTPUT_FILE="$(dirname "$REPLAY_FILE")/$(basename "$REPLAY_FILE" .replay)_recorded.mp4"
fi
if [[ "$OUTPUT_FILE" != /* ]]; then
    OUTPUT_FILE="$REPO_ROOT/$OUTPUT_FILE"
fi
OUTPUT_FILE="$(next_available_output_path "$OUTPUT_FILE")"

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_replay_$RANDOM"
VM_REPLAY_PATH="/tmp/${RUN_ID}.replay"
VM_VIDEO_PATH="/tmp/${RUN_ID}.mp4"
VM_GAME_LOG="/tmp/${RUN_ID}.game.log"
VM_FFMPEG_LOG="/tmp/${RUN_ID}.ffmpeg.log"
VM_GAME_BINARY="$VM_GAME_DIR/YourOnlyMoveIsHUSTLE.x86_64"
VM_DEBUG_LOG="/tmp/yomi_autoreplay_debug.log"
FFMPEG_ORB_PID=""
GAME_ORB_PID=""

cleanup() {
    run_vm "
        pkill -f YourOnlyMoveIsHUSTLE.x86_64 2>/dev/null || true
        pkill -INT -f '$VM_VIDEO_PATH' 2>/dev/null || true
        rm -f '$VM_REPLAY_PATH' '$VM_VIDEO_PATH' '$VM_GAME_LOG' '$VM_FFMPEG_LOG' '$VM_DEBUG_LOG'
    " >/dev/null 2>&1 || true
    if [ -n "$GAME_ORB_PID" ] && kill -0 "$GAME_ORB_PID" 2>/dev/null; then
        kill -TERM "$GAME_ORB_PID" 2>/dev/null || true
        wait "$GAME_ORB_PID" 2>/dev/null || true
    fi
    if [ -n "$FFMPEG_ORB_PID" ] && kill -0 "$FFMPEG_ORB_PID" 2>/dev/null; then
        kill -TERM "$FFMPEG_ORB_PID" 2>/dev/null || true
        wait "$FFMPEG_ORB_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

log "Pre-flight checks..."

for cmd in orb ffmpeg; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        err "$cmd is not installed"
        exit 1
    fi
done

if ! orb list 2>/dev/null | grep -q "$VM_NAME"; then
    err "OrbStack VM '$VM_NAME' not found"
    exit 1
fi

if orb list 2>/dev/null | grep "$VM_NAME" | grep -q "stopped"; then
    log "Starting VM '$VM_NAME'..."
    orb start "$VM_NAME"
    sleep 2
fi

if ! run_vm "test -f '$VM_GAME_BINARY'" >/dev/null 2>&1; then
    err "Game not found in VM at $VM_GAME_BINARY"
    exit 1
fi

if [ "$DRY_RUN" = true ]; then
    log "Replay file: $REPLAY_FILE"
    log "Output file: $OUTPUT_FILE"
    log "VM replay path: $VM_REPLAY_PATH"
    log "Dry run complete"
    exit 0
fi

log "Killing stale game processes in VM..."
run_vm "pkill -f YourOnlyMoveIsHUSTLE.x86_64 || true" >/dev/null 2>&1 || true

log "Ensuring Xvfb on display $VM_DISPLAY..."
run_vm "
    if ! pgrep -af 'Xvfb $VM_DISPLAY' >/dev/null 2>&1; then
        rm -f /tmp/.X*-lock /tmp/.X11-unix/X* 2>/dev/null || true
        Xvfb $VM_DISPLAY -screen 0 ${VM_RESOLUTION}x24 -nocursor >/dev/null 2>&1 &
    fi
" >/dev/null
sleep 1

if [ "$SKIP_MOD_PUSH" = false ]; then
    log "Packaging and pushing mod to VM..."
    scripts/package_mod.sh >/dev/null
    run_vm "mkdir -p '$VM_GAME_DIR/mods' && rm -rf '$VM_GAME_DIR/mods/YomiLLMBridge'" >/dev/null
    orb push -m "$VM_NAME" "$REPO_ROOT/mod/YomiLLMBridge" "$VM_GAME_DIR/mods/" >/dev/null
    orb push -m "$VM_NAME" "$REPO_ROOT/dist/YomiLLMBridge.zip" "$VM_GAME_DIR/mods/YomiLLMBridge.zip" >/dev/null
else
    log "Skipping mod push (--skip-mod-push)"
fi

log "Copying replay file into VM..."
orb push -m "$VM_NAME" "$REPLAY_FILE" "$VM_REPLAY_PATH" >/dev/null

log "Starting ffmpeg capture in VM..."
run_vm "rm -f '$VM_VIDEO_PATH' '$VM_FFMPEG_LOG'" >/dev/null
orb run -m "$VM_NAME" bash -lc "
    ffmpeg -y -f x11grab \
        -video_size $VM_RESOLUTION \
        -framerate $FRAMERATE \
        -i $VM_DISPLAY \
        -t $MAX_RECORD_SECONDS \
        -c:v $VIDEO_CODEC -preset $PRESET -pix_fmt yuv420p \
        '$VM_VIDEO_PATH' </dev/null 2> '$VM_FFMPEG_LOG'
" >/dev/null 2>&1 &
FFMPEG_ORB_PID=$!
sleep 1

log "Launching replay playback in VM..."
orb run -m "$VM_NAME" bash -c "
    rm -f '$VM_GAME_LOG' '$VM_DEBUG_LOG'
    export LIBGL_ALWAYS_SOFTWARE=1
    export LD_LIBRARY_PATH='$VM_GAME_DIR':\${LD_LIBRARY_PATH:-}
    export YOMI_AUTOSTART_REPLAY_PATH='$VM_REPLAY_PATH'
    export YOMI_AUTOSTART_REPLAY_GRACE_SECONDS='$END_GRACE_SECONDS'
    export YOMI_AUTOSTART_REPLAY_SPEED_MOD='$PLAYBACK_SPEED_MOD'
    DISPLAY='$VM_DISPLAY' '$VM_GAME_BINARY' > '$VM_GAME_LOG' 2>&1
" >/dev/null 2>&1 &
GAME_ORB_PID=$!
sleep 2
if ! kill -0 "$GAME_ORB_PID" 2>/dev/null; then
    err "Replay playback process exited immediately"
    run_vm "
        echo '--- replay debug log ---'
        cat '$VM_DEBUG_LOG' 2>/dev/null || true
        echo '--- game log ---'
        cat '$VM_GAME_LOG' 2>/dev/null || true
    " || true
    exit 1
fi

log "Waiting for replay playback to finish..."
replay_completed=false
for _ in $(seq 1 "$MAX_RECORD_SECONDS"); do
    if run_vm "grep -q 'AutoReplayRecorder: replay capture complete, quitting' '$VM_DEBUG_LOG'" >/dev/null 2>&1; then
        replay_completed=true
        break
    fi
    if ! kill -0 "$GAME_ORB_PID" 2>/dev/null; then
        err "Replay playback process exited before completion"
        run_vm "
            echo '--- replay debug log ---'
            cat '$VM_DEBUG_LOG' 2>/dev/null || true
            echo '--- game log ---'
            cat '$VM_GAME_LOG' 2>/dev/null || true
        " || true
        exit 1
    fi
    sleep 1
done

if [ "$replay_completed" != true ]; then
    err "Replay playback timed out after ${MAX_RECORD_SECONDS}s"
    run_vm "
        echo '--- debug log ---'
        cat '$VM_DEBUG_LOG' 2>/dev/null || true
        echo '--- game log ---'
        cat '$VM_GAME_LOG' 2>/dev/null || true
    " || true
    exit 1
fi

sleep 2

log "Stopping ffmpeg capture..."
run_vm "
    pkill -INT -f '$VM_VIDEO_PATH' 2>/dev/null || true
" >/dev/null

if [ -n "$FFMPEG_ORB_PID" ]; then
    for _ in $(seq 1 20); do
        if ! kill -0 "$FFMPEG_ORB_PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    wait "$FFMPEG_ORB_PID" 2>/dev/null || true
    FFMPEG_ORB_PID=""
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"
log "Pulling derived video to $OUTPUT_FILE..."
if ! orb pull -m "$VM_NAME" "$VM_VIDEO_PATH" "$OUTPUT_FILE" >/dev/null; then
    err "Failed to pull derived video from VM"
    run_vm "
        ls -l '$VM_VIDEO_PATH' '$VM_GAME_LOG' '$VM_FFMPEG_LOG' '$VM_DEBUG_LOG' 2>/dev/null || true
        echo '--- replay debug log ---'
        cat '$VM_DEBUG_LOG' 2>/dev/null || true
        echo '--- ffmpeg log ---'
        tail -n 120 '$VM_FFMPEG_LOG' 2>/dev/null || true
        echo '--- game log ---'
        tail -n 120 '$VM_GAME_LOG' 2>/dev/null || true
    " || true
    exit 1
fi

log "Replay recording complete"
log "Output video: $OUTPUT_FILE"
