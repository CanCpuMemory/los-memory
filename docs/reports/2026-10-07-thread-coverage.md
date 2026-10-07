# Nowledge 线程维度覆盖率与 DSH 会话索引可映射性探测

- **日期**：2026-10-07（实测窗口 12:22–12:25 CST）
- **状态**：P5 前置探测完成。**本轮不做任何实现**，不写代码、不改 schema、不动服务。
- **方法**：对两个数据源各取一份**本地只读快照**后用 SQL/Python 全量对账。
  - 影子库（M3）：`tar` 拉取 `shadow.sqlite3` + `-wal` + `-shm` 到 `/tmp/m3shadow/`（含 33 MB WAL，不含 WAL 会读到过期数据），本地用 sqlite3 3.54.0 查询。`records` 实测 2048 条，与 M3 上 `mode=ro` 直连计数一致。
  - DSH 会话索引（M1）：`cp /Users/echerlos/.dsh/storages/session-index.db /tmp/si.db`（源库无 `-wal` 文件，主文件即完整状态），sha256 `9e4dc077…e57dd`。
  - 主库（Nowledge REST）：只用 GET 语义的读命令（`nmem stats`、`nmem threads list`、`nmem memories list`）。
- **只读声明**：未修改 `los-memory` 仓库任何已有文件（本报告是唯一新增文件）；未执行任何 `git` 操作；未修改 M3 影子库、未修改任何运行中的服务或数据库；所有 SQL 均为 `SELECT`，对源库仅做读取与拷贝。

---

## 结论先行

**判定：可借用，但只能覆盖「三分之一」的线程证据面——精确地说是 deepseek-harness 一家，且仅限已挂载 durable memory 的那部分线程。把它当作 codex/grok 线程的证据来源不可行。**

1. **线程维度本身就很稀疏**：2048 条镜像记录里只有 **742 条（36.23%）** 带 `source_thread`；去重后只能看到 **153 个线程**，而主库有 **349** 个（`nmem stats`）/ **347** 个（`nmem threads list --limit 400` 的 `total`）。
2. **这个差距的性质已经查清，不是「线程没镜像」**：影子**根本没有 thread 实体表**，线程维度只以「记忆上的外键」形式存在；而记忆镜像是 **1:1 完整**的（主库 2048 条记忆 id ∩ 影子 2048 条 `source_id` = 2048，双向差集为 0）。所以 **194 个主库线程是「一条 durable memory 都没挂上」的线程**，它们在影子里结构性不可见。差距 = **线程与记忆的关联稀疏**，而非镜像漏表。
3. **可映射性：deepseek-harness 100%，codex/grok 0%**。
   - `deepseek-harness`：**49/49 线程全部命中**（254/254 记录，100%）。
   - `codex`：**0/101 线程命中**（0/481 记录）。
   - `grok`：**0/3 线程命中**（0/7 记录）。
   - 合计：**49/153 线程 = 32.03%**，**254/742 带线程记录 = 34.23%**，占全部 2048 条镜像仅 **12.40%**。
4. **事件级数据是可用的**：49 个命中会话在 DSH 索引里共有 **65,245 条事件**（占索引 553,666 条的 11.78%），49/49 都有事件，可按 `(session_id, seq)` 与毫秒 `ts` 回放。
5. **建议映射键**：`(source_system, source_instance, source_id)` = `('deepseek-harness', '<DSH 部署实例 m1>', 'session-<uuid>' 规范化形式)`。必须先做 id 规范化——实测 deepseek-harness 有**两种** id 形态。

### 覆盖率总表（分子/分母/百分比）

