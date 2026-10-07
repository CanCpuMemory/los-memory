# P2 写入闭环最小设计评审（隔离库）

日期：2026-10-07。状态：**设计评审稿**。本轮只出设计，**不实现、不写现网、不建库**。
依据：[总体架构](memory-service-architecture.md) §4–§7 · [检索处理](memory-retrieval-pipeline.md) §2 · [路线图](memory-roadmap.md) §4 · [就绪度复核](nowledge-replacement-readiness.md) W-09（缺口 G4）。

## 0. 为什么需要这一层，以及为什么现在只做设计

当前缺口 G4 是结构性的：影子**只读**，替代 Nowledge 所需的身份、幂等、修订、冲突、撤回、outbox 一项都不存在（L2 未达成）。而 P2 的验收矩阵要求"同一事件重复提交 100 次仅一个语义提交""两个 agent 基于同一 revision 同时修改，一个成功另一个 409"这类**负向**行为——这些不能靠后来补，必须一开始就把契约钉死。

因此本评审只做三件事：定契约、定边界、定负向测试清单。**实现前必须由用户确认 §7 的开放问题**。

## 1. 不变量（违反任何一条即设计失败）

| # | 不变量 | 为什么 |
| --- | --- | --- |
| I1 | 正式写入仍然只走 Nowledge | 双轨期不得出现两个主库 |
| I2 | 新库是 `service.sqlite3`，独立 schema/迁移/备份，**不导入**旧 codex/claude/shared 库 | 总体架构 §2 |
| I3 | 从 Nowledge 导入的记录标记 `imported_read_only`，永不被新写入路径更新 | 防止影子数据反客为主 |
| I4 | 权限由服务端凭据计算，**不接受**模型自报 owner/space | 架构 §4 |
| I5 | 只写"至少一次传输 + 幂等应用"，**不声称**端到端恰好一次 | 架构 §6 |
| I6 | 冲突不做 last-write-wins；保留双方提案与来源 | 架构 §6 |
| I7 | 撤回先阻断规范读取，再异步清理派生投影 | 架构 §9 |

## 2. 五个契约

### 2.1 身份与范围

```
principal  := 由凭据推导（设备凭据 → owner_id）
grants     := (owner, space, project, action) 的白名单，可到期/撤销
effective_scope = requested_scope ∩ granted_scope
```

- `owner_id` / `space_id` / `visibility` 由服务端补齐，**客户端不可提交**。
- `project_id` 来自项目注册表（多个设备路径/remote 别名/worktree 映射到同一 `project_id`；冲突时 `unassigned`，禁止按目录名猜）。
- 工具请求只表达"想检索哪些范围"，不表达"我是谁"。
- 凭据不落明文：存哈希 + 可撤销的 key id。

### 2.2 事件与幂等

```
POST /v1/events:batch
{ client_event_id, source_instance, thread_id, source_seq, role, observed_at, content_ref/hash }
```

- 幂等键 = `(principal, client_event_id)`。重复提交返回**同一** receipt（同 `server_seq`）。
- 同一幂等键 + 不同载荷 → `conflict`，**不覆盖**（这是与"重复提交"必须分开的两件事）。
- `server_seq` 是服务端单调序号，客户端时间戳只作 `observed_at` 参考，不作排序依据：离线设备时钟不可信。
- 事务写入 `event + receipt + outbox` 后才回 `accepted`。`accepted` **只表示原事件已持久化**，不表示"长期事实已提取"或"向量已可搜"——回执里显式区分 `canonical_committed` / `lexical_ready` / `semantic_ready`。

### 2.3 修订与冲突

```
memory_units(memory_id, owner/space/project, kind, visibility, current_revision, lifecycle)
memory_revisions(revision_id, parent_revision, content, claim_status, valid_from/to, recorded_at,
                 supersedes, contradicts, extracted_by, evidence_links[])
```

- 更新必须携带 `base_revision`。比较失败 → `409` + 当前 revision + 双方提案，**不合并、不覆盖**。
- `claim_status ∈ {proposed, asserted, unverified, undeclared}`。`undeclared` 是**默认**（实测 Nowledge 66% 无值），不得默认 `asserted`。
- 事实时间（`valid_from/to`）与记录时间（`recorded_at`）分开；未知时间留 `null`，不填伪时间。
- 时间纠错**新增修订**，不回写抹掉旧的认知记录。

### 2.4 撤回与擦除

| 操作 | 语义 | 效果 |
| --- | --- | --- |
| `:retract` | 事实纠错 | 保留审计修订，从普通检索隐藏 |
| `:erase` | 物理删除 | 生成清除计划：正文/来源 span/向量/图边/摘要/缓存/附件；备份按保留期过期 |

- 两者都先写 tombstone（**不含原文**）以阻断旧事件复活。
- 不能承诺不可达设备或离线备份瞬时删除；恢复时**先重放擦除清单再开放读取**。
- 回执必须列出"还有哪些投影/设备未完成"，`erasure_pending` 可见。

### 2.5 outbox 与离线

- 客户端：收到 receipt 才清 outbox 项；断网重试**同一** `client_event_id`。
- 服务端 changelog 提供 opaque cursor，绑定 `(owner, space, permission_epoch)`；权限变化强制重新取可见快照；游标过期返回 `resnapshot_required`。
- 离线缓存只能读标记 `stale` 的内容；离线写入只能自称 `queued`，**不能**回 `accepted`。

