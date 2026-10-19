# P2-02 写入运行时：就绪判定（2026-10-19）

状态：**设计已完成，实施仍阻塞**。本文不改 §1 的 I1–I7 不变量，也不重新打开
`p2-write-path-minimal-loop.md` §7 已判掉的五个问题；只回答“现在能不能开工 P2-02”。

## 1. 设计面已齐

- 契约：`docs/design/p2-write-path-minimal-loop.md`（身份/幂等/修订/冲突/撤回/outbox）
- 决策：该文 §7（2026-10-10）已逐条判掉五个开放问题，含依据、四轴影响、反证条件
- 负向测试：N1–N11 已列；P2-01 契约层已落地一部分
- 状态机：`service.sqlite3` 独立 schema，不导入旧库

⇒ **不需要再写一份 L2 设计**；B2 的设计项可以按“已有设计 + 本次就绪判定”收口。

## 2. 数据面仍未过门

2026-10-19 实测（`scripts/shadow_eval.py --refresh-shadow --backend shadow --scope native`）：

| 指标 | 值 | 对 P2-02 的含义 |
|---|---|---|
| overall Hit@5 | 0.771 | 检索整体可用 |
| semantic_paraphrase | **0.000** | 语义改写类无区分度；“写进去能被正确检索到”无法在该类上验收 |
| project coverage | **25.85%**（644/2491） | 身份/范围契约的 `project_id` 仍大面积缺失 |
| claim_undeclared | **1469/2491** | `claim_status` 默认值未清，修订语义无法可靠分类 |
| relational_multihop | 0.600 | 多跳仍弱，不影响 L2 写入契约，但影响 L3 |

P2-02 的验收语句是“写进去的事实能被正确检索到”。在 semantic=0.000、project=25.85%
的状态下开工，测到的会是**数据缺口**，不是写路径。按 `p2-write-path-minimal-loop.md`
§7.6 的反证条件，这属于**外部阻塞**，不得降低门槛。

## 3. 判定

**P2-02 不开工。** 阻塞项不是设计，而是：

1. semantic_paraphrase 评测集要有区分度（当前两边都是 0.000）；
2. project 映射/claim_status 的数据契约要补齐到可验收比例；
3. 主库 recall probe 的 `stale_projection_suspected` 若仍存在，先修主库索引，不用写入路径掩盖。

## 4. 下一步（按顺序）

1. 重做 semantic_paraphrase 的 10 条 case（人工标注 expected IDs；先证明人能找到，再测模型/检索）；
2. 补 project 映射与 `claim_status` 的来源规则（不接受按目录名猜）；
3. 重跑 40 例，记录 project 覆盖率与 semantic 子类；只有 semantic 有非零区分度才进入 P2-02；
4. P2-02 第一版只在隔离库跑 N1–N11，不写现网、不建第二主库。

## 5. 回滚

本文只是就绪判定；若后续决定开工，P2-02 的隔离库可直接删除，不影响 Nowledge/影子。