| # | 指标 | 分子 | 分母 | 覆盖率 |
|---|---|---|---|---|
| A | 镜像记录带 `source_thread` | 742 | 2048 | **36.23%** |
| B | 影子可见线程 ÷ 主库线程（`nmem stats` 349） | 153 | 349 | **43.84%** |
| C | 影子可见线程 ÷ 主库线程（`threads list` total 347） | 153 | 347 | **44.09%** |
| D | **DSH 可映射线程 ÷ 影子可见线程** | **49** | **153** | **32.03%** |
| E | **DSH 可映射记录 ÷ 带线程记录** | **254** | **742** | **34.23%** |
| F | **DSH 可映射记录 ÷ 全部镜像记录** | **254** | **2048** | **12.40%** |
| G | DSH 可映射线程 ÷ 主库线程 | 49 | 349 | 14.04% |
| H | DSH 可映射线程 ÷ 主库 deepseek-harness 线程 | 49 | 112 | 43.75% |
| I | deepseek-harness 线程映射成功率 | 49 | 49 | **100%** |
| J | codex 线程映射成功率 | 0 | 101 | **0%** |
| K | grok 线程映射成功率 | 0 | 3 | **0%** |
| L | 命中会话的事件 ÷ 索引全部事件 | 65,245 | 553,666 | 11.78% |

---

## 1. Nowledge 线程维度有多稀疏

### 1.1 实测数字

| 指标 | 数值 |
|---|---|
| 镜像记录总数 | **2048**（全部 `active=1`、`lifecycle_state='active'`、`is_crystal=0`） |
| 带 `source_thread` 的记录 | **742（36.23%）** |
| 带 `source_thread.id` 的记录 | 742（与上一行相等：742 条有 `source_thread` 的记录 100% 都有 `id`） |
| 去重线程数（仅计 `source_thread.id` 非空） | **153** |
| 去重 (source, id) 对数 | 153（与上相等：**跨 source 无 id 碰撞**） |

按 `source_thread.source` 分布：

| source | 记录数 | 占 2048 | 去重线程数 |
|---|---|---|---|
| codex | 481 | 23.49% | 101 |
| deepseek-harness | 254 | 12.40% | 49 |
| grok | 7 | 0.34% | 3 |
| **合计** | **742** | **36.23%** | **153** |

### 1.2 与主库 349 个线程的差距

主库（只读 GET）实测：

```
nmem stats                     -> threads 349
nmem threads list -n 400 -j    -> returned 347, total 347
    codex 177 | deepseek-harness 112 | grok 44 | kimi-code 13 | claude-code 1
```

逐 source 对照：

| source | 主库线程（list，347） | 影子可见线程 | 影子不可见（未挂记忆） |
|---|---|---|---|
| codex | 177 | 101 | 76 |
| deepseek-harness | 112 | 49 | 63 |
| grok | 44 | 3 | 41 |
| kimi-code | 13 | 0 | 13 |
| claude-code | 1 | 0 | 1 |
| **合计** | **347** | **153** | **194** |

**差距 = 196（对 349）或 194（对 347）。** 其中 `kimi-code`(13) 与 `claude-code`(1) 这两个 source 在影子里**一条记录都没有**（它们的线程没有产出任何 durable memory）。

`nmem stats`(349) 与 `nmem threads list`(347) 相差 2 —— **实测到差异，原因未查证（推断：统计口径含列表不返回的线程）**，在本报告所有比例中同时给出两个分母。

### 1.3 这个差距意味着什么：是「线程稀疏」，不是「线程没镜像」

这是本次探测最重要的一条纠正。两条实测证据：

**(a) 影子没有线程实体表。** `records` 是唯一业务表，schema 里没有任何 thread 表；线程维度只存在于记忆快照 JSON 的 `source_thread` 字段里。所以「线程有没有被镜像」这个问法本身不成立——影子从来没有独立镜像过线程。

**(b) 记忆镜像是 1:1 完整的，所以「记录里的线程」就等于「主库的线程-记忆关联」。**

```python
import json, sqlite3
main = {m['id'] for m in json.load(open('nmem_mem.json'))['memories']}   # nmem memories list -n 2500 -j
sh   = sqlite3.connect('file:shadow.sqlite3?mode=ro', uri=True)
shadow = {r[0] for r in sh.execute("select source_id from records")}
# 实测：main=2048, shadow=2048, 交集=2048, main-only=0, shadow-only=0
```

