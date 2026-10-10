# 项目现状审计与待办地图（2026-10-10）

状态：**只读审计**。本轮不写现网、不部署、不改 launchd、不写主库。所有结论标注了取证方式与置信度；推断与实测分开写。

基准坐标：`main` = `origin/main` = `feature/nowledge-readiness` = `2260788`，工作树 0 未提交；交付 tag `shadow-readiness-2026-10-07` = `ac6664b`。

为什么写这份：14 天观察窗口在 **2026-10-10 13:14** 到期，是必须做一次全链路实测的时点。实测发现的问题不在"影子跑得好不好"，而在**代码 / 现网 / 门禁三者已脱钩**，以及**几个仪器造好了但没人看**。

---

## 1. 影子侧运行健康度（实测，全部只读）

| 指标 | 实测值 | 判据 |
| --- | --- | --- |
| 观察窗口 | 2026-09-26T13:14:02 → 2026-10-10T22:13:03（14.37 天） | 门槛 1 |
| 成功 / 失败轮数 | 4015 / 6 | — |
| 到位率（对 300 s 理想轮数） | **97.0%** | — |
| 中位间隔 / 最大间隔 | 307.1 s / 28.2 min | — |
| 累计 checked / changed | 399,156 / 884 | — |
| 记录 total / active | 2419 / 2301 | — |
| manifest_count vs active | 2301 = 2301 | 10-07 的 "+8" 偏差已消失 |
| 最旧验证时间 | 2026-10-10T20:07:18（≈2.1 h） | ≤24 h ✅ |
| 检索索引 | ready（indexed 2419） | — |
| 24 h 计量 | 275 轮 / 32,493 请求 / 1.75 GB，errors=0 | — |
| 异机备份 | 最近 17.7 h 前，回读 True | RPO ≤24 h ✅ |
| 告警 | `alerts: []` | 7 条门槛全部满足 |
| 客户端握手 | ok，tools = shadow_search/get/status/compare | — |

采集方式：M3 `sync.out.log`、M3 影子库 `shadow status`、`scripts/shadow_report.py report`、本机备份台账、launchd 状态。

**结论**：影子本体在门槛内。下面 §2–§7 是真正的待办。

---

## 2. P0-2　CI 在主分支上长期为红

**实测**：`gh run list` 连续 8 次 `failure`，最早回到 2026-03-13。最近 3 次（10-08）失败点固定在 `unit-and-integration (3.11/3.12)` 的 **Run unit tests**：CI 报 `4 failed / 648 passed`。

**复现**（排除 Python 版本因素——用仓库同一 venv，Python 3.12.9）：

```
纯净检出（git archive HEAD）：3 failed / 649 passed
FAILED tests/unit/test_hub_lite_record_script.py::TestHubLiteRecordScript::test_script_runs_successfully
FAILED tests/unit/test_hub_lite_record_script.py::TestHubLiteRecordScriptArtifacts::test_log_file_created
FAILED tests/unit/test_hub_lite_record_script.py::TestHubLiteRecordScriptArtifacts::test_log_file_contains_all_record_types
```

**根因（定位到行）**：`scripts/create_hub_lite_records.py:67` 把 `logs` 列入 `required_dirs = ["memory_tool", "tests", "docs", "logs"]`，而 `logs/` 在 `.gitignore` 里。全新检出没有 `logs/` → 检查 FAIL → `acceptance_state = "BLOCKED"` → 脚本返回非零；测试同时断言 `returncode == 0` 与 `"SUCCESS: All records created successfully."`。

**本地"全绿"是假象**：工作区里累积了 214 个未跟踪的 `logs/` 产物，所以本地首次运行就已通过该检查。CI 是干净检出，因此永远失败。

**影响**：这是仓库唯一的机械质量门。它红了数月，意味着 §4/§6 这类"改完靠 CI 兜底"的假设事实上不成立。

**修法（两选一）**：把 `logs/` 当输出目录（脚本自己 `mkdir(parents=True, exist_ok=True)`，从 `required_dirs` 移除），或让测试自建夹具目录。推荐前者——`logs/` 是产物不是前置条件。

---

## 3. P0-3　M3 现网发布落后仓库 11 个提交（344 行 shadow.py）—— **同日已修复**

> **处置结果（2026-10-10，采集之后）**：现网已升到 `9cf8fbc8755ad65173ec` = 提交 `03c15ab` 的 `memory_tool` 树摘要；四个 launchd job 与 `serve` 同源。升级后首个 sync 轮次报 `tombstones_deferred: 118`、`missing: 0`（升级前一轮 `missing: 2`、无该字段），灌水机制停止。步骤与验收见 `docs/reports/2026-10-10-shadow-14day-formal.md` §16.2。以下为修复前的取证记录。
>
> **脚本缺口已修（提交 `b2b9009`）**：`deploy_shadow.py` 现在部署时扫描 `co.los.memory-shadow*.plist`，只重写各 job 自己的 `WorkingDirectory` 并重新 bootstrap，输出报告哪些被改。此前它只重写 `co.los.memory-shadow.plist` 与 `serve`，另外两个 job 必须手工对齐——"四个 job 指向三个发布"就是这么来的。

