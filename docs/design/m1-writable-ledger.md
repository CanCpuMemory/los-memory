# M1 写入运行时：可写台账 + `mem` CLI + evolve 导入（2026-11-13）

状态：**已实现**。范围 = 写库、写/读 CLI、语料导入、检索。**不含**注入面（P2）与切换（P4），见 §7。

## 0. 与既有文档的边界（先读这节，否则会误判本层违约）

| 既有物 | 本层怎么处理 |
| --- | --- |
| `memory_tool/service_contract/` | **原样保留**：契约层仍无运行时、不建库、不被 CLI 导入。本层是新加的一层，不 import 它，也不改它的测试期望 |
| `memory_tool/shadow*.py` | **未改**（读写路径、schema、语义都不动）。只复用其语料词汇的理解，不 import 其运行时 |
| 三个 profile 库（codex/claude/shared） | 不读不写。`mem` 在**配置解析之前**分派，profile 参数对它无意义（有负向测试） |
| Nowledge | 不写、不读，不参与本层 |

**与 `p2-write-path-minimal-loop.md` 的字面冲突：已裁决（2026-11-13，Lead）**——二者的对象不同：该文 I1/§8 管的是 **Nowledge 主库替换线（B0–B7）**，M1 承接的是 **DSH 记忆语料 `~/.dsh/memories`**（原由 `dsh-memory-evolve` 写）。M1 满足 I2（独立库/独立 schema/不导入旧库），**不接管 Nowledge 主库的写入权，I1 仍然成立**。裁决已落成两处状态注：该文 §8 末尾 + `nowledge-replacement-batches.md` B3 一栏（标明"仅 DSH 语料侧的 B3 前置"）。

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
- **`project` 身份 = evolve 目录名 = `sha1(字面绝对 cwd)[:12]`**（稳定、可机械反解）；人类 slug 是**显示层**，由工作区注册表在读取期解析（见 §2.2）。写侧传 slug 或 hash 都会解析成 hash 落库，所以同一事实不会因为导入时带了什么参数而变成两个可寻址名字。
- **`branch_scope`**：`NULL` = 所有分支可见（`evolve` 对"没有 `[branch:…]` 标签"的语义），否则逗号分隔的分支集（`[branch:main,dev]`）。命中判定是**集合相交**（`main` 不会误匹配 `main-old`）。
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
| `mem import-evolve` | `--root ~/.dsh/memories --only <tracks> --project-map <json> --project-map-from-registry [<path>] --samples N [--dry-run]` |
| `mem snapshot` | `--cwd --branch --project --date --max-entries --max-chars --full-threshold-entries --full-threshold-chars --summary-chars --json`（§2.1） |
| `mem init` / `mem status` | `--db`；`status` 另报 `project_slug_assigned/unassigned`（§2.2） |

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

### 2.1 `mem snapshot` 契约（P2 注入插件每回合只调它一次）

成功时**直接输出以下对象**（不套 `{"ok","command","data"}` 信封，字段名定死；失败仍走上面的错误信封）：

```json
{"ok":true,"schema":1,"cwd":"…","branch":"main","project":"2d4994d462af","project_slug":"dsfolder",
 "tracks":{"memory":{"mode":"summary","count":340,"entries":[{"id":"…","summary":"…","content_len":2304}],"truncated":0},
           "user":{"mode":"full","count":4,"entries":[…],"truncated":0},
           "key":{"mode":"summary","count":451,"entries":[…],"truncated":0,"branch_filtered":true}},
 "daily":{"date":"2026-11-13","count":55},"project_log":{"count":1819},
 "slots":{"active":48,"deprecated":19,"same_slot_multi_active":0},
 "degraded":[]}
```

