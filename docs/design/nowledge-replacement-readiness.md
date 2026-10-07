# los-memory 替代 Nowledge：进展复核、缺口与收敛设计

日期：2026-10-07。状态：**复核 + 设计**，本轮只读采集，未修改现网配置、未改同步周期、未提交代码、未接触主库写入。
范围：以 2026-09-26 批复的双轨方案为设计基线，复核"los-memory 在未来替代 Nowledge"这一目标的**实际执行进度**，用近期运行日志与现场实测补齐证据，给出缺口清单与收敛设计。

配套文档：[双轨方案与迁移门槛](dual-track-memory.md) · [分阶段路线图](memory-roadmap.md) · [总体架构](memory-service-architecture.md) · [检索处理](memory-retrieval-pipeline.md) · [影子运行手册](../manuals/SHADOW_MEMORY.md) · [2026-09-26 部署记录](../reports/2026-09-26-dual-track.md) · [资源评估](../reports/2026-09-26-resource-assessment.md)。

> 本文所有"实测"结论都可用第 10 节命令复现；凡未经实测的推断均标注为"推断"。本文取代不了 `docs/current/CURRENT_STATE.md` 的当前行为口径。

---

## 0. 结论摘要

一句话：**影子这条腿已经跑起来了，替代这条路还没有真正开始。**

| 维度 | 2026-09-26 批复时的预期 | 2026-10-07 实测 | 判定 |
| --- | --- | --- | --- |
| 只读镜像可用性 | 双轨验证 | M3 连续运行 10.95 天，3,073 轮，0 错误 | ✅ 超预期 |
| 镜像覆盖 | 1,655 条 | 2,048 / 2,048（default space durable） | ✅ |
| 每条新鲜度 | 未承诺 | 最旧验证 103 分钟，旋转周期 102 分钟 | ✅（远好于 24h 门槛） |
| 同步流量 | 估计 1.33 GiB/日 | 实测 1.53 GiB/日、34,848 请求/日 | ⚠️ 未优化，P1 未启动 |
| 检索能力对等 | 字面基线 | 无 FTS（全表扫描）；中文改写查询 0 命中 | 🔴 结构性缺口 |
| project 维度 | 按 `metadata.project` 过滤 | **0 / 2,048 条带 project**，过滤参数恒空 | 🔴 文档与实现不符 |
| 写入路径 | 门槛 5 要求 | 完全不存在（明确只读） | 🔴 未启动 |
| 线程 / 实体 / 图 / Working Memory | 门槛 5 要求 | 0 条镜像（主库有 349 线程 / 2,911 实体 / 2,353 标签 / 25 社区） | 🔴 未启动 |
| 客户端接入 | Kimi/Codex/Grok | Codex ✅ Kimi ✅ **Grok 已掉线** DSH ❌ | ⚠️ 回落 |
| 真实使用 | 用于比较检索 | DSH 会话索引里 `los-memory-shadow` 仅 3 次命中（全来自一次只读调研），`shadow_search` 真实调用 0 次；同期 `nowledge` 767 次 | 🔴 零采纳 |
| P0 评测基线 | 40 例 | 无 case manifest、无 harness、无基线快照 | 🔴 未启动 |
| 异机备份 / 恢复 | RPO≤24h / RTO≤1h | 仅 M3 同机 2 份副本，均为 09-26，已 11 天未刷新 | 🔴 未启动 |
| 代码交付 | — | HEAD = origin = `0ab9f9c`，实现全在工作树（27 项未提交） | ⚠️ 不可复现 |

**必须纠正的一条认知**：当前 los-memory **不能当记忆库用**。M1 上 `los-memory` 不在 PATH、`pip show los-memory` 无包、`~/.local/share/los-memory-shadow/` 只有 `client-backups/`。真正活着的只有 M3 上那个只读影子 MCP。它是一个**导入适配器 + 对照订阅源**，不是可读写的记忆服务。把它当"记忆库在用"会带来事实只写进 Nowledge、影子侧不可见写入的认知风险（见 §6 风险 R3）。

---

## 1. 判定口径：把"替代"拆成可验收的能力层

现有文档把"替代"挂成 6 条门槛，但没有定义**替代到什么程度**。本文引入 L0–L4 分层，避免"影子能搜 = 可以替代"的滑坡。

| 层 | 名称 | 能力 | 当前状态 |
| --- | --- | --- | --- |
| **L0** | 只读镜像 | 单一 space 的 durable 记录镜像 + 只读 get/search/status | ✅ 已达成（本文件 §2） |
| **L1** | 检索对等 | 词项 + 中文 + 精确键路径；在冻结评测集上不劣于 Nowledge 基线 | 🔴 未达成（无 FTS、无评测） |
| **L2** | 可写闭环 | 认证身份、事件、修订、幂等、冲突、撤回、outbox、来源证据 | 🔴 未达成（零实现） |
| **L3** | 全能力 | 混合检索、时间关系图、Working Memory/handoff、会话/线程索引 | 🔴 未达成 |
| **L4** | 主库切换 | 单一主写入口、cutover epoch、旧库只读回退窗口 ≥14 天 | 🔴 未启动 |

