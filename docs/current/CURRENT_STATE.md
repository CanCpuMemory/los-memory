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
- Active session state is now bound to the current database path, so profile-level session files do not leak session state across different SQLite databases
- `admin doctor` performs read-only diagnostics and does not create a missing database file as a side effect of health checks
- `Observation` records now persist structured `metadata`, and `observation add/edit`, `memory get`, and `memory export` preserve that metadata round-trip
- `Feedback` records now also persist structured `metadata`, and both `observation feedback` and `review apply` propagate it into feedback history
- `--profile` is a storage-partition selector only; tenant/user/request/trace identity belongs in structured metadata rather than profile naming
- The current stable smoke contract for upstream runtime verification is `review apply --file ... --dry-run` plus `admin manage stats`

## Current Testing Notes

- Test layout includes `tests/unit`, `tests/cli`, `tests/integration`, and BDD runners in `tests/test_*_bdd.py`
- CI includes targeted Ruff checks on maintained surfaces, docs command lint, CLI contract tests, unit tests, selected integration smoke, one approval migration E2E path, hub-lite integration coverage, and a BDD smoke run
- Always treat live `pytest` and CI results as the source of truth for pass/fail counts

## Current Documentation Rule

- Use this file and `README.md` for current behavior
- Use `docs/manuals/VPSAGENTWEB_WRITEBACK_CONTRACT.md` for the current controlled writeback contract
- Use `TODO.md` for the live follow-up list and deferred scope
- Treat implementation plans, architecture reviews, child-session notes, and reports as historical or planning material unless they explicitly say they are current
