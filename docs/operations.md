# Operations

## Current local commands

Set up the daemon environment:

```bash
cd /Users/nlevine/Dev/yomi-ai/daemon
uv sync
```

Validate all schema files parse as JSON:

```bash
uv run --project daemon python scripts/check_schemas.py
```

Run the daemon handshake server locally:

```bash
uv run --project daemon yomi-daemon
```

Run the daemon with an explicit runtime config file:

```bash
uv run --project daemon yomi-daemon --config daemon/config/default_config.json
```

Run the daemon with verbose logging:

```bash
uv run --project daemon yomi-daemon --log-level DEBUG
```

Run the daemon smoke tests:

```bash
uv run --project daemon pytest tests/daemon
```

Run a single local match (daemon waits for mod connection):

```bash
scripts/run_local_match.sh --p1-policy baseline/random --p2-policy baseline/block_always
```

Run a round-robin tournament:

```bash
scripts/run_round_robin.sh baseline/random baseline/block_always baseline/greedy_damage
```

Generate tournament pairings without running matches:

```bash
uv run --project daemon python -m yomi_daemon.tournament.cli schedule baseline/random baseline/block_always
```

Generate a tournament report from existing run artifacts:

```bash
uv run --project daemon python -m yomi_daemon.tournament.cli report --runs-dir runs/
```

## Quality gates

Run all quality checks before committing:

```bash
uv run --project daemon ruff format
uv run --project daemon ruff check
uv run --project daemon ty check
uv run --project daemon pytest
```

## Mod harness scripts

Offline harness scripts test mod bridge logic without a running game:

```bash
uv run --project daemon python scripts/mod_bridge_harness.py
uv run --project daemon python scripts/mod_observation_harness.py
uv run --project daemon python scripts/mod_decision_harness.py
uv run --project daemon python scripts/mod_runtime_harness.py
```

## Runtime config notes

- The default daemon config file lives at `daemon/config/default_config.json`.
- Config precedence is: built-in defaults, selected config file, CLI overrides, then
  environment-variable secret resolution.
- Provider credentials stay out of JSON config files. Policy entries reference them by
  `credential_env_var`, and the loader resolves the value from the current process environment.
- Replay video recording is enabled by default. Control it via `replay_capture.enabled` in the
  config file, or override with `--record-replay` / `--no-record-replay` CLI flags. Additional
  replay settings (vm_machine, display, resolution, framerate, video_codec, preset) can be set
  in the `replay_capture` config section.

## Security

### Localhost-only binding

The daemon is designed for local use only and defaults to `127.0.0.1`. If you change `transport.host` to a non-local address (e.g. `0.0.0.0`), the daemon will log a warning. Do not expose the daemon to untrusted networks.

### Shared-secret authentication

The daemon optionally requires clients to present a shared secret in the `hello` handshake. To enable:

1. Set `transport.auth_secret_env_var` in the daemon config to an environment variable name (e.g. `"YOMI_AUTH_SECRET"`).
2. Export that variable: `export YOMI_AUTH_SECRET=your-secret-here`.
3. Set `transport.auth_token` in the mod's bridge config to the same value.

Clients that omit the token or send the wrong value receive a WebSocket close with code `1008` (policy violation). When no `auth_secret_env_var` is configured, authentication is not enforced.

### Credential safety

- Provider API keys are never written to config files, artifacts, or logs. They are resolved from environment variables at runtime.
- The `.gitignore` excludes `.env` and `*.env` files. Only `.env.example` (which contains no real keys) is tracked.
- Provider error messages in `stderr.log` and log output are sanitized to remove API key patterns before writing.

## Artifact layout

Per-match artifacts belong under `runs/<timestamp>_<match_id>/`.

`WU-004` introduces the manifest foundation that later work units will write into that directory.
The expected layout remains:

```text
runs/<timestamp>_<match_id>/
  manifest.json
  events.jsonl
  decisions.jsonl
  prompts.jsonl
  metrics.json
  result.json
  replay_index.json
  stderr.log
  replay.mp4          (when replay_capture.enabled, default on)
  match.replay        (when replay_capture.enabled, default on)
```

The initial manifest skeleton is intended to exist before the first turn so failed or incomplete
matches still retain pinned config, version, and seed metadata.

## Troubleshooting

**Port conflicts**: If the daemon fails to start with `Address already in use`, another process holds the port. Use `lsof -i :8765` to identify it. Pass `--port 0` to let the OS pick a free port.

