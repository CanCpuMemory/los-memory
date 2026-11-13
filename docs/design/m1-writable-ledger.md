# M1 写入运行时：可写台账 + `mem` CLI + evolve 导入（2026-11-13）

状态：**已实现**。范围 = 写库、写/读 CLI、语料导入、检索。**不含**注入面（P2）与切换（P4），见 §7。

## 0. 与既有文档的边界（先读这节，否则会误判本层违约）

| 既有物 | 本层怎么处理 |
| --- | --- |
| `memory_tool/service_contract/` | **原样保留**：契约层仍无运行时、不建库、不被 CLI 导入。本层是新加的一层，不 import 它，也不改它的测试期望 |
| `memory_tool/shadow*.py` | **未改**（读写路径、schema、语义都不动）。只复用其语料词汇的理解，不 import 其运行时 |
| 三个 profile 库（codex/claude/shared） | 不读不写。`mem` 在**配置解析之前**分派，profile 参数对它无意义（有负向测试） |
| Nowledge | 不写、不读，不参与本层 |

⚠️ **与 `p2-write-path-minimal-loop.md` 的字面冲突（需 Lead 裁决，见 §7）**：该文 I1/§8 写"正式写入仍只走 Nowledge、不建 `service.sqlite3`"，其对象是 **Nowledge 主库替换线（B0–B7）**。M1 写的不是 Nowledge 主库，而是 **DSH 记忆语料 `~/.dsh/memories`**（当前由 `dsh-memory-evolve` 写）。所以本层满足 I2（独立库/独立 schema/不导入旧库），但确实新建了 `service.sqlite3`，与该文 §8 字面相反。

## 1. schema（`SCHEMA_VERSION = 1`）

路径：`~/.local/share/los-memory/service.sqlite3`；`MEMORY_SERVICE_DB` 覆盖；`--db` 再覆盖。WAL + `foreign_keys=ON`。

```
units(id PK, track, project, branch_scope, scope_key, state, slot, superseded_by,
      created_at, updated_at, day, origin, origin_key)      -- 扩展列：day/origin/origin_key
revisions(revision_id PK, unit_id, revision, content, digest, claim_status, created_at)
events(id PK AUTOINCREMENT, kind, unit_id, revision_id, actor, at, payload_json)  -- 只追加
units_fts(trigram) / cjk_bigrams(term, unit_id)              -- 派生，可重建
```

- `track ∈ {memory,user,key,project,daily}`；`state ∈ {active,archived,deprecated}`。
- `scope_key = track|project|branch|day`（值里出现 `|` 一律拒绝，避免作用域歧义）。
- `revisions.content` 存**整条原文**（含 `[id:…]`/`[date]` 等头标记），可回读；`digest = sha256(canonical)`。
- `events` **只追加**：本包不 UPDATE/DELETE 它（有测试对比写入前后的历史行）。
- `origin ∈ {cli, import:evolve}`；`origin_key` 唯一索引 —— 导入幂等靠它，不靠文本相似度。

## 2. CLI 形态（`los-memory mem …` / `python3 -m memory_tool mem …`）

| 子命令 | 关键参数 |
| --- | --- |
| `mem add` | `--track --project --branch --slot --day --content/--content-file - [--claim-status] [--dry-run]` |
| `mem replace` | `--id --content/--content-file -` |
| `mem supersede` | `--id --content/--content-file - [--slot]` |
| `mem archive` | `--id [--reason]` |
| `mem list` | `--track --project --branch --day --state {active,archived,deprecated,all} --limit --offset` |
| `mem expand` | `--id`（unit + 全部 revisions + events） |
| `mem search` | `<query> --track --project --limit --include-archived --rebuild-index` |
| `mem import-evolve` | `--root ~/.dsh/memories --only <tracks> --project-map <json> --samples N [--dry-run]` |
| `mem init` / `mem status` | `--db` |

输出：默认 JSON（`{"ok","command","dry_run","db","simulated_fresh","data"}`；失败为 `{"ok":false,"error":{"code","message"},"details"}`），`--format text` 为人类视图。

