# B1 · 记录契约：一个口径、原因可归因、80% 目标被数据否掉（2026-10-21）

> 归属：`docs/design/nowledge-replacement-batches.md` 的 **B1 批次证据面**。
> 只读审计 + 派生投影重建（`record_facets` 是派生表，可整表重建）；不改 `records` 语义、不写主库。
> 私有语料不进仓库：本文件只出现聚合数字与标签名。

## 0. 四句话结论

1. **同一天两个 coverage 数字（0.249 / 0.2089）是同一份数据的两种算式**，已在代码层合成一个定义（`memory_tool.shadow.coverage`），评测器改为调用它。
2. **覆盖率现在必带原因**：683 assigned / 579 policy_excluded / 514 key_label_unmapped / 821 unclassified / 56 no_labels / 27 multi（合计 2680 = 活跃记录数）。
3. **`project ≥80%` 在当前上游标签下不可达**：可命名上限（ceiling）**45.67%**。这不是"还没干活"，而是语料里 2,079 个不同标签中绝大多数是主题标签、且日常写入走 `label_daily`/`label_memory-evolve`（占 1,019 / 525 条）。要过 80% 只能让**写入侧**带上 project（B3），从我们这侧再贴标签是造假。
4. 因此 B1 的门槛按数据改为：**每条活跃记录都带一个已声明的原因 + ceiling 已测量 + 无 claim 冲突 + 投影不是陈旧态**；80% 挪到 B3 的验收里。

## 1. 两个数字的来源（必须是同一个定义）

| 路径 | 旧算式 | 旧值 |
| --- | --- | --- |
| `memory_tool/shadow.py::coverage`（`status.contract`） | `project NOT IN ('unassigned','multi')` / **active** | **0.249** |
| `scripts/shadow_eval.py::shadow_facts` | `1 - unassigned / active`，其中 unassigned 取自**全表**（含 inactive）且 **multi 被算作已分配** | **0.2089** |

算术核对：全表 2811 行、active 2676；inactive 里 134 行 project=unassigned ⇒ `1 - (1983+134)/2676 = 0.2089`。两个算式各自"说得通"，但它们回答的不是同一个问题——**这就是为什么"覆盖率"这个词在 10-21 之前不可审计**。

修法：评测器 `from memory_tool.shadow import coverage`，并把 `projects`/`kinds` 的统计也改成只算 active。模块自测 `tests/unit/test_shadow_contract.py::test_coverage_has_one_definition` 钉住两条路径同值。

## 2. 原因词表（每个活跃记录恰一个）

| reason | 含义 | 实测 |
| --- | --- | --- |
| `assigned` | 命中注册表里的项目标签（含别名 `label_deepseek-harness`→`deepseek-harness`） | **683**（25.49%） |
| `multi` | 同时带 ≥2 个注册项目（真属性，不按标签顺序裁决） | 27 |
| `policy_excluded` | 所有标签都是已登记的**主题**标签（扩展后 60 个） | 579 |
| `key_label_unmapped` | 唯一项目线索是 `label_key-<hash>`，而映射尚未由人声明 | 514 |
| `unclassified` | 至少一个标签从未被分类（长尾，1,957 个不同标签） | 821 |
| `no_labels` | 完全无标签 | 56 |

**ceiling**（今天最多可命名）= `assigned + multi + key_label_unmapped` = **1224 / 2681 = 45.65%**。

`label_key-<hash>` 是 DSH 的**项目级关键记忆**标签；把 hash 解析成项目是一次人工裁决（`~/.dsh/memories/projects/<hash>/`），因此 `KEY_LABEL_PROJECTS` 留空并由审计把它们单列——**看得见的项目信号，不能被悄悄折成 unassigned，也不能靠猜**。

## 3. claim_status 的来源规则（实测证明"优先级"是精确的）

活跃记录里：**1,116 条两处都有且取值一致，1,564 条两处都没有，0 条不一致**（top-level 与 `metadata.claim_status` 是同一字段随写入路径移动了位置）。因此规则写成：record 优先 → 回退 metadata → 否则 `undeclared`；**一旦两处不一致，记 `claim_source='conflict'` 而不是择一**（`contract.claim_conflicts` 是一等输出，审计把它当硬失败）。当前 0 冲突。