- `count` = 该轨活跃条目**自然条数**；`truncated` = 因 `--max-entries`/`--max-chars` 被裁掉的条数，恒有 `count - truncated == len(entries)`。裁的是**尾部**（保留语料顺序，便于与 evolve 快照逐段 diff）。
- `mode` = **判定结果**：仅当"条目数 ≤ `--full-threshold-entries` **且** 总字符（按**正文**）≤ `--full-threshold-chars`"才 `full`（边界含等号）。它**不改变**裁切行为（裁切只由 `--max-*` 决定），由渲染方决定怎么呈现。
- **`--max-chars` 计的是"实际发射的字符"，不是"存储的正文字符"**：`summary` 模式每条按 `len(summary) + len(id) + "- [id] " 前缀` 计；`full` 模式才按正文长度计（同一函数分支）。**计费对象必须等于发射对象**——2026-11-13 这里出过一个静默丢条缺陷（旧实现一律按正文计费：340 条摘要只发 32.7k 字符，却被 340 条正文约 1MB 判超限，`truncated=130`），已修并配 3 条负向测试（把旧实现放回去前两条必红）。**只丢整条、不留半条摘要**；单条自身超过 `max_chars` 且轨道非空时仍发射 1 条（短答优于空轨），差额计入 `truncated`。
- 默认 caps 已按现网尺度校准：`--max-entries 500`、`--max-chars 120000`（`summary_chars` 160）。实测见 §8：memory 340 条摘要 ≈ 5.3 万字符、key 451 条约 5.7 万 ⇒ 默认下**三轨零截断**，余量约 1.8 倍。
- `entries[].summary` = `[summary:…]` 标签，否则正文首行（受 `--summary-chars` 限制）；**正文永不返回**（省 token，全文用 `expand --id`）。
- `branch` 缺省取 `git -C <cwd> rev-parse --abbrev-ref HEAD`；取不到（无 git / detached HEAD）⇒ **不过滤** + `degraded:["branch_unresolved"]`（fail-open，与 evolve 同策略）。`--branch` 显式给出则不依赖 git，且 `branch_filtered:true`。
- `degraded` 另可含 `project_map_unavailable`（注册表读不到）。**缺库不是 degraded，是 exit 2**（读命令不建库）。

### 2.2 `--project-map-from-registry`（机械反解，不猜）

`project` 的 hash 就是 `sha1(字面绝对 cwd)[:12]`，所以把**工作区注册表里的项目根逐个算 sha1** 即可建 `hash → registry key` 映射（默认 `~/syncfolder/project/los-workspace/.workspace/projects.json`；注册表里 `children` 无 `path` 的按 `<父路径>/<key>` 推导——那是注册表自己的嵌套契约，不是猜测）。**没有目录名兜底**：解析不到的 hash 保持 `project_slug: null`。

显式给了 `--project-map-from-registry` 而文件缺失/结构不认识 ⇒ **exit 2**（判不了）；未给则该映射是**尽力而为**：不可用时报 `degraded`/`project_map.available:false`，命令继续（命令不得因为本机没有工作区登记而不可用，CI 也要能跑）。`status` 用同一映射报 `project_slug_assigned/unassigned`（对 `key`+`project` 轨的去重 project 值计数；hash 或已解析 slug 两种存量形态都算 assigned）。

**已知限制（不是缺陷，2026-11-13 裁决）**：11/73 是**注册表表达力的真实上限**——其余是子目录 cwd（如 `<repo>/dsplugins/x`）或搬迁前的字面路径（如 `~/Downloads/projects/*`），注册表里没有这些"项目根"，机械反解就没有候选可算。**P4 对账时按 `project_slug_unassigned` 计数处理，不判失败**；要提高覆盖只能新增数据源（会话 cwd 台账），那是 P2/P4 的决策，不是 M1 的欠账。

**实测覆盖（2026-11-13）**：注册表 36 个 hash，语料 73 个 project 目录**只命中 11**（`dsfolder` ✓，`project_hash('/Users/echerlos/syncfolder/project/dsfolder') = 2d4994d462af`）。其余是子目录 cwd 或搬迁前的字面路径（如 `~/Downloads/projects/*`），注册表本身表达不了（见下方「已知限制」）。

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

`tests/unit/test_ledger_{store,import,cli,snapshot}.py`：**69 条**，`python3 -m pytest tests/unit/test_ledger_store.py tests/unit/test_ledger_import.py tests/unit/test_ledger_cli.py tests/unit/test_ledger_snapshot.py -q`。

