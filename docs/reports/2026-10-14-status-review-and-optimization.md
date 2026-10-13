# 全链路盘点与优化（2026-10-14）

状态：**盘点 + 三处已完成的修复**。生产侧三件事（M3 部署、`compact --vacuum`、`git push`）**未做**，列在 §8 等用户决定。

本轮做与不做：

- **只读取证**：M3 影子（`status`、四类 job 日志、launchd、releases、backups）、DSH 侧配置与日志、本地三个 profile 数据库。
- **写入**：本仓代码、测试与文档。M3 上的存储实验跑在该机 `/tmp` 的副本上（`sqlite3` backup API 取的快照），实验结束即删除。
- **副作用如实披露**：取证时跑了一次全量 `pytest`，它当时**仍会写生产记忆库**（+18 行，`timestamp=2026-10-13T23:27:56Z`）。这既是本轮要修的问题，也是最直接的复现证据；修复后同一套测试不再写生产库（运行前后 `shared` 库均为 6081 行，见 §2）。

---

## 1. 全链路现状（实测）

| 层面 | 实测值 | 判据 |
| --- | --- | --- |
| 仓库 | `main` = `4cc702f`，工作树 0 未提交，`git fsck` 干净，仅 1 个 tag | ✅ |
| 测试（修复前） | `pytest -q` **905 passed**，29.9 s | ✅ |
| CI | 最近一次 2026-10-11 `success`；**10-13 的提交从未推送，因此从未跑过 CI** | ⚠️ §6 |
| 现网发布 | M3 四个 job 同源 `c6d42b7de04664f8d907`，等于 HEAD 的 `memory_tool/**` 树摘要（本地复算一致） | ✅ |
| 影子镜像 | total 2457 / active 2339 / 索引 ready 2457，`unresolved_errors: 0` | ✅ |
| 同步 | 24 h：272 轮 / 33,616 请求 / 1.69 GiB / **0 错**，实测间隔 317.3 s | ✅ |
| 墓碑 | `tombstones_deferred: 118`、`missing: 0` | ✅ |
| 召回探针 | `verdict: ok`、`recent_rate 1.0`（6/6）、`control_rate 1.0`（3/3）、`errors: []` | ✅ §4 |
| 告警 | 2026-10-13 13:14 起 `alerts: []`，投递 `pushed/up` HTTP 200，台账已自封顶（49,151 B） | ✅ |
| 备份 | 2026-10-14 04:31，`integrity ok` / `readback_ok`，2457 条，83.5 s | ⚠️ RPO 23.98 h，见 §6 |
| 存储 | 文件 194.8 MiB，freelist 32.0 MiB | ❌ §3（**当日已回收至 70.5 MiB，见 §3.6**） |
| 数据契约 | `project` 已分配 622/2339 = **26.6%**，`claim_status=undeclared` 1460/2339 | ⚠️ §7 |
| 对照台账 | `compare-results.jsonl` **仍是 1 条**（与 10-07 相同） | ❌ §5 |

---

## 2. P0-1 测试套件往生产记忆库写入（已修）

### 2.1 症状与取证

跑一次全量 `pytest`，`~/.local/share/llm-memory/memory.db`（`shared` profile）的 observation **从 6063 增到 6081**。新增 18 行的 `timestamp` 是 `2026-10-13T23:27:56Z`，正是本轮启动测试的时刻，标题为脚本固定的三种：

```
[los-memory] Implementation phase started
[los-memory] Implementation verification
[los-memory] Implementation complete
```

同一形态的 18 行批次在 `2026-10-13T05:12:20Z` 还有一批——那是上一轮会话在 13:12 跑测试留下的（与提交 `4cc702f` 的 13:12:43 同分钟）。**即每次全量测试都追加 18 行。**

### 2.2 根因