升级判据（每层独立验收，不允许跨层借力）：
- L0→L1：40 例评测集上字面/中文类不劣于冻结基线，且**真实工具调用 > 0**（当前为 0）。
- L1→L2：隔离库上通过身份负向测试与幂等/冲突/撤回契约；**不产生第二个主库**。
- L2→L3：留出集上语义/多跳类净增益，资源达标。
- L3→L4：路线图 P6 全部生产候选门槛 + 用户确认切换窗口。

---

## 2. 设计方案 vs 实际执行：达成面

### 2.1 已达成且经实测确认

| 项 | 证据 |
| --- | --- |
| M3 影子常驻 | `launchctl` job `co.los.memory-shadow`，`runs = 2423`，`last exit code = 0`，`StartInterval = 300` |
| 连续运行 | `sync.out.log` 3,073 轮，2026-09-26 13:14 → 2026-10-07 11:57，跨度 10.95 天，**0 轮带错误**，`sync.err.log` 0 字节 |
| 覆盖 | `total = 2048, active = 2048, unhydrated = 0, manifest_count = 2048` |
| 新鲜度 | 记录验证年龄 min/median/max = 0.6 / 51.9 / 103.2 分钟；**>2h 为 0 条，>24h 为 0 条** |
| 旋转语义 | 2,048 ÷ 100 条/轮 × 5 分钟 = 102 分钟，与 max 103.2 分钟一致 → 确实是**旋转刷新**，不是全库 5 分钟新鲜 |
| 幂等与去重 | `changed` 累计 415（含 394 条新增 + 少量修订）；`revisions` 3,737 且按 `(space, source_id, digest)` 主键去重；展示字段 `time` 已从 digest 排除 |
| 只读 MCP 活体可用 | 2026-10-07 12:04 现场经 SSH 完成 `initialize` → `tools/list`（3 工具）→ `shadow_status` → `shadow_search`，全链路成功 |
| 单元/契约测试 | `pytest tests/unit/test_shadow.py tests/cli/test_search_consistency.py -q` → **7 passed**；全仓 `--collect-only` = 682 项 |
| 同步锁 | `fcntl.flock` 非阻塞独占，手工同步与定时任务互斥 |

结论：**L0 只读镜像这一层，可以判定为达标**，且 14 天可用性观察已完成 10.95/14 天。

### 2.2 与设计文档不符的地方（需修正文档或修实现）

这几条不是"没做"，而是**文档承诺的能力在实现里根本不存在**，属于契约级偏差，优先级高于新功能。

| # | 文档怎么说 | 实际是什么 | 影响 |
| --- | --- | --- | --- |
| D1 | `SHADOW_MEMORY.md`：`shadow_search(query, limit, project?)`，"项目来自源记录 `metadata.project`，没有则为 `unassigned`" | 2,048 条记录中 **`metadata.project` 出现 0 次**，顶层也无 `project` | `project=` 过滤**恒返回空**（除字面 `"unassigned"`）。调用方会把"过滤后为空"误读成"这个项目没有记忆"。这是当前最容易被误用的接口陷阱 |
| D2 | 设计按 `kind` 分类记忆 | 源 schema 用 **`unit_type`**：`fact 429 / event 416 / context 413 / learning 306 / decision 200 / procedure 168 / plan 110 / preference 6` | 架构文档 §5、路线图里的 `kind` 是**外来词**，落地时必须定义映射，否则新库与影子维度对不上 |
| D3 | 「`claim_status` 区分 proposed/asserted」 | **1,350 / 2,048（66%）为 null**；有值者 `asserted 582 / proposed 48 / planned 34 / explored 29 / unverified 5` | 多数记录没有声明状态。默认当成 `asserted` 会违反"不得因模型反复提及升级为断言"的设计原则 |
| D4 | 来源用 `source_app` 枚举（kimi-code/codex/grok） | 顶层 `source` 是**自由文本证据描述**（820 条 `deepseek-harness`、742 条 `agent`、118 条 `codex`，另有 "Codex local migration run 2026-09-22"、"docs/plan/…" 等长句）；可靠来源维度在 `metadata.source_app`（1,644 条）与 `metadata.thread_source`（742 条） | 以顶层 `source` 做来源鉴权/隔离会失效 |
| D5 | 迁移门槛 3 隐含"备份恢复已具备" | M3 `backups/` 只有 2 个文件，均 2026-09-26（`restore-probe-final.sqlite3` 20.3 MB、`verified-1790399734.sqlite3` 13.6 MB），**11 天未刷新，且全在同机** | 同机副本不构成灾难恢复；RPO/RTO 从未计时 |
| D6 | 「观察 14 天」 | 已 10.95 天，按期达成点为 **2026-10-10 13:14**（还剩 3.05 天） | 门槛 1 差 3 天 + 未出 14 天报告 + 无告警 |
| D7 | 手册称影子已接入 Grok | `~/.grok/config.toml` **0 servers**；`grok mcp list` 为空；Grok 从 `~/.claude.json` 继承 5 个 server（context7 / codebase-memory-mcp / exa / nowledge-mem / pencil），**没有 los-memory-shadow** | Grok 这条腿已掉线，违背门槛 4 的"三客户端"要求 |
| D8 | — | `cordis.patch.yml` 只有 `nowledge-mem` / `nowledge-mem-mcp`，DSH 里没有任何 los-memory 接入 | DSH 是本机主用 harness，未接入意味着影子没有进入日常闭环（见 §4 采纳证据） |

