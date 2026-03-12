# YOMI Hustle LLM Arena

This repository implements the local mod-plus-daemon system described in [the unified spec](specs/unified_spec.md). The codebase is organized around work units in [the implementation plan](plans/v0.md).

## Repository layout

- `daemon/`: Python package, local tooling, and daemon-side implementation.
- `docs/`: architecture, operations, protocol, and game-internals notes.
- `mod/`: Godot mod sources under `mod/YomiLLMBridge/`.
- `prompts/`: versioned prompt templates used by daemon adapters.
- `schemas/`: versioned JSON schemas for the IPC protocol.
- `scripts/`: local helper scripts for schema checks, decompilation, and orchestration.
- `runs/`: per-match artifacts, committed only with `.gitkeep`.
- `tests/`: reusable fixtures plus repository-level tests.

## Local daemon setup

The daemon is `uv`-first. Run all Python commands through `uv`.

```bash
cd /Users/nlevine/Dev/yomi-ai/daemon
uv sync
```

From the repository root, the equivalent commands are:

```bash
uv sync --project daemon
uv run --project daemon pytest tests
uv run --project daemon ruff check daemon/src tests scripts/check_schemas.py
```

## Local verification

Schema parsing:

```bash
uv run --project daemon python scripts/check_schemas.py
```

Daemon smoke tests:

```bash
uv run --project daemon pytest tests/daemon
```

Daemon quality gates:

```bash
uv run --project daemon ruff format daemon/src tests scripts/check_schemas.py
uv run --project daemon ruff check daemon/src tests scripts/check_schemas.py
uv run --project daemon ty check
uv run --project daemon pytest tests
```

## Artifact location

Match artifacts are written under `runs/<timestamp>_<match_id>/`. The directory stays mostly empty in `WU-000`; later work units populate manifests, telemetry, prompts, and result logs there.

## Ownership boundaries

- Protocol semantics and wire schemas live under `schemas/` and `docs/protocol.md`.
- Daemon orchestration, adapters, and artifact writing live under `daemon/src/yomi_daemon/`.
- Godot bridge logic lives under `mod/YomiLLMBridge/`.

The authoritative requirements remain [the unified spec](specs/unified_spec.md) and [the work plan](plans/v0.md).