双向差集都为 **0**，主库 2048 条记忆与影子 2048 条记录**逐 id 完全一致**。

**因此结论是**：那 194 个不可见线程，是**一条 durable memory 都没有的线程**——蒸馏/捕获环节没有覆盖到它们，而不是镜像同步漏了线程。差距的正确读法是「**2048 条记忆只覆盖了主库 347 个线程中的 153 个（44.1%）**」：线程维度在记忆层的投影本身就稀疏，影子只是忠实地反映了这份稀疏。

---

## 2. DSH 会话索引的规模与覆盖

### 2.1 规模（实测，快照时点 2026-10-07 12:22）

| 指标 | 数值 |
|---|---|
| `sessions` | **1025** |
| `events` | **553,666** |
| `ingest_files` | 1047 行 / 1044 个 distinct `session_id` |
| 有 ingest 文件的会话 | **1025 / 1025 = 100%** |
| 有事件的会话 | **1025 / 1025 = 100%** |
| `ingest_files` 中指向不存在会话的孤儿引用 | 19 个 distinct `session_id` |
| `events_fts` 真实索引文档数 | **41,248**（见 2.4 的坑） |

### 2.2 时间范围（`created_at`/`ts` 是毫秒）

| | 原始毫秒 | 可读（CST） |
|---|---|---|
| `sessions.created_at` 最小 | 1786678525205 | 2026-08-14 11:35:25 |
| `sessions.created_at` 最大 | 1791346017560 | 2026-10-07 12:06:57 |
| `events.ts` 最小 | — | 2026-08-14 11:52:37 |
| `events.ts` 最大 | — | 2026-10-07 12:13:00 |

```sql
select datetime(min(created_at)/1000,'unixepoch','localtime'),
       datetime(max(created_at)/1000,'unixepoch','localtime') from sessions;
```

### 2.3 按 cwd 分组的会话数 top 10

| cwd | 会话数 |
|---|---|
| `/Users/echerlos/.dsh/scheduler-reports` | 381 |
| `/Users/echerlos/syncthing/project/dsfolder` | 182 |
| `/Users/echerlos/syncfolder/project/dsfolder` | 78 |
| `/Users/echerlos/Downloads/projects/deepseek-harness` | 45 |
| `/Users/echerlos/syncfolder/project/lzlyx` | 38 |
| `/Users/echerlos/syncfolder/project/cantool` | 36 |
| `/Users/echerlos/Downloads/projects/lzlyx` | 36 |
| `/Users/echerlos/syncfolder/project/wechatdp` | 34 |
| `/Users/echerlos/syncfolder/project/lot2extension` | 34 |
| `/Users/echerlos/Downloads/projects/cantool` | 25 |

```sql
select cwd, count(*) from sessions group by 1 order by 2 desc limit 10;
```

### 2.4 两个必须记录的事实

**(a) `sessions.id` 有三种以上形态**（影响匹配规则设计）：

| 形态 | 数量 | 占比 |
|---|---|---|
| `session-<uuid>`（44 字符） | 797 | 77.76% |
| 裸 `<uuid>`（36 字符） | 205 | 20.00% |
| 其它（`wechat-*` 19、`smoke-*` 3、`lark-*` 1） | 23 | 2.24% |

```sql
select case when id like 'session-%' then 'session-<uuid>'
            when length(id)=36 then 'bare <uuid>' else 'other' end shape, count(*)
from sessions group by 1;
```

**(b) 任务书里的 `event_fts` 实际叫 `events_fts`**，且它是 external-content FTS5 表：

```sql
select count(*) from events_fts;          -- 553,666  ← 读的是内容表 events，不是索引！
select count(*) from events_fts_docsize;  --  41,248  ← 真实索引文档数
select count(*) from events where text is not null and length(text)>0;  -- 41,248
```

