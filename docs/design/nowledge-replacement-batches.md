# 替代 Nowledge：分批判定与设计（2026-10-21）

状态：**设计稿**。本文件不实现、不建库、不改现网、不切写入口；它把"替代"拆成可逐批验收的批次 B0–B7，并补上此前没有独立批次的**总结**与**清理**两条轴。

依据：2026-10-21 只读实测（§1）、[替代就绪复核](nowledge-replacement-readiness.md) G1–G12、[路线图](memory-roadmap.md) P0–P6、[P2 写入闭环设计](p2-write-path-minimal-loop.md)（含 §7 五项决策）、[P2-01 契约层](p2-01-contract-layer.md)、[双轨判据](dual-track-memory.md)。

## 0. 效力与边界

**本文件取代**
- `docs/manuals/SHADOW_INVOCATION_POLICY.md` §6「不启动主库切换」→ 改为「**在 B6/B7 门槛达成前不切换**」；该文其余路由策略继续有效。
- 2026-10-19 `p2-write-path-readiness-2026-10-19.md` 把 `semantic_paraphrase = 0.000` 当作**数据面**阻断项的理由。实测表明该指标测不出区分度（§1.4），在 B0 重做该类别之前，它只能记为**度量缺陷**，不能用来 gate 写入运行时。

**本文件不取代**
- 不变量 I1–I7（`p2-write-path-minimal-loop.md` §1）。
- 路线图 P0–P6 的阶段语义与指标定义（§9）。批次与阶段是两层：阶段说"要做到什么"，批次说"这一轮交付什么、怎么验收"。
- Nowledge 仍是**唯一正式写入目标**，直到 B7 完成并记录 cutover epoch。

## 1. 事实基线（2026-10-21 10:30–11:00 实测）

| 轴 | 实测 | 判据 |
| --- | --- | --- |
| 记录（导入） | 镜像 `total 2803 / active 2670`，索引 `ready 2803`；24h `271 轮 / 34,149 请求 / 1.998 GB / 0 错`，单轮 8 s | 搬运这一层没有缺口 |
| 记录（写入） | `shadow_mcp` 四工具全只读；P2-02 运行时未开工；自身库 `codex 7 / claude 0 / shared 6081` 行（夹具族为主） | **不是记录入口** |
| 记录（实际链路） | `~/.dsh/memories` → `mem-sync-evolve.py` → Nowledge：最近 `entries 2338 / created 12 / skipped 2326 / failed 0` | 日常写入在 Nowledge |
| 记录（契约） | `project` 683/2680 = **25.49%**，**ceiling 45.65%**（B1 后带原因：policy_excluded 579 / key_label_unmapped 514 / unclassified 821 / no_labels 56 / multi 27）；`claim_undeclared` **1564**（含`claim_source` 三态，0 冲突）；引用透传 thread 1160 / grounding 1096 | 语义可归因，但覆盖率受上游标签限制 |
| 召回 | 影子按字面：5 条改写/取词查询全部 rank 1；Nowledge 语义召回能返回相关记录但**排序不按输出的分数**（见 §1.4） | 影子是字面匹配；主库排序弱 |
| 召回（语义来源） | `nmem status`：`embedding.mode=local-gguf`，`degraded=false` | Nowledge 语义是本地向量 |
| 总结 | 影子 0；本地只有单条正则 hook（`cli.py:1183` 才启用）；Nowledge Working Memory 有 09-19 起的逐日存档 | 本轴未开始 |
| 清理（空间） | 10-14 实测回收 124.3 MiB（194.8→70.5 MiB）+ WAL TRUNCATE 归零；今天 **206 MiB / freelist 96.4 MiB / FTS 载荷 47.1 MiB** | 空间层成熟，但按需 |
| 清理（语义） | 只有硬 `DELETE`（`operations.py:853/880`）；`project archive` 只是把 project 改名（`cli.py:1429`）；Nowledge 有 delete/archive/forget/deprecate/supersede/evolves，且有 4 条待处理 review | **层级不同** |
| 采纳 | `compare` 台账在 **M3**（`~/.local/share/los-memory-shadow/compare-results.jsonl`）只有 **1 条**（2026-10-07 的 "los-memory 双轨"；M1 侧那份 0 条只是路径不同，别读成"台账没建"）；影子 MCP 已于 2026-10-19 接入 `desktop`（本会话实测 `shadow_status` 可用） | 工具在位，但 `compare` 没被当作流程使用 |