- `tests/unit/test_hub_lite_record_script.py` 有 6 个用例各起一次子进程跑 `scripts/create_hub_lite_records.py`，脚本每次建 3 条记录（6 × 3 = 18）。
- 脚本构造 `HubLiteSession` 时不传 `db_path`，而 `memory_tool/hub_lite.py` 的默认值是 `resolve_db_path("shared", None)` —— **操作者的真实共享库**。
- 10-10 那次 hermetic 修复只覆盖了**日志产物**（`HUB_LITE_LOG_DIR`，用于修 CI 在干净检出上必红的问题），**写入目标没修**。这与 10-08 修过的"测试写进操作者生产状态目录"是同一类失败。

累积量（`shared` 库实测）：`[los-memory] Implementation*` 系列 ≥ **1,551 行**；hub-lite trace 项目下按日 555（10-10）、522（03-12）、171（10-07）、144（10-08）、81（03-13）、36（10-13）……

### 2.3 修法

| 文件 | 改动 |
| --- | --- |
| `memory_tool/hub_lite.py` | 新增 `_resolve_db_path()`：显式 `db_path` → `MEMORY_DB_PATH` → `shared` profile。**profile 兜底仍是字面 `shared`**：这是跨仓控制面台账，刻意不跟随 `MEMORY_PROFILE`（否则会把台账按人拆开）。 |
| `scripts/create_hub_lite_records.py` | 新增 `--db`；启动时打印 `Database:` 一行，让"这次写进了哪个库"直接可见。 |
| `tests/unit/test_hub_lite_record_script.py` | `run_script()` 同时把 `MEMORY_DB_PATH` 指向 `tmp_path`；新增 3 个用例。 |

新增用例（`TestHubLiteRecordScriptWriteTarget`）：

1. `test_records_go_to_the_configured_database` —— 记录确实落在配置的库里（3 条，标题三方校验）。
2. `test_records_do_not_fall_back_to_the_profile_default` —— **负向控制**：同时把 `HOME` 指向 scratch 目录，于是"profile 默认值"解析到该 scratch 目录**内部**；若脚本忽略 `MEMORY_DB_PATH`，记录就会出现在那里并触发断言，而真实台账**不可能**被碰到。
3. `test_db_flag_outranks_the_env_override` —— `--db` 优先于环境变量。

### 2.4 验收（含负向控制）

- 负向控制：把 `hub_lite.py` 那行改回 `db_path or resolve_db_path("shared", None)`，用例 2 **立刻红**，且失败输出里的路径是 pytest 的 `tmp_path`（证明它安全）。
- 恢复修复后：`tests/unit/test_hub_lite_record_script.py` 10 passed；`shared` 库运行前后均为 **6081 行**（修复前同样操作会 +18）。

---

## 3. P0-2 影子库 4 天重新膨胀到 194.8 MiB（机制已修，生产回收待批）

### 3.1 实测：VACUUM 之后又长回来了

10-10 批准后执行 `compact --vacuum`，文件 166.6 MiB → **69.7 MiB**（回收 97.0 MiB，见 `docs/design/retention-policy.md` §6）。四天后（10-14）同一份库是 **194.8 MiB**，而记录只从 2432 条长到 2457 条（+25）。按异机备份台账的体积序列（MiB）：

```
10-07 57.2 | 10-10 04:34 156.7 | 10-10 23:18 166.6 | 10-11 04:30 112.9
10-12 173.2 | 10-13 194.7 | 10-14 194.8
```

VACUUM 之后 13 小时（10-11 04:30）就已经回到 112.9 MiB —— **增长是立即恢复的，不是缓慢漂移**。

### 3.2 副本实验：可回收 124.3 MiB，且大头不在 freelist

在该机 `/tmp` 上对**快照副本**（`sqlite3` backup API，未触碰生产库）执行：

| 步骤 | 文件 | freelist | `records_fts` + `cjk_bigrams`（dbstat） |
| --- | --- | --- | --- |
| 现状 | 194.8 MiB | 32.0 MiB | **136.2 MiB** |
| `records_fts` `'optimize'`（0.3 s） | 194.8 MiB | 121.8 MiB | **46.4 MiB** |
| 随后 `VACUUM`（0.2 s） | **70.5 MiB** | 0.0 MiB | 44.1 MiB |

