# Current State

This document records the current, implemented state of `los-memory`.

## Isolated Nowledge shadow (updated 2026-10-07)

- `memory_tool.shadow` mirrors default-space durable records through canonical Nowledge REST reads; stores source IDs, snapshots, hashes, revisions and refresh attempts in a separate private SQLite database.
- `memory_tool.shadow_mcp` exposes `shadow_search`, `shadow_get`, `shadow_status` and `shadow_compare` over stdio; all four are read-only. Search is literal substring matching; `shadow_search` returns `{results, meta}` where `meta` names the index path per term (`trigram` / `bigram` / `scan`), the uncovered terms, and the coverage behind any filter. `kind` filters on the mapped `unit_type`; `claim_status` is `undeclared` when the source has none.
- Read-path rollover phase 1 is **on**: `shadow_compare` answers the shadow side immediately and queues the query; the M3 job `co.los.memory-shadow-compare-drain` (every 900 s, max 8 uncached lookups per run, 24 h query cache) queries the primary off the critical path and appends to `compare-results.jsonl`. `shadow compare-report` aggregates divergence by query form. The primary is never called synchronously in a comparison path because `nmem memories search` costs a measured ~13 s. Policy: `docs/manuals/SHADOW_INVOCATION_POLICY.md`.
- The compare ledger resolves from the **served database's directory** (`PRAGMA database_list`), not from the process default, so pointing the MCP at a fixture database keeps its queue beside that fixture; an explicit `state_dir` still overrides. Phase 1 has collected 1 sample so far — the gap is adoption, not plumbing: no real `shadow_compare` call has been made since the instrument went on.
- A record the source reports as gone is deactivated but kept, and is re-probed at most once per `TOMBSTONE_RECHECK_SECONDS` (24 h) rather than every rotation; each run reports `tombstones_deferred`. Deletions are still discovered by the manifest.
- `shadow recall-probe` detects what the existing alerts structurally cannot: a primary that reports `Search Index: Ready` while content written after some point is unretrievable. It takes rare literal anchors from the mirror and asks the primary for them, in three groups — `recent` (the signal), `control` (the oldest records, proving the primary answers at all) and `span` (evenly spaced, locating the boundary). The verdict compares `recent` against `control`; the `*_created_at` fields are indicative only and `created_at` is not index-ingestion order. Read-only on both sides; each probe costs the primary ~13 s, so it is a scheduled job, never a read path. Measured 2026-10-08: `recent_rate` 0.0, `control_rate` 0.667. **No job is installed yet** — see `docs/manuals/SHADOW_MEMORY.md`.
- Derived structures are `records_fts` (FTS5 trigram), `cjk_bigrams` (2-character CJK, which trigram cannot serve), `record_facets` / `record_labels` (kind/project/claim_status/source_app) and the `sync_runs` / `sync_errors` metering ledgers. All are rebuildable from `records` with `shadow reindex`; an index that was never built reports `search_index.state = not_built` instead of answering "no memories".
- `project` is **not** a Nowledge field: 0 of 2,048 mirrored records carry `metadata.project`. It is assigned only from a registered label (`memory_tool/shadow_registry.py`) or an explicit `metadata.project` declaration, else `unassigned` — currently 557 / 2,048 (27.2%) assigned, with 19 records marked `multi` because they genuinely carry two registered project labels. Filtering is label-set membership, so a `multi` record is still found under each of its projects.
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
- Search treats user text literally: `%` and `_` in a query match themselves, not as LIKE wildcards (via `escape_like` / `like_pattern` in `memory_tool/utils.py`, paired with an explicit `ESCAPE '\'` clause). In `mode="auto"` the FTS5 tokenizer still splits an identifier such as `los_memory` into `los` + `memory`, so containment and token matching can still disagree there; that ranking question is open (see `TODO.md`)
- `memory search --semantic` is a **deterministic lexical-overlap re-ranker, not a learned embedding**: it hashes tokens into 32 dimensions, with 2-character windows added for runs of Han characters. Measured on the shared ledger it is at parity with `mode="auto"` on Hit@1 but behind on Hit@5 at roughly 70x the latency, so `auto` remains the default and no retrieval-quality claim should be made without re-running `scripts/measure_core_search.py`
- Core retrieval quality is measured by `scripts/measure_core_search.py` (rarity-controlled Hit@K plus `--self-check`, which is a CI step); `tests/unit/test_core_search_quality.py` is the CI-able gate and fails if `--mode` stops defaulting to `auto`
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