### 1.4 一处必须纠正的判据（2026-10-21 已用仪器量清）

两条无字面重叠的改写查询（"记忆库替代 nowledge 的迁移门槛"、"两个记忆库谁说了算 避免双写"）在影子上 0 命中，在 Nowledge 上分别 5 条 / 3 条且标注 Semantic Match。因此 `semantic_paraphrase = 0.000` **不能读成"主库无语义能力"**。

B0 的仪器（`scripts/eval_human_check.py`）把它量成了三条可复核的事实：

| # | 事实 | 判据 |
| --- | --- | --- |
| 1 | 语料没有坏：40 条 case 的 **41 个 relevance≥2 期望记录全部可读回**（`memories show` 成功） | `--show-only` → `found 41 / record_missing 0` |
| 2 | 影子侧字面可用：5 条语义 case 的**自述锚点全部 rank 1**（`SIGKILL`、`真实运行陷阱`、`固定外部观察输入`、`已实现但未接线`、`reclaim worker`） | MCP `shadow_search` 逐条实测 |
| 3 | 主库侧：**3/5 的锚点在自己的 top-20 里找不到该记录**（`SIGKILL` / `真实运行陷阱` / `固定外部观察输入`）；另外 2 条 rank 1 | `nmem memories search <anchor> -n 20 -j` |
| 4 | 主库的排序**不按它自己输出的分数**：查询 c11 时期望记录分数 **0.6492（全场最高）却排第 18**，第 1 名只有 0.5348 | 同一响应里的 `score` 与位置对比 |
| 5 | 主库**没有相关度下限**：乱码锚点 `zzz-anchor-that-cannot-exist-20261021` 仍返回 5 条 | 负向控制 |
| 6 | **40 条留出集上的基线：影子 Hit@5 0.771 / Nowledge 0.200**（Recall@5 0.700 / 0.186，`forbidden hits@20` 0 / 1，两后端 0 错）；主库的 0.200 **不是因为记录不存在**（`memories show` 可读），而是带下划线的标识符与中文短词检不出来（`exact_identifier` 1/5、`chinese_short` 0/5，而拉丁短语 rank 1） | `shadow_eval.py --freeze` 冻结为 `baseline-20261021.json` |

结论：`0.000` 既不是"无语义能力"，也不是"语料没有区分度"，而是**在一个排序层本身很弱的主库上量 top-5**。这把尺子可以用，但必须同时报**绝对值**（recall@5 的分子分母）而不是只报相对基线的差值——否则 B2 会以"打赢一个自己都排不对的基线"结项。B0 的负向控制与判据已在 `docs/reports/2026-10-21-b0-ruler-and-baseline.md` 落表。

## 2. 替代的定义（可验收）

| # | 定义 | 验收句子 |
| --- | --- | --- |
| D1 | **写**：唯一主写入口，幂等/修订/冲突/撤回/审计齐备 | 同键重放同 receipt；同键异载荷 409；撤回后普通读阻断 |
| D2 | **读**：留出集不劣于冻结基线，语义类有区分度 | 语义类 ≥ +10pp 且精确/中文不退化；越权与过期 0 |
| D3 | **总结**：能生成可用的焦点面与交接面，且每句可回读 | handoff ≥90% 无需重复提供关键事实；结论无来源即降级 |
| D4 | **清理**：删除前有可逆层，删除后可收据 | archive 不进默认召回但可按 id 读；恢复后重放擦除清单不复活 |
| D5 | **运营**：单写者、可回退、可恢复 | RPO ≤24h / RTO ≤1h；回退窗口 ≥14 天 |

**非目标（明确不做）**：多主同时写；默认全量历史回灌；图优先（P4 只保留有实测收益的轻量边）；默认开启 LLM 改写 query；无人确认的自动淘汰。

### 2.1 替代最小集 vs 可选集

Nowledge 有 40+ 子命令，替代不等于逐个复刻。**必需**：写入与回执、修订/冲突/撤回、语义+字面混合召回、Working Memory/bundle、清理生命周期、备份恢复、审计与计量。**可选（切换清单里显式放弃或延后）**：图扩展与 wiki、communities、skills、library/collab/mailbox、多 space 协作、移动端同步。