### 2.3 影子库自身的结构限制（可复现）

```
tables : records, revisions, state, attempts
indexes: 除主键隐式索引外，无任何额外索引；无 FTS 表
```

- `shadow.py:search()` 是 `SELECT * FROM records WHERE space=? AND active=1` 全表拉取 + Python `casefold()` 子串扫描，**25.1 ms（p50）/ 25.6 ms（p95）** 于 2,048 条；随正文规模线性劣化。
- 查询语义为"空格分词后**全部** term 都要命中"。因此 `"单人维护项目的交付流程"` 这种整串中文查询 **0 命中**，而按 title/body 子串的 `"los-memory"` 有 3 命中。
- `attempts` 主键是 `(space, source_id)` 且用 `INSERT OR REPLACE` → **只保留每个 ID 最近一次尝试**，`status` 里的 `unresolved_errors` 实为"最近一次失败的记录数"，不是可持续审计的错误台账。
- 404 记录 `active=0` 后永久留在 `records`，**没有 tombstone 保留期与清理策略**。

---

## 3. 运行证据：近期日志与执行情况分析

### 3.1 调度与可用性（`sync.out.log`，3,073 轮）

| 指标 | 实测 |
| --- | --- |
| 观察跨度 | 10.95 天 |
| 实际轮数 / 理想轮数（300 s） | 3,073 / 3,152（缺 79 轮，97.5% 到位） |
| 中位间隔 | 306.9 s |
| > 7 分钟缺口 | 7 次：1 次在 2026-09-26 13:14（首轮 8.7 min）、5 次集中在 09-26 17:36–18:06（每次 ≈7.2 min），1 次 2026-09-28 20:53（**28.2 min**） |
| 最大单次缺口 | 28.2 min（推断为休眠/重启，非失败） |
| 带错误轮数 | **0** |
| 日志体积 | 514 KB / 10.95 天 ≈ 46 KB/日（约 16 MB/年），**无轮转上限** |

28.2 分钟的缺口说明"launchd 在登录用户 domain、`RunAtLoad=false`"这一形态在休眠/重启后的恢复语义确实有洞，但仍在 24h 门槛内。

### 3.2 覆盖率与变更速率

- manifest 从 1,654 条 → 2,048 条（+394，约 36 条/日），累计 `changed=415`，即**修订约 21 次/11 天**。
- 最后一次内容变更为 **2026-10-06 22:44**（`revisions.max(received_at)`），此后全为 `changed: 0`。
- `revisions = 3,737` 与 `records = 2,048` 的差额主要来自 09-26 的初始基线灌入与当时的一次 digest 膨胀（`time` 字段），不是用户事实变更了两倍——**不能拿该差额推算修订率**。

### 3.3 同步流量（2026-10-07 现场实测）

| 项 | 实测 |
| --- | --- |
| 清单请求数 | 21 次（2,048 条 ÷ 100/页） |
| 清单响应字节 | 5,431,447 B = **5.18 MiB** |
| 单条平均正文 | 2,652 B |
| 清单墙钟 | 3.79 s |
| 单轮成本 | 21 + 100（按 ID 刷新）= **121 请求 / ≈5.43 MiB** |
| 每日成本（288 轮） | **34,848 请求 / ≈1.53 GiB** |

对比 09-26 报告的"4.67 MB / 33,696 请求 / 1.33 GiB"，规模增长后成本同比例上升。**清单 API 返回完整正文而非仅 ID**，这是最大的一处浪费；A-1（每轮再按 ID 拉 100 条正文）才是有效载荷。

### 3.4 检索能力对照（现场探测）

| 查询 | Nowledge（主库） | 影子（只读 MCP） |
| --- | --- | --- |
| `los-memory` | 命中 | **3 命中**（25 ms） |
| `记忆` | 命中 | 3 命中 |
| `单人维护项目的交付流程` | 3 条带分数结果（0.56 / 0.48 / 0.44） | **0 命中** |
| `记忆库替代 nowledge 的迁移门槛` | 3 条带分数结果（0.91 / 0.91 / 0.46） | **0 命中** |

两个重要观察：

1. **影子无法覆盖改写类问题**，这是 L1 的硬缺口，也是 P3 向量要解决的问题——但 P3 之前还有 P0/P1/P2。
2. **不能假定 Nowledge 是强基线**。上表第一条改写查询的 top-3 里，"良之隆数字化一期"与"Cross-project agent loop"在语义上都偏离意图，只有 0.44–0.56 的弱分。所以路线图里"不劣于 Nowledge 基线"必须写成"不劣于**冻结快照**的 Nowledge 排名"，并且要先承认基线本身可能不高——否则会用"打平一个弱基线"冒充替代就绪。

### 3.5 采纳证据（最关键的执行事实）

用 DSH 会话索引（1,021 个会话）统计：

