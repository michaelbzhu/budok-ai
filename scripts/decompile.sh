#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${repo_root}/docs/decompile-output"

mkdir -p "${output_dir}"
cat <<'EOF' > "${output_dir}/README.md"
# Decompile Output

`WU-001` owns the real game decompilation workflow.

This placeholder directory exists so the canonical script path already works and has a documented output location.
EOF

printf 'Prepared placeholder output at %s\n' "${output_dir}"
