# P2 写入闭环最小设计评审（隔离库）

日期：2026-10-07（§7 决策记录补于 2026-10-10）。状态：**设计评审稿 + §7 已决策**。只出设计，**不实现、不写现网、不建库**。
依据：[总体架构](memory-service-architecture.md) §4–§7 · [检索处理](memory-retrieval-pipeline.md) §2 · [路线图](memory-roadmap.md) §4 · [就绪度复核](nowledge-replacement-readiness.md) W-09（缺口 G4）。

## 0. 为什么需要这一层，以及为什么现在只做设计

当前缺口 G4 是结构性的：影子**只读**，替代 Nowledge 所需的身份、幂等、修订、冲突、撤回、outbox 一项都不存在（L2 未达成）。而 P2 的验收矩阵要求"同一事件重复提交 100 次仅一个语义提交""两个 agent 基于同一 revision 同时修改，一个成功另一个 409"这类**负向**行为——这些不能靠后来补，必须一开始就把契约钉死。

因此本评审只做三件事：定契约、定边界、定负向测试清单。§7 的五个开放问题已于 2026-10-10 评估并给出决策记录（含依据、四轴影响与反证条件）；其中两处数据/部署边界仍归用户（§7.6）。

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

## 7. 决策记录（2026-10-10）

### 7.0 评估基线与判据

本节把原"开放问题"逐条判掉。**授权**：用户于 2026-10-10 将 §7 的评估授权给 agent（原话：按设计思路自行评估，依可维护性/可扩展性/可观测性/可回溯，整体按 DSH 纪律与 harness 设计思路）。**边界**：§1 的 I1–I7 不变量不变，本节只决定"怎么做"，不决定"要不要守约束"；若某条结论与不变量冲突，改的是结论。

四个轴按可回答的问题定义，否则评估会退化成形容词：

| 轴 | 判据问题 |
| --- | --- |
| 可维护性 | 一次变更的爆炸半径多大？能否单调回退？是否引入第二个真相源需要同步？ |
| 可扩展性 | 新增一个维度（空间/项目/客户端/服务商）是改契约还是改代码？ |
| 可观测性 | 不登录、不改代码，能否回答"现在什么状态、哪里退化、退化多久"？ |
| 可回溯 | 半年后仅凭持久记录，能否回答"这条事实是谁、用哪个凭据、基于哪个 revision、何时写的，以及为何变成现在这样"？ |

harness 纪律中与本文件直接相关的几条：**一个循环、多个适配器**（业务逻辑只有一份）；**契约先于实现，负向测试先于实现**；**每项能力都有界**（重试/队列/过期都有上限且上限被证明会触发）；**降级必须具名**，不得静默；**派生结构一律可重建**；**不复活已废弃表面**。

### 7.1 试点空间：隔离空间 + 真实只读切片，不迁移真实写入

**结论**：写路径只对专用隔离空间（`p2-lab`）开放，且只接受显式自研实验数据；同时把一份**真实项目的只读切片**导入同一隔离库（`imported_read_only=1`）用于压真实形状与体量。los-memory 自身的事实继续写 Nowledge，**不迁移写入权**。

**依据**：I2（独立库）、I3（导入记录永不被新写路径更新）；路线图 §4"新写入能力只在隔离测试空间和显式自研实验数据上验证"。纯合成数据能证明契约、证明不了形状；直接迁真实项目则让实验库获得事实权威。

**四轴**：可维护性——整个实验室空间可整体丢弃，回退是"导出或删除该库"，不与真实事实混居；可扩展性——只读切片复用影子导入适配器，将来真迁一个项目是**范围变更**而非 schema 变更；可观测性——provenance 是列也是回执字段，任何答案都能报"多少来自导入、多少来自实验写入"；可回溯——实验空间里每条 revision 要么是可追溯的导入，要么带 `client_event_id` 的实验写入，没有第三种。

**反证条件**：若契约在真实形状上失败，说明需要更多/更真实的只读切片——**而不是**放开真实项目的写入权。

### 7.2 身份来源：客户端本地生成凭据 + 服务端登记，SSH/Tailscale 只作传输