| 关键词 | 命中事件数 |
| --- | --- |
| `nowledge` | **767** |
| `los-memory-shadow` | **3** |
| `shadow_search` | 1 |
| `shadow_status` | 1 |

那 3 次命中全部来自 **2026-10-07 11:47–11:49 一次只读调研会话**（调研员被派去"搞清楚 los-memory 是什么形态"），不是任何一次真实检索任务。换句话说：

> 影子跑了 10.95 天、3,073 轮、0 错误，但**没有任何一次真实用户检索走过它**。

这条事实比任何功能缺口都重要：当前双轨的"对照"价值尚未被使用验证，迁移门槛 4（"在 Kimi/Codex/Grok 新会话分别完成真实工具检索"）**证据为零**。

### 3.6 主库与镜像的能力差距（`nmem stats`）

| 对象 | 主库 Nowledge | 影子 los-memory | 覆盖 |
| --- | --- | --- | --- |
| durable memories | 2,048 | 2,048 | **100%** |
| threads | 349 | 0 | 0% |
| entities | 2,911 | 0 | 0% |
| labels | 2,353 | 仅作为快照字段，未建投影 | 0%（不可检索） |
| communities | 25 | 0 | 0% |
| Working Memory | 有 | 无 | 0% |
| 写入 | 有 | 无（明确只读） | 0% |
| 服务端 | v0.10.86 / cli v0.10.91 | — | — |

影子替代的是主库能力面的**约 1/6**（只覆盖 durable 记录文本），而门槛 5 要求的写入、来源证据、导出恢复、Working Memory、会话索引**一项都没有**。

---

## 4. 缺口清单

按"是否阻塞迁移叙事"排序。每条给证据与关闭条件。

| ID | 缺口 | 证据 | 影响 | 关闭条件 |
| --- | --- | --- | --- | --- |
| **G1** | P0 评测基线完全缺失 | 仓内无 case manifest / fixture / golden 文件（按 `*case*`/`*eval*`/`*golden*`/`*fixture*` 检索只命中 `memory-retrieval-pipeline.md`，因其文件名含 `retrieval` 子串，非评测资产） | 后面所有"提升/不退化"都无法判定；这是路线图 §2 的停止条件 | 40 例私有 case + 双后端 harness + 冻结的 Nowledge 基线快照入库（脱敏） |
| **G2** | 检索能力不对等 | 影子无 FTS、无索引、全表扫描；两条中文改写查询 0 命中 | L1 不成立；中文是主用例，当前只能查"记得原文片段"的题 | FTS5 + 中文双字/分词路径 + 结构列投影；40 例上不劣于基线 |
| **G3** | `project` 维度名存实亡 | `metadata.project` 命中 0/2,048；工具 schema 仍暴露 `project` 参数 | 过滤后空结果被误读为"该项目无记忆"；跨项目隔离叙事无法验证 | 定义 project 投影（§5.3）并重建；或**从工具 schema 删除该参数**直到投影可用 |
| **G4** | 零写入路径 | `shadow.py` 无任何写源 API 的调用；MCP `INSTRUCTIONS` 明示只读 | 门槛 5 结构性未启动；"记忆库在用"的认知无法落地 | 隔离 `service.sqlite3` 上的身份/事件/幂等/冲突/撤回最小闭环（P2） |
| **G5** | 线程/实体/图/Working Memory 全空 | `nmem stats` vs 影子表结构 | 无法承接"找回未完成事项/决策演变/跨会话 handoff" | P3–P5；会话索引可先借 DSH `session-index` 试点（§5.6） |
| **G6** | 客户端接入回落 | Grok 0 server；DSH 未接；Claude 未纳入 | 门槛 4 的三客户端要求当前只有 2 个，且无真实调用 | Grok 重新注册 + DSH 接入 + 各端一次真实检索留证 |
| **G7** | 零采纳 | §3.5：`los-memory-shadow` 3 次命中全来自调研，`nowledge` 767 次 | 双轨的"对照"假设未被验证；无法回答"影子结果是否够用" | 每客户端 ≥1 次真实任务检索 + 记录是否改变结论 |
| **G8** | 异机备份与恢复未验证 | 仅同机 2 份 09-26 副本；无加密、无异地、无 RPO/RTO 计时 | 门槛 3 未过；单机损坏即 10 天数据与全部 revision 丢失 | 每日加密异机备份（RPO≤24h）+ 恢复演练计时（RTO≤1h）+ 恢复后重放擦除清单 |
| **G9** | 14 天窗口与报告未闭环 | 已 10.95/14 天（达成点 2026-10-10 13:14）；无 14 天报告；无告警；日志无轮转 | 门槛 1 差 3 天 + 可观察性不足 | 生成 14 天报告（§9 口径）+ 连续失败告警 + 日志轮转上限 |
| **G10** | 观测语义不精确 | `attempts` 只存最近一次；`unresolved_errors` 名不副实；无 24h 滚动流量计量 | P1 要求"先补 24 小时实际流量/刷新/错误计数"，当前测不了 | 在 `state` 表落每轮 bytes/requests/duration + 24h 滚动聚合 |
| **G11** | 交付不可复现 | HEAD = origin = `0ab9f9c`；27 项工作树改动未提交；M3 release `0c1fcfd44e6f01326ce2` 由工作树内容摘要生成 | 无 release→commit 映射，不可审计；任何机器重建都得不到同一份代码 | 提交到分支 + tag + 在 reports 记录 release↔commit↔部署时间 |
| **G12** | 单点形态风险 | job 属登录用户 launchd，`RunAtLoad=false`；已观测 28.2 min 缺口 | 休眠/重启/登出语义未验证；无人值守可用性无证据 | 评估 `LaunchDaemon` 或独立常驻服务 + 断网/重启恢复演练 |

