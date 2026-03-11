# Current State

This document records the current, implemented state of `los-memory`.

## Product Shape

- Primary runtime shape: local Python CLI and Python API backed by SQLite
- Main entrypoint: `los-memory`
- Backward-compatible script entrypoint: `python3 memory_tool/memory_tool.py`
- Default profiles: `claude`, `codex`, `shared`

## Current Command Surface

- Core commands: `memory`, `observation`, `session`, `checkpoint`, `project`, `tool`, `review`, `admin`
- Experimental extensions: `incident`, `recovery`, `knowledge`, `attribution`
- Deprecated migration surface: `approval`

## Current Architecture Notes

- The active CLI path is implemented in `memory_tool/cli.py`
- The active observation CRUD path is still served from `memory_tool/operations.py`
- `memory_tool/core/` is now a compatibility layer that forwards historical imports to the active top-level modules
- New development should target the top-level `memory_tool.*` modules rather than `memory_tool.core.*`

## Current Testing Notes

- Test layout includes `tests/unit`, `tests/cli`, `tests/integration`, and BDD runners in `tests/test_*_bdd.py`
- CI includes docs command lint, CLI contract tests, unit tests, selected integration smoke, one approval migration E2E path, and a BDD smoke run
- Always treat live `pytest` and CI results as the source of truth for pass/fail counts

## Current Documentation Rule

- Use this file and `README.md` for current behavior
- Treat implementation plans, architecture reviews, child-session notes, and reports as historical or planning material unless they explicitly say they are current