**退出码是三态，不是 `errors.py` 的 0–5**（两套并存、不混用；后者是 profile 命令的错误码）：

| 码 | 含义 | 例 |
| --- | --- | --- |
| `0` | 判完了（含"空结果"） | 检索无命中、`list` 空、`dry-run` 成功 |
| `1` | 真否定：请求成立但目标不允许 | `--id` 不存在、`replace` 打在 archived 上 |
| `2` | 判不了：缺库/未初始化/输入不可用/请求不成立 | 空 content、缺 `--project`、语料根不存在、库缺失 |

失败路径最多到 `2`，**绝不返回 0**（工具崩了不能读成"没事"）。

**dry-run 语义**：库已存在且已初始化 ⇒ 在真库内 `SAVEPOINT` + `ROLLBACK`（"这次会对这个库做什么"）；库缺失或为空文件 ⇒ 在 **scratch 内存库**上模拟，并回报 `simulated_fresh:true`（`--dry-run` 绝不创建它要检查的文件）。
**读写纪律**：写命令可建库；**读命令（list/expand/search/status）绝不建库**（对齐 `admin doctor` 的既有纪律）。

## 3. 写与检索语义

- **幂等**：同 `scope_key` + 同 digest 且仍 active ⇒ 返回同一 id，不产生第二修订（只追加 `add_deduped` 事件）。`origin_key` 命中时，digest 变化 ⇒ **追加修订**而非新建。
- **槽位**（`slot`）：同 scope_key 同 slot 只允许一个 active；新的激活会把旧的置 `deprecated` + `superseded_by=新id` + `slot_supersede` 事件。**不同 project/scope 的同名 slot 互不影响**（负向测试）。
- **取代**（`supersede`）：新建单元 + 旧单元 `deprecated`，链上只有一个 active 头；历史原文仍可由 `expand` 读到。
- **归档**（`archive`）：默认召回（`list`/`search`）排除，`expand` 仍可读。
- **修订追加**（`replace`）：只对 active 单元；同内容替换=零写入。
- **检索**：每词按能力选路 —— ≥3 字符走 `units_fts`(trigram)，2 字符 CJK 走 `cjk_bigrams`，其余 `scan`。索引**只收窄候选**，Python 子串判定保持权威 ⇒ 召回不会比全扫差（索引缺失时降级为 scan，而不是返回空）。`meta` 报 `paths/usable/mode/scan_terms/candidates`，让"没命中"与"索引没建"可分。评分 = 命中词数 + 出现密度（非学习型相关性）。
- 索引索引的是**正文 + 人类可读头标记（`[summary:…]`/`[<project>]`）**，不含 `[id:…]`/日期，避免按元数据命中。

## 4. 导入映射（evolve markdown → track）

| 语料 | track | project / day |
| --- | --- | --- |
| `MEMORY.md` | memory | — |
| `USER.md` | user | — |
| `projects/<hash>/KEY.md` | key | `project = <hash>`（= `sha1(字面 cwd)[:12]`，可复核；不猜 slug） |
| `projects/<hash>/MEMORY.md` | project | 同上 |
| `daily/YYYY-MM-DD.md` | daily | `day = 文件名`（显式排除 `*.todo.md`） |
| `TODOS*.md`、`projects/<hash>/TODOS.md` | **不导入** | 计数 + 字节数写进报告（待办归宿主插件） |

