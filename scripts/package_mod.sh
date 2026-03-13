#!/usr/bin/env bash
# Package the YomiLLMBridge mod into a distributable zip file.
#
# Usage:
#   scripts/package_mod.sh
#
# Output:
#   dist/YomiLLMBridge.zip
#
# The zip contains the YomiLLMBridge/ directory tree ready for extraction
# into a YOMI Hustle mods/ directory.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOD_SRC="$REPO_ROOT/mod/YomiLLMBridge"
DIST_DIR="$REPO_ROOT/dist"
OUTPUT="$DIST_DIR/YomiLLMBridge.zip"

# --- Validate mod source ---

if [ ! -d "$MOD_SRC" ]; then
    printf 'ERROR: Mod source directory not found: %s\n' "$MOD_SRC" >&2
    exit 1
fi

METADATA="$MOD_SRC/_metadata"
if [ ! -f "$METADATA" ]; then
    printf 'ERROR: _metadata file not found: %s\n' "$METADATA" >&2
    exit 1
fi

# Validate _metadata is valid JSON
if ! python3 -c "import json, sys; json.load(open(sys.argv[1]))" "$METADATA" 2>/dev/null; then
    printf 'ERROR: _metadata is not valid JSON: %s\n' "$METADATA" >&2
    exit 1
fi

# --- Package ---

mkdir -p "$DIST_DIR"
rm -f "$OUTPUT"

# Create zip from the mod/ parent so the zip contains YomiLLMBridge/ as root
(cd "$REPO_ROOT/mod" && zip -r "$OUTPUT" YomiLLMBridge/ \
    -x "YomiLLMBridge/.import/*" \
    -x "YomiLLMBridge/.godot/*" \
    -x "*.uid" \
    -x "*__pycache__*" \
    -x "*.pyc" \
)

printf 'Packaged mod: %s\n' "$OUTPUT"