同一批数据里 `claim_undeclared=1564`（56.4%）——仍是最需要被解释的字段，但**它不是"未知被当成 asserted"**，语义上就是未声明。

## 4. 引用透传（只存引用，不建图）

`source_thread` 与 `source_grounding` 现在进 `record_facets`：`thread_id`（1,160 条有）与 `has_grounding`（1,096 条有）。用途是 B4（结论必须可回读）与 B5（删除要能交代引用），本批**不做**任何图结构。

## 5. 审计与三态

`scripts/shadow_contract_audit.py`（只读，`mode=ro`）：

```bash
python3 scripts/shadow_contract_audit.py --strict            # 0 一致 / 1 真不一致 / 2 判不了
python3 scripts/shadow_contract_audit.py --strict --json
```

- `--strict` 的硬判据：原因词表已知、直方图之和 == 活跃记录数、投影非陈旧（无 `unpopulated`）、`claim_conflicts == 0`、注册表非空。
- **`coverage-target` 默认只报告不拦人**（`--fail-under-target` 才拦）——理由见 §7：用一个当前不可达的阈值当门禁，只会训练出"忽略这条门禁"。
- **exit 2 是"判不了"**：在 B1 之前的快照上实测 `record_facets lacks claim_source, has_grounding, reason, thread_id (run los-memory shadow reindex)`，退出码 2。少了它，"读不到新列"会被读成"覆盖率是 0"。

## 6. 迁移与部署实测

`record_facets` 新增 4 列（`reason`/`claim_source`/`thread_id`/`has_grounding`），迁移用 `PRAGMA table_info` 守卫；旧行的默认值是 **`'unpopulated'`**，审计把它报成 `project_reason_stale` 而不是当作一个真实类别。

| 步骤 | 实测 |
| --- | --- |
| 本地重建副本（2811 行） | `reindex` 19.4 s |
| 部署 M3（内容寻址 release `b1edb54d05f0ce922eae`） | 四个兄弟 launchd 作业一并重指向 |
| 线上 `reindex` | **14.0 s**，2816 行 |
| 线上 `shadow_contract_audit --strict` | `OK`，exit 0 |
| 线上 sync（kickstart） | `checked 100 / changed 1 / errors []`，`project_reason_stale 0` ⇒ 新写入的记录立即带 reason |

## 7. 门槛按数据改写（这是本批最重要的产出）

数据说：`assigned 683` + 可回收的两块 `key_label_unmapped 514`（人工映射）+ `unclassified 821`（1,957 个长尾标签）⇒ 即使把能做的都做了，也到不了 80%；**ceiling 45.65% 就是今天的天花板**。而 1,019 条 `label_memory-evolve` 与 525 条 `label_daily` 说明：日常写入根本不带项目标签。

因此：

| 项 | 原写法 | 改为 |
| --- | --- | --- |
| B1 验收 | `project ≥80%` | **每条活跃记录带已声明原因；ceiling 已测；`claim_conflicts=0`；无陈旧投影**。80% 移到 **B3**：写入侧必须带 `project`，并给出"新写入记录的 project 覆盖率 ≥95%" |
| 长期目标 | 80% | 保留 80% 作为**B6 读切换**前的总门槛，但它的主语从"镜像覆盖率"变成"写入侧带 project 的覆盖率" |

## 8. 复跑

```bash
python3 -m pytest tests/unit/test_shadow_contract.py -q            # 9 例，含负向控制
python3 scripts/shadow_contract_audit.py --strict
python3 - <<'PY'                                                    # registry 移除的负向控制
# 见 tests/unit/test_shadow_contract.py::test_removing_the_registry_lowers_coverage_and_fails_the_audit
PY
```

## 9. 诚实边界

- `unclassified` 的 821 条不是"数据脏"，是**我们没有把 1,957 个标签逐个判定为主题还是项目**；`label_popup×28` 这类就是典型主题标签，批量把它们登记成主题会让数字变好而不让能力变好——本批刻意不做批量认领。
- `coverage_ceiling` 是**今日标签下的最大值**，不是可达目标：它把 `key_label_unmapped` 算作可命名，而这一步需要人工逐条确认 hash↔项目。
- 本批没有触碰 `records` 语义、FTS 语义与检索排序；`reindex` 重建前后 `records` 行数与 digest 不变（只重建派生表）。