**Connection failures**: Verify the daemon is running and the mod's `bridge_url` matches the daemon's host:port. Check `stderr.log` in the run directory for handshake rejection reasons.

**Missing artifacts**: If a run directory is missing expected files, the match likely ended before the first decision request. The artifact writer initializes lazily on the first `decision_request` envelope.

**Hash mismatch warnings**: `state_hash` and `legal_actions_hash` are logged for debugging but not validated server-side. A mismatch between mod and daemon hashes is informational only and does not affect decision routing.

**All-fallback scenarios**: If every turn produces a fallback decision, check that the configured policy is reachable. For baseline policies, this should never happen. For provider-backed policies, verify credentials and network connectivity.

## Performance

`decision_timeout_ms` controls the per-turn decision deadline. Default is 10000ms. For LLM-backed policies, 30000ms is recommended to accommodate API latency. Decision requests for both players are processed concurrently — when two requests arrive in the same turn, their LLM API calls run in parallel (~7s per concurrent pair vs ~14s sequential).

`metrics.json` in each run directory tracks per-match latency statistics, fallback counts, and token usage. Use it to identify regressions after code changes.

## End-to-end match (macOS + OrbStack)

`scripts/run_match.sh` automates the full match lifecycle on macOS with an OrbStack VM:

```bash
# Run with defaults (uses match.conf if present, else daemon/config/llm_v_llm.json)
scripts/run_match.sh

# Use a specific config
scripts/run_match.sh my_match.conf

# Override daemon config from CLI
scripts/run_match.sh --daemon-config daemon/config/llm_first_test.json

# Skip mod packaging/push (reuse mod already in VM — faster iteration)
scripts/run_match.sh --skip-mod-push

# Disable replay recording
scripts/run_match.sh --no-replay

# Preview what would happen
scripts/run_match.sh --dry-run
```

The script handles everything:

1. Kills stale game processes in the VM
2. Ensures Xvfb is running
3. Auto-detects the bridge IP from `ifconfig bridge100`
4. Patches the mod config with the bridge IP, packages the zip, pushes to the VM, restores the config
5. Starts the daemon with `--host 0.0.0.0`
6. Launches the game in the VM
7. Polls for match completion (checks `result.json` status)
8. Waits for replay video capture
9. Prints a formatted result summary
10. Cleans up on exit/Ctrl+C

### Configuration via `match.conf`

Copy `match.conf.example` to `match.conf` and customize:

```bash
cp match.conf.example match.conf
```

Key settings: `VM_NAME`, `VM_GAME_DIR`, `DAEMON_CONFIG`, `BRIDGE_IP_MODE` (auto/manual), `RECORD_REPLAY`, `ENV_FILE`. See the example file for full documentation.

### Prerequisites

- `uv` and `orb` (OrbStack) installed
- OrbStack amd64 VM with game installed (see `docs/macos.md` for one-time setup)
- API keys in `.env` for provider-backed policies

## Manual live match workflow

For running matches without OrbStack (game running natively on Linux), use the individual scripts:

### 1. Package the mod

```bash
scripts/package_mod.sh
```

### 2. Install the mod

```bash
scripts/install_mod.sh --game-dir /path/to/yomi-hustle
```

### 3. Start a live match

```bash
scripts/run_live_match.sh --game-dir /path/to/yomi-hustle
```

For provider-backed policies:

```bash
ANTHROPIC_API_KEY=sk-ant-... scripts/run_live_match.sh \
  --p1-policy anthropic/claude --p2-policy baseline/random
```

### 4. Verify artifacts

After a completed match, the run directory contains all standard artifacts (see Artifact layout below).

## Reproducibility

Baseline (non-provider) runs are fully deterministic given the same inputs:

- **`trace_seed`**: set in the runtime config. Combined with `match_id`, `player_id`, `turn_id`, `state_hash`, and `legal_actions_hash` to derive per-turn RNG seeds.
- **`match_id`**: part of the seed material. Same `match_id` + same `trace_seed` + same config → identical decisions.
- **Config**: `policy_mapping`, `fallback_mode`, and `decision_timeout_ms` all affect behavior.

To reproduce a baseline run exactly, reuse the `trace_seed`, `match_id`, and full config from the original `manifest.json`.

Provider-backed runs (Anthropic, OpenAI, OpenRouter) are inherently non-deterministic due to model sampling. The `trace_seed` still controls fallback and tie-breaking logic, but primary decisions may vary.