## 3. 不变量（I1–I7 之外新增三条）

| # | 不变量 | 违反后果 |
| --- | --- | --- |
| **I8** | **每批必须先有尺子**：不能机械测量的批次不开工 | 10-19 的阻断项正是坏尺子；没有尺子的批次会拿"感觉更好"结项 |
| **I9** | **每批可单批回退**：删入口 / 回上一发布，且不改 Nowledge 唯一写目标（直到 B7） | 任一批失败不得连带冻结写路径 |
| **I10** | **四轴不借力**：读得更多不算总结；删得更快不算清理；导入覆盖率不算写入 | 否则切换时才发现缺的是语义能力 |

## 4. 批次 × 四轴 × 阶段

| 批次 | 主题 | 记录 | 召回 | 总结 | 清理 | 对应阶段 |
| --- | --- | --- | --- | --- | --- | --- |
| B0 | 尺子与接线 | — | ● | — | — | P0 |
| B1 | 记录契约（镜像侧） | ● | ○ | — | — | P0/W-00 |
| B2 | 语义召回（只读） | — | ● | — | — | P3 |
| B3 | 写入运行时（隔离库） | ● | ○ | — | ○ | P2-02/03 |
| B4 | 总结（WM/bundle/handoff） | — | ○ | ● | — | P5 |
| B5 | 语义清理（生命周期） | — | — | — | ● | P2/P4 |
| B6 | 读路径切换 | — | ● | ○ | — | P5/P6 |
| B7 | 写入口切换（cutover） | ● | ● | ● | ● | P6 |

● 主交付　○ 依赖/顺带

## 5. 批次定义

每批格式固定：目的 / 依赖 / 交付 / 机械验收 / 负向控制 / 回退 / 明确不做 / 规模。

### B0 尺子与接线（S）— **已完成 2026-10-21**

- **目的**：把"更好"变成可判定，并把工具接到实际会用的会话里。
- **依赖**：无。
- **交付（实际形态）**：
  1. **仪器**：`scripts/eval_human_check.py`（`--show-only` 可读性 / `--depth` 锚点探测 / `--negative-control` / `--self-test`）+ `tests/unit/test_eval_human_check.py`（7 例，离线）。
  2. **语料复核结论：不需要重做**。40 条 case 的 41 个期望记录全部可读回；3/5 的"锚点查不到"是主库排序的性质，不是语料缺陷（证据见 §1.4）。因此 B0 的产出从"重做类别"改为"给类别补两把判据"：锚点 rank 与主库分数/位置一致性。
  3. **冻结基线**：`shadow_eval.py --refresh-shadow --scope native --backend both --freeze` 产出快照 + 报告 `docs/reports/2026-10-21-b0-ruler-and-baseline.md`。
  4. **desktop 接线**：已于 2026-10-19 完成，本次**实测**（本会话 `shadow_status` / `shadow_search` / `shadow_compare` 全部可用）。文档里"影子 MCP 只在 web profile"的说法已纠正。
  5. **`compare` 台账开始积累**：起 1 条真实样本（c11 改写查询：影子 0 命中 / 主库 rank 18）。
- **机械验收**：
  ```bash
  python3 scripts/eval_human_check.py --self-test
  python3 scripts/eval_human_check.py --show-only                 # 期望：found 41 / record_missing 0
  python3 scripts/eval_human_check.py --category semantic_paraphrase --negative-control
  python3 -m pytest tests/unit/test_eval_human_check.py -q
  python3 scripts/shadow_eval.py --refresh-shadow --scope native --backend both --freeze --out <path>
  ```
  判据：`record_missing == 0`；8 类的两后端数字落表；语义类的**绝对值**（命中数/总数）同时列出；`compare` 台账出现第一条真实样本。
- **负向控制（已实测）**：伪造一个不存在的 expected id ⇒ `record_missing` + `rc=1`；乱码锚点 ⇒ 主库仍返回 5 条，证明"空结果"不能当判据（所以负向控制判的是"没有期望记录泄漏"，不是"没有结果"）。
- **回退**：删脚本、测试、报告与 `--freeze` 产物即可；无数据变更、无 MCP 变更。
- **明确不做**：不改检索实现、不改数据契约、不动同步周期；**不修主库排序**（那是 Nowledge 侧，修了就不是"在它自己的规则下比"）。
- **为什么排第一**：B2/B4/B5/B6 的验收全部依赖这把尺子；10-19 已经因为坏尺子误挡了一次。

