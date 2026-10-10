# TODO

## Dual-track migration (approved 2026-09-26)

- [x] Isolated canonical Nowledge mirror, revision history, refresh receipts and read-only SSH MCP.
- [x] Fix Chinese substring fallback, scoped deduplication, metadata ranking filters and mixed embedding coverage.
- [x] Configure local Kimi/Codex/Grok with primary Nowledge and comparison shadow.
- [x] Document proposed cross-device/provider/agent/model/project architecture, retrieval pipeline and upstream research.
- [ ] P0 — Freeze canonical evidence and 40 real retrieval cases; expand to 120 before hybrid tuning.
- [ ] P1 — Measure and reduce sync traffic, collect 14 days of operation, prove off-host restore.
- [ ] P2 — Implement authenticated identity/event/revision contracts in isolation; validate real multi-client workflows.
- [ ] P3 — Evaluate Chinese full-text + learned vectors, RRF, version-safe generation switching and degradation.
- [ ] P4 — Evaluate evidence-backed temporal relations; retain only graph features with measured benefit.
- [ ] P5 — Implement scoped Working Memory, handoffs, thread evidence and revocation propagation.
- [ ] P6 — Validate NAS34 candidate deployment and pass migration gates before the user's switch-window decision.

Readiness review (2026-10-07) re-baselined progress against these stages: `docs/design/nowledge-replacement-readiness.md`. It records gap list G1–G12 and work packages W-00–W-11. The 14-day availability window closes 2026-10-10 13:14.

Full-chain read-only audit at the window close: [`docs/reports/2026-10-10-status-audit-and-todo-map.md`](docs/reports/2026-10-10-status-audit-and-todo-map.md). Shadow-side health is inside all gates (97.0% cadence, 24 h flow 1.75 GB / 0 errors, oldest verify ≈2.1 h, backup 17.7 h, no alerts). The open items are the code↔deployment↔gate decoupling below, not the mirror's own availability.

Work packages (2026-10-07):

- [x] W-00 data contract: `unit_type→kind`, claim_status default `undeclared`, registry-based `project` set semantics (557/2048 = 27.2% assigned, 19 `multi`), honest `shadow_search` reply.
- [x] W-02 retrieval projection: trigram `records_fts` + `cjk_bigrams` for 2-character CJK, per-path index trust with reported fallback (8 queries index vs forced scan: 0 differences).
- [x] W-03 traffic experiment: `sync --manifest-cache-seconds` (default 0 = production unchanged). Live cadence **not** switched; 24h baseline accrues in `sync_runs`.
- [x] W-04 source API probe: `/memories` has **no** incremental capability; `/fs/find` (lightweight listing) + `/fs/stat` (`updated_at`) found, but `since` filters on `created_at` only. See §5.5.1 of the readiness review for the revised P1 plan.
- [x] W-05 off-host encrypted backup + timed restore drill (RTO 12.92 s, identity digest identical); daily launchd job.
- [x] W-06 client access: Codex/Kimi/Grok real agent-driven calls (Grok re-registered); DSH plugin active. Residual: DSH new-session call.
- [x] W-07 operation report generator + 7 alert thresholds (each proven to fire) + M1 hourly alert job + M3 daily log rotation.
- [x] W-09 P2 write-path minimal design review (design only; 5 open questions need a user decision before implementation).
- [x] W-10 sync metering: `sync_runs` + bounded `sync_errors` ledger, rolling 24h traffic in `status.metering`.
- [x] W-11 thread coverage probe: 49/49 deepseek-harness threads map to the DSH session index; codex/grok do not (254/742 records total).
- [x] W-01 P0 evaluation: 40-case private corpus + dual-backend harness + frozen Nowledge baseline (scope=native: shadow Hit@5 0.771, isolation violations 0; Nowledge default search 0.200). The `semantic_paraphrase` category scores 0.000 for **both** backends and must be redesigned before P3.
- [x] W-08 delivery: branch `feature/nowledge-readiness` + tag `shadow-readiness-2026-10-07` pushed to origin; at delivery time the live M3 release `7b5e77bff1ad36c06563` equalled the tag's `memory_tool` tree digest. **Superseded 2026-10-07 13:43** — the live release is now `7aaac3fcd55fffe2a143` (= code at `545f0e7`), and `main` has since been advanced to `2260788`; see the P0 deployment-drift item below.

