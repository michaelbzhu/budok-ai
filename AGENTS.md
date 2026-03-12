# AGENTS

This repository is organized around work units in `plans/v0.md`.

## Work units

- Treat each `WU-xxx` as the authoritative unit of implementation.
- Each work unit is intended to land as one logical commit.
- Before starting work, read the target work unit fully, including scope, implementation details, blocked-by dependencies, acceptance criteria, and tests.
- Do not start a work unit until all listed blockers are complete in the current branch.

## Python

- Use `uv` for all Python dependency management and Python execution.
- Use commands like `uv sync`, `uv run pytest`, `uv run python ...`, `uv run ruff ...`, and `uv run ty ...`.
- Do not use raw `pip`, `python`, or `pytest` commands unless a work unit explicitly requires something else.
- Standard Python quality-gate commands are `uv run ruff format`, `uv run ruff check`, `uv run ty check`, and `uv run pytest`.

## Commits

- Keep changes scoped to the active work unit.
- Before committing, update the markdown task checkboxes in that work unit for completed acceptance criteria and completed tests.
- Do not check off tasks that are only partially complete.
- If a work unit needs follow-up work, leave the remaining tasks unchecked.

## Quality gates before commit

- Verify the work unit's acceptance criteria that are claimed complete.
- Add the tests listed in the work unit that are in scope for the implemented changes.
- For Python changes, run `uv run ruff format`, `uv run ruff check`, `uv run ty check`, and `uv run pytest` before commit unless the work unit explicitly narrows scope.
- Make sure docs and config changes stay consistent with `specs/unified_spec.md` and `plans/v0.md`.
- Make sure the commit message reflects the work unit and the reason for the change.

## Default expectations

- Prefer additive, parallelizable changes that minimize merge conflicts with other work units.
- Keep protocol changes schema-first and versioned.
- Preserve the project boundary: Godot mod logic in `mod/`, daemon/orchestration logic in `daemon/`.