**结论**：`freelist_bytes` 只显示了 32 MiB，而 `optimize` 一项就再把 **89.8 MiB** 从"活着的死段"变成可回收页；`compact --vacuum` 合计可回收 **124.3 MiB**。这就是 10-10 报告里"实际回收比预测多 38 MiB"的同一机制，只是当时被当成一次性偏差。

### 3.3 根因（在代码里）

- `index_record()` 对每条记录做 `DELETE FROM records_fts WHERE rowid=?` + `INSERT`；`reindex()` 更是**先删空 `records_fts` 再逐条重插**。
- FTS5 把删除的 term 保留为墓碑，**只有段合并（`optimize`）才真正释放**。
- 而 `optimize` 当时只存在于**手工** `shadow compact` 里 —— `reindex()` 自己制造垃圾却不清理。两次全量 reindex（10-10 部署、10-13 部署）≈ 两代陈旧索引 ≈ 观测到的 89.8 MiB。

### 3.4 修法

1. **`reindex()` 末尾自己 `optimize`**（同一个事务内）：谁制造垃圾谁负责合并。刻意**不加到 `index_record()`**：它每次同步写都会跑，逐记录合并整个索引是 O(index)/record。
2. **新增 `fts_index_bytes()` 并接进 `status.storage`**：`freelist_bytes` 结构上**看不见**这类浪费（死段在被合并前算"已用页"）。此前唯一的观测手段是在主机上跑 `dbstat` —— 只存在于操作者 shell 里的仪器等于没有仪器。该字段取 FTS5 shadow 表的块长度之和（`dbstat` 是编译期选项，不可依赖），文档中明确它是**索引载荷**口径、不是页账，只能与自身纵向比较。
3. 文档：`docs/design/retention-policy.md` 补"生产者自清理"这一层，把层 0 从"一次性动作"改成"由生产者负责 + 可选节奏"。

### 3.5 测试（含负向控制，数字都是实测）

| 用例 | 钉住什么 | 负向控制（去掉 `optimize` 调用） |
| --- | --- | --- |
| `test_reindex_merges_the_segments_it_invalidates` | `reindex` 发出合并指令，且合并后索引文档数/检索/CJK 大词表不变 | **红**（trace 里没有 optimize） |
| `test_repeated_rebuilds_do_not_grow_the_live_index` | 连续 4 次重建，活页数不增长：修复后 71/71/71/71；修复前 72/71/87/87（`automerge=0` 时为 72/87/99/112） | **红**（87 > 72+4） |
| `test_status_reports_index_bloat_that_the_freelist_hides` | 未合并的重建必须体现为索引变大，合并后回到同一量级（47932 → 47937 B，5 B 结构差，故用容差而非等号） | 依赖前两条，未单独跑 |

刻意**没有**写"字节数必须降多少"的单元断言：在夹具规模上 FTS5 的 automerge 本来就能兜住大部分，字节级断言只会测到噪声（实测仅差 3 页）。**字节效应在生产镜像上测**（§3.2），单元测试只钉机制。

### 3.6 生产侧执行结果（2026-10-14，用户批准后执行完毕）

