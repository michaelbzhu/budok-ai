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
uv run --project daemon yomi-daemon --host 127.0.0.1 --port 8765
```

Run the daemon smoke tests:

```bash
uv run --project daemon pytest tests/daemon
```

Artifacts created by later work units belong under `runs/`.
