# 影子库保留策略（2026-10-10）

状态：**决策记录**。依据 2026-10-10 对现网影子库的只读实测。本文只定义策略与边界；**在现网执行 `VACUUM` 属状态变更，尚未执行**（见 §6）。

起因：`TODO.md` 的"Define a retention policy for inactive `records` rows and `revisions`"一直是待办，而 2026-10-10 起告警新增了一条会**持续触发**的高severity 项（主库陈旧投影），台账开始以每小时 ~330 B 稳定增长，于是"哪里真的占空间"必须先用数字回答，不能凭直觉。

---

## 1. 实测基线

`dbstat` 逐对象占用（`shadow.sqlite3` = 174.7 MB）：

| 对象 | 占用 | 性质 |
| --- | --- | --- |
| `records_fts_data` | **60.2 MB** | 派生（FTS5 trigram 索引） |
| `cjk_bigrams` + `cjk_bigrams_space` | **21.1 MB** | 派生（2 字 CJK） |
| `sqlite_*` 自动索引等 | ~1.2 MB | 结构 |
| `revisions` | 16.1 MB | 历史（**每修订存全量快照**） |
| `records` | 9.1 MB | 内容源（2432 行，含 118 个 tombstone） |
| `records_fts_content` | 3.8 MB | 派生 |
| `records_fts_idx` | 0.2 MB | 派生 |
| `record_labels` / `record_facets` | 0.9 MB | 派生 |
| **freelist（空闲页）** | **58.9 MiB（15080 页）** | **空页，零数据损失** |

行数：records 2432 = active 2314 + inactive 118；`revisions` 每记录最多 5、平均 1.73（历史很浅）。

## 2. 两个把结论反转的发现

**发现一：可回收空间比"能删的东西"大一个数量级。**
`VACUUM` 可无损失回收 **58.9 MiB**；而删掉全部 118 个 tombstone 只能省约 **0.4 MB**（它们住在 9.1 MB 的 `records` 里，118/2432 ≈ 4.9%）。也就是说，一个"清理不活跃记录"的策略能拿回的是**百分之零点几**，代价是毁掉删除可审计性与上游恢复的复活能力（影子对 tombstone 的设计意图正是保留）。

**发现二：库体积主要是派生结构，不是历史。**
派生结构（`records_fts*` + `cjk_bigrams*` + facets/labels）合计 **≈85 MB**，占文件近一半；`revisions` 只占 16.1 MB（9%）。所以"清理历史即可瘦身"这个未经验证的假设不成立，**先量后定**是本策略的第一条纪律。

## 3. 策略（分层，按收益排序）

### 层 0 — 空间回收（零数据损失，收益最大）

- `records_fts` 执行 FTS5 `'optimize'`：合并段 b-tree，安全、幂等、不改任何行。
- `PRAGMA freelist_count` 作为**可回收量的常规指标**（已进 `shadow status` 的 `storage`，见 §5）。
- `VACUUM` 仅在显式 `--vacuum` 时执行：它需要磁盘上一份完整副本与排他锁，所以是**被请求才做**，不是默认行为。实现为 `shadow compact [--vacuum]`。

### 层 1 — 派生结构：不设保留期，只设重建契约

`records_fts` / `cjk_bigrams` / `record_facets` / `record_labels` 一律**不设 TTL**：它们可由 `records` 用 `shadow reindex` 完整重建，因此保留期这个概念对它们没有意义——空间的正确工具是层 0 的合并与重写，不是删除行。

### 层 2 — `revisions`：保留，用测量而不是删除来约束

- **不删除修订**。设计文档明确要求 superseded/retracted 保留历史、删除可审计；删修订等于删掉"为什么变成现在这样"。
- 增长与真实变更量成正比（实测 14 天 884 次 changed ≈ 63/天 → 约 0.24 MB/日、~87 MB/年），量级可接受，因此策略是**度量 + 阈值告警**，不是自动裁剪。
- 若将来真的需要压缩：正确方向是给历史修订换**存储形态**（增量/压缩），而不是丢行。**本轮不做**，也不预设它一定会做。

### 层 3 — inactive `records`：无限期保留

