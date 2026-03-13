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

Run the daemon smoke tests:

```bash
uv run --project daemon pytest tests/daemon
```

## Runtime config notes

- The default daemon config file lives at `daemon/config/default_config.json`.
- Config precedence is: built-in defaults, selected config file, CLI overrides, then
  environment-variable secret resolution.
- Provider credentials stay out of JSON config files. Policy entries reference them by
  `credential_env_var`, and the loader resolves the value from the current process environment.

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

## Reproducibility

Baseline (non-provider) runs are fully deterministic given the same inputs:

- **`trace_seed`**: set in the runtime config. Combined with `match_id`, `player_id`, `turn_id`, `state_hash`, and `legal_actions_hash` to derive per-turn RNG seeds.
- **`match_id`**: part of the seed material. Same `match_id` + same `trace_seed` + same config → identical decisions.
- **Config**: `policy_mapping`, `fallback_mode`, and `timeout_profile` all affect behavior.

To reproduce a baseline run exactly, reuse the `trace_seed`, `match_id`, and full config from the original `manifest.json`.

Provider-backed runs (Anthropic, OpenAI, OpenRouter) are inherently non-deterministic due to model sampling. The `trace_seed` still controls fallback and tie-breaking logic, but primary decisions may vary.
