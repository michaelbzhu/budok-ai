#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${repo_root}/docs/decompile-output"
project_dir="${output_dir}/project"
report_dir="${output_dir}/reports"
reference_hooks="${output_dir}/reference-hooks.json"
manifest_path="${output_dir}/manifest.json"
readme_path="${output_dir}/README.md"
timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
mode="smoke"
game_pck_path="${YOMI_PCK_PATH:-}"
gdre_command="${GDRETOOLS_CMD:-}"

json_string_or_null() {
  if [[ -n "$1" ]]; then
    printf '"%s"' "$1"
  else
    printf 'null'
  fi
}

while (($# > 0)); do
  case "$1" in
    --game-pck)
      game_pck_path="${2:-}"
      shift 2
      ;;
    --gdre-cmd)
      gdre_command="${2:-}"
      shift 2
      ;;
    --mode)
      mode="${2:-}"
      shift 2
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

mkdir -p "${output_dir}" "${project_dir}" "${report_dir}"

cat <<'EOF' > "${reference_hooks}"
[
  {
    "script_path": "res://game.gd",
    "signal_or_method": "_ready -> player_actionable",
    "owned_by": "mod/YomiLLMBridge/bridge/TurnHook.gd",
    "evidence": "_AIOpponents/AILoader.gd extends res://game.gd and AIController.gd connects to the player_actionable signal."
  },
  {
    "script_path": "player object",
    "signal_or_method": "action_selected",
    "owned_by": "mod/YomiLLMBridge/bridge/ActionApplier.gd",
    "evidence": "_AIOpponents/AIController.gd connects target_player.action_selected and rewrites queued_action, queued_data, and queued_extra."
  },
  {
    "script_path": "Main scene action buttons",
    "signal_or_method": "find_node(\"P1ActionButtons\"/\"P2ActionButtons\")",
    "owned_by": "mod/YomiLLMBridge/bridge/LegalActionBuilder.gd",
    "evidence": "_AIOpponents/AIController.gd walks action_buttons.buttons, button.action_name, and button.state.data_ui_scene to enumerate legal options."
  },
  {
    "script_path": "res://ui/CSS/CharacterSelect.gd",
    "signal_or_method": "script override via installScriptExtension",
    "owned_by": "future multiplayer or character-selection work",
    "evidence": "char_loader overrides CharacterSelect.gd directly and uses take_over_path()-style script takeover to patch vanilla resources."
  }
]
EOF

cat <<EOF > "${manifest_path}"
{
  "generated_at": "${timestamp}",
  "mode": "${mode}",
  "repo_root": "${repo_root}",
  "output_dir": "${output_dir}",
  "project_dir": "${project_dir}",
  "report_dir": "${report_dir}",
  "reference_hooks": "${reference_hooks}",
  "inputs": {
    "game_pck_path": $(json_string_or_null "${game_pck_path}"),
    "gdre_command": $(json_string_or_null "${gdre_command}")
  },
  "notes": [
    "This script is the canonical entry point for WU-001 decompilation work.",
    "Smoke mode only prepares the documented output layout and reference hook manifest.",
    "A real decompile run requires a local YOMI Hustle PCK plus a GDRETools or gdsdecomp command supplied by the operator."
  ],
  "next_steps": [
    "Put the supported game build PCK on disk.",
    "Set YOMI_PCK_PATH or pass --game-pck.",
    "Set GDRETOOLS_CMD or pass --gdre-cmd to the exact GDRE invocation used on this machine.",
    "Write the extracted project into docs/decompile-output/project/ and save any symbol inventory under docs/decompile-output/reports/."
  ]
}
EOF

cat <<'EOF' > "${readme_path}"
# Decompile Output

This directory is the canonical output root for `scripts/decompile.sh`.

## Layout

- `manifest.json`: generated run metadata and required operator inputs.
- `reference-hooks.json`: stable hook inventory extracted from reference mods used during `WU-001`.
- `project/`: destination for the decompiled Godot project for the supported game build.
- `reports/`: operator-written symbol inventories, hashes, and validation notes from the inspected build.

## Smoke usage

Run from the repository root:

```bash
scripts/decompile.sh
```

That command always prepares the layout above so tests and future work units have a stable location.

## Real decompile workflow

1. Install GDRETools or `gdsdecomp` locally.
2. Locate the YOMI Hustle `.pck` for the supported build.
3. Re-run the script with local inputs recorded in the generated manifest:

```bash
YOMI_PCK_PATH="/absolute/path/to/game.pck" \
GDRETOOLS_CMD="/absolute/path/to/your/gdre invocation" \
scripts/decompile.sh --mode real
```

`WU-001` does not hard-code a GDRE CLI because local setups vary. The contract is that all extracted sources go under `project/` and all symbol notes go under `reports/`.
EOF

printf 'Prepared decompile workspace at %s\n' "${output_dir}"
