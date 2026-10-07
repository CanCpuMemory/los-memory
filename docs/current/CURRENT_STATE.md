# Current State

This document records the current, implemented state of `los-memory`.

## Isolated Nowledge shadow (updated 2026-10-07)

- `memory_tool.shadow` mirrors default-space durable records through canonical Nowledge REST reads; stores source IDs, snapshots, hashes, revisions and refresh attempts in a separate private SQLite database.
- `memory_tool.shadow_mcp` exposes only `shadow_search`, `shadow_get`, `shadow_status` over stdio. Search is literal substring matching; `shadow_search` returns `{results, meta}` where `meta` names the index path per term (`trigram` / `bigram` / `scan`), the uncovered terms, and the coverage behind any filter. `kind` filters on the mapped `unit_type`; `claim_status` is `undeclared` when the source has none.
- Derived structures are `records_fts` (FTS5 trigram), `cjk_bigrams` (2-character CJK, which trigram cannot serve), `record_facets` / `record_labels` (kind/project/claim_status/source_app) and the `sync_runs` / `sync_errors` metering ledgers. All are rebuildable from `records` with `shadow reindex`; an index that was never built reports `search_index.state = not_built` instead of answering "no memories".
- `project` is **not** a Nowledge field: 0 of 2,048 mirrored records carry `metadata.project`. It is assigned only from a registered label (`memory_tool/shadow_registry.py`) or an explicit `metadata.project` declaration, else `unassigned` — currently 576 / 2,048 (28.1%) assigned. A filtered empty result is not evidence that a project has no memories.
- M3 runs a launchd refresh every 300 seconds, at most 100 records per invocation; the initial baseline used an explicit larger batch. This is a rotating refresh, not a five-minute freshness guarantee for every record. `sync --manifest-cache-seconds N` (default 0 = unchanged production behaviour) can decouple the full listing from the per-record refresh for a measured traffic reduction.
- Nowledge remains the only formal write target. Raw conversations, Working Memory generation, learned semantic retrieval and primary-write migration are not implemented in the shadow.
- Core auto search falls back to LIKE on no FTS match; metadata filters apply to hash-vector ranking; mixed embedded/unembedded records are included; deduplication scopes to project and kind; content edits invalidate old embeddings and refresh content hashes.
- See `docs/manuals/SHADOW_MEMORY.md`, `docs/reports/2026-10-07-shadow-search-index.md` and `docs/reports/2026-09-26-dual-track.md` for operation and validation boundaries.

## Product Shape

- Primary runtime shape: local Python CLI and Python API backed by SQLite
- Main entrypoint: `los-memory`
- Preferred Python module entrypoint: `python3 -m memory_tool`
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
- `observation bulk --input <json|@file|@->` supports multi-item writeback from inline JSON, files, or stdin
- `Feedback` records now also persist structured `metadata`, and both `observation feedback` and `review apply` propagate it into feedback history
- `memory search` and `memory list` support metadata-native equality filters via `--metadata-filter`
- `--profile` is a storage-partition selector only; tenant/user/request/trace identity belongs in structured metadata rather than profile naming
- The current stable smoke contract for upstream runtime verification is `review apply --file ... --dry-run`, `admin manage stats`, and `observation delete --dry-run`

## Current Testing Notes

- Test layout includes `tests/unit`, `tests/cli`, `tests/integration`, and BDD runners in `tests/test_*_bdd.py`
- CI includes targeted Ruff checks on maintained surfaces, docs command lint, CLI contract tests, unit tests, selected integration smoke, one approval migration E2E path, hub-lite integration coverage, and a BDD smoke run
- Always treat live `pytest` and CI results as the source of truth for pass/fail counts

## Current Documentation Rule

- The proposed cross-device service is described in `docs/design/memory-service-architecture.md`, `memory-retrieval-pipeline.md`, and `memory-roadmap.md`. These do not mean authenticated HTTP writes, learned vector search, graph extraction, or NAS34 deployment are implemented.

- Use this file and `README.md` for current behavior
- Use `docs/manuals/VPSAGENTWEB_WRITEBACK_CONTRACT.md` for the current controlled writeback contract
- Use `TODO.md` for the live follow-up list and deferred scope
- Treat implementation plans, architecture reviews, child-session notes, and reports as historical or planning material unless they explicitly say they are current