- **`§` 只有独占一行才是分隔符**：行内 `§`（章节引用）保留。实测语料 9,617 个独立 `§` 行、**867 行含行内 `§`**；`text.split("§")` 会造出碎片。
- **文件身份是 `(st_dev, st_ino)`**：symlink 目录与硬链接都只读一次，别名分组写进报告（实测 7 组 symlink + 14 组别名，含 `projects/faab4b6033b2 → 2d4994d462af`）。
- **导入身份**：有 `[id:8hex]` ⇒ `evolve:id:<track>:<project>:<id>`（上游改文 ⇒ 同单元新修订）；无 id ⇒ `evolve:hash:<track>:<project>:<rel>:<digest>[#n]`（同文件真重复保留并记 `duplicates_kept`）。
- **生命周期 token**：`[slot:]` → 槽位交接；`[supersedes:xxxx]` → 目标置 deprecated（解析不到则计 `supersede_unresolved`，不静默）；`[deprecated(:xxxx)]` → 该单元直接 deprecated。
- **只增不删**：第二遍必须 `created=0`；上游删除不由本层处理（P4 对账）。

## 5. 实测（2026-11-13 21:03，语料是活的，数字带时间戳）

```
python3 -m memory_tool mem import-evolve --root ~/.dsh/memories --dry-run
  memory 340 / user 4 / key 768 / project 2772 / daily 2808 / 合计 6,692 条（184 文件）
  deprecated_imported 19 / supersede_edges 19 / unresolved 0 / duplicates_kept 0
  TODOS 跳过 16 文件 / 344,587 B；别名 14 组；dry-run 未创建任何文件（7.6 s）
python3 -m memory_tool mem import-evolve --root ~/.dsh/memories --db <临时库>
  第 1 遍 created=6692（5.8 s）；第 2 遍 created=0 / unchanged=6692（0.46 s）
  status：units 6692 / revisions 6692 / events 6730 / fts 6692 / bigram 557,251 / 128.7 MB
```
检索实测（同一临时库）：`superagent` 491 命中·trigram；`记忆` 195·bigram；`los-memory` 51·trigram；多词 `superagent 派发` 104·混合路；单次 CLI 0.16–0.24 s。`网关端口` 0 命中 —— 已用 `grep -F` 复核语料确无该连续串（不是漏检）。

## 6. 测试

`tests/unit/test_ledger_{store,import,cli}.py`：**44 条**，`python3 -m pytest tests/unit/test_ledger_store.py tests/unit/test_ledger_import.py tests/unit/test_ledger_cli.py -q`。
负向控制（每条都验证"守卫坏掉就会红"）：同 slot 第二个 add 必须把旧条 deprecated 且 `same_slot_multi_active==0`；**同名 slot 换 project 不得被弃**；重复 add 幂等且 revisions 不增；supersede 链只剩一个 active；archive 后 list 不含而 expand 可读；replace 打在 archived 上被拒；空 content ⇒ exit 2；库缺失 ⇒ exit 2 且**不创建文件**；dry-run 前后 events/revisions 不变且缺失库不被创建；events 历史行逐字节不变；`§` 行内不切（并断言 naive split 会切）；导入第二遍 created=0；`[supersedes:]` 落成真边；改文变修订不变单元；symlink 别名只导一次；TODOS 计数且零导入；`--only` 生效；语料根缺失 ⇒ exit 2；`mem` 不落 profile 库（重定向 HOME 验）。

## 7. 未做 / 需要 Lead 决策

**未做（明确留给后续阶段）**：注入面 P2（DSH 侧读/写入口、`memory` 工具接管、Working Memory/bundle、提示注入与披露）与切换 P4（evolve 停写、水位、语料↔库对账、双写与回退剧本、fail-closed 门禁）。本层目前是"能写、能查、能导入"的底座，**没有任何调用方**。

需要裁决的三点：
1. `p2-write-path-minimal-loop.md` §8「不建 `service.sqlite3`」与本次实现字面冲突 → 修该文状态行，还是把 M1 定位补进 `nowledge-replacement-batches.md`（B3 一栏）？
2. `mem` 的 0/1/2 与 `memory_tool/exit_codes.py` 的 0–5 并存（本文 §2 已写明分工）。是否要把 `mem` 的错误码也登记进 `exit_codes.py` 词表？
3. `project` 目前是 evolve 目录 hash（可复核但不可读）。是否接项目注册表做 hash→slug 映射（现只能 `--project-map` 手工给）？