### B1 记录契约（镜像侧，M）— **已完成 2026-10-21**

- **目的**：让镜像里的记录**具备可验收的语义字段**，否则 B2 的语义检索和 B5 的清理都建在残字段上。
- **依赖**：B0（已完成）。
- **交付（实际形态）**：
  1. **一个覆盖率定义**：`memory_tool.shadow.coverage` 是唯一实现，`scripts/shadow_eval.py` 改为调用它（此前两份算式给出 0.249 与 0.2089）。
  2. **原因词表**：`assigned` / `multi` / `policy_excluded` / `key_label_unmapped` / `unclassified` / `no_labels`，每个活跃记录恰一个，写进 `record_facets.reason`。
  3. **claim 来源规则**：record → metadata → `undeclared`，两处不一致记 `claim_source='conflict'`（不择一）；`claim_conflicts` 是一等输出。
  4. **引用透传**：`thread_id` + `has_grounding`（只存引用，不建图）。
  5. **审计**：`scripts/shadow_contract_audit.py`（三态 exit 0/1/2；`coverage-target` 默认只报告）。
- **机械验收（已实测）**：
  ```bash
  python3 -m pytest tests/unit/test_shadow_contract.py -q     # 9 例
  python3 scripts/shadow_contract_audit.py --strict            # 线上 OK / exit 0
  ```
  判据：原因直方图之和 == 活跃记录数、无 `unpopulated`、`claim_conflicts == 0`、注册表非空；`project_reason != {}`。
- **负向控制（已实测）**：临时清空注册表再 `reindex` ⇒ coverage 1.0→0.0 且审计报 `registry-non-empty` 硬失败；B1 之前的快照上审计 exit 2（"判不了"，而不是"覆盖率 0"）；正文提到项目目录但没有注册标签的记录必须留 `unassigned`。
- **门槛按数据改写**：`project ≥80%` 在当前上游标签下**不可达**（可命名上限 45.65%：日常写入不带项目标签，1,019 条 `label_memory-evolve`、525 条 `label_daily`）。B1 的门槛改为"原因齐全 + ceiling 已测 + 无冲突 + 投影不陈旧"；**80% 移到 B3**（写入侧必须带 `project`，新写入记录的覆盖率 ≥95%）。
- **回退**：`record_facets` 是派生表，`reindex` 可整表重建；`records` 语义未变。
- **明确不做**：不追平 Nowledge 的 threads/entities；不从路径/线程/正文推断项目；**不批量把长尾标签认领成主题**（那会让数字变好而不让能力变好）。

### B2 语义召回（只读）— **步1（模型选型）已完成 2026-10-21**

- **目的**：补上 FTS 没有的东西——同一件事的不同说法（改写、跨工具延续、关系跳跃）。这是唯一"光靠镜像侧做不到"的能力。
- **依赖**：B0（尺子）。
- **步1 实测（详见 `docs/reports/2026-10-21-b2-model-selection.md`）**：`multilingual-e5-small` int8 语义类 recall@5 **0.642**（`bge-small-zh-v1.5` 0.300、`MiniLM-multilingual` 0.425）；索引粒度**一条记录一条向量**（分块 chunk-max 把改写打到 **0.0**）；文本 **`title×2 + body[:1200]`**；不加 `passage:` 前缀（反降）。
- **参数变更**：RSS 触发线 **300 → 800 MiB**（e5 稳态 610–715 MiB，bge 只用 139 MiB 但语义低 27pp）；模型与运行时**都不进仓库**（118 MB 按 sha256 进 generation manifest）。
- **机械验收（改写门槛已按数据改写）**：
  - **先扩改写探针到 ≥20 例**（当前 5 例每格 20pp，**不可判** 0.8）；
  - 语义类 **MRR@10 ≥ 0.7**、**best-rank@20 ≥ 0.85**（当前 0.708 / 0.85–0.90）；
  - 改写 recall@5 **≥0.8 或 ≥FTS+10pp**（扩探针后判）；
  - **字面类走 FTS，不退化**；每条语义候选带 `canonical_revision` + `index_generation`；
  - **语义侧不可用必须报 `semantic_unavailable`**，不得静默返回"没有相关记录"。
