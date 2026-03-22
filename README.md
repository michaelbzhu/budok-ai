# budok-ai

This repository implements the local mod-plus-daemon system described in [the unified spec](specs/unified_spec.md). The codebase is organized around work units in the implementation plans ([v0](plans/v0.md), [v1](plans/v1.md)).

## Repository layout

- `daemon/`: Python package, local tooling, and daemon-side implementation.
- `docs/`: architecture, data flow, operations, protocol, game-internals, and prompt-engineering notes.
- `mod/`: Godot mod sources under `mod/YomiLLMBridge/`.
- `prompts/`: versioned prompt templates used by daemon adapters.
- `schemas/`: versioned JSON schemas for the IPC protocol.
- `scripts/`: local helper scripts for schema checks, decompilation, and orchestration.
- `runs/`: per-match artifacts, committed only with `.gitkeep`.
- `tests/`: reusable fixtures plus repository-level tests.

## Local daemon setup

The daemon is `uv`-first. Run all Python commands through `uv`.

```bash
cd /Users/nlevine/Dev/budok-ai/daemon
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

Mod bridge handshake harness:

```bash
uv run --project daemon python scripts/mod_bridge_harness.py --mode print-hello
uv run --project daemon python scripts/mod_bridge_harness.py --mode handshake
```

Godot bridge smoke run:

```bash
uv run --project daemon yomi-daemon
godot3 --no-window --path mod --script res://BridgeHandshakeSmoke.gd
godot3 --no-window --path mod --script res://BridgeActionApplySmoke.gd
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

The authoritative requirements remain [the unified spec](specs/unified_spec.md) and the work plans ([v0](plans/v0.md), [v1](plans/v1.md)).

## Documentation

Start with [`docs/setup.md`](docs/setup.md) to install dependencies and configure the system. Then:

- [`docs/experiments.md`](docs/experiments.md) — running matches, tournaments, analyzing results, benchmarking
- [`docs/data_flow.md`](docs/data_flow.md) — end-to-end turn lifecycle from game signal to applied action
- [`docs/architecture.md`](docs/architecture.md) — daemon module map, config, manifest, mod bridge files
- [`docs/protocol.md`](docs/protocol.md) — v2 wire protocol, envelope contract, payload schemas
- [`docs/game_internals.md`](docs/game_internals.md) — YOMI Hustle hook points, mod bridge ownership
- [`docs/operations.md`](docs/operations.md) — commands, security, artifacts, troubleshooting
- [`docs/prompt_engineering.md`](docs/prompt_engineering.md) — prompt templates, response parsing, provider adapters

## Known limitations

- Provider decisions (Anthropic, OpenAI, OpenRouter) are non-deterministic due to model sampling. Only baseline policies produce reproducible runs.
- `character_data` is supported for 5 of 6 characters (Cowboy, Robot, Ninja, Mutant, Wizard). Alien returns empty/no data.
- Daemon-only benchmarks simulate the WebSocket protocol but do not run against a live game instance.