**Still gated on time or a user decision** (not unfinished work):

- [ ] **Read-path rollover phase 1 (now on)**: call `shadow_compare` alongside normal lookups; the M3 job `co.los.memory-shadow-compare-drain` fills in the primary side every 900 s. Collect ≥30 real queries per query form before setting the phase-2 category boundary. Operator policy: `docs/manuals/SHADOW_INVOCATION_POLICY.md`.
- [ ] **Phase 2**: route literal/short-CJK queries to the shadow in one client (DSH first), gate on human spot-check ≥20 answers and on `nowledge_only` not causing misses; rollback = remove the MCP entry.
- [x] **P0 — CI 在主分支上长期为红（2026-10-10 实测 / 已修）**: `unit-and-integration` 的 "Run unit tests" 在干净检出上必失败，根因两条：(a) `scripts/create_hub_lite_records.py` 把 gitignore 掉的 `logs/` 当 `required_dirs`，脚本因此 `Acceptance state: BLOCKED` 并非零退出；(b) `tests/unit/test_hub_lite_record_script.py` 的产物用例硬编码了一个脚本早已不再生成的 child-task id（`…20260309063153`），只能匹配到操作者机器上累积的陈旧文件——**假绿**，比失败更糟。修法：`logs/` 移出 `required_dirs`；脚本新增 `HUB_LITE_LOG_DIR`（默认 `ROOT/logs`）让用例把产物写进 `tmp_path` 并断言本次运行自己的产物。顺带修掉 CI 第 4 个失败：`KnowledgeBase.get_unused_entries` 的 cutoff 用本地 naive `datetime.now().isoformat()`，与以 `utc_now()` 写入的 `…Z` 值做字符串比较，**在 UTC 主机上 `Z` 排在 `.` 之后，导致该查询恒返回空**（本地 +08:00 只是碰巧为真）——已把 cutoff 固定为 UTC 同格式，并加时区无关性回归用例。验证：干净树 + `TZ=UTC` 跑 `pytest tests/unit -m "not e2e"` **655 passed**；8 个 CI job 本地逐条通过；全套 **784 passed**；`MEMORY_DISABLE_EXTENSIONS=knowledge` 仍能干净禁用。**注意**：`main` 上的 CI 需下一次推送/PR 才会真正变绿。
- [x] **P0 — M3 现网发布落后仓库 11 个提交（2026-10-10 实测 / 已修）**: 修复前现网发布 `7aaac3fcd55fffe2a143` = `545f0e7`，且 `maintenance` 还指向第三份更旧的代码 `b7fb36d5b326a9c0ace5`；现网缺 `TOMBSTONE_RECHECK_SECONDS`（墓标仍每轮重探）、`recall_probe`、compare 台账落盘路径修正，后果是日 `missing` 0（至 10-06）→ 33 → 129 → 440（10-09）→ **1229（10-10）**。现网已升到 `9cf8fbc8755ad65173ec` = 提交 `03c15ab` 的树摘要，四个 job + `serve` 同源；升级后首个 sync 轮次 `missing: 0`、`tombstones_deferred: 118`，灌水停止。步骤与验收见 `docs/reports/2026-10-10-shadow-14day-formal.md` §16.2。
- [ ] **`deploy_shadow.py` 只对齐一个 job（新发现）**: 它只重写 `co.los.memory-shadow.plist` 与 `serve`，`compare-drain` 与 `maintenance` 必须手工改 `WorkingDirectory`——本次"三个 job 指向两个发布"正是这么来的。应让脚本接收 job 清单或自动发现同前缀 job，否则每次部署都会重新引入同一漂移。
- [ ] **P1 — DSH 影子 MCP 反复掉线并会静默注销工具（2026-10-10 实测）**: `~/.dsh/logs/dsh-web.log` 每天 21–22 条 shadow 事件；10-08 08:13 与 10-09 08:17 两次走到 `10/10 consecutive failed reconnect attempts — tools unregistered`；10-10 21:45 在 attempt 9/10 才重连。根因判为宿主侧网络/睡眠抖动 + `ConnectTimeout=5` + 10 次上限，M3 侧可排除（caffeinate、uptime 12 天、sync 0 缺口）。**本次全新 DSH 会话的工具面里没有 `shadow_*`**，即 W-06 残留项未关闭。先修这一条，否则"阶段 1 每形态 ≥30 条"不可能达成。
- [ ] **P1 — 主库检索覆盖部分且不可预测（2026-10-10 探测器定论）**: 主库挂起已由 `NMEM_BOOT_AUTO_REINDEX=0` drop-in 于 10-07 15:42 打断（`reindex/status` `active=false`、`errors=[]`），但挂起只是被取消、**没有回填**。`shadow recall-probe` 两次运行（手动 + launchd kickstart）结论一致：`verdict=stale_projection_suspected`、`recent_rate 0.0`（5 探针全灭）、`control_rate 0.667`（主库在应答）。独立交叉验证：2026-10-10T14:25 的记录按 ID 可读、按其标题辨识短语查不到；同日 09:45 的另一条能稳定排第 1（2/2）。**结论：不是整齐的"某日之后全丢"，而是覆盖部分且不可预测，而 `/search-index/status` 始终报 `available: true`**。probe 的 `newest_retrievable_created_at`(2026-07-03) 只是被探集合的边界，不可读成"索引止于 7 月"；probe 自身 `reading` 也警告 anchor 可能失真。索引重建属上游/用户决策，**不要反复重建**（会再次挂死）。
- [x] **P0 — 出 14 天正式运行报告**: 已完成，见 [`docs/reports/2026-10-10-shadow-14day-formal.md`](docs/reports/2026-10-10-shadow-14day-formal.md)（14.38 天、4016 轮 / 6 带错、到位率 97.0%、中位间隔 307.1 s、24 h 1.63 GiB / 0 错、RTO 132.97 s），草案稿已标注取代。门槛 1–5 通过；门槛 6（真实任务采纳）**未通过**，阻塞在下方 DSH MCP 掉线项。报告同时修正两处口径：`missing` 列是墓碑重探次数、`restore-drill` 的 `digest_match` 应与台账摘要比对（工具缺陷，另列待修）。
- **P1 cadence switch** — `--manifest-cache-seconds` is implemented and the 24 h baseline now exists in `sync_runs` (275 runs / 32,493 requests / 1.75 GiB / 0 errors per day). Per the approved plan the live 300 s cadence stays until a user decision switches it; the TODO suggestion is a 30-minute listing interval so new-record discovery keeps margin against the ≤1 h gate. Note the tradeoff: during the primary's suspected retrieval gap (see the P1 probe item) freshness is a functional parameter, not a cost parameter.
- **P2 implementation** — design review only; the 5 open questions in `docs/design/p2-write-path-minimal-loop.md` §7 need a user decision first.

