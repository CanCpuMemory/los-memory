# Nowledge Mem REST API 增量同步能力探测报告

- **日期**：2026-10-07
- **状态**：探测完成（全部问题已给出结论，无遗留待测项）
- **探测目标**：Nowledge Mem 服务端 `v0.10.86`（`GET /capabilities` 自报），REST 基址 `http://100.68.106.96:14242`
- **探测方法**：经 `ssh m3-t`，使用 M3 上的 `/Users/echerlos/.local/bin/python3`（3.13）以标准库 `urllib` 发起 HTTP 请求，从 `~/.nowledge-mem/config.json` 读取 `apiUrl` 与 `apiKey`（请求头 `X-NMEM-API-Key`）。凭证仅在 M3 内存中读取，本报告不落任何密钥。
- **只读声明**：**本次探测的全部请求均为只读。** 未发起任何 `POST` / `PUT` / `PATCH` / `DELETE`；未修改 M3 上任何文件、配置或状态（脚本经 stdin 管道执行，未在 M3 落盘任何文件）；未修改 `los-memory` 仓库内任何已有文件；未执行任何 git 写操作（未 commit / add / checkout）。本报告文件是本次任务中**唯一**新建的文件。
  - 说明：探测过程中调用了 `If-None-Match` / `If-Modified-Since`，二者属于 HTTP 条件请求，语义只读，符合约束。
  - 说明：`GET /memories/{id}/export`、`GET /fs/cat` 亦为只读 GET，不产生副作用。

---

## 0. 结论先行

