# B0 · 尺子与接线：语料可读性、主库排序缺陷、冻结基线（2026-10-21）

> 归属：本报告是 `docs/design/nowledge-replacement-batches.md` 的 **B0 批次证据面**。
> 只读审计（未写入影子库或主库；唯一写入是 `compare` 台账新增 1 条真实样本与 `--freeze` 基线文件）。
> 私有语料不进仓库：本文件只出现 case id、`memories show` 判定与排名，不出现记录正文。

## 0. 三句话结论

1. **语料没有坏**：40 条 case 的 **41 个** `relevance≥2` 期望记录**全部可读回**（`memories show` 成功）。
2. **`semantic_paraphrase = 0.000` 不是"无语义能力"，也不是"语料没区分度"**：影子侧 5 条自述锚点全部 rank 1；主库侧 **3/5 的锚点在自己的 top-20 里找不到该记录**，且其中一条（c11）期望记录的分数是**全场最高**（0.6492）却排在**第 18**。
3. **这把尺子现在能用，但必须同时报绝对值**：主库在该类别处于地板，B2 的验收因此加了"绝对 recall@5 ≥ 0.8"的分支（见 §5）。

## 1. 仪器

`scripts/eval_human_check.py`（stdlib only，只读），三种模式：

| 模式 | 判据 | 用途 |
| --- | --- | --- |
| `--show-only` | `memories show <id>` 能否读回 | "期望记录存在吗"——不存在就是语料缺陷 |
| `--category <c> --depth N` | `memories search <自述锚点> -n N` 里该记录的排名 | "人用这条记录自己的话能不能找到它" |
| `--negative-control` | 乱码锚点是否泄漏**期望 id** | 证明判据不是"看有没有结果" |

判定词表（只有前两个失败）：`record_missing` / `no_anchor` / `anchor_absent` / `anchor_ranked_low` / `anchor_found_top5` / `found` / `no_expectation`。

**为什么负向控制不能判"空集"**：乱码锚点 `zzz-anchor-that-cannot-exist-20261021` 在 `nmem memories search` 上**仍返回 5 条**（0.45–0.53 分的无关记录）——该查询没有相关度下限，也没有空集。所以判据必须是"没有**期望 id** 泄漏"，不是"没有结果"。

## 2. 实测

### 2.1 期望记录可读性（全部 40 条 case）

```
python3 scripts/eval_human_check.py --show-only
→ human-check: 41 expectation(s) | found 41 | no_expectation 5 | record_missing 0
```

`no_expectation 5` 是 `no_answer` 类别（c36–c40）**设计上没有正确答案**，不是缺陷。

### 2.2 语义类 5 条的锚点探测（主库）

| case | 查询（改写，无字面重叠） | 自述锚点 | 影子 rank | 主库 rank（depth 20） |
| --- | --- | --- | --- | --- |
| c11 | 有些功能明明做完了却像没接上是什么问题 | `已实现但未接线` | 1 | **1**（但见 2.3：另一条查询下期望记录在第 18） |
| c12 | 回收卡住任务的组件接进主流程了吗 | `reclaim worker` | 1 | **1** |
| c13 | 对比两份媒体清单时怎么让结论站得住 | `固定外部观察输入` | 1 | **> 20（缺失）** |
| c14 | 生成覆盖率快照时线上会遇到什么坑 | `真实运行陷阱` | 1 | **> 20（缺失）** |
| c15 | 进程被强杀之后怎么确认还能恢复 | `SIGKILL` | 1 | **> 20（缺失）** |

### 2.3 主库的分数与位置不一致

同一响应里，c11 的改写查询：

```
rank 1  score 0.5348  1d6b8f8b…  CanTool filter-list subscription status — MVP done
rank 5  score 0.4503  sha256:0c3d7af1…
rank 18 score 0.6492  1643f3e4…  los '已实现但未接线' 反模式   ← 期望记录，分数最高
```