Stage goals, dependencies, acceptance metrics and rollback are maintained in `docs/design/memory-roadmap.md`; architecture and retrieval contracts are design proposals, not current runtime features.

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

- [x] Repo currently has no immediate feature blocker.
- [x] Cleanup pass (2026-10-08): escape LIKE wildcards in every user-input search path; `los_memory` matched 895 of 5,355 real records that do not contain it, and a bare `%` or `_` returned the whole table. See `docs/reports/2026-10-08-core-capability-fixes.md`.
- [x] Cleanup pass (2026-10-08): resolve the shadow compare ledger from the served database instead of the process default, so a fixture or test run can no longer write into the operator's production state directory (10 such artifacts had accumulated).
- [x] Cleanup pass (2026-10-08): emit CJK bigrams from the embedding tokenizer; `--semantic` scored Hit@1 0.033 on the real ledger because a whole CJK run was one token. Now at parity on rank 1 but still behind on Hit@5 at ~70x the latency, so `auto` stays the default.
- [x] Cleanup pass (2026-10-08): stop re-probing deactivated records every rotation (24 h tombstone recheck), which had been inflating the report's `missing` count.
- [x] Cleanup pass (2026-10-08): add `scripts/measure_core_search.py` plus a CI self-check and fixture-based quality gate; core retrieval previously had no quality measurement anywhere.
- [ ] Keep legacy flat-command compatibility until downstream grouped-command migration is fully absorbed and verified across all integrators.
- [ ] If a future integrator needs correction provenance beyond current fields, extend feedback metadata rather than introducing a second correction object model.
- [ ] Continue non-blocking complexity cleanup opportunistically if new hotspots emerge.
- [ ] Make `--semantic` a bounded re-ranker over literal candidates instead of an O(N) full scan (946 ms at 5,355 records), measured with `scripts/measure_core_search.py` before and after. Do not raise its dimensionality first: dim 32 → 256 measured +0.033 Hit@1 for 4-6x the cost.
- [x] Cleanup pass (2026-10-08): retry a single-term search query as a quoted FTS phrase. FTS5 gives `- / . :` operator meaning, so path/filename/identifier queries raised `sqlite3.OperationalError` — swallowed into a full-table LIKE scan in `mode="auto"` (100% of such queries, ~20x the indexed latency) and surfaced as a raw SQLite error under `--mode fts`. p95 measured 7.7 ms → 0.5 ms; multi-word AND semantics deliberately preserved.
- [ ] Decide whether literal containment should outrank an FTS token match, so a separator-variant match cannot outrank the exact record. Sampling 80 unique-identifier queries per separator group after the quoting change gave a **mixed** result (Hit@1 0.775 → 0.812 underscore, 0.887 → 0.863 other separators), so this needs a larger frozen set before any change — do not act on the original single `los_memory` example, which turned out to be a tail case.
- [ ] Fix `scripts/shadow_backup.py restore-drill` reference selection: it compares the restored database against the **current live** mirror, so `digest_match` is `false` for every normal case where the mirror grew after the backup was taken (measured 2026-10-10: `records_restored=2329` vs `records_expected=2419`). The restored identity digest `2cb736b0…` **is** byte-identical to that backup's `identity_digest` in `backup-ledger.jsonl`, so the restore itself is sound — the instrument reads a success as a failure and erodes trust in the drill. Compare against the ledger digest first and report the live digest as advisory. Also note the RTO trend: 12.92 s (2026-10-07) → **132.97 s** (2026-10-10) as the backup grew 60 MB → 164 MB; still inside the ≤1 h gate, but re-measure as the mirror grows.
- [ ] Give the shadow alert a delivery channel; it currently records to `alerts.jsonl` and exits 1 with nowhere to notify. 2026-10-10 实测补充：该 launchd job 在 M1 睡眠期间漏跑（`runs=76`、最后落盘 18:09，而盘点时刻 22:14，漏 4 次），所以投递通道之外还要处理"睡眠期不触发"这个前提。
- [ ] Define a retention policy for inactive `records` rows and `revisions`.
- [x] Build the primary recall canary (2026-10-08): `shadow recall-probe` takes rare literal anchors from the mirror and asks the primary for them, in `recent` / `control` / `span` groups so a stale projection cannot be confused with a dead primary. Read-only on both sides; 23 tests. Measured on the live mirror: `recent_rate` 0.0, `control_rate` 0.667. Manual: `docs/manuals/SHADOW_MEMORY.md`.
- [x] Install the recall probe as a scheduled job (suggested: M3 launchd every 6-12 h, off the 300 s sync cadence). **Done 2026-10-10**: M3 job `co.los.memory-shadow-recall-probe`, `StartInterval=21600`, output `recall-probe.out.log`, verified by `launchctl kickstart` (not just a written plist). The same pass made `rotatelog` accept repeated `--log` so the maintenance job now caps `sync.out.log`, `compare-drain.out.log` and `recall-probe.out.log` (only the first was capped before).
- [ ] **The probe reports but nothing watches it**: no alert threshold reads its `verdict`, so a `stale_projection_suspected` result still needs a human to open `recall-probe.out.log`. Add an 8th threshold to `scripts/shadow_report.py` — and, per this project's own standard, a test proving it fires — then give alerts a delivery channel (the item below is the other half of the same job).
- [ ] 2026-10-10 re-opened: two shadow tests emit `ResourceWarning: unclosed database` (`memory_tool/shadow.py` search path) under default pytest warning mode, so the suite is no longer warning-clean after the 2026-03-13 pass that made it so. Suite result at this revision: 781 passed / 11 warnings.
- [ ] Widen the recall probe's boundary resolution only if needed: closing the bracket properly needs a bisection, and each step costs the primary ~13 s.
- [x] Cleanup pass (2026-03-13): align Makefile shortcuts with the modern `los-memory` / `python -m memory_tool` CLI entrypoints while preserving local workflow compatibility.
- [x] Cleanup pass (2026-03-13): align core README/manual command examples with the modern `python -m memory_tool` / `python -m memory_tool.viewer` / `python -m memory_tool.ingest` entrypoints while keeping the legacy script path documented as compatibility-only.
- [x] Cleanup pass (2026-03-13): align remaining active lsclaw integration manuals to the modern `python -m memory_tool` review-apply entrypoint while keeping compatibility notes explicit elsewhere.
- [x] Cleanup pass (2026-03-13): surface current warning debt by removing blanket warning suppression, then fix or explicitly filter expected approval migration deprecation warnings.
- [x] Cleanup pass (2026-03-13): close test-side resource leaks in SSE/adapter coverage so the suite stays warning-clean under default pytest warning mode.
- [x] Cleanup pass (2026-03-13): refresh stale future-development guidance so it reflects the current CI/test layout instead of historical placeholders.
- [x] Cleanup pass (2026-03-13): add bulk observation write support from JSON payloads, including `@-` stdin input, as a primary writeback path.
- [x] Cleanup pass (2026-03-13): add metadata-native equality filters for `memory search` / `memory list` across CLI and Python client surfaces.