| # | 动作 | 结果 |
| --- | --- | --- |
| 1 | 异机加密备份（回退点） | `shadow-20261013T234504Z.sqlite3.enc`，2457 条，`integrity ok` / `readback_ok true`，65.0 s |
| 2 | 部署当前树到 M3 | 发布 **`9934bc20a2cca2b86e43`**，等于当时的 HEAD 树摘要；四个既有 job 全部 `changed: true` 一次对齐；再跑一次同树部署为幂等（同一摘要、四个 job `changed: false`） |
| 3 | `compact --vacuum` | **`file 204,210,176 → 73,908,224 B`（194.8 → 70.5 MiB），`reclaimed_bytes = 130,301,952 = 124.3 MiB`**，freelist `28,844,032 → 0`。**与预测的 124.3 MiB 完全一致。** 新仪器直接显示了死段：`fts_index_bytes 112,099,675 → 21,092,183`（106.9 → 20.1 MiB） |
| 4 | 新增周更 `compact` job | `co.los.memory-shadow-compact`（`StartInterval=604800`，只 optimize、不带 `--vacuum`——VACUUM 仍按策略"被请求才做"）。已 `kickstart` **实证运行**，输出 `reclaimed_bytes: 0`（刚 VACUUM 过，幂等）。同时把 `compact.out.log` 加进 maintenance 的 `--log` 列表，否则新 job 会重造"无界台账"这类问题 |
| 5 | **发现并修掉 `--vacuum` 的半个承诺** | 上面的 124.3 MiB 只是**主库文件**的回收：VACUUM 把每一页都推过 WAL，WAL 会**保持与整库同量级的已分配空间**——实测 `shadow.sqlite3` 73.9 MiB 旁边躺着 **74.3 MiB 的 `-wal`**，只有显式 checkpoint 才释放。手工 `PRAGMA wal_checkpoint(TRUNCATE)` 归零后，把该步骤**写进 `compact()`**（`--vacuum` 路径末尾），使"回收空间"这条承诺在磁盘上成立 |
| 6 | 部署修正后的树 | 发布 **`b366080b3d4aa630ab19`**（五个 job 全部 `changed: true` 一次对齐），并**实测**：再跑一次 `compact --vacuum` 回收了 sync 重新分配的 1,388,544 B（1.32 MiB），结束后 **`-wal` = 0**；周更 job `kickstart` 仍正常（`reclaimed_bytes: 0`） |

**端到端磁盘效果**：`194.8 MiB 主库 + ~15 MiB WAL` → **`70.5 MiB 主库 + 0 WAL`**。

**执行后验证**：`PRAGMA integrity_check = ok`；`records 2457 / revisions 4249 / records_fts_docsize 2457 / cjk_bigrams 181100` **全部未变**；trigram（`applied_bytes` 7 命中）与 bigram（`记忆` 10 命中）两条路径正常；sync 轮次 `missing=0 / errors=[]`。

**自动对齐已覆盖新 job**：`deploy_shadow.py` 扫描 `co.los.memory-shadow*.plist`，第二次部署的输出里已列出 `co.los.memory-shadow-compact (changed: false)`——即它不会像 10-10 那样漂移成一个"手工对齐"的 job。

**仍未做**：`git push`（用户本轮未选）。

### 3.7 周期节奏的取舍

`reindex` 自合并之后，主要产生源已经消失（一次全量重建不再留下一代死段）。周更 `compact` 是**第二层保险**，覆盖单记录路径（`put` 的 DELETE+INSERT）在长期运行后可能积累的段碎片；`optimize` 实测 0.3 s，代价可忽略。**文件不会因此变小**——`optimize` 只把死页转入 freelist 供复用，文件收缩仍只在显式 `--vacuum` 时发生（策略要求如此）。

---

## 4. P0-3 `recall-probe` 假红：文档与 TODO 此前与事实脱钩

### 4.1 事实（2026-10-13 修复，此前只有提交信息，没有文档）

`com.echerlos.los-memory-shadow-alert` 曾长期每小时报 `high / primary_stale_projection`（"主库应答 control 探针 0.6667，但取不回近期内容"）。实际是**探针自身判据错**：

- 探针把 `nmem memories search <id>` 的命中集合里是否含该 id 当作"可检索"。
- 这对导入期 **UUID** 形态的 id 成立（实测 5/5 命中自身），对我们写入路径产生的 **`sha256:<hex>`** id **不成立**（实测 3/3 不命中自身）——检索返回 10 条文本相似结果，永不含自身。
- 这批记录在主库里**确实存在**（`GET /memories/<id>` = 200，`/memories?limit=6` 也列得出来）。探针测的是"我的查询方法能否命中"，不是"主库能否取回"。
- 这也解释了边界为何"恰好"停在很早的日期：那是 UUID 记录与 sha256 记录的分界，**不是主库的故障时点**。