**已修的缺陷（2026-11-13 真库联调发现）**：`_render_track` 的预算按**正文**长度计费，而 `summary` 模式只发摘要 ⇒ 340 条摘要（32.7k 字符）被判超 200k 预算、`truncated=130`；注入侧不看 `truncated` 就等于**静默丢条**。修法 = 按实际发射字符计费（`full` 分支才按正文）。回归 `test_budget_is_charged_for_what_is_emitted_not_the_body` / `test_truncation_follows_the_title_budget` / `test_full_mode_budget_still_charges_the_body`：把旧实现放回去前两条必红，把 `full` 分支改成按摘要计第三条必红（两种变异均已实测）。
负向控制（每条都验证"守卫坏掉就会红"）：同 slot 第二个 add 必须把旧条 deprecated 且 `same_slot_multi_active==0`；**同名 slot 换 project 不得被弃**；重复 add 幂等且 revisions 不增；supersede 链只剩一个 active；archive 后 list 不含而 expand 可读；replace 打在 archived 上被拒；空 content ⇒ exit 2；库缺失 ⇒ exit 2 且**不创建文件**；dry-run 前后 events/revisions 不变且缺失库不被创建；events 历史行逐字节不变；`§` 行内不切（并断言 naive split 会切）；导入第二遍 created=0；`[supersedes:]` 落成真边；改文变修订不变单元；symlink 别名只导一次；TODOS 计数且零导入；`--only` 生效；语料根缺失 ⇒ exit 2；`mem` 不落 profile 库（重定向 HOME 验）。

## 7. 裁决记录与未做项

**四项裁决（2026-11-13，Lead，已落进本文与关联文档）**：①契约冲突 → M1 是 DSH 语料写入线，不接管 Nowledge 主库替换线，I1 成立（§0，两处状态注已加）；②退出码 → **两套并存不合并**（`mem` 仪器三态 vs `exit_codes.py` 的 0–5 profile 错误码），只在 §2 保留分工说明；③`project` → 不猜，改机械反解（§2.2），映射进 `status`；④命名统一用 **M1**（P1 只作阶段别名）。

未做 / 仍需决策：

**未做（明确留给后续阶段）**：注入面 P2（DSH 侧读/写入口、`memory` 工具接管、Working Memory/bundle、提示注入与披露）与切换 P4（evolve 停写、水位、语料↔库对账、双写与回退剧本、fail-closed 门禁）。本层目前是"能写、能查、能导入"的底座，**没有任何调用方**。

1. **project slug 覆盖 11/73**（§2.2）。要不要把"会话 cwd 台账"（如 `dsh_session_catalog` / session 记录里的 cwd 列表）也作为**声明的**候选来源喂进反解？那仍是查表，但会引入新的数据依赖，属 P2 决策。
2. **`snapshot` 默认 caps 已按现网尺度校准（2026-11-13）**：`max_entries=500` / `max_chars=120000` / `summary_chars=160`，实测默认下 memory/user/key 三轨零截断（evolve 现网注入约 67k 字符，两边不互为短板；P2 侧传 `maxEntries=600`）。最终注入体积仍以 P2 diff 为准——这里给的是"不会平白丢条"的下限。
3. **`daily`/`project_log` 只给计数不给条目**（按 Lead 给的 schema）。若 P2 想在注入里显示「今天已写 N 条」以外的内容，需要扩 schema（`schema` 字段就是为这种变更留的）。

## 8. 样例（2026-11-13，真实语料导入临时库后实测）

`mem status`（临时库，避免在默认路径留状态）：
```
$ python3 -m memory_tool mem status --db "$T/s.sqlite3" --format text
db: /var/folders/…/s.sqlite3 (schema v1)
units=6696 revisions=6696 events=6734 fts_rows=6696 bigram_rows=557578 bytes=128876544
by_track={'daily': 2810, 'key': 768, 'memory': 340, 'project': 2774, 'user': 4}
by_state={'active': 6677, 'deprecated': 19}
project_slug: assigned=11 unassigned=62 (registry available=True, mapped_projects=36)
```

`mem snapshot`（cwd=dsfolder，**默认 caps**；`--format text` 是同一 payload 的人类视图。此为本轮预算修复后的实测）：
```
cwd=/Users/echerlos/syncfolder/project/dsfolder branch=main project=2d4994d462af slug=dsfolder
memory  summary count=340   shown=340   truncated=0
user    full    count=4     shown=4     truncated=0
key     summary count=451   shown=451   truncated=0
daily 2026-11-13 count=55
project_log count=1819
slots active=48 deprecated=19 same_slot_multi_active=0
degraded=[]
```
`key count=451` 是**分支过滤后**的条数（该 project 的 key 活跃条共 451，其中 419 条无 `[branch:]` 标签、32 条带 `main`）；换 `--branch superagent/T104-…` 得 419——过滤是真生效的，不是空转。

同一 cwd 抬到 `--max-entries 800 --max-chars 200000` 结果不变（三轨仍 340/4/451 全发射）。**旧实现**在同一条命令下是 `memory n=210/trunc=130`、`key n=178/trunc=273`，而实际发射的摘要只有 32,664 / 21,630 字符——这就是"计费对象不是发射对象"的可复现证据。