**实测映射**：`scripts/deploy_shadow.py` 用 `memory_tool/**/*.py` 排序后 `(路径 \0 内容)` 的 SHA-256 前 20 位作为发布名。

| 对象 | 摘要 |
| --- | --- |
| M3 现网发布（sync / compare-drain / serve 三处 `WorkingDirectory` 一致） | `7aaac3fcd55fffe2a143` |
| 该摘要对应的提交 | **`545f0e7`**（10-07） |
| 仓库 HEAD 树摘要 | `3448c702dcd8892283da` |
| 差距 | **11 个提交**，`memory_tool/shadow.py` 344 行变更 |

**在现网发布里逐项 grep 确认缺失**（不是推断）：

| 缺失能力 | 现网后果 |
| --- | --- |
| `TOMBSTONE_RECHECK_SECONDS`（`e9c1111`） | 已停用记录仍**每轮重探**，而非 24 h 一次 |
| `recall_probe`（`fe31391`） | 主库召回探测器**不在现网**，无法调度 |
| compare 台账落盘路径修正（`e9c1111`） | 台账仍解析进程默认目录 |
| `manifest_cache_seconds` | 已在现网（该开关早于 `545f0e7`） |

**后果已在报告里显现**：日 `missing` 计数 0（至 10-06）→ 33（10-07）→ 129 → **440（10-09）→ 1229（10-10）**。这正是 `e9c1111` 提交说明写的"每轮重探停用记录会灌水 `missing`"的现象：修复在 git 里，没在跑。当前 inactive 已积 118 条（2419 − 2301）。

**附带不一致**：`co.los.memory-shadow-maintenance`（rotatelog）仍指向更旧的发布 `b7fb36d5b326a9c0ace5`，与其余三个 job 不同源。

**处理**：用 `scripts/deploy_shadow.py --host m3-t` 部署当前树 + reindex；或显式决定继续钉在 `545f0e7`（则报告必须标注 `missing` 为灌水口径）。属现网状态变更，需用户确认。

---

### 3.1 发布摘要的粒度问题（2026-10-10 核验时发现，已处置）

`deploy_shadow.py` 的发布名是**整个 `memory_tool` 包**的内容摘要。这比"正在运行的影子是不是我审过的那份代码"这个问题**更粗**：任何核心侧改动（检索、CLI、知识库）都会翻转摘要，即使影子根本到不了那些文件。

实测例：本轮把 `--semantic` 改成有界重排器（只动 `memory_tool/operations.py`）后，现网发布 `80da24bde45b1711ed8b` 与 HEAD 树摘要 `49c60c318b40fc2fbca1` 不一致。逐文件核对显示**只差 `operations.py` 一个文件**（`cdc4f126…` vs `9572041f…`），而 `shadow.py` / `shadow_mcp.py` / `shadow_registry.py` 的依赖闭包只有 `.shadow_registry` 与 `.utils`，**不含 `operations.py`** —— 即对影子行为无影响。

处置：重新部署恢复精确对齐（四个 job 由脚本自动对齐）。记下这一点是因为它是个**信任仪器**问题：一个经常因无关原因报不一致的指纹，会被读的人学会忽略，这与本轮刻意避免的告警噪声是同一类失败。若要收窄，正确方向是同时记录**影子相关文件的传递闭包摘要**（`shadow*.py` + `utils.py`），让"对齐"回答的是"能在这里跑的那部分代码是否一致"。

## 4. P1-4　主库检索：挂起已止，但没有回填

### 4.1 主库当前状态（实测）

| 项 | 值 |
| --- | --- |
| server 版本 | `v0.10.86`（与冻结期一致，**未升级**） |
| `/health` | 200 |
| `/search-index/status` | `available: true`，model `Qwen3-Embedding-0.6B Q4_K_M` |
| `/search-index/reindex/status` | `active: false`、`run_id: null`、`errors: []` |
| `nmem stats` | memories 2301（= 影子 active，口径一致） |
| 单次检索延迟 | ≈12.8 s（与既有 ~13 s 测量一致） |

12.78 天的 Lance `ReplaceFresh` 挂起已在 **2026-10-07 15:42** 用 systemd drop-in `NMEM_BOOT_AUTO_REINDEX=0` 打断（该处置本身有记录，见主库 observation `794f96ec…`、`cf92c514…`）。**打断 ≠ 重建**：`active: false` 说明没有任何重建在跑。

