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