修复后（`4cc702f`，判据改为按 id 取回 `nmem memories show <id> -j`；非 "not found" 的失败走 error 通道并回退检索，"判不了"不读成"丢了"）：

| 字段 | 修复前 | 修复后（现网实测） |
| --- | --- | --- |
| `verdict` | `stale_projection_suspected` | **`ok`** |
| `recent_rate` | 0.0 | **1.0**（6/6） |
| `control_rate` | 0.667 | 1.0（3/3） |
| `newest_retrievable_created_at` | 2026-07-03 | 2026-10-13T19:00:51 |
| 告警 | 1 条 high，`status=down` | `alerts: []`，`status=up` |

### 4.2 影响面（本轮一并收口）

修复只存在于提交信息里，而 6 份文档仍在讲旧结论：`TODO.md:45`、`docs/current/CURRENT_STATE.md`、`docs/design/memory-roadmap.md`、`docs/design/p2-write-path-minimal-loop.md`、`docs/manuals/SHADOW_MEMORY.md`、`docs/reports/2026-10-10-*.md`（后两者是历史报告，保留原样并加时间限定；前四者改为现口径）。

特别是 `TODO.md` 的 **P1「主库检索覆盖部分且不可预测」**：它的两根支柱之一是探针判定，而那根已被证伪。**保留的**是独立的人工单点对照（同一记录按 id 可读、按标题辨识短语查不到；10-10T09:45 的记录稳定排第 1）——那说明的是**语义检索对查询形态敏感**，不是"主库丢了近期内容"。该条已按此重新基线化。

---

## 5. P1-4 影子 MCP 未接入本会话使用的 profile（阶段 1 卡死的真实原因）

- 影子 MCP 只配置在 **`web`** profile（`~/.dsh/profiles/web/cordis.patch.yml:206`），而本会话 `DSH_PROFILE=desktop`，desktop 的 patch 里 **0 处** shadow 配置（唯一 MCP 是 `mcp-los`）。
- **服务端是健康的**：本轮手工跑 stdio 握手，`serverInfo = los-memory-shadow v0.1.0`，`tools = shadow_search / shadow_get / shadow_status / shadow_compare`。
- 10-10 的 `maxAttempts: 1000000` 加固有效：10-10 起共 11 次 shadow 重连事件，最深到 `attempt 6/1000000` 即恢复，**无一次走到耗尽注销**。
- 后果：`AGENTS.md` 的"literal anchor 先用 shadow"在本会话**结构上无法执行**。这也解释了对照台账为什么自 10-07 起**只有 1 条**——不是 agent 不配合，是工具不在面上。阶段 1 的门槛（每查询形态 ≥30 条真实查询）在接线修好前不可能达成。
- 那唯一 1 条样本本身信息量不小：class `short_cjk`，`jaccard 0.0`、`shadow_only 3`、`nowledge_only 5`；延迟 shadow 19.15 ms vs primary 12.69 s。

---

## 6. 其余观察（本轮未处置）