### 4.2 对照探针（只读，主库侧每次 ≈13 s）

| 目标记录创建时间 | 按 ID 读 | 主库检索 top-10 |
| --- | --- | --- |
| 2026-10-10T09:45（`applied_bytes` 游标） | ✅ | ✅ 排名 1，score 0.99 |
| 2026-10-10T14:03（当日最新） | ✅ | ⚠️ 未出现 |
| 2026-10-05 / 10-06（稀有词 `launcherExecutePlan`） | ✅ | ❌ 未出现 |
| 2026-10-09T23:08 | ✅ | ❌ 未出现 |

**诚实口径**：主库检索是 semantic+BM25 混合，**top-10 缺席是强提示，不等于索引缺席**。反向对照也做过——同一个 10-10 记录用裸词 `applied_bytes` 查也进不了 top-10，说明召回对查询形态敏感。

能确定的只有两点：(a) 主库已恢复收录新写入（10-10 的记录能排到第 1）；(b) 10-05～10-09 的锚点即使拿稀有词也捞不出来。**冻结期窗口很可能仍是空洞，且没有任何回填计划。**

### 4.3 为什么这条没被现有告警发现

`recall-probe` 正是为此而建（`fe31391`，手册见 `docs/manuals/SHADOW_MEMORY.md`），但：

1. 它不在当时那条 M3 现网发布里（见 §3）；
2. 它没有装成任何定时任务。

**盲区可检测，但没人看。** 已知代价：每个探针花主库 ≈13 s，所以只能做定时任务，不能进读路径。

### 4.4 探测器上线后的定论（2026-10-10，采集之后）

现网升级后 `recall-probe` 可用，已手动跑一次并 kickstart 定时任务一次，**两次结论一致**：

| 字段 | 值 |
| --- | --- |
| `verdict` | **`stale_projection_suspected`** |
| `recent_rate` | **0.00**（5 个探针全灭） |
| `control_rate` | 0.667（3 个最老探针，2 中） |
| `errors` | `[]` |

即：**主库在应答，但取不回近期内容**——而它全程报 `Search Index: available`。独立交叉验证也做了：2026-10-10T14:25 的一条记录按 ID 可读、用其标题辨识短语查不到；同一天 09:45 的另一条却能稳定排到第 1（复跑 2/2）。

**因此 §4.2 的"疑似空洞"升级为"覆盖部分且不可预测"**（不是整齐的"某日之后全丢"，probe 的 `newest_retrievable_created_at=2026-07-03T07:44:25Z` 只是被探集合的边界）。probe 自身 `reading` 警告 anchor 可能失真、单次运行不构成证明——本轮有两次一致运行加独立单点验证，但仍应把"边界与趋势"交给调度出的时间序列。

已装成 M3 job `co.los.memory-shadow-recall-probe`（每 6 h）。**仍未做的是"被看见"**：没有告警阈值读它的 verdict。

---

## 5. P1-5　DSH 影子 MCP 反复掉线并会静默注销工具

**配置**（`~/.dsh/profiles/web/cordis.patch.yml:192`）：stdio over `ssh -T ... m3-t ~/.local/share/los-memory-shadow/serve`，`failOnStartupError: false`，`toolCallTimeoutMs: 30000`。插件 `dsh-mcp-los-memory-shadow` 已安装。

**日志实测**（`~/.dsh/logs/dsh-web.log`）：

- 每天 **21–22 条** shadow 事件，全部是 reconnect 类的；
- **2026-10-08 08:13**、**2026-10-09 08:17** 两次走到
  `10/10 consecutive failed reconnect attempts — tools unregistered`；
- 2026-10-10 21:45 在 **attempt 9/10** 才重连成功——离再次注销只差一次；
- 掉线时刻与同机 `lark-channel` 断连同秒；M1 `pmset` 日志显示该时段反复 Sleep/DarkWake（21:16、21:31、21:44、21:45）。

**根因（判为宿主侧）**：宿主网络/睡眠抖动 + `ConnectTimeout=5` + **10 次上限后不再重试**。M3 侧可排除——M3 已 caffeinate、uptime 12 天、期间 sync 0 缺口。

**本会话直接证据**：本次全新 DSH 会话的工具面上**没有** `shadow_*`。即 `TODO.md` 中 W-06 的残留项（"DSH 新会话内的真实工具调用"）**未关闭**，而且失败是静默的（`failOnStartupError: false`）。

**影响**：这是 §6 采纳证据与 §7 无关，直接卡住"读取路径轮换"的证据累积。

---

## 6. P1-6　读取路径轮换阶段 1 样本量仍为 1

| 项 | 实测 |
| --- | --- |
| `compare-results.jsonl` | **1 条**（与 10-07 相同，3 天未增） |
| `compare-pending.jsonl` | 0 |
| drain job（M3，900 s） | 每轮输出 `pending: 0, resolved: 0` |