---

## 5. 目标架构收敛设计

原则：**不推翻现有实现，把影子重新定位为"导入适配器 + 评测夹具"，并补齐它与 canonical 之间的契约**。新增能力一律先旁路、后替换。

### 5.1 定位修正（本轮最重要的设计决定）

```
现状定位（隐含）：  los-memory ≈ Nowledge 的替代品，正在验证
修正定位（建议）：  los-memory shadow = canonical 读取适配器 + 评测基线夹具
                    los-memory service = 未来的规范记忆服务（尚未实现）
```

`shadow.py` 保持只读、保持 `(space, source_id, digest)` 契约不变，**不改成双向写入口**（与既有设计一致）。差别在于：不再把影子当成"准记忆库"宣传，而是当成 P0 评测的**被测后端之一**。

### 5.2 数据契约修正（W-00，必须先于 P0）

在写评测用例之前先修数据契约，否则 40 例会建立在错误的字段假设上。

| 规范字段 | 来源（优先级） | 缺省语义 |
| --- | --- | --- |
| `kind` | `unit_type` 直读（8 值）+ 保留原值 | 未知 → `unknown`，不映射成 `fact` |
| `claim_status` | 顶层 `claim_status` | null → `undeclared`，**不得默认 asserted** |
| `project` | ① 注册过的 `label_ids`（`memory_tool/shadow_registry.py` 显式白名单）② 记录显式声明的 `metadata.project` | 都无 → `unassigned`；**不从线程 ID、宿主 app 或目录/路径推断**（`source_thread.id` 只编码宿主 app，实测不含项目） |
| `source_app` | `metadata.source_app` 优先，回退 `metadata.thread_source` | 顶层 `source` 是证据描述文本，**不作为 app 枚举** |
| `created_at` | 顶层 `created_at` | `time`（相对展示字段）已排除出 digest，继续保持 |
| `space` | `space_id`，当前仅 `default` | 其他 space 需独立授权与隔离 |

原始方案把 `label_<project>` 当成可推断的形态；实测 2,341 个 label 里项目名与主题名（`label_architecture`、`label_verification`、`label_daily`…）混在同一扁平命名空间，无法自动区分。因此改为**显式注册表白名单 + 显式声明兜底**，未注册一律 `unassigned`，并把实际覆盖率当作一等输出（`status.contract.project_coverage`）。实测 576 / 2,048 = 28.1%。

同时修正文档：`SHADOW_MEMORY.md` 的 `project?` 说明、`memory-service-architecture.md` §5 的 `kind` 词表、`memory-roadmap.md` 中依赖 `kind` 的表述。

### 5.3 检索投影设计（L1）

目标：**用最小改动把全表扫描换成有索引的词项 + 中文路径**，并在 40 例上可复现地不劣于冻结基线。**本轮不引入向量**（P3 再做）。

影子库新增结构列与投影（不改 `records/revisions` 语义，只加派生表，可整表重建）：

```
records_ext(space, source_id, kind, source_app, thread_source, project,
            created_at, digest, verified_at, active,
            PRIMARY KEY(space, source_id))
labels(space, source_id, label)                      -- 来自 label_ids
shadow_fts(title, content, source_id UNINDEXED, space UNINDEXED)  -- FTS5, unicode61
cjk_bigrams(term, space, source_id)                  -- 中文双字辅助索引
```

检索路径（全部先按 `space` + 授权范围约束）：
1. **精确键**：ID、`label_*`、commit、错误码 → 普通索引 / 等值。
2. **词项**：英文/代码 token → FTS5 BM25，title 与 label 命中加权。
3. **中文**：≥3 字符走 trigram；**双字/单字走 `cjk_bigrams`**，不从 FTS5 硬凑。
4. **超预算**：返回 `truncated` / `degraded` + 建议缩小范围，**不能返回空冒充"没有记忆"**。

配套约束：
- 分词器/词典变化 → 新 generation，可整表重建，不影响 canonical 快照。
- 检索结果携带 `digest` 与 `verified_at`；digest 与 canonical 不一致时丢弃或重取（现有 `get` 已按 digest 校验语义）。
- 明确不再拿当前 25 ms / 2,048 条外推：迁移到 14 万条消息规模前必须先有索引，否则线性劣化。

### 5.4 评测夹具设计（P0）

