# B2 步1 · 语义召回模型选型：e5-small 胜出，但「80% 改写召回」这个门槛还判不了（2026-10-21）

> 归属：`docs/design/nowledge-replacement-batches.md` 的 **B2 第一步证据面**。
> 只读实验：不装依赖进仓库、不改线上、产物在 `/tmp/b2models/`（模型与脚本可随时重取）。
> 私有语料不进仓库：本文件只出现聚合指标与 case id。

## 0. 四句结论

1. **模型选 `multilingual-e5-small`（int8, 118 MB）**：语义类 recall@5 **0.567–0.642**，`bge-small-zh-v1.5`（24 MB）只有 **0.30**，`paraphrase-multilingual-MiniLM-L12-v2` **0.425** 且内存更高（被支配）。
2. **文本构造比模型更影响结果**：同一模型 `title+body` 0.567 → `title×2+body` **0.642** → `title` only 0.60（但 MRR 最高 0.708）。**标题重复一次**是性价比最高的一行改动。
3. **反直觉的负结果：分块（chunk-max）把改写召回打到 0.0**。抽象问题问的是"那类现象"，片段里没有这个概念 → 逐记录一条向量优于分块取最大。
4. **内存门槛要改**：e5 稳态 RSS **~610–715 MiB**（bge 只有 139–169 MiB），超我更早写的 ">300 MiB 就重判" 触发线。两个数字都记下，按实测把线抬到 800 MiB，并把"质量差值"写进理由。

## 1. 方法（可复跑）

```
语料：~/.local/share/los-memory-shadow/eval/snap.sqlite3 的 active 记录 2676 条
文本：title + "\n" + content（截断由 --max-chars 控制；默认 1200）
编码：onnxruntime CPUExecutionProvider，tokenizer.enable_truncation(256)，
     mean-pooling + L2 normalize；query 前缀按模型约定
指标：把 40 例留出集按类切开 —— 语义类 20 例（semantic_paraphrase /
     relational_multihop / cross_tool_continuation / fact_revision）、
     字面类 10 例（exact_identifier / chinese_short）；no_answer 不参与
     统计；recall@5 按期望集合的覆盖率、另有 MRR@10 与 best-rank@20
```

脚本 `/tmp/b2models/spike.py`（含 `--text-mode`/`--chunk-size`/`--doc-prefix` 开关）。**为什么同时看 MRR**：5 个改写 case 的 recall@5 每格是 20pp，两个模型差一格读不出差异；排名从 18→3 与 3→18 在 recall@5 上都记成"miss"。

## 2. 结果

| 配置 | 模型字节 | 语义 recall@5 | 改写 recall@5 | MRR@10 | best@20 | 字面 recall@5 | 稳态 RSS |
| --- | --- | --- | --- | --- | --- | --- | --- |
| e5-small int8 · `title×2+body` | 118 MB | **0.642** | 0.5 | 0.635 | **0.90** | 0.6 | ~715 MiB |
| e5-small int8 · `title+body` | 118 MB | 0.567 | 0.5 | 0.662 | — | 0.60 | ~709 MiB |
| e5-small int8 · `title` only | 118 MB | 0.600 | 0.4 | **0.708** | 0.85 | — | ~704 MiB |
| e5-small int8 · `passage:` 前缀 600 字 | 118 MB | 0.542 | 0.4 | — | — | — | ~785 MiB |
| MiniLM-multilingual int8 · `title×2+body` | 118 MB | 0.425 | **0.0** | 0.355 | 0.55 | 0.1 | ~1005 MiB |
| bge-small-zh-v1.5 int8 · `title+body` | **24 MB** | 0.300 | 0.2 | — | — | 0.3 | **~139 MiB** |
| e5-small int8 · 分块 200/40（chunk-max） | 118 MB | 0.492 | **0.0** | 0.592 | 0.65 | 0.55 | ~709 MiB |

单条 rank（5 个改写 case，越小越好）——这就是"差一格"背后的真实差距：

| case | e5 `title×2+body` | e5 `title` only | e5 分块 | bge |
| --- | --- | --- | --- | --- |
| c11 | 2 | **1** | 32 | 8 |
| c12 | 2 / 342 | 22 / 657 | 30 / 1031 | — |
| c13 | 75 | 12 | 35 | — |
| c14 | **2** | **2** | 92 | — |
| c15 | **4** | 24 | 50 | — |

值得注意的两点：**c12/c13 是同一类失败**（问题对应"两条记录里的一个判断"，语义模型把泛化答案排得很后）；**c14/c15 在 e5 上稳在 top-4** —— 说明这不是"完全不会"，而是"一半类型能打中"。

## 3. 决策与参数变更