阶段 2 的门槛是"每种查询形态 ≥30 条真实查询"。在 §5 未修的前提下这个门槛**不可能自然达成**——顺序上必须先修 MCP 掉线。

---

## 7. P1-7　告警的结构性盲区与投递缺口

1. **没有投递通道**：告警只写 `alerts.jsonl` 并 `exit 1`，无处通知（既有 TODO，一直未做）。
2. **睡眠导致漏跑**：M1 告警 job `runs=76`、最后 `exit 0`，但最后一次落盘是 **18:09**，本次盘点时已 22:14 —— 漏 4 次。M1 在该时段反复进睡，launchd `StartInterval` 睡眠期间不触发。
3. **结构上发现不了 §4**：现有 7 条门槛都不覆盖"索引报告 ready 但内容检索不到"。

---

## 8. P2-8　等用户决策（不是未完成的活）

| # | 事项 | 出处 |
| --- | --- | --- |
| 1 | P2 写入闭环 5 问：试点空间 / 身份签发方式 / 409 裁决入口 / P2 与 P0-P1 排期 / 是否上 HTTP | `docs/design/p2-write-path-minimal-loop.md` §7 |
| 2 | P1 同步周期切换：`--manifest-cache-seconds` 已实现，24 h 基线现已可用（275 轮 / 1.75 GB / 0 错）；TODO 建议清单周期取 30 分钟 | `TODO.md` |
| 3 | 停用记录与 `revisions` 的保留策略（inactive 已积 118 条） | `TODO.md` |
| 4 | 分支/tag 模型：`main` 事实上已推进到 `2260788`，而交付文档仍写"origin/main 未推进" | `docs/reports/2026-10-07-delivery.md` |

**注（§8.4）**：`docs/reports/2026-10-07-delivery.md` §1/§3 现在有两处过期陈述——运行发布已从 `7b5e77bf…` 变为 `7aaac3fc…`（对应 `545f0e7`，非 tag 所指提交），`origin/main` 已推进。该文件刻意"不写死 SHA"，但结论句仍需加时间戳限定。

---

## 9. P3-9　代码层待办（可择机）

- `--semantic` 改成有界重排器（当前 946 ms / 5,355 条全扫）；**不要先提维度**（32→256 只换 +0.033 Hit@1、4–6 倍开销）。
- 字面包含是否应压过 FTS token 匹配：n=80 采样为**混合、在噪声内**，需更大冻结集再动。
- 保留 legacy flat-command 兼容，直到下游分组命令迁移吸收完。
- ~~2 条 shadow 测试的 sqlite `ResourceWarning`~~ —— **已修（`b2b9009`）**：真实来源是 `tests/unit/test_search_like_escaping.py` 的 7 个用例各自 `make_conn()` 且从不关闭；回溯栈指向 `memory_tool/shadow.py` 只是因为 GC 恰好在那个生成器里跑。改成带 teardown 的 fixture 后，全套 791 passed / 0 warnings。
- ~~`co.los.memory-shadow-maintenance` 的发布指向与其余 job 对齐~~ —— **已修**（四个 job 同源，且 `deploy_shadow.py` 以后会维持它）。
- `tests/integration/test_hub_lite_integration.py` 有 2 个用例断言的是 gitignore 掉的 `logs/`、`control-plane/logs/` 产物 —— **已改为缺产物时 `pytest.skip` 并说明来源**（`b2b9009`）；模拟干净检出从 4–5 failed 变为 789 passed / 2 skipped / 0 failed。

---

## 10. 建议处理顺序

1. **出 14 天正式报告**（窗口今天闭；生成器实测可用，数据已齐）——纯文档，零风险。
2. **修 CI 的 `logs/` 前置条件**（§2）——唯一机械门，且已红数月。
3. **决定并处理 M3 发布落后**（§3）——或部署当前树（顺带让 `missing` 口径回归真实、让 recall-probe 可用），或显式钉住并把报告口径改成灌水值。
4. **把仪器接上电源**：recall-probe 装成 M3 定时任务（建议 6–12 h 一次，避开 300 s 同步节奏）+ 给告警加投递通道（§4、§7）。
5. **修 DSH 影子 MCP 掉线**（§5）——否则 §6 的采纳证据永远不会累积。
6. **回答 §8 的决策项**，才能启动写入闭环实现。
7. 收尾 §9。

---

## 11. 本轮边界

只读。未写文件（除本文与 TODO/CURRENT_STATE 对齐）、未部署 M3、未改 launchd、未写主库。复现用的临时检出目录已清理。主库侧全部为只读查询（`nmem memories search` / `show` / `stats` / `/search-index/*`）。