分数最高的记录排第 18，第 1 名的分数比它低 0.11。这不是"相似度低"，而是**输出的分数与排序不对应**。这条属主库侧行为（我们自己的影子不参与该排序），可独立报给 Nowledge；**不在本替代计划里修**（修了就不再是"在它自己的规则下比"）。

### 2.4 `compare` 台账（真实样本，非测试）

```
query           有些功能明明做完了却像没接上是什么问题
class           conceptual
shadow_ids      []                                    (字面 0 命中)
nowledge_ids    1d6b8f8b, sha256:6fe4baf5, sha256:03f0ac90, 29251ae5, sha256:0c3d7af1
shadow_only 0 / nowledge_only 5
```

台账在 **M3**（`~/.local/share/los-memory-shadow/compare-results.jsonl`），本次由 `co.los.memory-shadow-compare-drain` 落盘；M1 侧同名路径为空是**因为台账本来就在影子宿主上**，不要读成"台账没建"。

## 3. 冻结基线

```
python3 scripts/shadow_eval.py --refresh-shadow --scope native --backend both --freeze --out <path>
generated_at 2026-10-21T03:26:48Z | elapsed 520 s | limit 20 | scope native
snapshot sha256 38ed29f7… | records total 2811 / active 2676
frozen → ~/.local/share/los-memory-shadow/eval/baseline-20261021.json (created: true)
```

| 类别 | shadow Hit@5 | nowledge Hit@5 | shadow Recall@5 | nowledge Recall@5 |
| --- | --- | --- | --- | --- |
| exact_identifier | **1.000** (5/5) | **0.200** (1/5) | 1.000 | 0.200 |
| chinese_short | **0.800** (4/5) | **0.000** (0/5) | 0.800 | 0.000 |
| semantic_paraphrase | 0.000 | 0.000 | 0.000 | 0.000 |
| project_isolation | **1.000** | 0.200 | 0.900 | 0.200 |
| fact_revision | **1.000** | 0.400 | 0.933 | 0.400 |
| relational_multihop | **0.600** | 0.200 | 0.367 | 0.200 |
| cross_tool_continuation | **1.000** | 0.400 | 0.900 | 0.300 |
| no_answer | n/a | n/a | n/a | n/a |
| **overall** | **0.771** | **0.200** | 0.700 | 0.186 |

`forbidden hits@20`：shadow **0** / nowledge **1**；两后端 `errors` 均 0。

### 3.1 主库为什么是 0.200：记录在库里，但检不出来

| 判据 | 实测 |
| --- | --- |
| 记录确实存在 | `memories show 3d82ca71…` 返回 `CanPad 存储 ENOSPC 第二批通过：SQLite 满盘后需释放空间重开…` |
| 精确串检索 | `memories search SQLITE_IOERR_TRUNCATE -n 20` → 该记录**不在 20 条内**（`search_mode=fast_bm25_vector`） |
| 拉丁短语检索正常 | `memories search "resource-handoff-v1 storage abstraction"` → 目标记录 **rank 1** |

⇒ 不是"整条词法层死掉"，而是**带下划线的标识符与中文短词这一类**检不出来（`exact_identifier` 1/5、`chinese_short` 0/5）。这也解释了 10-19 为什么把 `semantic=0.000` 当阻断：真正弱的是**精确/词法**那一半，而当时的表格没有这一列。

**必须随结论一起声明**：这 40 条 case 是**按影子能力写出来的**（锚点来自影子记录的自述短语），对主库天然不利；它证明的是"在我们关心的问句上影子更好"，**不是**"主库对所有检索都更差"。

### 3.2 一个尚未统一的口径（留给 B1）

同一天两个"project coverage"：

| 来源 | 值 | 定义 |
| --- | --- | --- |
| `shadow_status.contract` | **0.249**（`project_assigned 666 / active 2675`） | 按契约面统计 |
| `shadow_eval` 的 `shadow.project_coverage` | **0.2089** | 由它自己的 facet 汇总反推（`1 - unassigned/active`） |

两个数都对"覆盖率"这个词负责，差的 4 个百分点不是噪声。**B1 的第一件事就是把这个口径统一到一个定义上**（否则 B1 的"≥80%"验收没有意义）。本次报告以 `shadow_status.contract` 为准。