- **负向控制（实施阶段必须全绿）**：过期 generation 的向量被丢弃；已删除/已撤回记录不得被语义路召回；语义服务不可用时结果不为空且带原因；融合不得让字面类 recall 下降。
- **回退**：语义路是**可关的旁路**（配置开关 + 独立进程），关掉即回到 B0 的 FTS 基线，无需回滚数据。
- **明确不做**：不做中文分词/BM25 改造（属 FTS 自身演进）；不把分块当默认；不让语义路成为字面查询的第一入口。

### B3 写入运行时（隔离库，L）

- **目的**：把 P2-01 的契约变成能跑的运行时，但**只在隔离库**。
- **依赖**：B1、B2（写-查闭环要用 B2 的检索）。
- **交付**：`service.sqlite3` 的事件/修订/outbox/幂等/冲突/撤回/擦除计划；N1–N11 先写测试后写实现；**一个**客户端（Codex 或 Kimi）真实写实验数据并查看 receipt。
- **机械验收**：`pytest tests/unit/test_service_contract_negative.py tests/unit/test_write_runtime_*.py` 全绿；24h 离线重放无错误覆盖；receipt 的三阶段位（`canonical_committed`/`lexical_ready`/`semantic_ready`）分别可观测；写进去的事实能被 B2 检索到。
- **负向控制**：同键异载荷 → 409（不覆盖）；越权 7 个读面（search/get/graph/cache/bundle/export/history）泄露计数 **0**；恢复备份后重放擦除清单 → 被删记忆不复活。
- **回退**：隔离库整体删除；Nowledge 不受影响。
- **明确不做**：不写现网、不建第二个主库、不开跨设备（那是 P2-03/B7 之前的独立门）。

### B4 总结（Working Memory / bundle / handoff，L）

- **目的**：替代 Nowledge 的**最硬一条**——它每天生成焦点面并把结论带来源地注入会话（本会话注入的 `Focus Areas / Briefing` 就是该产物）。
- **依赖**：B3（要有规范 revision 才能引用）。
- **交付**：WM 生成器（定时 + 变更触发）、bundle 组装（默认 2,000 tokens / 硬上限 6,000，含"可继续读取"的来源入口）、失效键（revision / 权限 epoch / 项目映射变化）、handoff 结构（目标/已完成证据/未完成步骤/code revision/失败与约束/thread IDs）。
- **机械验收**：20 组真实续接任务 ≥90% 无需重复提供关键事实，关键事实错误与越界 0；每条结论可回读到 source revision/span；撤回后 5 分钟内旧结论不再出现；默认 bundle ≤2,000 tokens（按目标模型 tokenizer，取不到则用保守字节上限并报告口径）。
- **负向控制**：删掉某条结论的来源 → 该结论必须降级为 `missing_evidence`，不得留在高可信区；收紧权限 → bundle 立即收缩且不泄露"另一端存在"；同一条摘要喂给两个 agent 必须带不同 scope 的失效键。
- **回退**：停生成器 + 删 bundle endpoint；规范记忆不受影响（WM 是派生面）。
- **明确不做**：不从会话总结生成全局规则（规则只存权威文档入口）；不做跨用户级大摘要。

### B5 语义清理（生命周期，M）

- **目的**：把"删或留"升级为"可逆、可审计、可回读"的生命周期，这是 Nowledge 与 los-memory 差距最大的一条**语义**能力。
- **依赖**：B3。
- **交付**：`archive`（默认召回隐藏，按 id 可读）、`deprecate`、`supersede`（版本链）、`merge`（带证据且可 `split` 还原）、review 队列（矛盾/重复候选，人工确认后应用）。
- **机械验收**：archive 后不进默认召回但 `get by id` 仍可读；supersede 后默认排除旧 revision、历史查询需显式权限；merge 可拆回两条原始记录（逐字段比对）；review 应用前必须带 `plan_id`（预览与应用同一实现）；erasure 回执列出剩余投影/设备。
- **负向控制**：archive 一条仍被 bundle 引用的记忆 → 必须红；merge 后撤销 → 两条原始记录完整恢复（含 revision 链）；没有证据的 deprecate 必须被拒。
- **回退**：生命周期操作都是新增修订 + 状态位，回退 = 反向操作；物理 `erase` 只按请求执行且带收据。
- **明确不做**：不做无人确认的批量淘汰；不删除历史修订（保留认知链）。
- **对照样本**：Nowledge 侧现有 4 条待处理 review（含 1 条 `flag_contradiction`）可直接用作语义等价测试的对照输入。

