# TODO

## Completed

- [x] Restore parser-level compatibility for legacy flat commands during downstream migration.
- [x] Formalize observation metadata write path and round-trip readback.
- [x] Formalize feedback metadata write path and review-apply propagation.
- [x] Publish a repo-local writeback contract for `VPS Agent Web` / controlled integrators.
- [x] Keep `README.md` and `docs/current/CURRENT_STATE.md` aligned with the implemented metadata + profile boundary.
- [x] Freeze `review apply --file ... --dry-run`, `admin manage stats`, and `observation delete --dry-run` as stable smoke contract targets.
- [x] Cleanup pass (2026-03-12): enforce SQLite foreign keys at connection bootstrap.
- [x] Cleanup pass (2026-03-12): align checkpoint observation query to snapshot boundary (`timestamp <= checkpoint.timestamp`) and keep checkpoint project in sync during project archive.
- [x] Cleanup pass (2026-03-12): preserve observation `metadata` on session/checkpoint read paths.
- [x] Cleanup pass (2026-03-12): align hub-lite observation writes with core format (`tags` as JSON, UTC `Z` timestamp).
- [x] Cleanup pass (2026-03-12): align schema/contracts drift (`contracts.SCHEMA_VERSION` source-of-truth, session/base schema enum updates, schema-version tests updated).
- [x] Cleanup pass (2026-03-12): make approval request + audit + event persistence atomic, and delay in-memory event broadcast until post-commit.
- [x] Cleanup pass (2026-03-12): enforce DB-side enum guards for `sessions.status`, `observation_links.link_type`, and `feedback_log.action_type` (new-table `CHECK` + v15 migration normalization + guard triggers for legacy tables).
- [x] Cleanup pass (2026-03-12): extend DB-side status guards to incident/recovery/approval status fields (v16 migration normalization + legacy-table triggers).
- [x] Cleanup pass (2026-03-12): extend DB-side non-status enum guards to incident/recovery/approval core enums (`incident_type`, `severity`, `execution_strategy`, `risk_level`, `approval_audit_log.action`) via v17 migration + legacy-table triggers.
- [x] Cleanup pass (2026-03-12): extend DB-side enum guards to `incident_observations.link_type`, `recovery_actions.action_type`, and `recovery_policies.trigger_type` (v18 migration normalization + legacy-table triggers; includes `database -> switch_database` action alias normalization).
- [x] Cleanup pass (2026-03-12): align response/schema contract drift (`schema.SCHEMA_VERSION` -> `1.1.0`, `output.success(**extra_meta)` no-op fixed, observation kind schema made extensible, incident extension paths switched to canonical shims).
- [x] Cleanup pass (2026-03-12): align CLI session status filter with DB/model enum by accepting `ended` in `session list --status`.
- [x] Cleanup pass (2026-03-12): split CLI parser construction into composable command-group builders (`_register_*_subcommands` + `_build_parser`) to reduce `parse_args` complexity without behavior changes.
- [x] Cleanup pass (2026-03-12): split CLI command dispatch into command-group helpers (`_dispatch_memory_command` / `_dispatch_observation_command` / `_dispatch_tool_command` / `_dispatch_admin_command` / `_dispatch_review_command`) while preserving `None`-return handlers (e.g. `memory export` stdout mode).
- [x] Cleanup pass (2026-03-12): split DB migration hotspot by extracting v15-v18 blocks into dedicated helpers (`_migrate_to_v15`...`_migrate_to_v18`) while preserving migration SQL/trigger behavior.
- [x] Cleanup pass (2026-03-12): further split DB migration path by extracting v13/v14 metadata migrations into dedicated helpers (`_migrate_to_v13`, `_migrate_to_v14`).
- [x] Cleanup pass (2026-03-12): complete migration hotspot extraction by moving legacy blocks `v1-v12` out of `migrate_schema()` into composable helpers (`_migrate_to_v1`...`_migrate_to_v12`) with behavior parity.
- [x] Cleanup pass (2026-03-12): reduce trigger hotspot complexity by splitting `_ensure_status_guard_triggers`, `_ensure_non_status_enum_guard_triggers`, and `_ensure_followup_enum_guard_triggers` into table-scoped helper installers while preserving trigger SQL contracts.
- [x] Cleanup pass (2026-03-12): split `cli_recovery.py` parser registration and command handling into composable `_add_recovery_*` / `_handle_*` helpers while preserving CLI contract and return payload shape.
- [x] Cleanup pass (2026-03-12): split `cli_incidents.py` parser registration and command dispatch into composable `_add_incident_*` / `_handle_incident_*` helpers while preserving incident + attribution command behavior.
- [x] Cleanup pass (2026-03-12): split `cli_approval.py` parser registration into `_add_approval_*` helpers and switched action dispatch to an explicit handler map while preserving command semantics.
- [x] Cleanup pass (2026-03-12): split `cli_knowledge.py` parser registration into `_add_knowledge_*` helpers and switched action dispatch to handler mapping while preserving CLI behavior.
- [x] Cleanup pass (2026-03-12): split `doctor.run_all_checks` into execution/aggregation/grouping helpers to reduce branching complexity while preserving health-report output contract.
- [x] Cleanup pass (2026-03-12): split `ResolutionExtractor.extract_from_incident` in `knowledge_base.py` into load/extract/build helpers while preserving resolved-incident extraction behavior and defaults.
- [x] Cleanup pass (2026-03-12): deduplicate `ApprovalAPI.approve_request` / `reject_request` through shared decision pipeline helper while preserving optimistic-lock, audit/event transactionality, and response/error contracts.
- [x] Cleanup pass (2026-03-12): split `share.run_share` into query/session/bundle/write helpers while preserving export filters and bundle format.
- [x] Cleanup pass (2026-03-12): split `share.run_import` into bundle-load/session-import/observation-import helpers while preserving dry-run and session-id remap semantics.
- [x] Cleanup pass (2026-03-12): deduplicate `ApprovalStore.approve` / `reject` through shared `_transition_request` path while preserving optimistic-lock and audit semantics.
- [x] Cleanup pass (2026-03-12): split baseline `_ensure_enum_guard_triggers` into table-scoped helper installers for sessions/observation-links/feedback guard triggers.
- [x] Cleanup pass (2026-03-12): split `apply_feedback` into action-scoped helpers (`delete`/`correct`/`supplement`) while preserving feedback recording and auto-apply behavior.
- [x] Cleanup pass (2026-03-12): split `run_search` into FTS/LIKE execution helpers + shared row/tag filtering helpers while preserving fallback semantics and payload shape.
- [x] Cleanup pass (2026-03-12): split `find_similar_observations` into source-load/candidate-load/scoring/result helpers while preserving similarity weighting and threshold behavior.
- [x] Cleanup pass (2026-03-12): split migration internals of `_migrate_to_v9`/`_migrate_to_v10` into table/index/seed helper units while preserving migration SQL behavior.
- [x] Cleanup pass (2026-03-12): split `KnowledgeBase.search` into FTS id lookup / row retrieval / filter / scoring helpers while preserving ranking and fallback behavior.
- [x] Cleanup pass (2026-03-12): split `VPSAgentWebClient._make_request` into request-build / single-attempt execution / response-parse / retry-error helpers while preserving retry and HTTP error semantics.
- [x] Cleanup pass (2026-03-12): reduce migration adapter branching duplication by introducing guarded backend accessor helpers and shared HMAC header preparation while preserving LOCAL/DUAL/REMOTE routing semantics.
- [x] Cleanup pass (2026-03-12): split migration internals of `_migrate_to_v8` / `_migrate_to_v16` / `_migrate_to_v17` into table-index and enum-normalization helper units while preserving SQL and trigger behavior.
- [x] Cleanup pass (2026-03-12): split `MemoryClient.add` / `capture` into project-tag-session resolution and title-summary extraction helpers while preserving write payload and capture semantics.
- [x] Cleanup pass (2026-03-12): split `DualWriteManager._execute_with_fallback` into read-only/side-exec/success-resolution/error-aggregation helpers while preserving dual-write mode behavior and error contracts.
- [x] Cleanup pass (2026-03-12): deduplicate migration adapter `approve_request` / `reject_request` through shared decision pipeline while preserving HMAC re-sign and LOCAL/DUAL/REMOTE routing behavior.
- [x] Cleanup pass (2026-03-12): add direct `VPSAgentWebClient` unit coverage for 4xx/5xx, timeout/URL retry exhaustion, and non-JSON response fallback parsing.
- [x] Cleanup pass (2026-03-13): split `run_clean` into cutoff/where/delete/vacuum helpers and `run_manage` into action dispatch + per-action query helpers while preserving payload contracts.
- [x] Cleanup pass (2026-03-13): split `ApprovalAPI.create_request` into duplicate-check / risk-validate / transactional persist / response-build helpers while preserving error and transaction semantics.
- [x] Cleanup pass (2026-03-13): split CLI ValueError mapping `_build_cli_error` into focused not-found / validation / review matchers while preserving standardized error-code mapping.
- [x] Cleanup pass (2026-03-13): split `cli.main` into configuration/init/doctor/standard-command helpers while preserving CLI output and exit-code behavior.
- [x] Cleanup pass (2026-03-13): split `viewer.Handler.do_GET` into API route dispatch and endpoint-specific handlers while preserving auth, 404, and 500 response behavior.
- [x] Cleanup pass (2026-03-13): split `_register_observation_subcommands` into per-subcommand parser builders while preserving CLI options/defaults and legacy compatibility.
- [x] Cleanup pass (2026-03-13): split `share._write_html_bundle` into header/sessions/observations rendering helpers while preserving export content semantics.
- [x] Cleanup pass (2026-03-13): split `apply_feedback` into operation-loader + action-dispatch helpers while preserving delete/correct/supplement behavior and feedback logging semantics.
- [x] Cleanup pass (2026-03-13): split `AutoRecoveryEngine.evaluate_and_recover` into evaluation-step helpers while preserving trigger/policy/incidents/recovery result semantics.
- [x] Cleanup pass (2026-03-13): split `_register_memory_subcommands` into per-subcommand parser builders while preserving memory command options/defaults.
- [x] Cleanup pass (2026-03-13): refactor `migrate_schema` to table-driven ordered migration steps while preserving version bump semantics.
- [x] Cleanup pass (2026-03-13): split approval-audit and incident non-status enum guard installers into focused helper units while preserving trigger SQL contracts.
- [x] Cleanup pass (2026-03-13): split `MemoryClient.edit` into serialization + run helpers while preserving update payload and not-found behavior.
- [x] Cleanup pass (2026-03-13): split v10 approval table migration into table-scoped builders while preserving schema SQL and constraints.
- [x] Cleanup pass (2026-03-13): split v9/v10/v11 migration table/index builders into finer helper units while preserving migration SQL behavior.
- [x] Cleanup pass (2026-03-13): split `ensure_schema` core table bootstrapping into table-scoped helpers while preserving bootstrap + migration behavior.
- [x] Cleanup pass (2026-03-13): split `ApprovalStore._ensure_tables` into table/index helpers while preserving DDL SQL and commit behavior.
- [x] Cleanup pass (2026-03-13): split `KnowledgeBase._ensure_tables` into table/fts/trigger/index helpers while preserving FTS sync and commit behavior.

