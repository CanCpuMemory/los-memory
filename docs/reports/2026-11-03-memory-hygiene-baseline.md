# 记忆语料卫生基线（2026-11-03，只读）

状态：**实测报告**。本文件只记录"现在是什么样"，不做任何修改；方案与判据见
[记忆整理与清理方案](../design/memory-hygiene-and-consolidation.md)（下称"方案"）。

## 1. 目的

方案的第一条纪律是**先量后定**（沿用 [保留策略](../design/retention-policy.md) 的做法：那次实测把
"删掉不活跃记录能省空间"的结论反转了）。本报告回答四个问题：

1. 真正在写的记忆语料有多大、由什么组成？
2. 冗余到底在**条目**上还是**轨道**上？
3. 现有生命周期标记（`[id:…] [slot:…] [supersedes:…] [deprecated…]`）用到什么程度？
4. 现有"健康"数字里，哪些是**空分母的零**？

## 2. 方法与可复跑命令

仪器：`scripts/hygiene_scan.py`（P0，只读）。语料：`~/.dsh/memories`（DSH 记忆存储 ——
它的写入经 `mem-sync-evolve.py` 进入 Nowledge，是"实际在写"的那一层）。

```bash
python3 scripts/hygiene_scan.py --self-test                     # 12 条断言，含 3 组负向控制
python3 scripts/hygiene_scan.py --corpus dsh-md:~/.dsh/memories # 人读视图
python3 scripts/hygiene_scan.py --corpus dsh-md:~/.dsh/memories --json --out /tmp/hygiene.json
```

判定口径：

- **轨道**：`MEMORY.md` / `USER.md` / `KEY.md` / `daily/*.md`（`TODOS*.md` 是另一套 schema，排除）。
- **条目**：以只含 `§` 的行为分隔；头部 token 依次为 `[id:…] [slot:…] [supersedes:…] [deprecated…] [日期]`。
- **冗余**：同轨按"归一化正文"（剥头部 token、去空白）判精确重复；跨轨按**条目集合 hash** 判轨道重复；
  近似重复用 5-gram Jaccard ≥ 0.8（倒排索引挡住无区分度的 gram，否则 1,474 条轨道是 ~110 万次交集）。

## 3. 实测

环境：M1 本机，语料 54 MB，扫描耗时 **47.8 s**（135 轨 / 7,594 条，单线程）。

| 指标 | 值 | 说明 |
| --- | --- | --- |
| 轨道数 | **135** | 含 `daily/` 与 `projects/*` |
| 条目总数 | **7,594** | active 7,590 / deprecated 4 |
| 带身份证 `[id:…]` | **302（4.0%）** | 集中在 KEY 轨（148×2）与少数重写过的条目 |
| 槽位 `[slot:…]` | **5** | 全局 `MEMORY.md` 的 slots = **0** |
| 正文自称"修正/取代/已过时" | **212** | 机械 `[supersedes:id]` 边：**0** 条（无未落实、也无已落实） |
| 生命周期全空的轨道 | **50** | 条目 ≥20 但 id/slot/deprecated 全 0 |
| 同轨精确重复 | **2** | `daily/2026-08-16.md` 内两条；条目级重复极少 |
| 近似重复（≥0.8） | **1 对** | `MEMORY.md` 两条"跨项目可复用两条"（0.81） |
| 轨道重复 | **14 组 / 多出 2,378 条（31.3%）** | 见下表 |

### 3.1 轨道重复明细（前 8 组，按多出条目数）

| 多出条目 | 轨道（逐字节相同的两份） |
| --- | --- |
| 1,476 | `projects/2d4994d462af/MEMORY.md` = `projects/faab4b6033b2/MEMORY.md` |
| 336 | `projects/2d4994d462af/KEY.md` = `projects/faab4b6033b2/KEY.md` |
| 200 | `projects/763897047cd2/MEMORY.md` = `projects/c08ebee3a588/MEMORY.md` |
| 98 | `projects/9f2343a585ad/MEMORY.md` = `projects/fd2b32b8ea91/MEMORY.md` |
| 88 | `projects/0e6891157526/MEMORY.md` = `projects/5539d0bb53c5/MEMORY.md` |
| 80 | `projects/4ac17af9cd18/MEMORY.md` = `projects/bbb8a7223e68/MEMORY.md` |
| 36 | `projects/763897047cd2/KEY.md` = `projects/c08ebee3a588/KEY.md` |
| 22 | `projects/9f2343a585ad/KEY.md` = `projects/fd2b32b8ea91/KEY.md` |

验证方式：直接比对文件 `sha256` 与 scan 的"条目集合 hash"两条独立判据，结论一致
（`35505de551bb` 那一对 `MEMORY.md` 都是 3,102 行）。成因是同一项目经两个 cwd 别名各建了一份轨道
（本仓 `path-alias` 一类的旧路径问题），**不是**内容重复写入。

### 3.2 判据分布（scan 输出）

| bucket | safety | count | 解释 |
| --- | --- | --- | --- |
| `track_duplicate` | auto_safe | 14 | 7 对项目 ×（MEMORY + KEY） |
| `exact_duplicate` | auto_safe | 2 | 同轨同一正文两份 |
| `claim_without_edge` | review_only | 212 | 声明取代但没有机械边 |
| `lifecycle_blind` | review_only | 50 | 轨道完全没有标记 |
| `near_duplicate` | review_only | 1 | Jaccard 0.81 |

## 4. 这些数字决定了什么

1. **先合并轨道，再谈删条目**：31.3% vs 0.03%。合并轨道不损失内容，且可拆回；删条目是最后手段。
2. **`sameSlotMultiActive = 0` 不能读成健康**：全局槽位是 0。按本仓既有纪律（`slots > 0` 才认那两个 0），
   这里必须报"空分母"，而不是报绿。
3. **瓶颈是"意愿 → 机械后果"这一步**：212 条自述修正 vs 0 条机械边。写侧已实现
   `[supersedes:id]`（dsh-memory-evolve `lib/lifecycle.js`），但**没人执行**。
4. **判据要按结构走**：真正可自动化的是 D1/D2/D3/D4（集合 hash、边、槽位、归一化 hash）；
   212 条文本候选里大概率混着"只是提到过"的条目，只能进复审队列。
5. **`daily/` 是证据面**：最大单轨 172 条/日、30 天已千条级；本次不设自动归档（方案 §12）。

## 5. 本报告**没有**测的

- **召回质量**：卫生动作是否让 Hit@5 退化，要用 B0 的冻结集仪器另测（方案 §10 `recall_regression`）。
- **衰减分数**：需要"召回计数"，本机没有该字段；P1 才引入，且只用于排序与复审优先级。
- **Nowledge 侧与影子侧**：两边各有自己的删除/保留语义（Nowledge 有 delete/archive/forget/deprecate/
  supersede/evolves），本报告的 7,594 条是 DSH 存储的计数，不能当成主库条数。
- **条目语义正确性**：本仪器只判结构，不判"这条事实是否仍然成立"——那属于 D10（外部探针）与人工复审。