真实的 FTS 覆盖是 **41,248 / 553,666 = 7.45%**，且恰好等于「有非空 `text` 的事件数」——即**每一个带文本的事件都被索引了**，索引本身没有漏。**坑**：`count(*)` 会虚报满行数（这条教训与同日 `2026-10-07-shadow-search-index.md` 记录的一致）。

---

## 3. 可映射性（最关键）

### 3.1 实测前置修正：id 形态不是两类，是四类

任务书假设影子 `source_thread.id` 有两类形态。实测（`regex` 分类全量 153 个 id）是**四类前缀 / 五种形态**：

| source | id 形态 | 去重 id 数 | 记录数 | 后缀长度 |
|---|---|---|---|---|
| codex | `codex-<uuid36>` | 101 | 481 | 36 |
| deepseek-harness | `deepseek-harness-session-<uuid36>` | 41 | 230 | 36 |
| deepseek-harness | `deepseek-harness-<uuid36>`（**无 `session-` 中段**） | 8 | 24 | 53 |
| grok | `grok-<uuid36>` | 3 | 7 | 41（含 `grok-`） |
| **合计** | | **153** | **742** | |

deepseek-harness 的两种形态是本轮新发现，直接决定匹配规则必须**两条都试**（41 个命中 `session-<uuid>`，8 个命中裸 `<uuid>`）。

### 3.2 尝试过的匹配规则与结果（全量，非抽样）

| 规则 | deepseek-harness (49) | codex (101) | grok (3) |
|---|---|---|---|
| R1 全 id 直等 `sessions.id` | 0 | 0 | 0 |
| R2 去前缀后裸 uuid == `sessions.id` | **8 命中** | 0 | 0 |
| R3 `'session-'+uuid == sessions.id` | **41 命中** | 0 | 0 |
| R4 前缀保留形 `codex-<uuid>` 直等 | — | 0 | — |
| R5 `sessions.id LIKE '%<uuid>%'`（子串） | 全中（R2/R3 已覆盖） | **0 / 101** | **0 / 3** |
| R6 `ingest_files.path LIKE '%codex%'` | — | **0** | — |
| R7 uuid 出现在 `events.value_json`/`text`（抽 2 个） | — | **0 / 2** | **0 / 1** |

```python
# 全量匹配（可复现）
PREFIX = {'deepseek-harness': ['deepseek-harness-session-', 'deepseek-harness-'],
          'codex': ['codex-'], 'grok': ['grok-']}
for src, tid, n in shadow_threads:            # 153 个 (source, id) 对
    uid = strip_prefix(src, tid)
    hit = uid in sessions or ('session-' + uid) in sessions
```

### 3.3 结果

| source | 线程命中 | 线程未命中 | 命中率 | 记录命中 | 记录未命中 | 记录命中率 |
|---|---|---|---|---|---|---|
| deepseek-harness | **49** | 0 | **100%** | **254** | 0 | **100%** |
| codex | 0 | 101 | **0%** | 0 | 481 | **0%** |
| grok | 0 | 3 | **0%** | 0 | 7 | **0%** |
| **合计** | **49** | 104 | **32.03%** | **254** | **488** | **34.23%** |

### 3.4 原始例子

**匹配成功（deepseek-harness，3 例）**

| `source_thread.id` | 命中的 `sessions.id` | 命中规则 | 该线程记录数 |
|---|---|---|---|
| `deepseek-harness-session-0b3b686c-c847-4479-a52c-49dc255bb64d` | `session-0b3b686c-c847-4479-a52c-49dc255bb64d` | R3 | 26 |
| `deepseek-harness-session-ee107383-45c6-417c-98d9-b9ac3f8af844` | `session-ee107383-45c6-417c-98d9-b9ac3f8af844` | R3 | 20 |
| `deepseek-harness-1897ffd9-c928-41aa-8723-bcf1bebee114` | `1897ffd9-c928-41aa-8723-bcf1bebee114` | R2（裸 uuid） | 3 |