| # | 候选能力 | 结论 | 证据强度 | 关键证据 |
|---|---|---|---|---|
| 1 | `/memories` 列表支持按修改时间增量过滤（`updated_since` 等 10 个候选） | **不支持** | **强（实测 + 官方文档 + 运行时 OpenAPI 三方一致）** | 10 个参数分别在「极早时间/极晚时间」下返回与基线**逐字节相同**（179,170 B，ID 序列完全一致）；`updated_since=not-a-date` 亦返回 200 而非 422，证明该参数不在 schema 中 |
| 2 | 响应头提供 `ETag` / `Last-Modified` / `Cache-Control`，可做条件请求 304 | **不支持** | **强（实测原始响应头）** | `/memories` 与 `/memories/{id}` 响应头仅有 `content-type` / `vary` / CORS / `content-length` / `connection` / `date`；无 `ETag`、无 `Last-Modified`、无 `Cache-Control` |
| 3 | 存在轻量清单端点（只返回 ID，不含正文） | **`/memories` 上不支持**；但 **`/fs/find` 是一个真实可用的轻量清单** | **强（实测字节数 + 正文检查）** | `/memories/ids` → 404；`fields=id` / `select=id` / `include=id` 三者均返回**逐字节相同的 179,170 B**且仍含 `content`。而 `/fs/find` 全量 2,118 条仅 **610,003 B（0.58 MiB）/ 3 请求**，且**不含正文**（只有 `path` + 160 字符 `snippet`） |
| 4 | 存在变更流 / 同步端点（`/changes`、`/memories/changes`、`/sync`、`/memories/sync`） | **不支持（四个全部 404）**；但存在两条**非游标式**变更通道：`GET /events/stream`（SSE 推送）与 `GET /agent/feed/events`（带日期范围的事件表） | **强（实测状态码）/ 中（SSE 语义未核实）** | 四个候选端点全部 404。`/events/stream` 返回 200 `text/event-stream`（10 秒空闲窗口内无事件）；`/agent/feed/events` 可用 `date_from`/`date_to` 真实过滤（2019 → total 0；2026-10-06~07 → total 112） |
| 5 | 分页有 offset/limit 之外的模式（`total` 字段、`next` 链接） | **部分支持：有 `total`，无 `next` 链接，无游标** | **强（实测原始 JSON）** | `pagination` 原始 JSON 为 `{"limit": 100, "offset": 0, "total": 2048, "has_more": true}` —— 有 `total`，无 `next`/`next_cursor`。`cursor` / `page_token` / `next_cursor` 三个候选参数在 `/memories` 上均被静默忽略 |
| 6 | 单条 GET 比列表项更"新鲜"或字段更全 | **不支持（字段集合完全相同，23 = 23，无任何差异）** | **强（实测字段集合 diff）** | 列表项与 `GET /memories/{id}` 返回的字段集合**完全一致**，`ONLY IN SINGLE: []`、`ONLY IN LIST: []`。两者均**不暴露 `updated_at`** |
| 7 | 官方文档说明增量能力 | **已核实：官方文档明确「不支持增量复制」** | **强（官方文档原文）** | [Sync Across Devices](https://mem.nowledge.co/docs/sync.md) 明确：同步 = 「one Mem hub and many clients」，并显式列出**不属于**的能力，含「offline-first multi-master sync between separate databases」。官方 [List Memories 文档](https://mem.nowledge.co/zh/docs/api/memories/get) 亦只列出 7 个查询参数，无任何时间/cursor 参数 |

**一句话总结**：`/memories` 列表端点**不具备任何增量同步能力**（无时间过滤、无条件请求、无轻量清单、无游标、无变更流）；但服务端确实存在两条**未被官方纳入"同步"叙事**的增量/轻量通道 —— `/fs/find`（按 record-time 增量 + 轻量清单 + 游标）与 `/fs/stat`（逐 ID 返回 `updated_at`）。这两者可以支撑 P1 的分层同步方案。

---

## 1. 探测环境与基线

### 1.1 只读基线请求（实测）

```
GET http://100.68.106.96:14242/memories?limit=100&offset=0&space_id=default&state=active
```

- **HTTP 状态码**：`200`
- **响应体字节数**：`179170`
- **完整原始响应头**：

```
content-type: application/json
vary: origin, access-control-request-method, access-control-request-headers
access-control-allow-origin: *
access-control-expose-headers: *
content-length: 179170
connection: close
date: Wed, 07 Oct 2026 04:22:30 GMT
```

- **`pagination` 原始 JSON**（这是 Q5 的答案）：

```json
{"limit": 100, "offset": 0, "total": 2048, "has_more": true}
```

- 返回记录数 `100`，`memories[0].id = 31e05ed1-d158-4d38-b283-6a41d4c8c744`
- 列表项的字段集合（23 个）：

```
['claim_status', 'confidence', 'content', 'created_at', 'id', 'importance',
 'is_crystal', 'is_favorite', 'is_latest', 'label_ids', 'lifecycle_state',
 'metadata', 'rating', 'review_status', 'source', 'source_derived',
 'source_grounding', 'source_thread', 'space_id', 'time', 'title',
 'trust_warnings', 'unit_type']
```

**关键观察**：列表项**没有 `updated_at` / `modified_at` 字段**。首 100 条抽样中 `has created_at: 100/100`、`has updated_at: 0/100`。也就是说，即便服务端支持按修改时间过滤，REST 模型也**没有任何字段可以承载"修改时间"**（详见 §7 与 §9）。`time` 字段只是人类可读的相对时间（如 `"5 hours ago"`）。

### 1.2 全量清单实测成本（与任务陈述一致）

```
GET /memories?limit=100&offset={0,100,...,2000}&space_id=default&state=active
```

| 项 | 实测值 |
|---|---|
| 请求数 | **21** |
| 记录数 | **2048** |
| 总字节数 | **5,431,447 B = 5.18 MiB** |

> 实测 5.18 MiB 与任务给出的「21 个请求、约 5.18 MiB」**完全吻合**，可作为后续对比基线。

### 1.3 服务端版本与契约来源（重要）

- `GET /capabilities` → `200`，响应体含 `"service":"nmem-server"`、`"version":"0.10.86"`。
- `GET /openapi.json` → `200`，**525,696 B**，`{"openapi":"3.1.0","info":{"title":"Nowledge Mem API",...,"version":"0.9.15"}}`。
- **版本偏差说明（实测 + 推断）**：运行时 OpenAPI 的 `info.version` 为 `0.9.15`，而 `/capabilities` 自报 `0.10.86`。
  - 实测：该 `openapi.json` 由运行中的服务端**在请求时生成**（FastAPI 运行时产物），其 `GET /memories` 参数列表与官方线上文档 [List Memories](https://mem.nowledge.co/zh/docs/api/memories/get) **逐项一致**（7 个参数，见 §2.3）。
  - 推断：`info.version` 只是一个**未随发版更新的元数据字符串**，路径与参数才是当前运行实例的真实契约。三方（运行时 OpenAPI、官方文档、实测行为）互相印证，故本报告以之为权威依据。

---

## 2. Q1：列表端点是否支持按修改时间过滤？

### 2.1 方法

对 `/memories?limit=100&offset=0&space_id=default&state=active` 追加每一个候选参数，每个参数取**两个差异极大的值**：

- `EARLY = 2000-01-01T00:00:00Z`
- `LATE  = 2099-12-31T23:59:59Z`

判定标准（按任务要求）：与不带参数时的**记录集合 / 数量 / 字节数**完全相同 → 视为「静默忽略」= 不支持。同时以 `identical_to_baseline`（返回的 ID 序列是否逐项相同）作为最强判据，而非只看数量。

对可能表示 ID 游标的 `after` / `cursor` / `page_token` / `from` / `since`，另用**第 51 条记录的真实 ID** 作为值再测一轮。

### 2.2 实测结果：10 个候选全部被静默忽略

| 候选参数 | 取值 | 状态码 | 字节数 | 记录数 | ID 序列与基线完全相同？ |
|---|---|---|---|---|---|
| `updated_since` | `2000-01-01T00:00:00Z` | 200 | 179170 | 100 | **是** |
| `updated_since` | `2099-12-31T23:59:59Z` | 200 | 179170 | 100 | **是** |
| `since` | `2000-01-01T00:00:00Z` | 200 | 179170 | 100 | **是** |
| `since` | `2099-12-31T23:59:59Z` | 200 | 179170 | 100 | **是** |
| `modified_since` | `2000-01-01T00:00:00Z` | 200 | 179170 | 100 | **是** |
| `modified_since` | `2099-12-31T23:59:59Z` | 200 | 179170 | 100 | **是** |
| `modified_after` | `2000-01-01T00:00:00Z` | 200 | 179170 | 100 | **是** |
| `modified_after` | `2099-12-31T23:59:59Z` | 200 | 179170 | 100 | **是** |
| `after` | `2000-01-01T00:00:00Z` | 200 | 179170 | 100 | **是** |
| `after` | `2099-12-31T23:59:59Z` | 200 | 179170 | 100 | **是** |
| `from` | `2000-01-01T00:00:00Z` | 200 | 179170 | 100 | **是** |
| `from` | `2099-12-31T23:59:59Z` | 200 | 179170 | 100 | **是** |
| `start_date` | `2000-01-01T00:00:00Z` | 200 | 179170 | 100 | **是** |
| `start_date` | `2099-12-31T23:59:59Z` | 200 | 179170 | 100 | **是** |
| `cursor` | `2000-01-01T00:00:00Z` | 200 | 179170 | 100 | **是** |
| `cursor` | `2099-12-31T23:59:59Z` | 200 | 179170 | 100 | **是** |
| `page_token` | `2000-01-01T00:00:00Z` | 200 | 179170 | 100 | **是** |
| `page_token` | `2099-12-31T23:59:59Z` | 200 | 179170 | 100 | **是** |
| `next_cursor` | `2000-01-01T00:00:00Z` | 200 | 179170 | 100 | **是** |
| `next_cursor` | `2099-12-31T23:59:59Z` | 200 | 179170 | 100 | **是** |

以真实记录 ID 作为游标值的第二轮（`after` / `cursor` / `page_token` / `from` / `since`，值 = 第 51 条 `sha256:1...`）：

```
[after=<id>]      status=200 bytes=179170 n=100 identical_to_baseline=True first3=['31e05ed1','f70df207','18395743']
[cursor=<id>]     status=200 bytes=179170 n=100 identical_to_baseline=True first3=['31e05ed1','f70df207','18395743']
[page_token=<id>] status=200 bytes=179170 n=100 identical_to_baseline=True first3=['31e05ed1','f70df207','18395743']
[from=<id>]       status=200 bytes=179170 n=100 identical_to_baseline=True first3=['31e05ed1','f70df207','18395743']
[since=<id>]      status=200 bytes=179170 n=100 identical_to_baseline=True first3=['31e05ed1','f70df207','18395743']
```

**结论（实测）**：**10/10 候选参数既不报错（无 400/422）、也不生效，全部被静默忽略。**返回字节数、记录数、ID 序列与顺序均与基线**逐字节相同**。注意任务要求的三种区分：

- **返回 400/422 报错**：**无任何一个**参数触发。
- **被忽略**：**全部 10 个**。
- **真的生效**：**0 个**。

### 2.3 对照实验：证明「不是所有参数都被忽略」（防止假阴性）

若不做事前对照，可能误判为「该端点根本不处理任何 query 参数」。因此做了**阳性对照**（实测）：

| 对照请求 | 状态码 | 字节数 | 记录数 | `pagination` | 说明 |
|---|---|---|---|---|---|
| `limit=5` | 200 | 10174 | 5 | `{"limit":5,...,"total":2048,"has_more":true}` | ✅ `limit` **生效** |
| `limit=1000` | 200 | 179170 | 100 | `{"limit":100,...,"total":2048,...}` | ✅ 生效并被**上限截断为 100**（schema `maximum: 100`） |
| `offset=100` | 200 | 201117 | 100 | `{"limit":100,"offset":100,...}` | ✅ `offset` **生效**（字节数不同即不同记录集） |
| `state=archived` | 200 | 194510 | 47 | `{"limit":100,"offset":0,"total":47,"has_more":false}` | ✅ `state` **生效**（total 变 47） |
| `state=all` | 200 | 179170 | 100 | `{...,"total":2118,...}` | ✅ 生效（total 变 2118） |
| `space_id=__nope__` | **422** | 87 | — | — | ✅ **证明本端点会做参数校验并返回 422** |
| `updated_since=not-a-date` | 200 | 179170 | 100 | `{...,"total":2048,...}` | ⚠️ **非法值也不报错** → 该参数根本不在 schema 中 |
| `bogus=1` | 200 | 179170 | 100 | `{...,"total":2048,...}` | 未知参数被忽略（符合 FastAPI 默认行为） |
| `limit=0` | 200 | 1851 | 1 | `{"limit":1,...}` | 被夹到 1 |
| `limit=-1` | 200 | 1851 | 1 | `{"limit":1,...}` | 被夹到 1 |
| `offset=-5` | **500** | 132 | — | — | 负 offset 触发 500（服务端缺陷，非本任务目标） |

**这一组对照极其关键**：
1. `space_id=__nope__` 返回 **422**，`updated_since=not-a-date` 返回 **200**。若 `updated_since` 真在 schema 中，非法日期至少应触发 422（官方 schema 声明 `space_id: string` 都会被校验）。**它不报错，证明它不在参数表中。**
2. `limit` / `offset` / `state` / `space_id` 全部可观测地生效，证明**该端点确实响应 query 参数**，因此 §2.2 的「全部相同」是真实的「不支持」，而非「端点不处理参数」的假象。

### 2.4 运行时 OpenAPI 权威确认

```
GET /openapi.json  →  200
paths["/memories"]["get"]["operationId"] = "list_memories_memories_get"
```

`GET /memories` 的**完整**参数列表（实测，仅 7 个）：

```
name='limit'          in=query required=False schema={"type":"integer","maximum":100,"minimum":1,"default":20}
name='offset'         in=query required=False schema={"type":"integer","minimum":0,"default":0}
name='state'          in=query required=False schema={"type":"string","default":"active"}
name='importance_min' in=query required=False schema={"type":"number","maximum":1,"minimum":0,"default":0}
name='space_id'       in=query required=False schema={"type":"string","default":"default"}
name='is_crystal'     in=query required=False schema={"anyOf":[{"type":"boolean"},{"type":"null"}]}
name='unit_type'      in=query required=False schema={"anyOf":[{"type":"string"},{"type":"null"}]}
```

**无任何时间过滤参数、无 cursor、无 fields/select。**

### 2.5 官方文档权威确认

官方 [List Memories GET](https://mem.nowledge.co/zh/docs/api/memories/get) 页面（2026-10-07 访问，HTTP 200）原文列出的 Query Parameters 为：

> `limit?` / `offset?` / `state?` / `importance_min?` / `space_id?` / `is_crystal?` / `unit_type?`

响应 schema 只有 `{"memories":[...],"pagination":{"limit":0,"offset":0,"total":0,"has_more":true}}`，其中 memory 对象字段为 `id, title, content, source, time, importance, rating, label_ids, is_favorite, source_thread, confidence, space_id, unit_type, metadata` —— **同样没有 `updated_at`**。

### 2.6 Q1 结论

> **不支持。证据强度：强。** 三方独立证据一致：(a) 10 个候选参数实测全部静默忽略且响应逐字节相同；(b) 运行时 OpenAPI 参数表只有 7 个非时间参数；(c) 官方文档列出相同 7 个参数。
>
> 同时，**REST 记忆模型不暴露任何"修改时间"字段**，因此"按修改时间增量"在数据模型层面也不成立（见 §7）。这也意味着：`updated_since` 这类参数**从设计上就不存在**，而非"存在但未实现"。

---

## 3. Q2：响应头是否支持条件请求（ETag / Last-Modified / Cache-Control）？

### 3.1 实测原始响应头

**列表端点** `GET /memories?limit=5&offset=0&space_id=default&state=active` → `200`，10174 B：

```
content-type: application/json
vary: origin, access-control-request-method, access-control-request-headers
access-control-allow-origin: *
access-control-expose-headers: *
content-length: 10174
connection: close
date: Wed, 07 Oct 2026 04:22:56 GMT
```

**单条端点** `GET /memories/31e05ed1-d158-4d38-b283-6a41d4c8c744?space_id=default` → `200`，1771 B：

```
content-type: application/json
vary: origin, access-control-request-method, access-control-request-headers
access-control-allow-origin: *
access-control-expose-headers: *
content-length: 1771
connection: close
date: Wed, 07 Oct 2026 04:22:56 GMT
```

### 3.2 判定

```
has ETag: False | has Last-Modified: False | has Cache-Control: False
```

- **`ETag`：不存在** → 无法构造 `If-None-Match`，**304 流程不可能存在**。
- **`Last-Modified`：不存在**。
- **`Cache-Control`：不存在**。

### 3.3 条件请求实测（补强）

即使服务端不发 `ETag`/`Last-Modified`，仍实测了条件请求是否被接受（均为只读）：

| 请求 | 状态码 | 字节数 | 结论 |
|---|---|---|---|
| `If-Modified-Since: Wed, 01 Jan 2020 00:00:00 GMT` | **200** | 179170 | 未返回 304，头被忽略 |
| `If-None-Match: "bogus-etag"` | **200** | 179170 | 未返回 304，头被忽略 |

> 由于响应中根本没有 `ETag`，任务要求的「用 `If-None-Match` 再请求一次，是否返回 304」**无法按原样执行**（没有有效 ETag 可用）；此处以 `"bogus-etag"` 与 `If-Modified-Since` 作为替代验证，二者均返回 `200` 全量正文。

### 3.4 Q2 结论

> **不支持。证据强度：强（实测原始响应头）。** `/memories` 与 `/memories/{id}` 均**不提供** `ETag`、`Last-Modified`、`Cache-Control`。因此**无法做条件请求 / 304 省流**：每次刷新都必须传输完整正文。这是 P1 流量问题的一个直接根因。

---

## 4. Q3：是否存在轻量清单端点（只返回 ID）？

### 4.1 实测结果

| 请求 | 状态码 | 字节数 | 记录数 | 首个条目是否仍含 `content` | 判定 |
|---|---|---|---|---|---|
| `GET /memories/ids` | **404** | 29 | — | — | **不存在**（响应体 `{"detail":"Memory not found"}`，说明被当作 `/memories/{id}` 路由，`id="ids"`） |
| `GET /memories?...&fields=id` | 200 | **179170** | 100 | **是** | 参数被忽略，与基线**逐字节相同** |
| `GET /memories?...&select=id` | 200 | **179170** | 100 | **是** | 参数被忽略，与基线**逐字节相同** |
| `GET /memories?...&include=id` | 200 | **179170** | 100 | **是** | 参数被忽略，与基线**逐字节相同** |

三者返回的字段集合与基线**完全一致**（23 个字段，含 `content`）：

```
['claim_status','confidence','content','created_at','id','importance','is_crystal',
 'is_favorite','is_latest','label_ids','lifecycle_state','metadata','rating',
 'review_status','source','source_derived','source_grounding','source_thread',
 'space_id','time','title','trust_warnings','unit_type']
```

- **`/memories/ids` 不存在**（404）。
- **`fields` / `select` / `include` 三种字段投影语法全部不支持**，均返回与完整清单相同的 179,170 B，且**仍然包含正文**。
- `/memories` 上**没有任何轻量清单模式**。若要拿 2,048 条记录的 ID，只能拉完整清单（5.18 MiB）。

### 4.2 但是：`/fs/find` 是一个真实可用的轻量清单（意外发现）

虽然 `/memories` 上没有轻量端点，探测中发现 **`GET /fs/find?path=/memories`** 完全满足「轻量清单」的判定标准：

```
GET http://100.68.106.96:14242/fs/find?path=/memories&limit=1000
→ 200, 274545 B, 1000 条, next_cursor="eyJhZnRlcl9pZCI6ImNyeXN0YWxfOGFkM2EwODEzM2I2In0"
```

单条目录项的**全部字段**只有两个：

```json
{"path": "/memories/by-id/0023c286-4fae-41c3-be2f-9eeddceca47f.memory.md",
 "snippet": "For solo-maintained projects (e.g. cankey, cantool, dsfolder, wechatdp), the accepted long-term rule is to proceed directly through commit → push upstream → mer"}
```

- `keys = ['path', 'snippet']`
- **`has content key: False`**（5/5 抽样确认）
- `snippet_len = 160`（固定截断）

**字节数对比（实测全量遍历）**：

| 通道 | 请求数 | 记录数 | 总字节数 | 相对成本 |
|---|---|---|---|---|
| `/memories`（含正文） | **21** | 2048 | **5,431,447 B = 5.18 MiB** | 基线 100% |
| `/fs/find`（仅 path+snippet） | **3** | **2118** | **610,003 B = 0.58 MiB** | **约 11.2%，请求数 1/7** |

游标遍历实测（证明 `next_cursor` 真实可用）：

```
page 1: bytes=274545 n=1000 next_cursor=eyJhZnRlcl9pZCI6ImNyeXN0YWxfOGFkM2EwODEz...
page 2: bytes=299559 n=1000 next_cursor=eyJhZnRlcl9pZCI6InNoYTI1NjpkOWMzNDIyODM0...
page 3: bytes=35899  n=118  next_cursor=None
TOTAL unique paths: 2118 | total bytes: 610003
```

> ⚠️ **注意口径差异（实测）**：`/fs/find` 返回 **2118** 条，等于 `state=all` 的 total（2118），而**不是** `state=active` 的 2048。即 `/fs/find` **不带 state 过滤**，会包含非 active 记录（archived=47 以及其他状态）。实测各 state 桶：`active=2048`、`archived=47`、`all=2118`；而 `deprecated` / `forgotten` / `deleted` 均返回 2118（未识别的 state 值被静默降级为"不过滤"）。**2118 - 2048 - 47 = 23 条属于哪个状态桶，本次未核实。**

### 4.3 Q3 结论

> **`/memories` 上不支持（证据强度：强）**；**但 `/fs/find` 提供真实可用的轻量清单（证据强度：强，实测字节数 + 正文缺失 + 游标遍历）**。
>
> - `/memories/ids` → **404 不存在**。
> - `fields=id` / `select=id` / `include=id` → **全部被忽略**，仍返回 179,170 B 含正文的完整清单。
> - 替代方案：`/fs/find?path=/memories&limit=1000`，**0.58 MiB / 3 请求**拿到全部 2,118 条的 ID 级清单。

---

## 5. Q4：是否存在变更流 / 同步端点？

### 5.1 四个指定候选：全部 404

| 请求 | 状态码 | 字节数 | 响应体 |
|---|---|---|---|
| `GET /changes` | **404** | 0 | （空） |
| `GET /memories/changes` | **404** | 29 | `{"detail":"Memory not found"}` |
| `GET /sync` | **404** | 0 | （空） |
| `GET /memories/sync` | **404** | 29 | `{"detail":"Memory not found"}` |

扩展探测（同样全部 404）：

| 请求 | 状态码 | 字节数 | 响应体 |
|---|---|---|---|
| `GET /memories/sync/status` | **404** | 0 | （空） |
| `GET /events` | **404** | 0 | （空） |
| `GET /memories/stream` | **404** | 29 | `{"detail":"Memory not found"}` |
| `GET /version` | **404** | 0 | （空） |

> 注意区分两类 404：`/memories/*` 下的路径返回 29 B 的 `{"detail":"Memory not found"}` —— 这是被 `/memories/{memory_id}` 路由**吞掉**后查不到该 ID 的结果，**不代表该功能存在**。顶层路径（`/changes`、`/sync`、`/events`、`/version`）返回 0 B 空体，是纯粹的路由不存在。

### 5.2 但存在两条非游标式变更通道（意外发现）

#### (a) `GET /events/stream` —— 服务端推送（SSE）

实测（原始 socket，10 秒读取窗口）：

```
GET /events/stream HTTP/1.1
→ HTTP/1.1 200 OK
content-type: text/event-stream
cache-control: no-cache
vary: origin, access-control-request-method, access-control-request-headers
access-control-allow-origin: *
access-control-expose-headers: *
connection: close
transfer-encoding: chunked
date: Wed, 07 Oct 2026 04:23:15 GMT
（10 秒内未收到任何事件，共 304 B，全部为响应头）
```

运行时 OpenAPI 中的官方描述原文：

> **Event Stream** — "Server-Sent Events stream for real-time updates. **Emits data-change, progress, and stage events as they occur.** Connect using the browser EventSource API or any SSE client."

- **实测**：端点存在，返回 `200` + `text/event-stream` + `chunked`。
- **未核实**：在 10 秒空闲窗口内**没有收到任何事件**，因此**无法确认 `memory_created` / `memory_updated` 是否真的会作为 SSE 事件推送**，也无法确认事件 payload 中是否含可用的变更游标。这需要制造一次写入才能验证 —— 而写入被本任务的只读约束禁止。**故标记为「未核实」，不当作可用能力。**

#### (b) `GET /agent/feed/events` —— 带日期范围的事件表（实测真实过滤）

```
GET /agent/feed/events?limit=5&include_total=true
  → 200, total=6607, returned=5, has_more=true
     event: memory_created        | 2026-10-06T22:41:34.288511+00:00 | CanPad UI/UX 对抗性评审结论（2026-10-07）
     event: thread_bulk_imported  | 2026-10-06T22:42:02.534169+00:00 | Synced 1 kimi-code conversation(s)

GET /agent/feed/events?limit=5&include_total=true&date_from=2019-01-01&date_to=2019-12-31
  → 200, total=0, returned=0, has_more=false          ← 真实生效

GET /agent/feed/events?limit=5&include_total=true&date_from=2026-10-06&date_to=2026-10-07
  → 200, total=112, returned=5                         ← 真实生效

GET /agent/feed/events?limit=5&include_total=true&date_from=2026-10-07&date_to=2026-10-07
  → 200, total=0, returned=0                           ← 真实生效
```

事件对象字段：`['created_at','description','event_type','id','metadata','related_memory_ids','resolved','severity','title']`

近 500 条事件的 `event_type` 分布（实测）：

```
  428  memory_created
   68  thread_bulk_imported
    3  user_question
    1  working_memory_updated
```

`thread_bulk_imported` 事件的 `metadata` 中**同时带有 `created_count` 与 `updated_count`**（实测样例：`"created_count": 0, "updated_count": 1`）—— 这**证明服务端确实会发生"更新"**，但**没有观察到 `memory_updated` 事件类型**（近 500 条中为 0）。

服务端为 `date_from`/`date_to` 提供的官方参数说明：`"Start of date range (YYYY-MM-DD). Overrides last_n_days lower bound"` / `"End of date range (YYYY-MM-DD, inclusive). Defaults to today."`

### 5.3 Q4 结论

> **四个指定候选全部不存在（404）。证据强度：强（实测状态码）。**
>
> 补充（明确区分实测与未核实）：
> - **实测**：`GET /events/stream` 是一个真实存在的 SSE 端点（200 + `text/event-stream`）；`GET /agent/feed/events` 是一个真实存在、且 `date_from`/`date_to` **确实生效**的事件表（含 `memory_created` 事件）。
> - **未核实**：SSE 是否推送记忆变更事件、事件是否携带可用游标 —— 10 秒窗口内无事件，需写入才能验证，受只读约束无法进行。
> - **实测**：`/agent/feed/events` 近 500 条中只有 `memory_created`（428 条），**没有 `memory_updated`**；但 `thread_bulk_imported` 的 metadata 中出现 `updated_count: 1`，说明**更新行为存在却未被事件类型覆盖**（至少不在近期样本中）。

---

## 6. Q5：分页是否有 offset/limit 之外的模式？

### 6.1 `pagination` 对象原始 JSON（完整贴出）

来自 `GET /memories?limit=100&offset=0&space_id=default&state=active`：

```json
{"limit": 100, "offset": 0, "total": 2048, "has_more": true}
```

其他实测样本：

```json
GET /memories?limit=5    → {"limit": 5, "offset": 0, "total": 2048, "has_more": true}
GET /memories?offset=100 → {"limit": 100, "offset": 100, "total": 2048, "has_more": true}
state=archived           → {"limit": 100, "offset": 0, "total": 47, "has_more": false}
state=all                → {"limit": 100, "offset": 0, "total": 2118, "has_more": true}
```

### 6.2 判定

| 特征 | 是否存在 | 证据 |
|---|---|---|
| `total` 字段 | ✅ **有** | `"total": 2048` / `2048` / `47` / `2118`，且随 `state` 变化 |
| `next` 链接 | ❌ **无** | `pagination` 只有 4 个键：`limit`/`offset`/`total`/`has_more` |
| `next_cursor` / 游标 | ❌ **无** | 同上；且 `cursor`/`page_token`/`next_cursor` 参数实测被忽略（§2.2） |
| 其他分页模式 | ❌ **无** | 运行时 OpenAPI 中 `/memories` 只有 `limit` + `offset` |

> 对比：**`/fs/find` 用的是真正的游标分页** —— 响应体形如 `{"paths":[...],"next_cursor":"eyJhZnRlcl9pZCI6..."}`，顶层**没有 `total`**，靠 `next_cursor` 递进（实测 3 页遍历全部 2,118 条）。这与 `/memories` 的 offset/limit 模型是两套不同的分页设计。

### 6.3 Q5 结论

> **部分支持。证据强度：强（实测原始 JSON）。** `/memories` 的分页模式为 **offset/limit + `total` + `has_more`**，**没有** `next` 链接、**没有**游标。`total` 可用（可直接得知 2048 条），但**无法据此省流** —— 拿 `total` 仍需拉完整页。真正的游标分页只存在于 `/fs/find`。

---

## 7. Q6：单条 GET 是否比列表项更"新鲜"或字段更全？

### 7.1 方法

取列表首条 `31e05ed1-d158-4d38-b283-6a41d4c8c744`，比对两边的字段集合：

```
GET /memories?limit=1&offset=0&space_id=default&state=active   → 取 memories[0]
GET /memories/31e05ed1-d158-4d38-b283-6a41d4c8c744?space_id=default
```

### 7.2 实测字段 diff

```
LIST   fields (23): ['claim_status','confidence','content','created_at','id','importance',
                     'is_crystal','is_favorite','is_latest','label_ids','lifecycle_state',
                     'metadata','rating','review_status','source','source_derived',
                     'source_grounding','source_thread','space_id','time','title',
                     'trust_warnings','unit_type']
SINGLE fields (23): 与上完全相同

ONLY IN SINGLE: []
ONLY IN LIST  : []
COMMON        : 全部 23 个
```

### 7.3 逐字段值比对

实测两边的 JSON **内容也完全一致**，包括 `created_at`（`2026-10-06T22:41:20+00:00`）、`metadata`（含 `state`/`created_via`/`unit_type_*`/`source_app`/`event_start`/`event_end`/`temporal_context`/`pagerank_score`/`created_at`）、`label_ids`、`is_latest`、`lifecycle_state`、`review_status`、`trust_warnings` 等。

- 单条 GET **并未**返回更多字段（如 `updated_at`、`version`、`access_count`、关系、实体等）。
- 单条 GET **并未**提供更"新鲜"的时间戳。
- 两边**都没有 `updated_at`**。

> **对比：`/fs/stat` 才提供 `updated_at`（见 §8.2）**，而 `/memories/{id}` 不提供。

### 7.4 Q6 结论

> **不支持。证据强度：强（实测字段集合 diff，23=23，差集为空）。** `GET /memories/{id}` 与列表项**字段集合完全相同、值也相同**，既没有更全的字段，也没有更新的时间戳。**这意味着"用单条 GET 刷新"在信息量上等价于列表项** —— 刷新单条并不会拿到列表拿不到的"修改时间"。
>
> 对 P1 的直接含义：**无法通过单条 GET 判断某条记录是否被修改过**（因为没有 `updated_at`）。真正的修改时间只能从 `/fs/stat` 拿到。

---

## 8. 意外发现（超出原问题，但对 P1 关键）

### 8.1 `/fs/find` 支持**真实生效**的 `since` / `until` 增量过滤

运行时 OpenAPI 中 `GET /fs/find` 的官方参数（实测摘录）：

```
path     (query) default "/memories"  "Nowledge FS path scope (e.g. '/memories')"
type     (query) "Filter by file type: memory | crystal"
unit_type(query) "Filter memories by unit type; also inferred from /memories/by-type/<unit_type>"
label    (query) "Filter by label name"
since    (query) "Lower record-time bound (YYYY-MM-DD or ISO8601)"
until    (query) "Upper record-time bound (YYYY-MM-DD or ISO8601)"
mentions (query) "Filter memories that MENTION this entity name"
limit    (query) maximum 1000, default 200
cursor   (query) "Opaque pagination cursor"
```

官方文档 [Fs Find](https://mem.nowledge.co/docs/api/fs/find/get) 原文确认：

> "Structural / metadata search. Returns paths. Phase 1 covers `type`, `unit_type`, `label`, `since`, `until`, and `mentions` over `/memories`."

**双向实测（证明 `since` 真的生效，而非被忽略）**：

| 请求 | 状态码 | 字节数 | 返回条数 |
|---|---|---|---|
| `/fs/find?path=/memories&limit=1000&since=2000-01-01` | 200 | 274545 | 1000（被 limit 截断） |
| `/fs/find?path=/memories&limit=1000&since=2026-01-01` | 200 | 274545 | 1000 |
| `/fs/find?path=/memories&limit=1000&since=2026-09-01` | 200 | 282374 | 1000 |
| `/fs/find?path=/memories&limit=1000&since=2026-10-06` | 200 | **21447** | **66** |
| `/fs/find?path=/memories&limit=1000&since=2026-10-07` | 200 | **31** | **0** |
| `/fs/find?path=/memories&limit=1000&since=2099-01-01` | 200 | **31** | **0** |
| `/fs/find?path=/memories&limit=1000&until=2000-01-01` | 200 | **31** | **0** |
| `/fs/find?path=/memories&limit=1000&until=2026-10-07` | 200 | 274545 | 1000 |
| `...&since=2026-10-06&until=2026-10-07` | 200 | 21447 | 66 |
| `...&since=2026-10-06&until=2026-10-06` | 200 | 21447 | 66 |

**结论（实测）**：`since` / `until` **真实生效** —— 极晚时间返回空（31 B）、极早时间返回全量、窗口收窄后条数下降。这与 `/memories` 上 10 个参数「两值都返回同一结果」形成鲜明对比。

**语义确认（实测）**：`since=2026-10-06` 返回 66 条，抽查其中记录的 `created_at` 全部 `>= 2026-10-06T00:00`（如 `2026-10-06T02:05:14`、`2026-10-06T13:27:44`、`2026-10-06T22:41:20`）；而 `offset=2047` 的旧记录 `created_at=2026-07-03T07:44:09` **不在**该集合中。官方文档亦称之为 **"record-time bound"**。→ **`since` 绑定的时间是"记录时间"（created_at），不是修改时间。**

### 8.2 ⚠️ 关键反例：`/fs/find?since` **检测不到"修改"**（只检测新增）

这是对 P1 最重要的反例，做了**决定性实验**：

**第 1 步**：抽样 81 条记录（从全部 2,118 条中每 26 条取 1），逐条调 `/fs/stat` 取 `created_at` 与 `updated_at`：

```
/fs/stat 平均响应 349 B
更新过（updated_at != created_at）的记录：14 / 81  ≈ 17.3%
样例：
  140f2a32  created 2026-07-27T05:34:46Z  updated 2026-07-27T05:36:35.025609Z
  19d8eeb6  created 2026-07-25T08:42:02Z  updated 2026-07-25T10:08:29.945783Z
  71adf6ac  created 2026-08-09T10:22:47Z  updated 2026-08-12T04:15:08.716789Z
  b3153905  created 2026-08-10T06:29:49Z  updated 2026-08-14T04:34:50.492739Z
  ...
```

> 即：**约 1/6 的记录在被创建之后又被修改过**。这不是罕见边角情况，而是常态。

**第 2 步（决定性）**：取 `140f2a32`（`created=2026-07-27T05:34:46Z`，`updated=2026-07-27T05:36:35Z`），构造一个**严格落在两者之间**的 `since`：

```
since = 2026-07-27T05:35:40Z      (created_at < since < updated_at)

GET /fs/find?path=/memories&limit=1000&since=2026-07-27T05:35:40Z
  → 200, 返回 1903 条
  → 目标 /memories/by-id/140f2a32-....memory.md 是否在结果中？ False
  → VERDICT: EDIT NOT DETECTED (since keys on created_at only)
```

**结论（实测）**：
> `/fs/find?since` **只按 `created_at` 过滤，完全看不到 `updated_at`**。一条 05:34:46 创建、05:36:35 被修改的记录，用 `since=05:35:40` 查询**查不到它**。官方文档措辞 "record-time bound" 与此一致 —— **这是设计如此，不是 bug**。

**这对 P1 的含义**：`/fs/find?since` 是**"新增检测器"，不是"变更检测器"**。它对新增记录有效、对**修改**无效、对**删除**更无效（删除的记录不会再出现在任何 `since` 结果里，且没有任何 tombstone 通道）。若 P1 只依赖它，会**静默漏掉约 17% 记录的后续修改**。

### 8.3 `/fs/stat` 暴露了 REST `/memories` 没有的 `updated_at`

实测（这是全 API 中唯一能拿到「记忆修改时间」的入口）：

```
GET /fs/stat?path=/memories/by-id/0ad175ba-6f1d-40c3-860e-54f58d79d68d.memory.md
→ 200, 349 B
{"path":"/memories/by-id/0ad175ba-6f1d-40c3-860e-54f58d79d68d.memory.md",
 "kind":"file","type":"memory","id":"0ad175ba-6f1d-40c3-860e-54f58d79d68d",
 "size":949,
 "created_at":"2026-10-06T02:05:14Z",
 "updated_at":"2026-10-06T02:05:14Z",     ← /memories/{id} 完全没有这个字段
 "labels":["CanKey","learning","mixed-input","review"],
 "mentions_count":0,"back_references_count":0,"importance":0.8}
```

- **响应体小**：平均 **349 B**（81 条抽样）→ 非常便宜的逐 ID 探测。
- **提供 `updated_at`** → 可以真正判断「这条记录自我上次见到后是否变过」。
- 相关只读端点：`GET /fs/cat?path=...` 返回完整正文（含 YAML front-matter），实测 2339 B。
- `/fs/ls` 的条目对象**也带 `updated_at`**：实测 `/fs/ls?path=/memories/by-type/decision` 返回 200 条目，`updated_at` 非空 `200/200`；但目录型条目（如 `/memories/by-id`）的 `updated_at` 为 `null`（`0/5` 非空）。条目字段：`['hint','id','kind','memory_count','name','path','size_hint','title','type','updated_at']`。

> **反例/风险（实测）**：`/fs/stat` 的 `updated_at` 精度是**亚秒级**（如 `2026-07-27T05:36:35.025609Z`），而 `created_at` 是秒级。在做时间比较时需注意时区与精度归一化。

### 8.4 官方文档核实（Q7）

- **官方文档可达**，且有三个机器可读入口（实测 200）：
  - `https://mem.nowledge.co/llms.txt`（文档索引）
  - `https://mem.nowledge.co/openapi.json`（官方 OpenAPI 3.1 规范）
  - `https://mem.nowledge.co/_llms/api.md`（API 操作索引）
- 官方 [List Memories GET](https://mem.nowledge.co/zh/docs/api/memories/get)（实测 HTTP 200）列出的查询参数**只有 7 个**（`limit`/`offset`/`state`/`importance_min`/`space_id`/`is_crystal`/`unit_type`），**与运行时 OpenAPI 完全一致**，**无任何时间或游标参数**。
- 官方 [Sync Across Devices](https://mem.nowledge.co/docs/sync.md)（实测 HTTP 200）**明确否定**增量复制能力，原文关键段：

  > "Yes. Nowledge Mem supports sync today. But it works in a specific way: **one Nowledge Mem instance is the single source of truth**; other clients connect to that same instance..."
  >
  > "**What This Is Not** — Mem does **not** currently mean:
  > * several independent local Mem apps automatically replicating and reconciling with each other
  > * a centralized Nowledge-hosted account backend
  > * **offline-first multi-master sync between separate databases**
  >
  > Today, sync means **one Mem hub and many clients**."

- 官方 [Nowledge FS](https://mem.nowledge.co/docs/nowledge-fs.md) 与 [Fs Find API](https://mem.nowledge.co/docs/api/fs/find/get)（实测 200）确认 `/fs/find` 的 `since`/`until` 语义为 **"record-time bound"**。

> **结论（Q7）**：**已核实。官方文档明确说明当前同步模型是「单一 Mem hub + 多客户端直连」，并显式排除"多主复制 / 增量对账"；官方 API 文档也未描述任何增量同步能力。**
>
> 需要诚实指出的**不一致点**：官方 `sync.md` 只讲"多客户端连同一实例"，**完全没有提及 `/fs/find` 的 `since` 增量能力，也没有提及 `/events/stream` 或 `/agent/feed/events`** —— 这些是本次探测从 `openapi.json` 挖出的、**未被官方"同步"叙事覆盖**的能力。因此对它们的**稳定性与支持承诺评估为「未核实」**（不要当作有 SLA 的官方契约）。

---

## 9. 对 P1 的含义

### 9.1 核心判断

**增量接口在 `/memories` 上不存在**（无时间过滤、无条件请求、无轻量清单、无游标、无变更流）。但**服务端确实存在可用的增量原语**，只是不在 `/memories` 上、也不在官方"同步"叙事里。

因此 P1 不应期待"一个增量参数解决问题"，而应采用**分层同步**。值得注意的是：本报告的探测**否定了"单条 GET 可作为变更探测手段"**这一常见假设（§7.4：`/memories/{id}` 与列表项字段、值完全相同，且无 `updated_at`）。

### 9.2 推荐方案：低频全量清单 + 高频按 ID 刷新

**已验证的成本对比（均为实测）**：

| 方案 | 请求数 | 字节数 | 说明 |
|---|---|---|---|
| A. 现状：全量 `/memories` 清单 | **21** | **5.18 MiB** | 含正文，是 P1 流量大头 |
| B. `/fs/find` 轻量全量清单 | **3** | **0.58 MiB** | 只有 `path` + 160 字符 `snippet`，**无正文** |
| C. `/fs/find?since=<last>` 增量 | **1**（窗口小则 1 页） | 实测 66 条 = **21,447 B** | 仅覆盖**新增**；**漏修改** |
| D. `/fs/stat` 逐 ID 探测 | 1 / 条 | **349 B / 条** | 唯一能拿到 `updated_at` 的入口 |

**分层设计建议**：

1. **低频全量对账（例如每 6–24 小时一次）**
   用 `GET /fs/find?path=/memories&limit=1000` + `cursor` 递进，**3 请求 / 0.58 MiB** 拿到全部 ID 级清单。
   比现状 **省 88.8% 流量、请求数从 21 降到 3**。
   用途：发现**删除**（ID 从清单中消失 —— 这是唯一能发现删除的手段，因为服务端没有任何 tombstone/变更流）。

2. **高频新增捕获（例如每 1–5 分钟一次）**
   用 `GET /fs/find?path=/memories&limit=1000&since=<上次水位>`，把水位设为上次成功同步的时间戳（注意用 `created_at` 语义）。
   窗口小的时候通常**1 个请求**即可（实测 66 条仅 21 KB）。
   ⚠️ **必须接受它的盲区**：只覆盖新增，**不覆盖修改**。

3. **修改捕获（必须单独做，无法用增量参数解决）**
   由于 `since` 看不到 `updated_at`（§8.2 决定性反例），修改只能靠：
   - 对**活跃子集**（例如近 N 天创建、或被高频访问的 ID）逐条 `GET /fs/stat`（**349 B/条**）比较 `updated_at`；
   - 或对全量做周期性 `/fs/stat` 扫描 —— **2,118 条 × 349 B ≈ 0.70 MiB**，仍**远低于**现状的 5.18 MiB，且可只扫活跃子集进一步降低。
   - 拿到"变了"的 ID 后，再用 `GET /memories/{id}?space_id=default`（约 1.7 KB）或 `GET /fs/cat` 拉取单条正文。
   - **注意**：`/fs/stat` 的 `updated_at` 是亚秒精度，比较时须做精度/时区归一化。

4. **正文获取**
   只在第 1/2/3 步识别出"新增或变更"的 ID 之后，才对这批 ID 拉正文（`/memories/{id}` 约 1.7 KB，或 `/fs/cat` 约 2.3 KB）。**绝不再为拿 ID 而拉 5.18 MiB 的含正文全量清单。**

**收益估算（推算，非实测端到端）**：稳态下若每轮只有数十条变化，单轮流量可从 **5.18 MiB 降到几十 KB 量级**（约 2 个数量级）；即便做全量 `/fs/stat` 扫描，也只需约 **0.70 MiB**，约为现状的 **13.5%**。

### 9.3 探测中发现的**反例与陷阱**（务必纳入 P1 设计）

| # | 反例 / 陷阱 | 证据 | 对 P1 的影响 |
|---|---|---|---|
| 1 | **`/fs/find?since` 检测不到修改** | §8.2 决定性实验：created `05:34:46` / updated `05:36:35` 的记录，`since=05:35:40` 查不到 | 若只靠 `since` 做增量，**会静默漏掉修改**。抽样显示 **14/81 ≈ 17.3%** 的记录被改过 |
| 2 | **`/memories` 模型完全没有 `updated_at`** | §1.1、§7.2：`/memories` 与 `/memories/{id}` 均无该字段（0/100 抽样） | 不能用 REST 列表或单条 GET 判断"是否被修改" |
| 3 | **`/memories/{id}` 并不比列表项更新鲜或更全** | §7.2 字段 diff：`ONLY IN SINGLE: []`、`ONLY IN LIST: []` | 否定"用单条 GET 刷新即可发现变更"的假设 |
| 4 | **无任何删除信号** | §5.1 四个变更流端点全 404；无 tombstone；`since` 也不可能返回已删记录 | 删除**只能**靠低频全量清单比对 ID 集合发现 → **低频全量对账不可省** |
| 5 | **`/fs/find` 无 state 过滤，返回 2118 条而非 2048** | §4.2 实测：`/fs/find` 遍历 2118 = `state=all`，而 active 是 2048 | 直接用 `/fs/find` 当清单会**多出 70 条非 active 记录**（archived=47 等）。若 P1 只要 active，需自行按需过滤/校验 state |
| 6 | **未识别的 `state` 值被静默降级为"不过滤"** | §4.2：`state=deprecated`/`forgotten`/`deleted` 均返回 2118 (= all) | 拼错的 state 不会报错，只会静默返回超集 —— **必须自行校验参数拼写**，否则会把非目标记录当成 active |
| 7 | **API 对未知/非法参数一律静默忽略，不报错** | §2.3：`updated_since=not-a-date` → 200（而 `space_id=__nope__` → 422）；`bogus=1` → 200 | **无法通过报错发现"用错了参数"**。若 P1 误以为 `updated_since` 生效，会得到**看似成功但完全未过滤**的全量结果 —— 这是一个极易逃过测试的静默失败模式 |
| 8 | **响应无 `ETag`/`Last-Modified`/`Cache-Control`** | §3.1 原始响应头 | 无法用条件请求省流；每次都由客户端自行判断是否需要更新 |
| 9 | **`limit` 上限硬性为 100** (`/memories`) | §2.3：`limit=1000` 被截为 100 | 全量清单**必然**是 21 个请求，无法通过调大 limit 减少轮次（`/fs/find` 的 limit 上限是 1000，故只需 3 轮） |
| 10 | **`offset=-5` 触发 500** | §2.3 | 分页参数需做客户端校验，负值会让服务端报错 |
| 11 | **`/events/stream` 的推送语义未核实** | §5.2(a)：10 秒窗口内 0 事件 | **不要**把 SSE 当作 P1 的可靠变更源去设计；如需采用，必须先做写入实验验证（本次受只读约束无法做） |
| 12 | **`/agent/feed/events` 近 500 条无 `memory_updated`** | §5.2(b)：428 `memory_created`、0 `memory_updated` | 事件表**不能**作为"修改检测"的依据 |
| 13 | **`/fs/find`、`/fs/stat` 未被官方"同步"文档覆盖** | §8.4：官方 `sync.md` 只讲"多客户端连同一 hub" | 这两条通道虽实测可用，但**无官方支持承诺**，版本升级可能变更 → P1 需加**降级/自检**（例如探测到 404 时回退到全量 `/memories`） |

### 9.4 若一定要用 `/memories`（如 `/fs` 不可用时）

由于 `/memories` 无轻量模式且 `limit` 上限 100，唯一省流手段是**降低全量清单频率**，并把单轮成本锁定在实测的 **5.18 MiB / 21 请求**。此时建议：

- 拉长周期（如 6–24 小时），接受较长的变更可见延迟；
- 用 `pagination.total` 做**廉价的存在性探针**（`limit=1` 即可读到 `total`，实测约 **1.9 KB**）：若 `total` 与上次一致，**仍不能**推断无修改（修改不改 `total`），但**能**发现新增/删除（`total` 变化）。
  - 实测依据：`GET /memories?limit=1&offset=0&space_id=default&state=active` → `total=2048`。`limit=0`/`limit=-1` 均返回 1 条（1851 B），故用 `limit=1`。
  - ⚠️ 这是**推断**的低成本探针用法，本次未做端到端验证（需要写入才能验证 `total` 的响应性）。但 `total` 会随 `state` 变化已实测（2048 / 47 / 2118），故其可响应性是**实测**的。

---

## 10. 实测 vs 推断（明确区分）

### ✅ 实测（有原始状态码 / 响应头 / 响应体支撑）

1. `/memories?limit=100&offset=0&space_id=default&state=active` → 200，179,170 B，100 条，`total=2048`（含完整原始响应头）。
2. 10 个时间/游标候选参数（`updated_since`/`since`/`modified_since`/`modified_after`/`after`/`from`/`start_date`/`cursor`/`page_token`/`next_cursor`）各取极早/极晚两个值 → **全部 200，字节数与 ID 序列与基线逐项相同**，无一个触发 400/422。
3. 以真实记录 ID 作为 `after`/`cursor`/`page_token`/`from`/`since` 的值 → 同样全部被忽略。
4. 阳性对照：`limit=5`→5 条 / 10,174 B；`limit=1000`→被截为 100；`offset=100`→201,117 B；`state=archived`→total=47；`state=all`→total=2118；`space_id=__nope__`→**422**；`updated_since=not-a-date`→**200**；`bogus=1`→200；`limit=0`/`limit=-1`→1 条；`offset=-5`→**500**。
5. 运行时 `GET /openapi.json` → 200，525,696 B，`GET /memories` **仅 7 个参数**（无时间参数）。
6. `/memories` 与 `/memories/{id}` 响应头**无 `ETag`、无 `Last-Modified`、无 `Cache-Control`**（原始响应头已贴）。
7. `If-Modified-Since: 2020-01-01` → 200（非 304）；`If-None-Match: "bogus-etag"` → 200（非 304）。
8. `/memories/ids` → **404**（29 B `{"detail":"Memory not found"}`）；`fields=id`/`select=id`/`include=id` → 均 200、179,170 B、仍含 `content`。
9. `/changes`→404(0 B)、`/memories/changes`→404(29 B)、`/sync`→404(0 B)、`/memories/sync`→404(29 B)；扩展的 `/memories/sync/status`、`/events`、`/memories/stream`、`/version` 亦全 404。
10. `pagination` 原始 JSON = `{"limit":100,"offset":0,"total":2048,"has_more":true}`（有 `total`，无 `next`/游标）。
11. `/memories/{id}` 与列表项字段集合完全相同（23=23，两个方向差集均为空），值亦相同；两者均无 `updated_at`。
12. `/fs/find?path=/memories&limit=1000` → 200，274,545 B，1000 条，`next_cursor` 可用；游标遍历 3 页共 **2,118 条 / 610,003 B**；条目仅 `['path','snippet']`，**无 `content`**。
13. `/fs/find` 的 `since`/`until` **真实生效**（极晚→0 条 / 31 B；极早→1000 条；窗口收窄→66 条 / 21,447 B）。
14. **决定性反例**：`140f2a32`（created `2026-07-27T05:34:46Z`，updated `2026-07-27T05:36:35Z`）在 `since=2026-07-27T05:35:40Z` 的 1,903 条结果中**不存在** → `since` 只看 `created_at`。
15. 81 条抽样中 **14 条 `updated_at != created_at`（≈17.3%）**；`/fs/stat` 平均 349 B，含 `updated_at`。
16. `/fs/ls?path=/memories/by-type/decision` → 200 条，`updated_at` 非空 200/200；目录型条目 `updated_at=null`。
17. `GET /events/stream` → 200，`content-type: text/event-stream`，chunked；10 秒内 0 事件。
18. `/agent/feed/events` 的 `date_from`/`date_to` **真实生效**（2019→0，2026-10-06~07→112，2026-10-07→0）；近 500 条分布 `memory_created 428 / thread_bulk_imported 68 / user_question 3 / working_memory_updated 1`。
19. `/capabilities` → 200，`version 0.10.86`；`/health` → 200 `{"status":"ok"}`。
20. 全量 `/memories` 遍历 = **21 请求 / 2,048 条 / 5,431,447 B（5.18 MiB）**。
21. 官方文档可达并已读取：`llms.txt`、[List Memories](https://mem.nowledge.co/zh/docs/api/memories/get)（7 参数）、[Sync Across Devices](https://mem.nowledge.co/docs/sync.md)（明确排除多主增量复制）、[Fs Find](https://mem.nowledge.co/docs/api/fs/find/get)（"record-time bound"）。
22. `state` 桶：`active=2048`、`archived=47`、`all=2118`；`deprecated`/`forgotten`/`deleted` 均返回 2118。

### 🔶 推断（合理但**未经直接验证**，不可当作实测）

1. **`/openapi.json` 的 `info.version = 0.9.15` 与 `/capabilities` 的 `0.10.86` 不一致，是"版本字符串未更新"**，而非"跑着旧版服务端"。依据：该 spec 在请求时生成、其 `/memories` 参数与当前官方线上文档逐项一致。**推断**。
2. **`/events/stream` 会推送记忆变更事件**（官方描述称 "Emits data-change ... events"），但本次 10 秒窗口内 0 事件，**未能验证**。**未核实**。
3. **`/fs/find` 与 `/fs/stat` 的长期稳定性 / 官方支持承诺**。二者实测可用且被官方 API 文档记录，但**未被官方"同步"文档提及**，其作为 P1 依赖的稳定性属**推断/未知**。
4. **`limit=1` 读 `pagination.total` 作为"新增/删除探针"** 的端到端有效性。`total` 随 `state` 变化是实测的，但"写入后 `total` 立即反映"未验证（受只读约束）。**推断**。
5. **2118 - 2048 - 47 = 23 条记录所属的状态桶**。未核实。
6. **P1 稳态流量可降 2 个数量级** 的收益估算。基于实测的各通道单轮成本外推，未做端到端实测。**推断**。
7. **`/fs/find` 的 `mentions` / `label` / `type` / `unit_type` 过滤是否真实生效**。本次只验证了 `since`/`until`/`cursor`/`limit`/`path`，其余参数**未逐一验证**。

---

## 11. 复现方式

以下命令即本次实际执行的内容（API key 一律从 `~/.nowledge-mem/config.json` 读取，**不在命令行或报告中出现**）。全部经 `ssh m3-t` 以 stdin 管道执行，**不在 M3 落盘任何文件**。

### 11.1 环境确认

```bash
ssh m3-t 'ls -la ~/.nowledge-mem/config.json && \
  /Users/echerlos/.local/bin/python3 -c "
import json,os
c=json.load(open(os.path.expanduser(\"~/.nowledge-mem/config.json\")))
print(c[\"apiUrl\"])          # http://100.68.106.96:14242
print(\"key_len\", len(c[\"apiKey\"]))"'
```

### 11.2 通用请求器（所有探测共用；只发 GET）

```python
import json, os, urllib.request, urllib.error
from urllib.parse import urlencode

cfg  = json.load(open(os.path.expanduser("~/.nowledge-mem/config.json")))
BASE = cfg["apiUrl"].rstrip("/")
KEY  = cfg["apiKey"]                      # 仅从 config.json 读取，不硬编码

def req(path, extra_headers=None):
    rq = urllib.request.Request(BASE + path)
    rq.add_header("X-NMEM-API-Key", KEY)          # 只读 GET；从不使用 POST/PUT/PATCH/DELETE
    for k, v in (extra_headers or {}).items():
        rq.add_header(k, v)
    try:
        with urllib.request.urlopen(rq, timeout=180) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
```

### 11.3 Q1：时间/游标参数是否生效（含阳性对照）

```python
BASEQ = {"limit": "100", "offset": "0", "space_id": "default", "state": "active"}
st, hd, bd = req("/memories?" + urlencode(BASEQ))
base = [m["id"] for m in json.loads(bd)["memories"]]
print("baseline", st, len(bd), json.loads(bd)["pagination"])

for p in ["updated_since","since","modified_since","modified_after","after",
          "from","start_date","cursor","page_token","next_cursor"]:
    for val in ("2000-01-01T00:00:00Z", "2099-12-31T23:59:59Z"):   # 极早 与 极晚
        q = dict(BASEQ); q[p] = val
        st, hd, bd = req("/memories?" + urlencode(q))
        ids = [m["id"] for m in json.loads(bd)["memories"]] if st == 200 else None
        print(f"{p}={val} -> {st} {len(bd)}B identical={ids == base}")

# 阳性对照：证明该端点确实响应 query 参数（而非全部忽略）
for tag, q in [("limit=5", {"limit":"5","offset":"0","space_id":"default","state":"active"}),
               ("state=archived", {"limit":"100","offset":"0","space_id":"default","state":"archived"}),
               ("space_id=__nope__", {"limit":"100","offset":"0","space_id":"__nope__","state":"active"}),
               ("updated_since=not-a-date",
                {"limit":"100","offset":"0","space_id":"default","state":"active",
                 "updated_since":"not-a-date"})]:
    st, hd, bd = req("/memories?" + urlencode(q))
    print(tag, "->", st, len(bd), bd[:120])
```

### 11.4 Q2：条件请求与响应头

```python
st, hd, bd = req("/memories?limit=5&offset=0&space_id=default&state=active")
print(st, len(bd)); [print(f"{k}: {v}") for k, v in hd.items()]
print("has ETag:", "etag" in {k.lower() for k in hd})   # False
st, _, bd = req("/memories?limit=5&offset=0&space_id=default&state=active",
                {"If-Modified-Since": "Wed, 01 Jan 2020 00:00:00 GMT"})
print("If-Modified-Since ->", st)                        # 200，非 304
st, _, bd = req("/memories?limit=5&offset=0&space_id=default&state=active",
                {"If-None-Match": '"bogus-etag"'})
print("If-None-Match ->", st)                            # 200，非 304
```

### 11.5 Q3：轻量清单候选

```python
for p in ["/memories/ids",
          "/memories?limit=100&offset=0&space_id=default&state=active&fields=id",
          "/memories?limit=100&offset=0&space_id=default&state=active&select=id",
          "/memories?limit=100&offset=0&space_id=default&state=active&include=id"]:
    st, hd, bd = req(p)
    first = json.loads(bd)["memories"][0] if st == 200 else {}
    print(p, "->", st, len(bd), "has_content:", "content" in first, "keys:", sorted(first.keys()))
```

### 11.6 Q4：变更流 / 同步端点

```python
for p in ["/changes","/memories/changes","/sync","/memories/sync",
          "/memories/sync/status","/events","/memories/stream","/version"]:
    st, hd, bd = req(p)
    print(p, "->", st, len(bd), bd[:80])
```

### 11.7 Q5：`pagination` 原始 JSON

```python
for q in [{"limit":"100","offset":"0","space_id":"default","state":"active"},
          {"limit":"5","offset":"0","space_id":"default","state":"active"},
          {"limit":"100","offset":"100","space_id":"default","state":"active"},
          {"limit":"100","offset":"0","space_id":"default","state":"archived"},
          {"limit":"100","offset":"0","space_id":"default","state":"all"}]:
    st, _, bd = req("/memories?" + urlencode(q))
    print(json.dumps(json.loads(bd)["pagination"]))     # 完整原始 JSON
```

### 11.8 Q6：单条 vs 列表字段 diff

```python
st, _, bd = req("/memories?limit=1&offset=0&space_id=default&state=active")
item = json.loads(bd)["memories"][0]
st2, _, bd2 = req(f"/memories/{item['id']}?space_id=default")
single = json.loads(bd2)
lk, sk = set(item), set(single)
print("ONLY IN SINGLE:", sorted(sk - lk))   # []
print("ONLY IN LIST  :", sorted(lk - sk))   # []
```

### 11.9 Q7 + 增量能力核实：官方文档

```bash
# 官方文档索引（机器可读）
#   https://mem.nowledge.co/llms.txt
#   https://mem.nowledge.co/openapi.json
#   https://mem.nowledge.co/_llms/api.md
# 关键页面
#   https://mem.nowledge.co/zh/docs/api/memories/get   -> 仅 7 个查询参数
#   https://mem.nowledge.co/docs/sync.md               -> 明确排除多主增量复制
#   https://mem.nowledge.co/docs/api/fs/find/get       -> since/until = "record-time bound"
# JS 渲染页面可用只读镜像读取：
#   https://r.jina.ai/https://mem.nowledge.co/zh/docs/api/memories/get
```

### 11.10 意外发现：`/fs/find` 增量 + 轻量清单

```python
from urllib.parse import quote

# 轻量清单（仅 path + snippet，无正文）
st, _, bd = req("/fs/find?path=/memories&limit=1000")
j = json.loads(bd); print(st, len(bd), len(j["paths"]), j["next_cursor"])
print(sorted(j["paths"][0].keys()))          # ['path', 'snippet']  -> 无 content

# 游标遍历全量
paths, cur, total_bytes = [], None, 0
for _ in range(6):
    q = "/fs/find?path=/memories&limit=1000" + (("&cursor=" + quote(cur)) if cur else "")
    st, _, bd = req(q); total_bytes += len(bd)
    j = json.loads(bd); paths += [p["path"] for p in j["paths"]]
    cur = j.get("next_cursor")
    if not cur: break
print("records:", len(paths), "bytes:", total_bytes)   # 2118 / 610003

# since / until 真实生效（双向对照）
for t in ["2000-01-01", "2026-10-06", "2026-10-07", "2099-01-01"]:
    st, _, bd = req(f"/fs/find?path=/memories&limit=1000&since={t}")
    print("since", t, "->", st, len(bd), len(json.loads(bd)["paths"]))
for t in ["2000-01-01", "2026-10-07"]:
    st, _, bd = req(f"/fs/find?path=/memories&limit=1000&until={t}")
    print("until", t, "->", st, len(bd), len(json.loads(bd)["paths"]))

# 全量 /memories 成本基线
total = n = pages = 0
for off in range(0, 2100, 100):
    st, _, bd = req(f"/memories?limit=100&offset={off}&space_id=default&state=active")
    if st != 200: break
    j = json.loads(bd); total += len(bd); n += len(j["memories"]); pages += 1
    if not j["pagination"]["has_more"]: break
print("memories sweep:", pages, n, total)      # 21 / 2048 / 5431447
```

### 11.11 决定性反例：`since` 看不到 `updated_at`

```python
from datetime import datetime

# 1) 采样，找出 updated_at != created_at 的记录
ids, cur = [], None
for _ in range(8):
    q = "/fs/find?path=/memories&limit=1000" + (("&cursor=" + quote(cur)) if cur else "")
    st, _, bd = req(q); j = json.loads(bd)
    ids += [p["path"].split("/by-id/")[1].replace(".memory.md", "")
            for p in j["paths"] if "/by-id/" in p and p.endswith(".memory.md")]
    cur = j.get("next_cursor")
    if not cur: break

diff = []
for mid in ids[::26]:
    st, _, bd = req(f"/fs/stat?path=/memories/by-id/{mid}.memory.md")
    if st != 200: continue
    s = json.loads(bd)
    if s.get("created_at") and s.get("updated_at") and s["updated_at"] != s["created_at"]:
        diff.append(s)
print("modified:", len(diff), "/", len(ids[::26]))    # 14 / 81

# 2) 取一条，构造严格位于 created 与 updated 之间的 since
s  = diff[0]
c  = datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
u  = datetime.fromisoformat(s["updated_at"].replace("Z", "+00:00"))
since = (c + (u - c) / 2).strftime("%Y-%m-%dT%H:%M:%SZ")

got, cur = [], None
for _ in range(8):
    q = f"/fs/find?path=/memories&limit=1000&since={since}" + (("&cursor=" + quote(cur)) if cur else "")
    st, _, bd = req(q); j = json.loads(bd)
    got += [p["path"] for p in j["paths"]]; cur = j.get("next_cursor")
    if not cur: break

target = s["path"]
print("since", since, "-> returned", len(got), "| target present:", target in got)
# 输出：since 2026-07-27T05:35:40Z -> returned 1903 | target present: False
# => EDIT NOT DETECTED：/fs/find?since 只按 created_at 过滤
```

### 11.12 `updated_at` 唯一入口与 SSE 探活

```python
# /fs/stat 是唯一能拿到 updated_at 的入口（/memories/{id} 没有该字段）
st, _, bd = req("/fs/stat?path=/memories/by-id/0ad175ba-6f1d-40c3-860e-54f58d79d68d.memory.md")
print(st, len(bd))     # 200, 349
print(json.loads(bd))  # contains created_at + updated_at

# SSE 探活（原始 socket，只读 GET，10 秒窗口）
import socket, time
HOST, PORT = "100.68.106.96", 14242
s = socket.create_connection((HOST, PORT), timeout=12)
s.sendall((f"GET /events/stream HTTP/1.1\r\nHost: {HOST}:{PORT}\r\n"
           f"X-NMEM-API-Key: {KEY}\r\nAccept: text/event-stream\r\nConnection: close\r\n\r\n").encode())
s.settimeout(10); buf = b""; t0 = time.time()
while time.time() - t0 < 10:
    try:
        d = s.recv(4096)
        if not d: break
        buf += d
    except socket.timeout:
        break
s.close()
print(len(buf)); print(buf[:400].decode("utf-8", "replace"))
# 200 + content-type: text/event-stream，10 秒内无事件

# 事件表（date_from/date_to 真实生效，且近 500 条无 memory_updated）
for q in ["/agent/feed/events?limit=5&include_total=true",
          "/agent/feed/events?limit=5&include_total=true&date_from=2019-01-01&date_to=2019-12-31",
          "/agent/feed/events?limit=5&include_total=true&date_from=2026-10-06&date_to=2026-10-07"]:
    st, _, bd = req(q); print(q, "->", st, json.loads(bd).get("total"))
```

### 11.13 运行 OpenAPI 分析

```python
st, _, bd = req("/openapi.json")
spec = json.loads(bd)
m = spec["paths"]["/memories"]["get"]
for prm in m.get("parameters", []):
    print(prm["name"], prm["in"], json.dumps(prm.get("schema", {})))
# 仅输出 7 个参数：limit / offset / state / importance_min / space_id / is_crystal / unit_type
print([p for p in spec["paths"] if any(t in p.lower() for t in ("sync","change","stream","delta"))])
```

---

## 12. 附：`.gitignore` / 仓库洁净性

- 本次任务**未修改** `los-memory` 仓库内任何已有文件（探测前后 `git status --porcelain` 中，除本报告外只显示一个**先前已存在**的未跟踪文件 `scripts/shadow_backup.py`，与本次任务无关）。
- 本报告是本任务中**唯一**新建的文件。
