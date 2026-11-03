# 记忆整理与清理方案（hygiene & consolidation）

状态：**设计稿 + P0 尺子（`scripts/hygiene_scan.py`）+ P1a 计划器（`scripts/hygiene_cleanup.py`）已实现**。
写者桥接（P1b）、节律作业（P2）、拓扑层（P3）**未实现**，本文件说明它们各自要满足什么判据。

依据：2026-11-03 对现网语料的只读实测（[基线报告](../reports/2026-11-03-memory-hygiene-baseline.md)）、
[空间轴保留策略](retention-policy.md)、[替代批次 B5 语义清理](nowledge-replacement-batches.md) §5、
[检索与图谱方案](memory-retrieval-pipeline.md) §2/§5，以及 8 个公开记忆系统（§3）。

## 0. 一句话

把"整理/退役/更新"从**人肉与临时脚本**，变成**有判据、有计划、有账本、可回退、按节律跑**的子系统：
判定与账本住在 los-memory，写入交给**拥有该存储的写者**；系统只提议与受控执行，永不静默破坏。

三条宪法（后文每个设计决定都可以回溯到这三条）：

| # | 宪法 | 违反后的具体症状（不是理论风险） |
| --- | --- | --- |
| **C1** | **派生面无权删除或改写规范记忆**（摘要 / 向量 / 图 / 上下文包 / bundle） | 一次摘要换代就把事实删掉；HippoRAG 类结构一旦被视为"扩充语料"，LLM 生成的边会冒充证据 |
| **C2** | **任何"看不见"都必须可逆且有收据**：退役 = 打标 + 进台账，不是删行 | MemoryBank 的破坏式随机遗忘（§2）：记忆消失且无法证明消失过 |
| **C3** | **判据必须是结构**（id / slot / 边 / 归一化 hash / 时间窗），文本相似度**只产候选** | 本仓 2026-11-01 回测：87 条历史建议在各自日期上全部判"新颖"（3-gram Jaccard < 0.5）⇒ 文本相似度作判据 87/87 失效 |

## 1. 实测基线（2026-11-03，只读；可复跑，命令见报告）

语料 = `~/.dsh/memories`（DSH 记忆存储，实际唯一在写的那一层），54 MB：

| 指标 | 实测 | 含义 |
| --- | --- | --- |
| 轨道数 / 条目数 | 135 / **7,594** | 轨道 = 一个 `MEMORY.md` / `KEY.md` / `USER.md` / `daily/*.md` |
| 状态 | active **7,590** / deprecated **4** | 生命周期标记基本没用起来 |
| 带身份证 `[id:…]` | **302（4.0%）** | 96% 的条目没有可机械引用的身份 |
| 槽位 `[slot:…]` | **5** | 槽位机制存在但未使用 |
| 正文自称"修正/取代/已过时" | **212** | 其中只有 **2 条**带机械 `[supersedes:id]` 边（同一条链，目标已 deprecated）⇒ 210 条是"声明取代但不落实" |
| 轨道重复 | **14 组**，多出 **2,378 条（31.3%）** | 同一项目经两个 cwd 别名各存一份，`MEMORY.md` 逐字节相同（最大一组 1,476 条） |
| 同轨精确重复 | **2 条** | 条目内重复极少 |
| 近似重复（5-gram Jaccard ≥0.8） | **1 对** | 同上 |
| 标记全空的轨道 | **50 个** | 条目 ≥20 但 id/slot/deprecated 全为 0 ⇒ 该轨道的生命周期是"盲"的 |

五条结论（每一条都改变了方案）：

1. **冗余的主因是轨道重复，不是条目重复**：31.3% vs 0.03%。先做"轨道合并"的收益比"删条目"大三个数量级，
   且不损失任何内容。
2. **现有探针的 `sameSlotMultiActive = 0` 是空分母的零**：全局轨 `slots = 0`。按本仓已有纪律
   （`slots > 0` 才认那两个 0），这个"健康"不能读成健康。
3. **212 条自述 vs 2 条机械边**（且那 2 条来自 2026-11-03 的最新一次写入）说明缺的不是"记录意愿"，而是**把意愿变成机械后果**的那一步；机制刚上线，尚未扩散。
4. **空间轴已经成熟**（`retention-policy.md`：FTS `optimize` + 显式 `VACUUM` + WAL TRUNCATE），
   本方案**不重复做空间**，只复用它的结论与工具。