## 4. 接线状态（纠正一处过期文档）

影子 MCP **已于 2026-10-19 接入 `desktop` profile**（`~/.dsh/profiles/desktop/cordis.patch.yml` 的 `insert` 行 `mcp-los-memory-shadow`）。2026-10-21 在本会话（desktop）实测：`shadow_status` / `shadow_search` / `shadow_compare` 均可用，`compare` 也能落盘。

因此 `docs/current/CURRENT_STATE.md` 里"desktop 没有影子 MCP，所以 `AGENTS.md` 的'先查影子'在桌面会话不可执行"这一段**已过期并已改**：现在的形态不是"不可用"，而是"没人用"。

## 5. 对批次计划的调整（依据上面的数据）

| 项 | 原写法 | 改为 | 原因 |
| --- | --- | --- | --- |
| B0 交付 1 | 重做 `semantic_paraphrase` 10 条 | **改为"给类别补两把判据"**（锚点 rank + 主库分数/位置一致性） | 记录 41/41 可读，语料不需要重做 |
| B0 验收 | 语义类"至少一个后端非零" | **改为 `record_missing == 0` + 两后端绝对值落表** | 主库在该类处于地板，要求"非零"等于要求我们替它修排序 |
| B2 验收 | 语义类 ≥ +10pp | **+10pp，或基线处于地板时绝对 recall@5 ≥ 0.8** | 相对增益在地板基线上不可测 |
| **B6 门槛** | 留出集不劣于冻结基线 | **照旧，但要写明现在已满足（0.771 vs 0.200），真正的门槛是绝对线 + 语义类 + 写入面** | 相对门槛在一个 0.200 的基线上没有约束力 |
| **B1 第一件事** | 补 project 字段语义 | **先统一 coverage 口径**（§3.2） | 同一天两个值，B1 的 ≥80% 无从判起 |
| §8 三项 | 待定 | 已决（ONNX 进程内 / B3–B6 在 M3 / CLI+台账） | 见设计文档 §8 |

**一个计划层面的重估**：原计划的隐含前提是"主库读得比我们好，所以要先把读补上再谈写"。实测把这个前提推翻了——在我们关心的问句上影子已经 0.771 vs 0.200，缺的只有**语义（两端皆 0）**与**关系跳转（0.600）**。因此后续的**主要风险从读侧转移到写侧**（B3/B7：幂等、撤回、擦除、恢复），B2 的价值是补齐语义而不是"追上主库"。

## 6. 复跑

```bash
python3 scripts/eval_human_check.py --self-test
python3 scripts/eval_human_check.py --show-only
python3 scripts/eval_human_check.py --category semantic_paraphrase --negative-control
python3 -m pytest tests/unit/test_eval_human_check.py -q
python3 scripts/shadow_eval.py --refresh-shadow --scope native --backend both --freeze \
  --out ~/.local/share/los-memory-shadow/eval/runs/$(date +%Y%m%d)-b0.json
```

## 7. 诚实边界

- 语义类只有 **5 条**：结论说的是"这 5 条的量级"，不是"8 类的总体质量"。
- 主库侧 3/5 缺失是在 **depth 20** 上判的；更深的排名未测（`nmem` 单次查询约 13–25 s，40 条一轮就是十几分钟）。
- `memories show` 可读 **证明记录在库里**，不证明"任何检索路径都找不到"——`nmem fs grep` 在本机对**已知存在**的串也返回空（见 §7.1），所以它没有被用作判据。
- `--freeze` 冻结的是**主库当次的 top-5**，不是"正确答案"；它只用于回答"相对基线有没有变差"。

### 7.1 一个被排除的仪器（记下来免得后人重试）

`nmem fs grep <串>` 对**能从 `memories show` 读到的记录正文**同样返回 `{"matches": []}`（正对照：`已实现但未接线` 也返回空），而 `fs ls /memories` 正常。也就是说它当前不是可用的"精确内容检索"面，**不能**用来证明"主库找不到"。