### B6 读路径切换（旁路 → 主读，M）

- **目的**：让新库承担主读，Nowledge 降为 fallback / 对照。
- **依赖**：B2、B4、B5（B4 与 B5 可并行）。
- **交付**：客户端按查询形态路由；`compare` 台账按形态累计；抽检清单。
- **机械验收**：每查询形态 ≥30 条**真实**查询；人类抽检 ≥20 条答案；`nowledge_only` 不造成漏检；越权/过期 0；延迟与退化路径（字面/语义/不可用）分别报告。
- **已量的现状（2026-10-21，B0 冻结基线）**：同一 40 条语料上 **shadow Hit@5 0.771 / Nowledge 0.200**（`forbidden hits@20` 0 / 1）。所以"不劣于基线"这条**已经满足**，它不再有约束力；本批真正要立的门槛是**绝对线**（每形态 ≥0.8 Hit@5）加**语义类**与**写入面**。详见 `docs/reports/2026-10-21-b0-ruler-and-baseline.md` §3。
- **负向控制**：故意让新库缺一条已知记录 → 必须走 fallback 而不是返回空；fallback 触发次数必须可见（否则"没漏"无法证明）。
- **回退**：删客户端 MCP 条目即回到纯 Nowledge 读。
- **明确不做**：不在此批改写入（写仍唯一走 Nowledge）。

### B7 写入口切换（cutover，M，强用户门禁）

- **目的**：把唯一写入口从 Nowledge 移过来，并有可执行的回退剧本。
- **依赖**：B3、B6 与 P6 六条门槛。
- **交付**：cutover epoch 记录；冻结 Nowledge 写；补齐最后增量；Nowledge 只读保留 ≥14 天；回退剧本（先冻结新库写入 → 导出切换后事件 → 显式映射/验证回灌旧库 → 恢复旧库写入）。
- **机械验收**：P6 门槛 1–6 逐条；RPO ≤24h / RTO ≤1h 实测；恢复后重放擦除清单不复活；规范核对按 ID/revision/正文摘要/作用域/生命周期（不只比条数）。
- **负向控制**：切换后故意提交一条只在新库的记录 → 旧库不得出现"半条"；回退演练一次（在隔离环境）必须成功。
- **回退**：按上述剧本；保留旧库只读窗口是回退的前提条件，不是可选项。
- **明确不做**：不做双写；不保留两条增量通道。

## 6. 依赖与并行

```
B0 ─┬─▶ B1 ─┐
    └─▶ B2 ─┴─▶ B3 ─┬─▶ B4 ─┐
                     └─▶ B5 ─┴─▶ B6 ─▶ B7
```

- B1 与 B2 可并行（都只读，互不改同一张表）。
- B4 与 B5 可并行（前者派生面，后者状态机）。
- B6 必须在 B4/B5 之后：否则"主读"上会出现"记得住但总结不出/清理不了"的错位。
- B7 只在用户给出切换窗口后启动（P6 门槛 5）。

## 7. 每批的证据格式（沿用路线图 §9，补两条）

每批报告必须含：git revision + dirty 摘要、schema / index / model / chunker 版本、硬件与并发、数据集 hash 与条数、命令、冷/热次数、成功/失败总数、p50/p95、分类质量、退化路径、费用口径、结论、回退。

新增两条：
- **尺子变更必须单独记**：改评测集/判据要写"旧尺子测出什么、为什么换、换后同一批数据的结果差异"，否则历史报告不可比。
- **不得只报总平均**：关键错误（越权、过期正文冒充最新、无证据批准、删了又复活）逐项单列，不能被平均分稀释。

## 8. 三个待定项：已决（2026-10-21，按数据选，随后续数据可调整）

原先把这三项留给你定；实测数据已经足以给出**默认决策**，它们全部是"可回退的局部选择"，改起来是配置/落点级别，不是重写级别。

### 8.1 嵌入模型与位置 → **ONNX 进程内，中英小模型，512 维以内**