- **私有语料**放本机私有目录，**不入 Git**；仓库只放脱敏 case schema 与统计。
- case 字段：`query, principal/scope, as_of, expected_ids+revision, forbidden_ids, support_sources, relevance(0/1/2)`。
- 8 类互斥主标签 × 5 例 = 40：精确标识、中文短词、语义改写、项目隔离、事实修订/历史、关系多跳、跨工具续接、无答案/撤回。
- harness 必须**同时打 Nowledge 与影子**，并把 Nowledge 的排名**冻结成快照**作为基线（因为基线本身可能弱，见 §3.4）。
- 40 例只用于**发现故障**；P3 前扩到 120 例（开发/留出各 60，同项目家族不跨集）。
- 门槛：期望 ID 可规范读取；越权/过期正文冒充/无证据批准为 0；当前失败案例可复现。

### 5.5 同步流量设计（P1）

在 §3.3 实测基线上分层降本：

| 方案 | 请求/日 | 字节/日 | 旋转 | 备注 |
| --- | --- | --- | --- | --- |
| 现状（300 s，batch=100，清单含全文） | 34,848 | ≈1.53 GiB | 102 min | 实测 |
| **无增量接口时**：清单 1 次/小时 + 刷新 100 条/15 min | 504 + 9,600 = **10,104**（−71%） | ≈**148 MiB**（−90%） | **5.1 h** | 仍满足 ≤24h 门槛 |
| 有增量接口时 | cursor 变更流 + 周期全量对账 | 远低于上表 | 事件驱动 | **需先探测**，不得假设 |

先做 **W-04 源 API 增量能力探测**（`updated_since` / cursor / ETag / 轻量 manifest）；无论结果如何，都要补：退避、时钟/睡眠恢复、同步锁（已有）、失败公平轮转（已有）、源实例识别、tombstone 传播、状态与告警、备份/日志保留上限。

### 5.6 会话/线程层的可借力量（P5 前置）

门槛 5 要求"会话索引"，而 DSH 侧已存在一个相邻资产：`~/.dsh/storages/session-index.db`（1,021 会话 / 事件 EAV + FTS5 投影，另有 `run-diff` 事件级 A/B 工具）。建议：

- 不在 los-memory 内重造会话索引，而是把 DSH session-index 作为**线程证据来源**之一，通过 `(source_system, source_instance, source_id, source_revision)` 映射接入（与总体架构 §2 的映射规则一致）。
- Nowledge 侧 `threads = 349`、`source_thread` 仅覆盖 742/2,048 条记录，说明**线程维度本身在主库里也是稀疏的**——接入前先测覆盖率，避免把稀疏当完整。
- 该接入属 P5 前置探索，**本轮只做覆盖率探测，不做实现**。

### 5.7 客户端接入矩阵（门槛 4）

| 客户端 | 现状 | 动作 |
| --- | --- | --- |
| Codex | ✅ `mcp_servers.los-memory-shadow` 经 `ssh m3-t …/serve` | 保持；补一次真实检索留证 |
| Kimi | ✅ `~/.kimi-code/mcp.json` 含 nowledge-mem + shadow | 保持；补一次真实检索留证 |
| Grok | ❌ 0 server（继承 5 个，无 shadow） | 重新注册；核实继承优先级，避免被 `~/.claude.json` 覆盖 |
| DSH | ❌ `cordis.patch.yml` 仅 nowledge-mem | **最高采纳价值**：接入后影子才进入日常闭环；接入属 profile patch，需遵循 DSH 插件接线规范 |
| Claude | ⚪ 未纳入 | 按需纳入，不默认扩面 |

### 5.8 备份与恢复（门槛 3）

- 保留现有 M3 同机 SQLite backup API 副本作为**快速回档**。
- 新增：**每日加密异机备份**（独立主机或对象存储）+ 保留期上限 + 备份清单含 `(space, source_id, digest, active)` 全表摘要。
- 恢复演练计时：从零恢复规范快照 + 检索可达，**RTO ≤ 1 h**；备份新鲜度 **RPO ≤ 24 h**。
- 恢复后**先重放擦除清单**再开放读取，防止被删记忆复活。
- 面板需暴露：最近成功备份时间、字节数、摘要是否一致。

### 5.9 可观测性与告警（P1 门槛）

- `state` 表每轮落 `{requests, bytes, duration, checked, changed, missing, errors}`，做 24h 滚动聚合。
- 连续 N 轮失败 / 最旧验证时间超阈 / manifest 骤降 → 告警（IM 或现有告警通道）。
- 日志轮转上限（当前无上限，46 KB/日）。
- `unresolved_errors` 改名或补一张**有界错误台账**（保留最近 K 次失败），避免"最近一次"冒充"未解决"。

### 5.10 交付与发布

- 把工作树实现提交到分支并打 tag；在 `docs/reports/` 记录 `release digest ↔ commit ↔ 部署时间`。
- M3 release 目录继续内容寻址，但必须能反查 commit。
- 重部署会替换 launchd 配置、新连接用新发布（既有语义），文档已说明，保持。

---

## 6. 风险