## 3. 最小 schema（隔离库，非本轮实现）

```sql
principals(key_id PK, owner_id, hashed_secret, device_id, created_at, revoked_at)
grants(owner_id, space_id, project_id, action, expires_at, PK(owner_id,space_id,project_id,action))
projects(project_id PK, owner_id, space_id, created_at)
project_aliases(project_id, alias_kind, alias_value, PK(alias_kind, alias_value))
source_events(server_seq INTEGER PK AUTOINCREMENT, client_event_id, source_instance,
              thread_id, source_seq, role, observed_at, received_at, content_hash,
              UNIQUE(owner_id, client_event_id))
sources(source_id PK, revision, uri, permission, chunker_version)
memory_units(memory_id PK, owner_id, space_id, project_id, kind, visibility,
             current_revision, lifecycle, imported_read_only INTEGER DEFAULT 0)
memory_revisions(revision_id PK, memory_id, parent_revision, content, claim_status,
                 valid_from, valid_to, recorded_at, supersedes, contradicts, extractor_version)
evidence_links(revision_id, source_id, source_revision, span, relation, source_family)
jobs(job_id PK, revision_id, operation, idempotency_key, lease_until, attempt,
     next_attempt, error_class, budget)
index_manifests(projection, generation, model_fingerprint, cursor, failures, active)
retractions(memory_id, kind, scope, created_at, receipt)
```

`service.sqlite3` 用 `PRAGMA foreign_keys=ON` + WAL；**单写节点**，不做多主 SQLite，不放同步盘（与现有 profile 库同样的纪律）。

## 4. 状态机（一条记忆）

```
proposed ──approve(with evidence)──▶ asserted ──supersede──▶ superseded
   │                                    │
   └──reject──▶ rejected                └──retract──▶ retracted ──erase──▶ erased
```
- `approve` 必须附来源 span；无证据的批准被拒（对应门槛 2 的"无证据批准为零"）。
- `proposed` 不进默认高可信区；模型反复提及不升级状态。
- `superseded` / `retracted` 保留历史，检索默认排除，历史查询需要单独权限。

## 5. 负向测试清单（实现后的验收，必须先写测试再写实现）

| # | 用例 | 期望 |
| --- | --- | --- |
| N1 | 同一事件重复提交 100 次 | 1 个语义提交，100 个 receipt 同 `server_seq` |
| N2 | 同幂等键、不同载荷 | `conflict`，旧记录不变 |
| N3 | 24h 离线 outbox 重放 + 乱序 + 进程中断 | 无错误覆盖、无记忆复活、`client_event_id` 幂等 |
| N4 | 两个 agent 基于同 revision 并发修改 | 一个成功，另一个 409 + 当前 revision + 双方提案 |
| N5 | 越权搜索 / get / 图边 / 缓存 / bundle / export / history | 全部拒绝，**泄露计数为 0**（逐面统计，不能只测 search） |
| N6 | 模型自报 owner/project 扩大权限 | 无效，`effective_scope` 不扩大 |
| N7 | 默认项目映射 / repo 改名 / 不同设备路径 / 同名 fork / agent_private / owner_shared | 各自符合预期；fork 不自动同一项目；冲突标 `unassigned` |
| N8 | 撤回后立即普通读取 | 阻断；派生投影/缓存 5 min 内失效 |
| N9 | 恢复备份后重放擦除清单 | 被删记忆不复活 |
| N10 | 权限 epoch 变化后的旧游标 | `resnapshot_required`，不返回越权增量 |
| N11 | 三客户端（Kimi/Codex/Grok）真实写实验数据并查看 receipt | 不能只验证工具发现 |

资源预算（继承路线图）：1 万 memory 时空闲/常态 RSS ≤256 MiB、短时 ≤512 MiB；3 并发写入持久化 p95 ≤500 ms、按 ID 读取 ≤200 ms（不含模型推理）。

## 6. 回退

禁用新增 API/客户端入口即可；实验事件在独立库，可整体导出或丢弃，**不影响**原 profile 库与 Nowledge。任何时刻 Nowledge 仍是唯一正式写入目标。

## 7. 开放问题（实现前需要用户确认）

1. **P2 在哪个空间试点**：只允许自研实验数据，还是也要把某个真实项目（如 los-memory 本身）迁进来？涉及"实验库与真实事实的边界"。
2. **身份来源**：设备凭据由谁签发（本机生成 + 手动登记，还是复用现有 SSH/Tailscale 身份）？
3. **冲突的人工裁决入口**：409 之后由谁决定？复用 DSH 会话、还是需要一个最小 review CLI？（注意：已废弃的 approval 模块**不得**复活成通用审批系统。）
4. **P2 与 P0/P1 的排期**：路线图写"P2-01 schema/API 契约与身份负向测试可同步设计，运行实现以 P0 结论为前提"。本评审满足了"可同步设计"这一半；实现是否等 P0/P1 过门槛？
5. **是否需要 HTTP**：首期继续 SSH stdio（现状），还是 P2 就上带认证的私网 HTTPS？影响凭据模型与验收方式。

## 8. 本轮不做

不建 `service.sqlite3`、不写任何 API、不改客户端默认入口、不迁移任何真实记录、不扩展 deprecated approval 模块。本文件是评审稿，不是实施授权。