1. **`main` 领先 `origin/main` 一个提交**：`main=4cc702f`，`origin/main=06a72d0`。10-13 的修复既没有异地副本，也没跑过 CI（CI 的 `on: push` 只覆盖 `main`）。按 `AGENTS.md` 的单分支纪律应当推送。
2. **备份 RPO 余量只剩约 78 秒**：最新台账 `rpo_seconds 86322.2`（23.98 h）对 ≤24 h 门槛。备份 job 跑在会睡眠的 M1 上，漏跑一次立刻破门槛（`alerts.jsonl` 已记录过 M1 睡眠期漏跑）。
3. **发布摘要粒度偏粗**：`deploy_shadow.py` 用整个 `memory_tool/**` 的树摘要做发布名。今天无漂移（四个 job 同源且等于 HEAD），但只要核心侧改一个影子到不了的文件，就会出现"因无关原因报不一致"的指纹。方向未变：同时记录影子相关文件的**传递闭包摘要**。
4. **M1 状态目录里有一个空的 `shadow.sqlite3`**（98 KB，`records` 0 行，10-10/11 遗留）。谁在 M1 本地跑影子命令都会得到"镜像为空"的假象。建议要么删、要么改名标注为遗留。
5. **`shared` 库的内容构成**（`~/.local/share/llm-memory/memory.db`，6081 行）：按项目/标题启发式分类，**98.6% 属于脚本或夹具族**——`test-isolation` 612、`test-fallback` 306、重复夹具标题（`User prefers dark mode` 等各 142）、`lsclaw` 2816、hub-lite trace 1591、`[los-memory] Implementation*` 1551；仅约 86 行像会话式记忆。sessions / checkpoints / links 全 0，feedback 17 条全是 supplement；`codex` profile 7 行、`claude` **0 行**。其中一部分是跨仓验收的**有意产物**，因此"算不算垃圾"是策略问题，本轮只量化、不删除。

---

## 7. 复现命令与验收

```bash
# 影子侧现状（在 M3 上，用现网 release 目录）
ssh m3-t 'cd ~/.local/share/los-memory-shadow/releases/c6d42b7de04664f8d907 && \
          ~/.local/bin/python3 -m memory_tool.shadow status'      # 含 storage.fts_index_bytes
ssh m3-t 'cd ~/.local/share/los-memory-shadow/releases/c6d42b7de04664f8d907 && \
          ~/.local/bin/python3 -m memory_tool.shadow recall-probe' # verdict 应为 ok

# 影子 MCP 握手（证明服务端健康；本会话的 desktop profile 没有它）
ssh -T m3-t '~/.local/share/los-memory-shadow/serve'   # 喂 initialize + tools/list

# 本仓验收
PYTHONDONTWRITEBYTECODE=1 pytest -q
python3 scripts/check_docs_cli_commands.py
ruff check --select F,B memory_tool/hub_lite.py scripts/create_hub_lite_records.py \
  tests/unit/test_hub_lite_record_script.py
```

验收判据（本轮）：

- 全量 `pytest` 通过，且 **`shared` 库行数在运行前后不变**（这是 P0-1 的判据，不是"测试绿了"）。
- `tests/unit/test_shadow_compact.py` 在**去掉 `reindex` 里的 `optimize` 后必须红**（已实测红），恢复后绿。
- `status.storage.fts_index_bytes` 在未合并重建后变大（已实测 ~2 倍）。

---

## 8. 待用户决定

1. ~~**M3 部署 + 回收**~~ —— **已执行（2026-10-14，用户批准）**：发布 `9934bc20a2cca2b86e43`，`compact --vacuum` 回收 **124.3 MiB**（194.8 → 70.5 MiB，与预测一致），并新增周更 `co.los.memory-shadow-compact`。详见 §3.6。文件收缩仍只在显式 `--vacuum` 时发生。
2. **推送 `main`**：本轮新增的提交（`5922033` 测试泄漏修复、`015a358` 存储修复、以及本报告所在提交）连同 10-13 的 `4cc702f` 一起待推——**均未推送，也未跑过 CI**。本轮用户未选，保持现状。
3. **影子 MCP 接线**：是否把该 MCP 加入 `desktop` profile（本 GUI 会话用的 profile），以解锁读取路径轮换阶段 1 的样本累积；还是明确"影子只在 `web` profile 可用"并把 `AGENTS.md` 的策略改成按 profile 限定。**仍待决定。**
4. **`shared` 库内容策略**：98.6% 是脚本/夹具族，是否清理、隔离或原样保留（本轮只量化未删）。**仍待决定。**
5. **备份 RPO 余量**：最新一次为 11615 s（3.2 h，因为本轮补做了一次备份），但**日常节奏下仍是 23.98 h 对 24 h 门槛、余量约 78 秒**；是否调整备份时刻/频率以拉开距离，或接受并写明。**仍待决定。**