**匹配失败（codex/grok，3 例）**

| `source_thread.id` | 试过的候选键 | 结果 | 该线程记录数 |
|---|---|---|---|
| `codex-019f9bd1-c202-7352-8555-04bf776e6bab` | `019f9bd1-…`, `session-019f9bd1-…`, `codex-019f9bd1-…`, 子串 LIKE | 全部 0 命中 | 2 |
| `codex-019f9c0e-cd4f-73a1-8522-fe92d34e0291` | 同上 | 全部 0 命中 | 2 |
| `grok-019ff327-51cd-7831-82eb-d686d3817af2` | 同上 | 全部 0 命中 | 2 |

**为什么 codex 是 0（实测 + 推断分开写）**
- 实测：`ingest_files` 的 1047 条路径**全部**位于 `/Users/echerlos/.dsh/sessions/`；`path LIKE '%codex%'` = 0。DSH 会话索引只摄取 DSH 自己写的 session 文件。
- 实测：codex 线程 uuid 在 `sessions.id`（含子串）、`sessions.cwd`、抽样 `events.value_json`/`text` 中均无出现。
- 推断：codex 会话记录在 Codex 自己的会话目录，本就不在 DSH 索引的摄取范围内；grok 同理（44 个主库 grok 线程在影子里只留下 3 个、在 DSH 索引里 0 个）。**这不是匹配规则没写对，而是数据源不在同一个索引里。**

---

## 4. 事件级可用性

### 4.1 命中会话的聚合

| 指标 | 数值 |
|---|---|
| 命中会话数 | **49** |
| 其中有 ≥1 事件的 | **49 / 49 = 100%** |
| 命中会话事件总数 | **65,245**（占索引 553,666 的 11.78%） |
| 命中会话事件时间范围 | 2026-08-14 17:49:25 → 2026-09-22 15:52:59 |
| 每线程镜像记录数 | min 1 / median 3 / max 26 |

注：命中会话事件的**最大 ts 是 2026-09-22**，而索引整体最大 ts 是 2026-10-07 —— **推断**（未深查）：9-22 之后被蒸馏出记忆的线程，其 `source_thread` 尚未回写到影子，或该窗口的蒸馏产出不再引用线程。此点不影响本轮结论。

### 4.2 一个具体会话的完整剖析

会话 `session-0b3b686c-c847-4479-a52c-49dc255bb64d`（对应线程 `deepseek-harness-session-0b3b686c-…`，26 条镜像记忆）：

- 元数据：`cwd=/Users/echerlos/syncfolder/project/cankey`，`created_at=2026-09-18 19:48:50`，`agent_preset=standard`
- 事件数：**10,199**；时间范围 **2026-09-18 19:49:09 → 2026-09-19 17:57:38**（约 22 小时）
- turn 跨度：1 → 19；`has_error=1` 的事件：**0**
- 摄取文件：`/Users/echerlos/.dsh/sessions/--Users-echerlos-syncfolder-project-cankey--/session-0b3b686c-…/session.v3.jsonl.zstd`，13,746,404 字节，`events=10199`

kind 分布：

| kind | 条数 |
|---|---|
| tool/result | 2,184 |
| tool/call | 2,179 |
| step/start | 1,894 |
| step/end | 1,894 |
| assistant/message | 1,893 |
| user/message | 106 |
| turn/start | 19 |
| turn/end | 18 |
| request/header | 12 |

事件样例（`seq` 严格递增、`ts` 毫秒单调）：

```
(4,  2026-09-18 19:49:09, turn/start,        '')
(6,  2026-09-18 19:49:09, step/start,        '')
(8,  2026-09-18 19:49:09, user/message,      '结合https://github.com/browser-use/jev')
(10, 2026-09-18 19:49:09, user/message,      'Current runtime context. This snapsh…')
```

