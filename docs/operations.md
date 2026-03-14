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
  replay.mp4          (when --record-replay enabled)
  match.replay        (when --record-replay enabled)
```

The initial manifest skeleton is intended to exist before the first turn so failed or incomplete
matches still retain pinned config, version, and seed metadata.

## Troubleshooting

**Port conflicts**: If the daemon fails to start with `Address already in use`, another process holds the port. Use `lsof -i :8765` to identify it. Pass `--port 0` to let the OS pick a free port.

**Connection failures**: Verify the daemon is running and the mod's `bridge_url` matches the daemon's host:port. Check `stderr.log` in the run directory for handshake rejection reasons.

**Missing artifacts**: If a run directory is missing expected files, the match likely ended before the first decision request. The artifact writer initializes lazily on the first `decision_request` envelope.

**Hash mismatch warnings**: `state_hash` and `legal_actions_hash` are logged for debugging but not validated server-side. A mismatch between mod and daemon hashes is informational only and does not affect decision routing.

**All-fallback scenarios**: If every turn produces a fallback decision, check that the configured policy is reachable. For baseline policies, this should never happen. For provider-backed policies, verify credentials and network connectivity.

## Performance profiles

Two timeout profiles are defined:

| Profile | `decision_timeout_ms` default | p95 budget | Fallback rate budget |
|---|---|---|---|
| `strict_local` | 2500 ms | 1200 ms | 0% |
| `llm_tournament` | 10000 ms | 15000 ms | < 5% |

Run the latency benchmark:

```bash
uv run --project daemon python scripts/benchmark_latency.py --profile strict_local --turns 20
uv run --project daemon python scripts/benchmark_latency.py --profile llm_tournament --turns 30
```

The benchmark prints a conformance table showing p50/p95/p99 latency and fallback rate against the spec budgets.

`metrics.json` in each run directory tracks per-match latency statistics and fallback counts. Use it to identify regressions after code changes.

## Live local workflow

Run a complete local match from mod packaging through artifact collection.

### 1. Package the mod

```bash
scripts/package_mod.sh
```

This creates `dist/YomiLLMBridge.zip` containing the mod directory tree.

### 2. Install the mod

```bash
scripts/install_mod.sh --game-dir /path/to/yomi-hustle
```

Point `--game-dir` to the directory containing the YOMI Hustle executable. The script copies the mod zip into `<game-dir>/mods/` and extracts it.

### 3. Start a live match

```bash
scripts/run_live_match.sh --game-dir /path/to/yomi-hustle
```

This starts the daemon, verifies prerequisites, and waits for the game to connect. Launch YOMI Hustle with the mod loader enabled; the mod auto-connects to the daemon on `127.0.0.1:8765`.

For provider-backed policies:

```bash
ANTHROPIC_API_KEY=sk-ant-... scripts/run_live_match.sh \
  --p1-policy anthropic/claude --p2-policy baseline/random
```

When the match ends, the daemon writes artifacts to `runs/<timestamp>_<match_id>/` and prints a result summary.

### 4. Verify artifacts

After a completed match, the run directory contains:

- `manifest.json` — config snapshot and seed
- `events.jsonl` — lifecycle events (MatchStarted, TurnRequested, DecisionReceived, etc.)
- `decisions.jsonl` — per-turn request/response pairs
- `prompts.jsonl` — prompt traces (when logging.prompts is enabled)
- `metrics.json` — latency, fallback rate, token usage
- `result.json` — winner, end reason, turn count, status
- `replay_index.json` — per-turn pointers into decisions and prompts
- `stderr.log` — error output

### Prerequisites

- `uv` installed
- YOMI Hustle (Steam build `16151810`) installed locally
- The game's mod loader must be set up to load mods from `<game-dir>/mods/`
- For provider-backed policies, set the relevant API key environment variables (see `.env.example`)

### Failure modes

The workflow surfaces clear errors for:

- Missing `uv`
- Missing mod zip (run `scripts/package_mod.sh` first)
- Invalid or missing game directory
- Missing mod installation
- Missing provider API keys

## Reproducibility

Baseline (non-provider) runs are fully deterministic given the same inputs:

- **`trace_seed`**: set in the runtime config. Combined with `match_id`, `player_id`, `turn_id`, `state_hash`, and `legal_actions_hash` to derive per-turn RNG seeds.
- **`match_id`**: part of the seed material. Same `match_id` + same `trace_seed` + same config → identical decisions.
- **Config**: `policy_mapping`, `fallback_mode`, and `decision_timeout_ms` all affect behavior.

To reproduce a baseline run exactly, reuse the `trace_seed`, `match_id`, and full config from the original `manifest.json`.

Provider-backed runs (Anthropic, OpenAI, OpenRouter) are inherently non-deterministic due to model sampling. The `trace_seed` still controls fallback and tie-breaking logic, but primary decisions may vary.