## External Follow-ups (`lsclaw`)

- [x] Add `check:los-memory-adapter` to a default gate path, not just standalone script entrypoints.
- [x] Expand `verify-los-memory-adapter` runtime coverage beyond `--help` checks to include real grouped-command smoke for `admin manage`, `observation delete`, and `review apply`.

## Phase 演进定位

los-memory 在 lsclaw 六阶段演进框架中的角色：

| Phase | 角色 | 状态 |
|-------|------|------|
| P2 可追踪性 | State Snapshot 持久化后端 | 待定义存储格式 |
| P4 领域隔离 | Memory Domain 持久化后端 | ✅ 已就绪（12 种类型 + scope 隔离） |
| P5 插件运行时 | Embedding Capability 提供方 | ⚠️ 待建设（Step 3b） |
| P6 大型治理 | Event Store（Event Sourcing 后端） | 🔴 远期 |

## Deferred

- [x] Former deferred backlog item completed: bulk write / stdin JSON as a primary writeback path.
- [x] Former deferred backlog item completed: metadata-native filters for `memory search` / `memory list`.
- [ ] **Step 3b: Embedding 管道下沉** (关联 T4)
  - 2026-09-26 实查：已有 `memory search <query> --semantic` 与 SHA256 token hash 32 维向量，并非学习式 embedding 或完整 TF-IDF。
  - 本轮修复 metadata 过滤和有/无 embedding 混合记录覆盖；真实检索质量仍须黄金样例评估。
  - lsclaw 管线迁移和可选模型 embedding 属于历史跨仓计划，未在本轮实施或验证。