## Local Remaining

- [ ] No immediate feature blocker in this repo.
- [ ] Keep legacy flat-command compatibility until downstream grouped-command migration is fully absorbed and verified across all integrators.
- [ ] If a future integrator needs correction provenance beyond current fields, extend feedback metadata rather than introducing a second correction object model.
- [ ] Continue non-blocking complexity cleanup opportunistically if new hotspots emerge.
- [x] Cleanup pass (2026-03-13): align Makefile shortcuts with the modern `los-memory` / `python -m memory_tool` CLI entrypoints while preserving local workflow compatibility.
- [x] Cleanup pass (2026-03-13): align core README/manual command examples with the modern `python -m memory_tool` / `python -m memory_tool.viewer` / `python -m memory_tool.ingest` entrypoints while keeping the legacy script path documented as compatibility-only.
- [x] Cleanup pass (2026-03-13): surface current warning debt by removing blanket warning suppression, then fix or explicitly filter expected approval migration deprecation warnings.
- [x] Cleanup pass (2026-03-13): close test-side resource leaks in SSE/adapter coverage so the suite stays warning-clean under default pytest warning mode.
- [x] Cleanup pass (2026-03-13): refresh stale future-development guidance so it reflects the current CI/test layout instead of historical placeholders.

## External Follow-ups (`lsclaw`)

- [x] Add `check:los-memory-adapter` to a default gate path, not just standalone script entrypoints.
- [x] Expand `verify-los-memory-adapter` runtime coverage beyond `--help` checks to include real grouped-command smoke for `admin manage`, `observation delete`, and `review apply`.

## Deferred

- [ ] Bulk write / stdin JSON as a primary writeback path.
- [ ] Metadata-native filters for `memory search` / `memory list`.
