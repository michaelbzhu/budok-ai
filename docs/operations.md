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

Run the daemon smoke tests:

```bash
uv run --project daemon pytest tests/daemon
```

Artifacts created by later work units belong under `runs/`.