- 保留全部 tombstone（上游恢复可复活、删除可审计）。策略只要求**报告计数**，不要求删除。
- 已生效的约束是"不每轮重探"（`TOMBSTONE_RECHECK_SECONDS = 24h`）——那约束的是**流量与报告口径**，不是存储。

### 层 4 — 日志与台账：有界，且已有工具

- `sync.out.log` / `compare-drain.out.log` / `recall-probe.out.log` 由 maintenance job 的 `rotate_log` 覆盖（上限 8 MiB / 保留 5）。
- `alerts.jsonl` **未覆盖**：它是只写的（仓库内无读者），且从 2026-10-10 起每小时增长约 330 B。加进 maintenance 的 `--log` 列表是一行 plist 改动，**属 launchd 变更，待确认**。

## 4. 明确不做

- 不删除 inactive 记录（省 0.4 MB，毁审计）。
- 不裁剪 `revisions` 行。
- 不给派生结构设 TTL。
- 不在 `sync` 路径里顺手 `VACUUM`（它要排他锁与整份副本，会把同步周期拖到不可预测）。
- 不把"库变小"本身当成功指标：层 0 的收益是一次性的，重复执行第二次回收 0 字节（已有测试钉住）。

## 5. 可观测性与验收

- `shadow status` 新增 `storage`：`page_size` / `page_count` / `freelist_pages` / `file_bytes` / `freelist_bytes`。于是"还有多少可回收"是一个**可查询字段**，不再依赖临时 `dbstat`。
- `shadow compact` 返回 `file_bytes_before/after`、`freelist_bytes_before/after`、`reclaimed_bytes`、`vacuumed`，可直接进报告。
- 测试：`tests/unit/test_shadow_compact.py`（6 例）覆盖"回收空闲页且不丢记录""不带 `--vacuum` 就不重写文件""空库是 no-op""幂等（第二遍回收 0）""索引状态在重写前后一致"。

## 6. 现网动作执行结果（2026-10-10，用户批准后执行）

| # | 动作 | 结果 |
| --- | --- | --- |
| 1 | 部署当前代码到 M3 | 完成，发布 `80da24bde45b1711ed8b`。**本轮实现的"兄弟 job 自动对齐"当场生效**：`compare-drain` / `maintenance` / `recall-probe` 三个 job 全部 `changed: true`，一次部署到位（此前这条漂移需要手工对齐三次）。 |
| 2 | `shadow compact --vacuum` | **执行完成，且优于预测**：file 174,710,784 B（166.6 MiB）→ **73,039,872 B（69.7 MiB）**；freelist 59,912,192 B → **0**；**回收 101,670,912 B = 97.0 MiB**。执行前先做了一次异机加密备份（`shadow-20261010T151752Z.sqlite3.enc`，2432 条，`integrity ok` / `readback_ok true`）。 |
| 3 | `alerts.jsonl` 轮转 | **位置修正**：该台账在 M1（告警 job 所在机），不在 M3，所以加进 M3 maintenance 轮转只会永远报 `missing`。已从 M3 撤回，改为**写它的脚本自己封顶**（`cap_ledger()` 复用 `rotate_log`，惰性导入、失败只记录不上抛），不新增第四个 launchd job。 |

**预测偏差（如实记录）**：§2 预测可回收 58.9 MiB（freelist 实测值），实际回收 **97.0 MiB**。多出的约 38 MiB 来自 FTS5 `optimize` 合并段 b-tree 与整库碎片整理——只数 freelist 会**低估**收益。

**执行后验证**：`PRAGMA integrity_check = ok`；records 2432 / active 2314 **未变**；revisions 4219 **未变**；索引 `ready` 2432；`shadow`/`applied_bytes` 走 trigram、`记忆` 走 bigram，均正常命中；sync 下一轮 23:14:48 `checked=100 / missing=0 / errors=[]` 正常。

三项均已于 2026-10-10 获用户批准后执行完毕。回退见 §7。

## 7. 回退

- `compact --vacuum` 不改任何行，失败时 `VACUUM` 自身是事务性的（失败不留半写状态）；真出问题的回退是"用当晚异机加密备份恢复"，与既有备份链路一致。
- 其余各项为配置/代码，回退＝改回 plist 或部署上一发布。