| 项 | 原定 | 改为 | 依据 |
| --- | --- | --- | --- |
| 模型 | 「bge-small-zh 或 e5-small，二选一」 | **multilingual-e5-small int8** | 语义 0.642 vs 0.300；改写 0.5 vs 0.2（与主库 0.20 的改写水平持平 ⇒ bge 等于没补语义） |
| 索引粒度 | 「>5 万 chunk 时再看」 | **一条记录一条向量（不分块）** | chunk-max 把改写打到 0.0，且索引成本 +2.5×（11,131 chunk / 533 s） |
| 文本构造 | 未定 | `title×2 + "\n" + content[:1200]` | 0.642 为实测最优；标题重复是"一句话改动" |
| 文档前缀 | 未定（e5 官方建议 `passage:`） | **不加** | 加权后反降到 0.542 且更慢；本语料是长中文叙述，官方前缀假设的短段落形状不成立 |
| 内存触发线 | RSS >300 MiB 重判 | **>800 MiB 重判** | e5 稳态 610–715 MiB；bge 的 139 MiB 换来 −0.27 语义召回，不值得当默认 |
| 依赖面 | 「ONNX 进程内」 | 确认：**模型与运行时都不进仓库**（118 MB 模型按 sha256 记在 generation manifest，运行时用独立 venv/服务进程） | 仓库 + lockfile 不该背 118 MB 二进制 |

## 4. 负结果（比正结果更值钱）

- **分块没用**：抽象问题问"这类现象叫什么"，答案在整篇的**概念**里，分块后每块都只说"某次具体操作" ⇒ 0.5 → 0.0。
- **MiniLM 被支配**：语义更低、内存更高、改写为 0。
- **bge-small-zh 不够**：24 MB/139 MiB 很便宜，但语义 0.30 与主库基线同档，"补语义"的价值不成立。
- **`passage:` 前缀反效果**：与官方文档的推荐相反；至少在"长中文叙述 + 抽象查询"这一形状下不适用。
- **耗时数字不可用**：同一配置三次测得 index 82 / 164 / 264 s（`index_ms_per_record` 31/61/99），机器负载波动大 ⇒ 排序不能靠本表耗时，实施阶段要在空闲机上重测。

## 5. 这条结论**不能**证明什么

- **没有证明"改写召回 ≥0.8"**：5 个 case 每格 20pp，当前 0.5 = 2.5/5。这个门槛在 5 例上**不可判**，不是"未达标"。
- 也不能证明"混合（FTS+语义）整体变好"：这批查询是完整中文问句，而现有 FTS 面是**子串**语义（`search_detailed` 按空白切词）⇒ 这些 case 上 FTS 返回 0 条，融合没有可融合的东西。真正需要融合的是字面类（FTS 强、语义弱）与改写类（语义强、FTS 空）两侧，所以**融合必须在实现阶段用两段测试证明**。
- 没有做中文分词/BM25 改造；`chinese_short` 上 e5 反而 1.0（纯语义向量对 2 字词也不差），但这不该被读成"可以不要 FTS"。

## 6. 门槛按数据改写（B2 验收）

| 项 | 原写法 | 改为 |
| --- | --- | --- |
| 改写召回 | 留出集 semantic 类 recall@5 ≥0.8（或 +10pp） | **先把改写探针扩到 ≥20 例**（当前 5 例不可判），再按 ≥0.8 / ≥FTS+Δ 判 |
| 语义总体 | 未定 | **语义类 MRR@10 ≥0.7**（当前 e5 `title` only 已达 0.708）且 **best-rank@20 ≥0.85**（当前 0.85–0.90） |
| 字面不退化 | 「不劣于基线」 | 保留：**字面类走 FTS 路径，语义候选必须带 `index_generation`，字面 recall@5 不得下降** |
| 资源 | RSS >300 MiB 重判 | **>800 MiB 重判**；索引时间在空闲机重测并记录 |

## 7. 下一步（B2 实施）

1. 扩改写探针（≥20 例，独立文件、不进已冻结的 40 例基线）——**这是判 B2 的前提**。
2. 落地：`vector_records(source_id, generation, dim, vec int8/float16)` + `index_generation` 表；编码在**独立进程**（避免把 700 MiB 拖进 CLI 主进程），落盘用 `mmap`。
3. 融合：RRF(k=60) 融合 FTS 排名与语义排名；**每条候选带 `canonical_revision` + `index_generation`**；语义侧不可用时报 `semantic_unavailable`（**不得静默退化成"没有相关记录"**）。
4. 负向控制：过期 generation 的向量必须被丢弃、已删除/已撤回记录不得被语义路召回、语义服务不可用时结果不得为空而不报原因。