| 选项 | 实测/成本 | 结论 |
| --- | --- | --- |
| **ONNX 进程内（`bge-small-zh-v1.5` 或 `multilingual-e5-small` 级，int8 ~95–120 MB）** | 无需新进程；M3 36 GB / M1 32 GB；repo venv 是 Python 3.12.9，`onnxruntime` 可装（1.19.2 有该平台轮子）；单块 ~5–15 ms（要在 B2 第一步量） | **采纳** |
| GGUF / llama.cpp | 为 100 MB 模型引一个 C++ 运行时 + 进程边界 + 模型文件两种格式并存 | 否决 |
| 复用 Nowledge 的 embedder | 把替代品绑在被替代品上；它降级/换模型时替代品一起坏 | 否决（契约上也禁止） |
| 独立 embedding worker 进程 | 只在 RSS 或吞吐顶到预算时才有意义 | 延后（触发条件见下） |

- 落点：`~/.local/share/los-memory/models/<model>-<rev>/`，模型文件 sha256 进 **generation manifest**（与索引清单同一把纪律，换模型 = 换 generation，旧向量整批丢弃）。
- **调整触发**：① 留出集 paraphrase recall@5 低于基线；② 服务 RSS > 300 MiB；③ 语料 > 5 万 chunk（届时再拆 worker 进程）。三者任一成立就重选，不回滚设计。

### 8.2 常驻位置 → **B3–B6 在 M3（新 launchd 单元），NAS34 留到 B7 之后且不是"现在装"**

实测：M3 = 36 GB / load 1.7 / 221 GB 空闲 / uptime 22 天 / **已经托管影子库与 5 个 launchd 作业**；NAS34 = 3 vCPU / **9.9 GB 总内存、可用 6.6 GB** / load ≈2 / `nmem.service` 自己占 1.7 GB（`MemoryMax=8G`）/ 另有 18 个容器 / eMMC 仅剩 3.9 GB。

- **决策**：B3 起在 M3 起**独立单元**（`com.echerlos.los-memory-service`，数据 `~/.local/share/los-memory/service.sqlite3`），夜间加密备份到 NAS n004（复用 restic 链路）。
- **为什么不是 NAS**：① 替代品与**被替代品同机**，切回 Nowledge 的退路会被同一次故障一起带走；② 6.6 GB 可用 + 8 G cgroup 上限 + 只有 3.9 GB 的 eMMC，B3 的库和备份都要另选 NVMe 池；③ B3/B4 只需"隔离库 + 可复现"，不需要 7×24。
- **为什么现在不结死**：M3 是笔记本（影子同步已经量到睡眠缺口）。B7 时要按实测重新判：若读 p95 经 tailnet ≤2× 本地、RSS ≤512 MiB 且睡眠窗口不影响写入，则 M3 留下；否则 NAS 必须上（届时是独立 systemd 单元 + NVMe 数据目录 + 与 Nowledge 不同 host 的备份）。
- **调整触发**：B7 门槛（P6 六条）任一条不满足。

### 8.3 清理的人工确认面 → **CLI + append-only 台账；UI 只读**

实测：`dsh-memory-console` 当前**只有一个只读 GET 端点**（`mode=fast|full`），没有跨进程锁，而 web 与 desktop 两个宿主都会跑它。给它加写动作 = 造第二个状态面，需要单写者约束与并发保护。

- **决策**：B5 的 review 面 = `los-memory cleanup plan|apply|revert`（写 `cleanup-ledger.jsonl`，带 `plan_id`、`expected`、`actual`、`reverted_by`），「记忆」tab 只**展示** `pendingPlans`（扩展它已在读的那份 JSON 报告），不提供操作按钮。
- **为什么**：B5 的验收本身就要求"预览与执行必须是同一次点击的两个阶段，且执行必须回显 `plan_id`"——CLI 天然满足，UI 要额外做并发与回滚；且台账在 UI 之外仍可审计。
- **调整触发**：若人工确认频率 > 10 次/周，再给 host 侧加写端点（单写者：host 拥有 DB，tab 只发意图）。

## 9. 本文件不做

不实现任何批次；不新增依赖；不建 `service.sqlite3`；不改同步周期与检索实现；不动 Nowledge 写入；不做历史回灌；不切读/写入口。