**能否按时间回放：能。** 主键 `(session_id, seq)` 保证同会话内全序；`ts` 为毫秒，可跨会话绝对排序；`turn`/`step` 提供会话内的粗结构层级；`kind`/`name`/`value_json` 足以重建事件流。约束：只有 41,248 条事件（7.45%）带可检索文本，`tool/call`+`tool/result` 占总量 38.9% 且大多只有参数摘要，回放能看到「发生了什么」，不一定能看到完整内容。

---

## 5. 结论与建议

### 5.1 可行性

**可行，但适用范围有硬边界：可以把 DSH session-index 当作 `source=deepseek-harness` 线程的权威证据来源，不能当作线程维度的通用补全。**

- 对该来源**成功率为 100%**：影子可见的 49 个 deepseek-harness 线程全部命中，254 条记录全部可锚定到具体会话，且每个会话都有事件、可回放（均值 1,331 事件/会话）。
- 但它的**收益上限只有 34.23%** 的带线程记录面（254/742），占全部镜像仅 12.40%。
- 换句话说：它能把「deepseek-harness 的 49 个线程」从「只有一个标题字符串」升级为「有完整事件流」，但**改变不了线程维度整体稀疏**这个事实（742/2048 带线程、153/347 线程可见）。

### 5.2 缺口（它给不了的）

| 缺口 | 规模 | 原因（实测） |
|---|---|---|
| codex 线程证据 | 0/101 线程、0/481 记录 | codex 会话不在 DSH 摄取范围（`ingest_files` 路径 100% 在 `~/.dsh/sessions/`，`%codex%`=0） |
| grok 线程证据 | 0/3 线程、0/7 记录 | 同上，非 DSH 会话 |
| kimi-code / claude-code | 主库 13 + 1 个线程，影子 0 条记录 | 这两个 source 没有产出任何 durable memory，连线程 id 都拿不到 |
| 未挂记忆的 dsh 线程 | 63 个（主库 112 − 影子可见 49） | 影子只能看到「有记忆的线程」；这些线程在影子中不存在，DSH 索引里即使有会话也无法与记忆对齐 |
| 9-22 之后的近期会话 | 命中会话事件止于 2026-09-22 | 推断：镜像线程引用滞后于索引增长（未深查） |

### 5.3 映射键建议

建议三元组 `(source_system, source_instance, source_id)`：

| 字段 | 取值 | 依据 |
|---|---|---|
| `source_system` | `json_extract(metadata,'$.original_source')` 或顶层 `source_thread.source` → `deepseek-harness` / `codex` / `grok` | 实测 742/742 条带线程记录两者一致；`metadata` 另有 `thread_source`、`source_app` 同值 |
| `source_instance` | DSH 部署实例标识，建议 `<host>:<index-db-path>`，如 `m1:/Users/echerlos/.dsh/storages/session-index.db` | 影子在 M3、索引在 M1，跨实例必须显式标注，否则 id 空间无法解释 |
| `source_id` | **规范化**后的 `session-<uuid>` | 关键：deepseek-harness 有两种形态，必须先剥 `deepseek-harness-session-` **和** `deepseek-harness-` 两个前缀得到 uuid，再统一存 `session-<uuid>`；解析时先试 `session-<uuid>` 再试裸 `<uuid>` |

补充要点：
1. **`sessions.id` 本身就是非规范化的**（797 个 `session-<uuid>` + 205 个裸 uuid + 23 个其它），所以规范化必须落在映射层，不能假设 `sessions.id` 可当主键直接对齐。
2. 反向锚定（从 DSH 会话找记忆）可用 `metadata.source_thread_id`，但要接受它有四种前缀形态，需要同一套归一化。
3. 建议把「能否映射」建成显式状态（`mapped` / `unmappable_source` / `no_memory`），而不是让 codex 的 0% 以「查不到」的形式静默表现——这正是本轮探测的价值所在。