5. **语境不同**：本仓 B5 面向未来 canonical store；眼下真正积累冗余的是 DSH 存储。所以引擎必须
   **语料无关**（适配器 + 计划 + 账本），否则会为影子库写一套、为真实语料再写一套。

## 2. 三个必须先否掉的做法

| 做法 | 为什么否掉 | 出处 |
| --- | --- | --- |
| **遗忘曲线直接删记忆** | MemoryBank 是这条路的参考实现：`retention = exp(-t/5*S)` 被 Python 解析成 `((-t)/5)*S`，**越常被召回越早被删**（强度 10 的一天存活率 13.5%，强度 1 是 82%）；删除是 `pop` + 原地重写 JSON、无墓碑、无备份、且不按用户过滤。设计思想（召回强化 + 陈旧衰减）可留，**"概率删除"必须否掉** | [MemoryBank 审读](https://github.com/neoneye/agent-memory-atlas/blob/da21abdef437812925c8332a8f6ff9bd90093faa/content/systems/memorybank.md) |
| **文本相似度判重/判冲突** | 本仓 2026-11-01 回测 87/87 失效；且"两条相似"与"同一条事实的两个版本"是两件事 | `92fe7d75`、AGENTS.md 记忆写入纪律 |
| **让 LLM 原地改写旧记忆**（A-MEM 的 memory evolution） | A-MEM 最有价值的是"笔记属性 + 链接 + 演化"，但它自己也把"无版本历史地重写旧笔记"列为风险。我们改成**只追加新 revision + 写 supersedes 边**，旧文本一字不改 | [A-MEM](https://arxiv.org/abs/2502.12110) |

## 3. 公开项目对照：采纳什么、改什么、拒什么

| 项目 | 机制 | 采纳 | 改造点（我们的约束） | 拒绝 |
| --- | --- | --- | --- | --- |
| **Letta / MemGPT** | 四层（message buffer / core / recall / archival）+ **sleep-time agent** 在空闲时整理记忆 | ①"离线整理窗口"作为**独立进程**，不占读路径延迟；②分层：上下文面 / 规范面 / 派生面职责分离 | 整理者是**确定性引擎 + 人工确认**，不是"agent 自己改自己的记忆"（无审计）；sleep-time 只允许产出 **计划**，不直接落盘 | 心跳式 agent 自管记忆 |
| **Mem0** | 写时对 top-s 相似邻居做 `ADD / UPDATE / DELETE / NOOP` 决策；图变体对过期边做 **invalidate** 而非删除 | ①四种写时动作作为**判据分类**（我们的 D3/D4/D6 对应 UPDATE/DELETE/NOOP 的机械版）；②`invalidate` 而不是 delete | 写时决策不做 LLM 自由裁量：只有结构化判据能落盘；"相似邻居"只用来**提名**候选 | 硬删除（Mem0 自己承认丢历史）；写时 poisoning 无防线 |
| **Zep / Graphiti** | 事实存**边**，四时间戳 `created_at / valid_at / invalid_at / expired_at`，用"失效"表达变化，支持时点查询 | ①四时间戳进条目的生命周期头（`valid_from/valid_to/recorded_at/expired_at`）；②失效 ≠ 删除；③"点时间可查"作为读面契约 | 我们的"边"先是**条目间边**（supersedes/contradicts），实体边放 P3 | 默认给每条记忆抽实体三元组（成本高，收益未测） |
| **A-MEM** | Zettelkasten 笔记：内容 + 关键词 + tag + 上下文 + 链接；新条目可触发旧条目的"演化" | ①条目带 `keywords/tags/context` 作为**判据输入**；②链接生成用"先提名后裁决"；③合并前先建链接 | 演化 = **新增 revision + 边**，绝不原地改旧文本；链接与演化都要**可拆回** | 无版本历史的重写 |
| **MemoryBank** | 召回强化 + Ebbinghaus 衰减（强度 S、上次召回时间） | **只采纳形状**：`score = exp(-Δdays / (τ·S))`（修正后的单调式）用于**排序与复审优先级**，不用于删除 | 必须有**单调性测试**（Δt↑ ⇒ score↓；S↑ ⇒ score↓ 的速度变慢）——这正是原实现唯一缺的测试 | 概率删除、无墓碑、加载路径里做突变 |
| **HippoRAG 2** | 短语节点 + passage 节点，PPR 图检索，LLM 识别记忆过滤 | ①"结构**辅助检索**，不用结构**扩充语料**"；②passage 节点必须保留原文（我们的条目本身就是 passage） | PPR 只在**实测有增益**时启用；先做有界 BFS（本仓 `memory-retrieval-pipeline.md` §5.2 已定） | 全量 OpenIE 建图（LLM 成本 + 噪声，且论文自己显示多跳优势随语料增长而衰减） |
| **Anthropic context engineering** | context rot；压缩（compaction）、结构化笔记、子 agent 三种长程策略 | ①把"上下文面"与"记忆清理"**分开**：compact 不是清理，不产生退役；②上下文包必须比记忆更短命、可重建 | 上下文包不得成为唯一副本；派生摘要必须带回读指针 | 用压缩代替记忆治理 |
| **Manus** | 文件系统即外部记忆；**压缩必须可还原**（丢正文保 URL/路径）；todo 复述；保留失败轨迹 | ①"压缩可还原"作为派生面硬约束；②退役时保留**指针**（id + 落点 + 摘要），不保留全文 | 我们的指针是 `id8 + revision + 摘要`，可从账本回读 | — |

## 4. 模型：五层 + 三轴 + 谁有权动谁

| 层 | 内容 | 权威性 | 谁可写 | 清理动作 |
| --- | --- | --- | --- | --- |
| **L0 证据** | 会话事件、工具结果、来源 span、revision 历史 | 不可变 | 采集器（追加） | 只按显式 erasure（带收据） |
| **L1 规范记忆** | 条目正文 + 身份 + 作用域 + 生命周期头 | **唯一权威** | 存储的**拥有者**（单写者） | deprecate / supersede / archive（都是加标记） |
| **L2 生命周期状态** | active / deprecated / superseded / archived / tombstone | 判定输出来自 L1 标记 | 同上 | 状态转移本身要进账本 |
| **L3 投影** | FTS、CJK bigram、向量、实体边、聚类 | 可重建，**无权威** | 引擎（可整批重建） | 只重建 / 过期，不"删记忆" |
| **L4 上下文面** | WM / bundle / handoff / 摘要 | 派生、短命 | 生成器 | 过期即弃，失效键驱动 |

三轴分开度量、分开治理（本仓 `I10 四轴不借力` 的延伸）：

- **空间轴**：字节、freelist、FTS 段 → 已有工具，本方案只调用。
- **语义轴**：失效 / 陈旧 / 冗余 / 冲突 → 本方案主体。
- **拓扑轴**：边、聚类、扇入扇出、级联影响 → P3。

## 5. 状态机与动作白名单

```text
            ┌───────────── write ─────────────┐
            ▼                                 │
         active ──deprecate──▶ deprecated      │
            │                     │            │
            ├──supersede──────────▶ superseded │
            │                     │            │
            └──archive────────────▶ archived ──┘(restore：只能回 active，且要新 revision)
                                     │
                                erase（仅显式 + 收据）──▶ tombstone
```

| 动作 | 机械后果 | 可逆 | 需要 plan_id | 谁执行 |
| --- | --- | --- | --- | --- |
| `touch`（只读复核，刷新召回计数） | 无正文变化 | — | 否 | 引擎 |
| `annotate`（补 tag/keywords/context） | 只加派生字段 | 是 | 否 | 引擎 |
| `deprecate(id, by?)` | 头部加 `[deprecated(:by)]`，默认召回隐藏，按 id 可读 | 是（去标记） | **是** | 存储拥有者 |
| `supersede(old, new)` | 新条目加 `[supersedes:old8]`，并把 old 标 deprecated | 是 | **是** | 存储拥有者 |
| `archive(id)` | 迁到 `KEY-archive.md` 同构区块，记 `archivedAt/reason/reviveCondition` | 是 | **是** | 存储拥有者 |
| `relocate(id, track)` | 跨轨移动（work↔project→life），保留 id | 是 | **是** | 存储拥有者 |
| `merge(group→id)` | 生成一条合并条目 + 对旧条目 supersede；**记录可拆回清单** | 是（`split`） | **是** | 存储拥有者 + 人工确认 |
| `split(id)` | 用合并时的清单还原 | 是 | 是 | 存储拥有者 |
| `erase(id)` | 物理擦除 + 收据（列出各投影剩余） | **否** | 是 + 显式授权 | 存储拥有者 |

**不做**：无确认的批量淘汰；原地改写旧正文；删 revision；删 tombstone（本仓 `retention-policy.md`
层 2/层 3 已定）；让 L3/L4 拥有以上任何动作。

## 6. 判据库（detectors）

每个判据都必须能回答三件事：**用哪个结构字段判定**、**误判的后果**、**能不能自动执行**。
`safety` 只有两档：`auto_safe`（语义上等价于"重复/已声明"，可自动但**仍走 plan+ledger**）与
`review_only`（必须有人裁决）。

| # | code | 判据（结构性） | 需要的字段 | safety | 误判后果 | 负向控制 |
| --- | --- | --- | --- | --- | --- | --- |
| D1 | `track_duplicate` | 两个轨道的**条目集合 hash** 相同（去序、去标记） | 全部条目归一化正文 | auto_safe | 合并错轨道 → 条目跨项目串味 | 两条只差一处的事实**不得**判同 |
| D2 | `supersedes_unresolved` | 新条目带 `[supersedes:X]`，而 X 仍 active | id + supersedes | auto_safe | 把并列结论误退役 | X 已 deprecated ⇒ 不报 |
| D3 | `slot_multi_active` | 同一轨道内同一 `[slot:s]` 有 >1 条 active | slot | auto_safe | 保留错的那条 | 不同轨道的同槽位不合并（槽位唯一性是**按轨道**的；实测 5 个槽位无跨轨冲突，P1 若要跨轨全局唯一必须另立判据） |
| D4 | `exact_duplicate` | 同轨归一化正文 hash 相同（长度 ≥12） | 正文 | auto_safe | 删掉唯一一份 | 短条目（<12）不判 |
| D5 | `near_duplicate` | 5-gram Jaccard ≥ 0.8，且非同 hash | 正文 | review_only | 把"补充"当"重复" | 阈值下调才报警（测试钉住） |
| D6 | `claim_without_edge` | 正文含"修正/取代/已过时"等声明，且无 `[supersedes]` | 正文 + 边 | review_only | 无（只提名） | 有边的条目**不得**报 |
| D7 | `expired_validity` | 生命周期头 `valid_to`/`expires_at` 已过 | 时间字段 | auto_safe | 误退役仍有效的事实 | 时区/未来时间夹到 0 |
| D8 | `orphan_evidence` | 来源记录在上游已 tombstone / 源不可达 | 上游状态 | review_only | 把上游索引故障当内容过期 | 上游"判不了"不得当"已删" |
| D9 | `decay_candidate` | 修正 Ebbinghaus 分数低于阈值，且**无入边、无 slot、从未被 bundle 引用** | 召回计数 + 边 + 引用 | review_only | 退役冷门但重要的知识 | 分数必须单调（见下） |
| D10 | `fact_drift` | 少数**可验证事实**（端口/版本/路径/端点）与外部探针真值不一致 | 探针输出 | review_only | 追逐瞬时故障 | 探针"判不了"（rc=2）不得计入漂移 |

D9 的分数与测试（这是"借鉴 MemoryBank 但修正其错误"的落点）：

```text
score = exp(-Δdays / (τ · S)),  S = 1 + 召回次数,  Δdays = 上次召回距今天数
```

必须钉住三条断言：`score(Δ=1,S=10) > score(Δ=1,S=1)`（强度**减缓**衰减，原实现正好相反）、
`score(Δ=10) < score(Δ=1)`、`Δ<0`（时钟回拨）夹到 0。**D9 只降排序与提名复审，永不自删。**

## 7. 拓扑方案（P3）

节点：`track` / `entry` / `slot` / `project` / `entity`（可选）/ `decision` / `incident`。
边（每条带 `source` / `confidence` / 四时间戳，失效保留历史）：

| 边 | 由谁产生 | 用途 | 权限 |
| --- | --- | --- | --- |
| `supersedes` | 写入时机械生成（已有 `[supersedes:id]`） | 版本链、默认召回排除旧版 | 权威 |
| `contradicts` | 人工/复审确认（**不自动**） | 冲突面；两边都保留并标注 | 复审 |
| `supports` / `derived_from` | 人工或抽取器（候选） | 证据链；摘要必须指回正文 | 候选 |
| `mentions` / `co_occurs` | 抽取器 | 只做检索扩展，**不冒充因果** | 候选 |
| `applies_to`（project/entity） | 注册表 | 作用域与跨项目边界 | 权威 |

三条硬约束：

1. **图是投影**：可以从 L1 全量重建；重建失败不得影响读记忆。
2. **级联守卫**：`archive` / `merge` 之前必须查**入边**——被 bundle、被 `supersedes` 链、被
   `derived_from` 引用者一律**拒绝**（B5 的负向控制："archive 一条仍被 bundle 引用的记忆必须红"）。
3. **聚类只产计划**：连通分量（同 slot / 同 hash / 同项目+时间窗 / 显式链接）产生 `merge` 候选；
   没有人工确认不合并。PPR 只在"关系类查询"上先做出界 BFS 的 A/B，胜出才开（HippoRAG 的
   "结构辅助检索"原则）。

## 8. 节律与作业

单写者、幂等、有上限、失败退避。名称沿用现有部署约定（`deploy_shadow.py` 扫
`co.los.memory-shadow*.plist`），先设计、未落 plist：

| 节律 | 作业 | 动作 | 上限 | 失败处理 | 台账 |
| --- | --- | --- | --- | --- | --- |
| 300 s | `co.los.memory-shadow`（现有） | 同步，**不做清理** | — | 现有退避 | `sync_runs` |
| 每日 04:10 | `memory-hygiene-scan` | `hygiene scan`，只读 | 全量（实测 48 s） | 退避 + 日报 | `hygiene-report.json` |
| 每日 04:20 | `memory-hygiene-plan` | 生成计划，**不执行** | ≤200 条/次 | 同上 | `plans/<plan_id>.json` |
| 每周日 05:10 | `memory-hygiene-apply` | **只执行 auto_safe**，且计划已过 24 h 观察窗 | ≤50 条/次 | 连续 2 次失败即停并告警 | `cleanup-ledger.jsonl` |
| 每月 1 日 05:40 | `memory-hygiene-consolidate` | 聚类 + 复审队列 + `compact` | 全量 | 同上 | 同上 |
| 每小时 | `memory-hygiene-queue` | 复审队列 >20 时提醒 | — | 静默即告警 | `queue-state.json` |

**静默即告警**：作业自己的心跳写 `hygiene-metrics.jsonl`；6 h 无新增即 `hygiene_stale`
（本仓已两次踩过"探针静默被读成正常"：`recall_probe_stale`、`memory_console` 自身）。

**kill switch**：`~/.local/share/los-memory-shadow/hygiene.disabled` 存在即整条链只读；
`apply` 另受 `--max-actions` 与 `--budget-seconds` 双重限制。

## 9. 计划 / 执行 / 回退契约

```json
{
  "plan_id": "sha256(规则版本 + 语料快照hash + 规范化候选集)  ← 内容寻址，刻意不含时间窗",
  "created_at": "...", "expires_at": "...(+7d)",
  "corpus": {"kind": "dsh-md", "root": "...", "snapshot_hash": "..."},
  "rules_version": "hygiene-1",
  "actions": [
    {"id": "a1", "code": "exact_duplicate", "op": "archive",
     "target": {"track": "...", "id8": "abcd1234", "content_hash": "..."},
     "keep": {"track": "...", "id8": "12345678"},
     "preconditions": ["target.hash unchanged", "keep still active", "no inbound edge"]}
  ]
}
```

不变量（每一条都能写成会红的测试）：

1. **无 plan_id 不执行**；plan 过期不执行；plan 里任一条前置条件在 apply 时不成立 ⇒ 该条
   `skipped` 并记原因，**不静默跳过整批**。
2. **执行前重读**：目标 `content_hash` 变了就放弃该条（并发写者优先）。
3. **幂等**：同 `plan_id` 重放不产生第二次动作。
4. **回退是同构操作**：`revert` 按台账逆操作，逆操作**自己**也写一行（`reverted_by` 指向原行）。
5. **台账 append-only**：`ts / plan_id / action / target / before_hash / after_hash / reason_code /
   actor / result / evidence`。行式台账按**文件名里的月份**归档（活文件=当前月），不看 mtime。
6. **收据**：`erase` 必须列出各投影的剩余数量；`archive` 必须能被 `get by id` 读回。

## 10. 指标与门禁

| 指标 | 定义 | 来源 | 目标/阈值 | 告警码 |
| --- | --- | --- | --- | --- |
| `duplicate_entries` | `track_duplicate` 多出的条目数 | scan | 单调下降到 0 | `hygiene_duplicate_growth` |
| `claim_without_edge` | D6 计数 | scan | 下降（新增写侧必须为 0） | `hygiene_claim_backlog` |
| `marked_ratio` | 带 id/slot/deprecated 的条目占比 | scan | ≥0.9 | `hygiene_unmarked` |
| `pending_plans` / `pending_age` | 待审计划数与最老龄期 | 台账 | ≤7 天 | `hygiene_review_backlog` |
| `applied` / `reverted` / `skipped` | 每次 apply 的三态 | 台账 | `skipped/applied` ≤0.2 | `hygiene_apply_noise` |
| `recall_regression` | 冻结集 Hit@5 的变化 | 复用 B0 仪器 | ≤1pp | `hygiene_recall_regression` |
| `job_silence` | 距上次成功 | 心跳 | ≤6 h | `hygiene_stale` |

门禁（`exit 0 judged / 1 finding / 2 cannot judge`，2 **不是**通过）：

```bash
python3 scripts/hygiene_scan.py --self-test                                   # 12 条断言 + 3 组负向控制
python3 scripts/hygiene_scan.py --corpus dsh-md:~/.dsh/memories --fail-on auto-safe   # 有 auto_safe 即 1
python3 -m pytest tests/unit/test_hygiene_scan.py -q
```

必须存在的负向控制：**"判不了"不是"干净"**（缺 root / 空 root ⇒ 2）；**短条目不判重**；
**有机械边不报文本候选**；**阈值上调必须静默**；**scan 不改语料**（内容+mtime 比对）；
**清理后冻结集召回不退化**；**archive 被引用条目必须拒绝**；**revert 后逐字段与原文一致**。

## 11. 分阶段

### P0 尺子（**已实现**）

- 交付：`scripts/hygiene_scan.py`（只读、三态退出、`--self-test`、`--json`、`--fail-on`）、
  `tests/unit/test_hygiene_scan.py`（11 例）、[基线报告](../reports/2026-11-03-memory-hygiene-baseline.md)。
- 验收：`--self-test` 12/12；实跑 135 轨 / 7,594 条 / 48 s；auto_safe 16、review_only 263。
- 不做：任何写入、任何 plist、任何 LLM 调用。

### P1a 计划器（**已实现**，只读）

- 交付：`scripts/hygiene_cleanup.py`（`plan` / `status` / `--self-test`，`--json`，三态退出）+
  `tests/unit/test_hygiene_cleanup.py`（10 例）。计划文件落
  `~/.local/share/los-memory-hygiene/plans/<plan_id>.json`（`LOS_MEMORY_HYGIENE_DIR` 可重定向，保持 hermetic）。
- 计划分三段，**只有第一段可被机器执行**：
  - `actions`：入口作用域、可机械判定（D2/D3/D4），每条带 `target{track,id8,label}`、`keep`、`why`；
  - `proposals.dedupe_track`：轨道重复组（14 组），每组给 `entry_set_hash` + `proposed_survivor` +
    `requires_decision: true`；
  - `proposals.review`：文本类候选（claim_without_edge / near_duplicate / lifecycle_blind）与
    `exact_duplicate_evidence_track`（`daily/` 是证据面，重复只报告不动）。
- 关键实现决定（每一条都可被测试咬住）：
  - `plan_id` **内容寻址**（规则版本 + 语料快照 hash + 规范化动作集，不含时间窗）⇒ 同语料同动作 ⇒ 同 id，
    重放幂等；语料一变 id 就变，这就是写者侧的前置条件依据；
  - `snapshot_hash` 取每个轨道**字节**的 sha256（不信 mtime）；
  - `daily/*` 永不进 `actions`（清理会改写当天日志）；
  - `writer` 字段点名写者与锁协议，避免"谁负责落盘"变成隐含假设。
- 实测（2026-11-04，`~/.dsh/memories`）：`actions = 0`、`dedupe_track = 14 组 / 多出 2,382 条`、
  `review = 269`（claim 216 / near-dup 1 / blind 50 / daily 精确重复 2）。**这个 0 是有意义的结论**：
  在"轨道去重"与"日刊不自动清"两条约束下，当前语料**没有一条可以无人确认就执行**的动作。

### P1b 写者桥接（未实现，需要写者所在仓开工）

- 为什么不在 los-memory 里写：语料属于 `dsh-memory-evolve`，它用 `<dir>/.memory.lock`
  （`openSync(…, 'wx')`）串行化，并按 `lib/lifecycle.js` 的头部词汇落标记。Python 侧再实现一个写者
  = 第二套锁 + 第二套标记，正是 §0 宪法 C2 与"单写者"要禁止的形态。
- 写者侧需要实现的最小面（契约已定，见 §9）：读 `plans/<plan_id>.json` → 在 `withLock` 内逐条
  复核 `target` 与 `snapshot_hash` → `deprecateEntry(entry, byId)` → 原子写 → 输出 receipt
  （`plan_id / action id / before_hash / after_hash / added_prefix`）。`revert` = 按 receipt 去掉
  `added_prefix`（含被补发的 `[id:…]`），恢复到字节一致。
- 验收：`deprecate` 后条目不在默认召回但 `get by id` 可读；`revert` 后文件与原文字节一致；
  并发写者改动目标 ⇒ `skipped` 而不是覆盖；同 `plan_id` 第二遍 apply 产生 0 动作。
- 负向控制：无 `plan_id` 拒绝；过期 plan 拒绝；`snapshot_hash` 不符拒绝整批；被 bundle/边引用的条目拒绝
  archive；锁未取到时报"判不了"（不是"清理成功"）。
- 回退：删写者脚本即可；数据面只多了标记，去掉标记即回到原状。

### P2 节律与告警

- 交付：§8 的 5 条 launchd（先 dry-run 一个月）+ 指标进 `memory_console.py` + 静默告警。
- 验收：连续 7 天 `applied>0` 且 `reverted=0`；`skipped/applied ≤0.2`；日报里四项指标可读。
- 负向控制：把 scan 换成"永远返回空"的桩 ⇒ `hygiene_stale` 必须响。
- 回退：`bootout` 全部新作业，删 plist。

### P3 拓扑与合并

- 交付：边表（SQLite：`edge(src,dst,pred,source,confidence,valid_from,valid_to,recorded_at,expired_at)`）、
  级联守卫、连通分量聚类、`merge/split`、有界 BFS；PPR 只做 A/B。
- 验收：合并可拆回两条原始记录（逐字段）；级联拒绝率可复核；关系类查询 A/B 提升 ≥10pp 才开 PPR。
- 回退：删边表（投影可重建）；`split` 逆向。

### P4 跨层一致性与 cutover 之后

- 交付：DSH 存储 ↔ Nowledge ↔ 新库的**同一份生命周期词汇**核对（三处都认 `id/slot/supersedes/deprecated`）；
  B7 之后清理权移交给 canonical store，影子降为报告。
- 验收：同一条记忆在三层上的 `state` 一致（不一致要能指出是谁的投影旧了）。

## 12. 边界与风险

**明确不做**：无人确认的批量淘汰；删历史 revision；给派生结构设 TTL；把"库变小"当成功指标；
用大模型自由裁量代替结构判据；对 `daily/` 轨道做自动归档（日刊是证据面，只允许过期提示）。

| 风险 | 表现 | 缓解 |
| --- | --- | --- |
| 误退役 | 一条仍被引用的结论被 archive | 级联守卫 + 24 h 观察窗 + 每周上限 50 |
| 轨道合并串味 | 两个同名项目被合成一个 | 只按**条目集合 hash 完全相同**合并，且保留原目录与可拆清单 |
| 成本 | 全量扫描 48 s、聚类更贵 | 增量（按 track mtime）+ 每月一次全量 |
| LLM 依赖 | 复审队列堆积 | P1/P2 零 LLM；只有 `merge` 与 `contradicts` 允许人工发起 |
| 写者冲突 | 引擎与插件同时改一个文件 | 单写者：引擎只产计划，执行权在存储拥有者；`preconditions` 重读校验 |

## 13. 未决（需要拍板）

1. **全局主轨是否允许自动 `deprecate`**：建议首月**只跑 dry-run**，把 auto_safe 三类（D1/D2/D3）
   的提议全部走人工，观察误报率再决定。
2. **轨道合并的归属**：保留哪个 hash 目录（建议保留"仍被配置引用/更新更晚"的那份，另一份改名留存 30 天）。
3. **复审队列是否引入 LLM**：建议不引入（成本 + 一致性）；若引入，只允许产生 `merge` 候选，
   且必须带证据 span。
4. **`daily/` 的保留期**：日刊是证据面，目前 30 天已 1,000+ 条；要不要设"归档到月度汇总"的
   TTL，属独立决策（本方案不改）。