**结论**：设备凭据由客户端本地生成、服务端登记（一次性人工确认，类似 known_hosts / 节点登记），服务端存哈希 + 可撤销 `key_id` + `device_id` + grants。**不**把 SSH/Tailscale 身份直接当 `owner_id`。凭据与传输解耦：同一凭据既能走 SSH stdio 也能走 HTTPS，因此 7.5 可以后答而不改身份模型。

**凭据形态（与 §3 schema 对齐）**：首期用**对称 bearer 凭据**——客户端本地生成 32 字节 secret（`umask 077`，与现有 `backup.key` 同纪律），服务端只存哈希，与 §3 的 `principals(hashed_secret, revoked_at)` 一致。它在 stdio 与将来的 HTTPS 下语义完全相同，且服务端无需维护验签状态。若将来需要"设备不可否认"或"凭据不可被服务端重放"，再升级为非对称签名（服务端存公钥）——那是 `principals` 的**凭证类型扩展**，不改授权模型与 `effective_scope` 计算。（此处刻意写清：原问题里的"密钥对"与非对称签名是两件事，不能一边说生成密钥对、一边让 schema 存 `hashed_secret`。）

**依据**：架构 §4"owner/space 由已认证 principal 计算，不接受模型自报"；§5 principals/grants"到期与撤销、不保存明文密钥"。

**四轴**：可维护性——撤销是一次行更新，不必动 tailnet ACL；可扩展性——`(owner, space, project, action) + 到期/撤销`这种授权模型**无法**由传输身份表达，这正是必须分开的理由；可观测性——回执带 `principal` + `key_id`，可回答"哪台设备、哪个凭据、凭据何时轮换"；可回溯——身份在自有库里带版本（`revoked_at`、轮换记录），设备换钥后历史回执仍可解释；tailnet 节点身份随网络拓扑变化，钉不住历史回执。

**明确的反模式**：直接复用 tailnet 身份会让 `owner_id` 变成"网络成员资格"的函数——离开 tailnet 即静默改变授权，且无法表达"某设备可写空间 A、不可写空间 B"。

**反证条件**：设备数或换钥频率使手工登记成为瓶颈（约 >10 台或频繁轮换）时，增加自助登记流程，但服务端 `principals` 表始终是唯一真相源。

### 7.3 冲突裁决：不是队列，是可观测状态 + 一次普通写入

**结论**：
1. 409 只**记录事实**：一条 `conflict` 记录（双方 `revision_id`、`base_revision`、`principal`、时间），两个 revision 都可按 ID 读到，且**都不进入默认高可信答案**。
2. 裁决 = 用 `supersede` / `retract` / 新 revision 做一次**普通写操作**，携带 `base_revision`。**没有队列、没有状态机、没有 risk level、没有 expiry**——这些正是 deprecated approval 的特征，一律不引入。
3. 人机入口复用现有 `review` 命令族（它已是稳定 smoke contract 的一部分、已在受控 writeback 中使用），只新增一个**只读**报告列出未解决冲突及双方证据。
4. DSH 会话只是"看报告、下决定"的地方，**不持有状态**、不是审批引擎。

**依据**：I6（保留双方提案、不做 last-write-wins）；架构 §10"记忆冲突 review 是受限修订操作，不能复活通用审批系统"；§6.3"关键决策冲突进入 review"。

**四轴**：可维护性——系统里仍只有 §4 那一个状态机，没有第二个需要保持同步的权威面；可扩展性——"谁可以裁决"是一个 grant（`action=resolve`），不是新代码；可观测性——未解决冲突是一个带年龄的可查询计数，这才是该看的指标，而不是"审批积压"；可回溯——双方提案与裁决都是带 parent 链接的 revision，决策历史**就是**记忆历史，不存在需要与记忆对账的平行审计日志。

**为什么不做专用队列/CLI**：那会复制写路径（两条改记忆的方式）并造出第二个权威表面，违反"一个循环、多个适配器"。

**反证条件**：出现无人认领的冲突堆积时，加**通知**，不加工作流；若真需要多方会签，那是 P2 之外的新领域，需要独立设计。

### 7.4 排期：契约现在做，运行时以 P0 退出条件为前提（并把 P1 依赖拆细）

**结论**：把 P2 实现拆成三段，各自有独立门禁：