### 5.4 边界与后续

- 本轮是 **P5 前置探测，不做任何实现**：没有修改影子 schema、没有新增表、没有接线 DSH 索引到任何运行中的服务。
- 所有结论均基于 2026-10-07 12:22–12:25 的**快照**；索引与影子都在持续写入（索引 `created_at` 最大已到 12:06，影子 WAL 12:21），数字需按同一口径重跑才可比较。
- **实测 vs 推断**已逐条标注；未查证的项共 3 处：349 vs 347 的 2 条差、9-22 后的事件滞后、`nmem stats` 与 `threads list` 口径差异。这 3 处都不影响主结论。

---

## 附：完整复现清单

```bash
# 0) 影子库快照（必须带 WAL，否则读到过期数据）
ssh m3-t 'cd /Users/echerlos/.local/share/los-memory-shadow && tar cf - shadow.sqlite3 shadow.sqlite3-wal shadow.sqlite3-shm' > /tmp/m3shadow/shadow.tar
tar xf /tmp/m3shadow/shadow.tar -C /tmp/m3shadow
sqlite3 /tmp/m3shadow/shadow.sqlite3 "select count(*) from records;"   # 2048

# 0b) DSH 会话索引快照（源库无 -wal，主文件即完整）
cp /Users/echerlos/.dsh/storages/session-index.db /tmp/si.db
shasum -a 256 /Users/echerlos/.dsh/storages/session-index.db           # 9e4dc077…e57dd

# 1) 线程稀疏度
sqlite3 /tmp/m3shadow/shadow.sqlite3 "
select count(*) total,
       sum(json_type(snapshot,'\$.source_thread')='object') with_thread
from records;"
sqlite3 -header -column /tmp/m3shadow/shadow.sqlite3 "
select json_extract(snapshot,'\$.source_thread.source') src, count(*) recs,
       count(distinct json_extract(snapshot,'\$.source_thread.id')) threads
from records where json_type(snapshot,'\$.source_thread')='object' group by 1 order by 2 desc;"
sqlite3 /tmp/m3shadow/shadow.sqlite3 "
select count(distinct json_extract(snapshot,'\$.source_thread.id'))
from records where json_extract(snapshot,'\$.source_thread.id') is not null;"   # 153

# 1b) 主库线程（只读 GET）
nmem stats
nmem threads list -n 400 -j | /usr/bin/python3 -c "
import sys,json,collections; d=json.load(sys.stdin)
print(d['total'], collections.Counter(t['source'] for t in d['threads']))"
nmem memories list -n 2500 -j > /tmp/nmem_mem.json

# 2) DSH 索引规模
sqlite3 -header -column /tmp/si.db "
select (select count(*) from sessions) sessions,
       (select count(*) from events) events,
       (select count(*) from ingest_files) ingest;"
sqlite3 -header -column /tmp/si.db "
select datetime(min(created_at)/1000,'unixepoch','localtime') mn,
       datetime(max(created_at)/1000,'unixepoch','localtime') mx from sessions;"
sqlite3 /tmp/si.db "select count(*) from events_fts_docsize;"   # 41248（真实索引数）

# 3) 可映射性（全量 153 个线程 × 规则矩阵）
#    见正文 3.2 的 Python 片段：strip_prefix → 试 bare uuid / 'session-'+uuid

# 4) 事件级
sqlite3 -header -column /tmp/si.db "
select count(*), datetime(min(ts)/1000,'unixepoch','localtime'),
       datetime(max(ts)/1000,'unixepoch','localtime')
from events where session_id in (<49 个命中会话 id>);"          # 65245
sqlite3 -header -column /tmp/si.db "
select kind, count(*) from events
where session_id='session-0b3b686c-c847-4479-a52c-49dc255bb64d' group by 1 order by 2 desc;"
```

报告路径：`docs/reports/2026-10-07-thread-coverage.md`