| ID | 风险 | 触发条件 | 缓解 |
| --- | --- | --- | --- |
| R1 | **单点不可用** | M3 休眠/登出/重启；已观测 28.2 min 缺口 | 恢复演练 + 评估 LaunchDaemon/常驻服务；门槛 1 前不得声称"无人值守可用" |
| R2 | **静默 schema 漂移** | Nowledge 改 `unit_type` / `source_app` / `metadata` 键 | 加 schema 探针：关键字段覆盖率下降即告警（当前 `metadata.project` 缺失就是一次未被发现的漂移） |
| R3 | **认知风险：把影子当记忆库** | 用户/agent 以为写入会进 los-memory | 工具描述与手册明示只读；写入仍走 Nowledge；MCP `INSTRUCTIONS` 已含此意，需在客户端可见处复述 |
| R4 | **拿弱基线冒充替代就绪** | 用"打平 Nowledge"论证可切换 | 基线冻结成快照 + 报告基线绝对值 + 关键错误单列，不用平均分稀释 |
| R5 | **评测集污染** | 用例与调参共用同一集合 | 40 例只发现故障；P3 前扩 120 例并拆开发/留出 |
| R6 | **历史 revision 膨胀** | 09-26 已有一次 digest 膨胀先例 | revision 保留期与压缩策略；按事实修订率而非总量推算增长 |

---

## 7. 判据修正建议（对现有文档）

| # | 现有表述 | 建议 |
| --- | --- | --- |
| 1 | `shadow_search(query, limit, project?)`，"项目来自 `metadata.project`" | 投影落地前**从工具 schema 删除** `project`；否则手册必须写明"当前恒返回空"。二者必选其一 |
| 2 | 门槛 2 "Top-5 命中率不低于 Nowledge 基线" | 改为"不低于**冻结快照**的 Nowledge 排名"，并要求报告基线绝对值 |
| 3 | "每 5 分钟更新"叙事 | 统一为"旋转周期 + 最旧验证时间"口径（当前 102 min / 103 min），不承诺全库 5 分钟 |
| 4 | 门槛 4 "三客户端完成真实检索" | 补一条可观测判据：**真实工具调用次数 > 0** 且留证（当前为 0） |
| 5 | 门槛 3 "备份恢复" | 明确"同机副本不算"，必须异机 + 计时 |
| 6 | 路线图 P1 "先补 24 小时实际流量" | 把 09-26 的 1.33 GiB 估计更新为实测 1.53 GiB / 34,848 请求，并要求 W-10 的计量先落地 |

---

## 8. 工作包与顺序

顺序原则：**先修契约与测量，再谈能力与切换**。W-00/W-01/W-10 可并行启动，W-02 是 W-01 后半段的依赖。

| 包 | 内容 | 依赖 | 交付 / 验收证据 | 对应 |
| --- | --- | --- | --- | --- |
| **W-00** | 数据契约修正：`unit_type→kind`、project 投影、`claim_status` 缺省、source_app 取值、文档同步 | — | §5.2 表格落地；`SHADOW_MEMORY.md` 不再承诺不存在的 `project` | D1–D4 |
| **W-01** | P0 评测：40 例私有 case + 双后端 harness + Nowledge 基线冻结 | W-00 | `docs/reports/` 出脱敏统计；失败案例可复现 | P0-01/02, G1 |
| **W-02** | 检索投影：结构列 + FTS5 + 中文双字索引 + 降级语义 | W-00 | 40 例字面/中文不劣于基线；p95 与扫描量入报告 | G2, §5.3 |
| **W-03** | 调度与流量实验：清单/刷新拆分 + 24h 计量 | W-10 | 24h 实测字节下降 ≥70%、请求下降 ≥50%；覆盖 100% | P1-03, G10 |
| **W-04** | 源 API 增量能力探测（updated_since/cursor/ETag/manifest） | — | 明确的"支持/不支持"结论 + 原始响应证据 | P1-02 |
| **W-05** | 异机加密备份 + 恢复演练 | — | RPO ≤24h、RTO ≤1h 计时记录 + 摘要一致 | P1-04, G8 |
| **W-06** | Grok 重新注册 + DSH 接入 + 各端真实检索留证 | — | 3+ 客户端各 ≥1 次真实调用记录 | 门槛 4, G6, G7 |
| **W-07** | 14 天报告 + 告警 + 日志轮转 | W-10 | 报告含每日失败/最旧验证/变化/容量；告警可触发 | 门槛 1, G9 |
| **W-08** | 提交/发布/映射 | — | 分支 + tag + release↔commit 记录 | G11 |
| **W-09** | P2 前置：身份/事件/写入最小闭环设计评审（隔离库） | W-01 | 评审通过的设计 + 负向测试清单；**不写现网** | 门槛 5, G4 |
| **W-10** | 同步观测计量（`state` 落盘 + 24h 聚合 + 错误台账） | — | 可回答"过去 24h 流量/失败/最旧验证" | P1-01, G10 |
| **W-11** | 会话/线程覆盖率探测（借 DSH session-index） | — | 覆盖率与映射可行性结论；不做实现 | P5 前置, G5 |

**关键路径**：W-00 → W-01/W-02 → W-07（14 天窗口 2026-10-10 13:14 到期）→ 门槛 1/2 判定 → W-09 之后才谈 L2。

### 8.1 执行状态（滚动更新，最后一次 2026-10-07）