| 段 | 内容 | 门禁 |
| --- | --- | --- |
| **P2-01 契约层** | 版本化 schema、receipt 语义、身份/授权模型、N1–N11 负向测试**先于实现** | **现在即可做**，不需要 P0：零运行风险，且是 P0 报告的对照物 |
| **P2-02 隔离库写入运行时** | `service.sqlite3`、事件/修订/outbox、幂等与冲突 | **P0 退出条件**：冻结语料达到要求规模，且所有期望 ID 可按规范来源读取 |
| **P2-03 跨设备与三客户端** | outbox 重放、乱序、进程中断、Kimi/Codex/Grok 真实写入 | P1 的"断网恢复不重复建记忆 + 恢复副本 ID/revision/hash 一致"；**不**依赖 P1 的流量目标 |

**为什么运行时必须等 P0（本轮新增的实证理由）**：写入闭环的验收语句是"写进去的事实能被正确检索到"。2026-10-10 实测主库 `recall-probe` 判定 `stale_projection_suspected`——`recent_rate 0.00`、`control_rate 0.667`（报告 §8.3）。在这个状态下任何"写-查"验收测的是**主库索引**，不是写路径。P0 的作用正是冻结一个可信的检索基线，否则 P2-02 会得到一个无法归因的失败。

**与路线图原文的差异（收紧）**：原文 P2 依赖写"P0；上线前 P1"。此处把 P1 依赖**拆细**——outbox/恢复能力依赖 P1 的恢复证据，流量优化（−70%）与写入正确性正交，不作为 P2-03 门禁。已同步到 `memory-roadmap.md` §10。

**明确不做**：不因本设计冻结 Nowledge 写入（那是 P6）；不因 P2 可用而产生两个主库（I1）。

**反证条件**：若 P0 的冻结语料无法建立（主库持续变化导致期望 ID 不可复现），P2-02 **不得开工**，此时阻塞项是主库索引问题本身，应显式记为外部阻塞而不是降低门槛。

### 7.5 传输：首期不上 HTTP，但把契约做成传输无关，并写明 HTTPS 的触发条件

**结论**：
- 应用层契约（请求/响应 schema + receipt + cursor 语义）**传输无关**，P2-01 只交付它 + 一个 in-process 适配器。
- 传输适配器按需增加：`stdio`（MCP，现有路径）与**常驻服务 + 薄桥**（同机 CLI/MCP 共享一个服务实例，架构 §3 的目标形态）。
- HTTPS 作为**第三个适配器**，触发条件显式化：出现**并发多设备写者**，或需要**服务端推送（changelog cursor/通知）**，或必须离开 tailnet/SSH 信任域。

**为什么不在 P2 就上 HTTPS**：可维护性——现网没有常驻服务、没有 TLS 生命周期与证书轮转，引入它会把 P2 验收从"契约是否正确"变成"部署是否正确"，两者同时失败时无法归因；可观测性——HTTPS 的主要增量是访问日志与健康端点，这在 stdio 下用同一条 receipt 台账 + 一个本地 status 命令即可获得；可回溯——两种传输下的 receipt 与 cursor 语义必须**完全一致**，先定义成传输无关的，迁移时才不会出现两套回执。

**必须写清的 stdio 真实代价**：当前部署是 SSH stdio 且**每个客户端一个进程**（不是架构 §3 说的"共享常驻服务 + 薄桥"），因此没有共享 outbox/游标；而且本轮实测证明它对宿主网络抖动敏感——DSH 影子 MCP 反复掉线，`dsh-mcp-client` 的默认策略 10 次尝试（退避总和约 121.5 s）耗尽后**注销全部工具且只能靠重启恢复**。这既是"常驻服务 + 薄桥"要解决的问题，也是 HTTPS 触发条件的一部分。

**反证条件**：若实验阶段就需要两个设备并发写入，或某客户端需要基于游标的变更流，直接上 HTTPS，不要在 stdio 上自制多路复用。

### 7.6 仍需用户确认的部分（未被授权评估）

- 7.1 的**真实只读切片的项目范围**（导入哪些项目、是否包含敏感内容类别）——涉及数据边界，属用户决定。
- 7.5 的 HTTPS 触发条件一旦命中，**部署位置与信任域**（NAS34 / M3 / 其他）属用户决定，并由 P6 承接。


## 8. 本轮不做

不建 `service.sqlite3`、不写任何 API、不改客户端默认入口、不迁移任何真实记录、不扩展 deprecated approval 模块。本文件是评审稿，不是实施授权。