| 包 | 状态 | 证据 |
| --- | --- | --- |
| W-00 | ✅ 完成 | `record_facets`/`record_labels` + `shadow_registry.py`；`shadow_search` 诚实化（`{results, meta}` + `meta.coverage`）；`SHADOW_MEMORY.md`/`CURRENT_STATE.md` 已改；M3 实测 project 覆盖 576/2048 = 28.1%，`claim_undeclared` 1,350 |
| W-02 | ✅ 完成 | trigram `records_fts`（另一路先落地）+ `cjk_bigrams` 补 2 字符中文；`meta.mode/paths/scan_terms/usable`；索引未建时回退扫描并上报。一致性抽检 8 查询索引 vs 强制扫描 **0 处不一致** |
| W-10 | ✅ 完成 | `sync_runs` 计量 + `sync_errors` 有界台账；`status.metering` 给出滚动 24h 请求/字节/错误与预计日流量 |
| W-03 | 🟡 实现完成，现网未切 | `sync --manifest-cache-seconds N`（默认 0 = 现网不变）；单轮实测已测，24h 基线由新计量表自然累积，切换判据见 §5.5 |
| W-11 | ✅ 完成 | 见 [会话/线程覆盖率探测](../reports/2026-10-07-thread-coverage.md) |
| W-04 | ✅ 完成 | 见 [源 API 增量能力探测](../reports/2026-10-07-source-api-increment.md) |
| W-05 | （见报告） | restic → syno + 恢复演练 |
| W-06 | （见报告） | Grok/DSH 接入 + 真实调用留证 |
| W-07 | 🟡 生成器就绪 | 14 天窗口 2026-10-10 13:14 到期，先出当前窗口报告 + 告警 + 日志轮转 |
| W-01 | （见报告） | 40 例 case + 双后端 harness + 冻结基线 |
| W-09 | （见设计） | P2 写入闭环设计评审 |
| W-08 | （见报告） | 分支 + tag + release↔commit 映射 |

---

## 9. 报告格式（沿用路线图 §9）

每份阶段报告必须含：git revision + dirty 摘要、schema/index/分词器版本、硬件与并发、数据集 hash/数量/时间、命令、冷热次数、成功/失败数、p50/p95、分类质量、退化路径、费用口径、结论与回退。

14 天报告额外固定项（当前缺）：每日失败数、**最旧成功验证时间**、来源变化与补齐、磁盘增量、客户端可达性、同步流量字节/请求、备份新鲜度与摘要一致性。

---

## 10. 附录：证据与复现命令

> 采集时间 2026-10-07 11:57–12:05 CST；全部只读。

```sh
# 调度与可用性
ssh m3-t 'launchctl print gui/$(id -u)/co.los.memory-shadow | grep -E "state|runs|last exit"'
ssh m3-t 'wc -l ~/.local/share/los-memory-shadow/sync.out.log'
ssh m3-t 'tail -5 ~/.local/share/los-memory-shadow/sync.out.log'
# 逐轮间隔/缺口/错误统计：读 sync.out.log 的 started_at 序列（见 §3.1 表格）

# 覆盖与新鲜度
ssh m3-t 'cd ~/.local/share/los-memory-shadow/releases/0c1fcfd44e6f01326ce2 && \
  ~/.local/bin/python3 -m memory_tool.shadow status'
# 结构 / 字段覆盖 / 索引
ssh m3-t '~/.local/bin/python3 -' <<'PY'
import sqlite3, json, collections
c=sqlite3.connect("/Users/echerlos/.local/share/los-memory-shadow/shadow.sqlite3")
print([r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")])
print([r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")])
u=collections.Counter(); p=0
for (s,) in c.execute("SELECT snapshot FROM records WHERE active=1"):
    r=json.loads(s); u[r.get("unit_type")]+=1
    if (r.get("metadata") or {}).get("project"): p+=1
print(u.most_common()); print("with project:", p)
PY

# 只读 MCP 活体
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"shadow_status","arguments":{}}}' \
| ssh m3-t '~/.local/share/los-memory-shadow/serve'

# 主库规模
nmem status; nmem stats
nmem memories search "单人维护项目的交付流程" --limit 3

# 客户端接入
codex mcp list | grep -i shadow
grok mcp doctor | sed -n '1,40p'
python3 -c "import json;print(list(json.load(open('$HOME/.kimi-code/mcp.json'))['mcpServers']))"

# 采纳证据（DSH 会话索引）
python3 - <<'PY'
import sqlite3
c=sqlite3.connect("/Users/echerlos/.dsh/storages/session-index.db")
for p in ("%los-memory-shadow%","%shadow_search%","%nowledge%"):
    print(p, c.execute("SELECT count(*) FROM events WHERE text LIKE ?",(p,)).fetchone()[0])
PY

# 交付状态
git -C ~/syncfolder/project/los-memory rev-parse HEAD origin/main
git -C ~/syncfolder/project/los-memory status --porcelain | wc -l
which los-memory || echo "NOT ON PATH"
```

---

## 11. 不在本轮范围

本文不执行：新接口实现、依赖安装、数据库迁移、批量历史导入、同步周期变更、主库切换、代码提交或推送、NAS34 部署、向量/图功能。以上均需按 §8 工作包另行授权。
