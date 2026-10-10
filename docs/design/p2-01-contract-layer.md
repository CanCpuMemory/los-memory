# P2-01 契约层交付（版本化 schema + receipt 语义 + 身份/授权模型）

日期：2026-10-11。状态：**已交付（契约层，零运行风险）**，负向测试先行。
依据：[P2 写入闭环最小设计评审](p2-write-path-minimal-loop.md) §2–§5 与 §7（2026-10-10 决策）· [总体架构](memory-service-architecture.md) §4–§6 · [路线图](memory-roadmap.md) §10。

本文件记录 **P2-01 段**交付了什么、每个决策的可判定语义是什么、N1–N11 里哪些今天真的被断言、哪些显式留给 P2-02/P2-03。它不是实施授权：P2-02（隔离库运行时）仍以 P0 退出条件为前提。

## 1. 交付边界

**做了**（全部为新增文件，不改任何现有行为）：

| 层 | 代码 | 内容 |
| --- | --- | --- |
| 版本 | `memory_tool/service_contract/versioning.py` | 契约版本常数、semver 兼容判定、6 个 wire schema 的加载与校验（自带校验器，零依赖） |
| 身份 | `memory_tool/service_contract/identity.py` | principal/凭据登记/撤销、grants、`effective_scope = requested ∩ granted`、服务端专有字段扫描 |
| 模型 | `memory_tool/service_contract/models.py` | 请求/结果/receipt 形状、`claim_status` 缺省规则、7 个读面、项目注册表、tombstone/擦除清单/cursor 结果 |
| receipt | `memory_tool/service_contract/receipts.py` | `accepted`/`queued`、阶段位、具名降级、客户端 outbox 状态机 |
| 存储声明 | `memory_tool/service_contract/storage.py` | `service.sqlite3` 的表/列声明 + 迁移版本 + **不执行**的 DDL |
| 适配器 | `memory_tool/service_contract/adapter.py` | in-process 参考实现（§7.5 授权的"一个 in-process 适配器"） |
| 测试 | `tests/unit/test_service_contract_negative.py` | N1–N11 负向测试（**先写**，见 §6） |
| 测试 | `tests/unit/test_service_contract_contract.py` | 版本策略/wire 一致性/提交原子性/存储声明漂移门/零运行风险 |

**没做**（与设计 §8 一致）：不建 `service.sqlite3`、不写任何 API/传输、不改客户端默认入口、不改 `memory_tool/__init__.py` 或 CLI、不导入任何真实记录、不碰 deprecated approval。

零运行风险的**机械证据**：`memory_tool.service_contract` 不被 `memory_tool` 或 `memory_tool.cli` 导入（子进程断言 `sys.modules`），`cli.py`/`__init__.py`/`__main__.py` 文本中不出现该包名，包内没有任何 `argparse`/`socket`/`http`/`urllib`/`sqlite3` 导入，且跑完一轮写-读-擦除后临时目录仍为空。见 `test_service_contract_contract.py` 末尾四条。

## 2. 版本化 schema

**契约版本**：`CONTRACT_VERSION = "1.0.0"`（应用层请求/回执），`WIRE_SCHEMA_VERSION = "1.0.0"`（JSON Schema 形状），`STORAGE_SCHEMA_VERSION = 1`（隔离库）。

兼容规则是**强制**的，不是装饰：请求必须声明 `contract_version`；缺省 → `missing_contract_version`，major 不同或 minor/patch 高于服务端 → `unsupported_contract_version`。同 major 且不高于服务端即接受（对客户端是加性兼容）。

**wire schema**（`schemas/v1/`，draft-07，`additionalProperties: false`）：`event-submission`、`memory-proposal`、`receipt`、`face-result`、`event-view`、`error`。schema 只允许使用校验器实现的关键字子集（`SUPPORTED_SCHEMA_KEYWORDS`），用了子集外的关键字（例如 `anyOf`）会**报错**而不是被静默忽略——"看起来有约束、实际没校验"比没有 schema 更糟。校验器自带、零依赖，避免"装了 jsonschema 才通过"的不确定测试。

形状与实现的一致性由测试机械保证：所有请求/回执/事件读/读面结果/拒绝都要通过各自 schema；每个错误的 `code` 必须出现在 `error.schema.json` 的枚举里；跨文件重复的内联定义（`requested_scope`/`evidence_link`/`degradation`）必须逐字一致；层内每一个 `to_wire()` 产物都必须能被 `json.dumps` 原样序列化（枚举/对象泄进 wire 会立刻失败）。

schema 覆盖是**具名**的：受 schema 校验的是传输会用到的信封（请求、receipt、事件读、读面结果、错误）。`ErasureManifest`/`ErasureReplay`/`CursorReadResult`/`Tombstone`/`ConflictRecord`/`RequestedScope`/`EffectiveScope` 目前只有 dataclass 形状 + `to_wire` + 可序列化门禁，**没有** JSON Schema；P2-02 出现真实传输时补齐这一组，而不是现在预言它们的字段。

**存储 schema 是声明，不是执行**。`STORAGE_DDL` 十二张表来自设计 §3，本层不打开任何连接；测试用一次性的 `:memory:` 连接验证它是合法 SQL 且列集合与声明一致（不产生文件）。为避免"设计文档与代码各自漂移"，漂移门要求：§3 草图里出现的**每个表、每一列**都必须在声明中存在；声明里多出来的列**必须**在 `STORAGE_SCHEMA_ADDITIONS` 登记并写明理由。据此发现并登记了 §3 草图的**两个缺陷**（未改设计文档正文，登记在代码里）：

1. `source_events` 的 `UNIQUE(owner_id, client_event_id)` 引用了一个从未声明的列，而且键比 §2.2 规定的 `(principal, client_event_id)` 更粗。声明改为 `principal_key_id` + `UNIQUE(principal_key_id, client_event_id)`。
2. `project_aliases` 没有 `environment` 列，而架构 §4 要求同一项目的生产/测试/开发配置可区分——不登记该列就无法同时注册两个环境。

## 3. receipt 语义

| 语义 | 规则 | 为什么 |
| --- | --- | --- |
| `accepted` 的含义 | 仅表示**原载荷已持久化**。`stages.canonical_committed` 为真，`lexical_ready`/`semantic_ready`/`graph_ready` 为假，并带具名降级 `projections_pending` | "写进去了"和"能搜到"是两件事；把两者合成一个字段就会写出无法归因的验收 |
| 幂等键 | `(principal, client_event_id)`。同载荷重放返回**同一** receipt（同 `server_seq`、同 `receipt_id`），`replayed=true` | 至少一次传输 + 幂等应用；端到端恰好一次不承诺 |
| 冲突 | 同键不同载荷 → `idempotency_conflict`（409），旧记录不变，拒绝里带旧 receipt 与两个载荷哈希 | 与"重复提交"是两件事，必须能分开 |
| 客户端自报 | 客户端只能产出 `queued`（`make_queued_receipt`）；`OutboxState` 里**没有** `accepted`；`accepted` 只能由服务端在同一事务里铸出 | 离线写入不能声称持久化 |
| outbox 清空 | 只有收到服务端 `accepted` receipt 才清（否则 `offline_write_not_accepted`）；冲突项进 `conflict` 状态，不静默丢弃 | 断网/崩溃重试必须复用同一个 `client_event_id` |
| 提交原子性 | event + receipt + outbox（记忆写入另加 unit/revision）在同一事务里；**序号也在事务内分配**，失败提交不消耗序号 | 失败提交不会留下"半个 accepted"，重试复用同一序号 |
| 提交后故障 | 故障注入在提交边界**之后**抛错时**不回滚**：写入已持久，重试返回同一 receipt（`replayed=true`） | 这是"写成功但回执丢了"的真实形态，也是幂等键存在的理由 |
| cursor | 不透明随机 token，服务端绑定 `(owner, space, permission_epoch)`。epoch 变化或换 principal 使用 → `resnapshot_required` 且 `deltas` 为空 | 绝不返回可能越权的部分增量。P2-02 可以换成签名式无状态 cursor，wire 语义不变（"不透明"是契约，不是实现） |
| 撤回 | 先阻断规范读取（search/get/cache 立刻看不到，按 ID 读 → `retracted_record_not_readable`），再异步清派生。tombstone **不含原文**；回执用 `derived_pending` 列出未完成的派生对象，并在 ≤300 s 内失效 | I7；"已撤回"必须立刻为真，"派生已清"是另一个带期限的事实 |
| 擦除 | 产出可重放的擦除清单（正文/来源 span/向量/图边/摘要/缓存/附件），`complete=false`，离线设备与备份保留期进 `deferred_targets`。重放幂等 | 恢复备份后必须先重放擦除清单再开放读取 |

## 4. 身份与授权模型（I4 的执行规则）

- `principal := (key_id, owner_id, device_id, actor_agent_id)` 全部由凭据推导；服务端只存 `hash_secret`（salted SHA-256）与可撤销的 `key_id`。注册时一次性返回 secret，快照里只有哈希。
- `grants := (owner_id, space_id, project_id, action)` + 到期/撤销，`action ∈ {read, event.append, memory.propose, memory.revise, memory.retract, memory.erase, conflict.resolve, history, export, changelog.read}`。
- `effective_scope = requested_scope ∩ granted_scope`。**拒绝矩阵**（每条都是可判定的，且都有测试）：
  - 载荷里出现服务端专有字段（`owner_id`/`space_id`/`visibility`/`device_id`/`actor_agent_id`/`key_id`/`hashed_secret`/`server_seq`/`receipt_*` …，任意深度）→ `identity_field_rejected`。**拒绝而不是静默剥离**：静默剥离会让调用方以为写进了另一个 space。身份扫描在 schema 校验**之前**执行，所以报的是身份违规而不是笼统的"未知字段"。
  - 凭据未知/被撤销/secret 错误 → 同一个 `unauthenticated`（不区分，避免探测 key_id）。
  - 显式点名了范围但没有一个落在授权内 → `scope_not_granted`（**不返回空结果**：静默空集就是"搜遍全部再猜归属"的温床）。有部分命中时按交集收窄，并把丢弃项放进 `denied`。
  - 未点名范围 → 默认取该 owner 的授权范围。
  - 缺少某个面所需的 action → `face_not_granted`。7 个读面逐面要求自己的 action（`export`、`history` 与 `read` 是三个不同的授权）。
- `visibility` 是服务端计算的（默认最保守的 `agent_private`），由 `set_visibility_policy`（授权变更，会推进 permission epoch）调整；同一 owner 的另一个 agent 读不到 `agent_private`，改policy后才读得到。
- **身份不是设备维度**：grants 键在 `(owner, space, project, action)`，同一 owner 的第二台设备天然继承同样的授权。这是架构 §4 的模型，测试里显式断言过，避免把"设备隔离"当成授权。

读面细节：`search` 只返回 `asserted`（`proposed` 不进默认高可信区）；`get` 按 ID 返回当前/指定 revision；`history` 需要独立授权且能读到 retracted 的 revision（但永远读不到 erased）；`cache` 返回曾投递过的条目并标 `stale`，撤回后立刻失效；`bundle`/`export` 在范围内导出；`graph` 目前**不编造边**——P2-01 没有关系投影（那是 P4），因此返回空集并给出具名降级 `graph_projection_not_implemented`。服务端返回的条目同时带 `lifecycle`（revision 自身状态）与 `unit_lifecycle`（该记忆当前状态），历史才看得出状态机演进。

## 5. N1–N11：今天被断言的部分与显式延后的部分

| # | 契约层今天断言的 | 显式延后（谁） |
| --- | --- | --- |
| N1 | 100 次同载荷提交 → 1 个事件、100 个 receipt 同 `server_seq`/`receipt_id`，首个 `replayed=false`；`accepted` 且 `lexical_ready=false` | 真实 100 次网络重试的传输层（P2-03） |
| N2 | 同键不同载荷 → 409 + 旧记录不变 + 旧载荷哈希；幂等键按 principal 隔离（不同 principal 同 ID = 两次提交） | — |
| N3 | outbox 状态机、乱序重放、崩溃后重放 3 轮仍 24 事件、`server_seq` 按到达顺序而非客户端时钟/`source_seq`、客户端不能自称 accepted | 24 h 真实离线、真实进程中断（P2-03）；P1 的恢复证据是门禁 |
| N4 | 同 base_revision 并发：一个成功、一个 409 + 当前 revision + 双方提案（败方提案**已持久化**且可按 ID 读）、冲突台账、两者都不进默认高可信 | 真实并发客户端（P2-03） |
| N5 | 7 个读面**逐面**统计外部真值 token 泄漏数 = 0；显式越权范围逐面被拒；按 ID 直取越权记录也被拒且错误里不含内容；凭据不可探测；`agent_private` 对同 owner 的另一 agent 不可见 | 缓存/bundle/export 的真实传输形态（P2-02/P2-03） |
| N6 | 载荷自报 owner/space/visibility/device/actor 一律无效（拒绝）；自报 project 不扩大 `effective_scope`；落库 scope 是授权 scope | — |
| N7 | 别名/设备路径/worktree→同一项目；同目录名 fork 不自动同项目；别名冲突标 `unassigned` 并可复核；`environment` 是别名键的一部分 | 真实 git remote 探测与注册表落库（P2-02） |
| N8 | 撤回后立即阻断 search/get/cache；tombstone 不含原文；`derived_pending` 命名待清对象；失效期限 ≤300 s；history 仍可读 | 真实 FTS/向量/图投影与缓存 5 min 失效（P2-02） |
| N9 | 备份→擦除→恢复→**重放擦除清单**：内容先复活（证明恢复真的发生）再被清除，重放幂等（第二次 `already_applied`）；快照不含明文 secret；恢复后幂等仍成立（去重读规范日志而非 receipt 表） | 真实异地备份/恢复（P1/P2-02）；恢复清单重放顺序（P2-02） |
| N10 | cursor 不透明；epoch 变化 → `resnapshot_required` 且 0 增量、0 泄漏；跨 principal 使用同样拒绝 | 真实多设备增量流（P2-03） |
| N11 | 三客户端 receipt 形状逐字段一致、幂等键互不串扰、各自只读到自己的内容 | 真实 Kimi/Codex/Grok 进程写入（P2-03） |

## 6. 先写负向测试的证据

按项目纪律，`tests/unit/test_service_contract_negative.py` 在实现**之前**写就并运行：当时的运行结果是 collection 失败（`ModuleNotFoundError: No module named 'memory_tool.service_contract'`，0 条被收集，即测试文件已存在而实现不存在），实现后该文件 44 条全绿。测试内部用外部真值（`FKTOKENALPHA`/`FKTOKENBETA` 与外部记录 ID）统计泄漏，不采信适配器自报的计数。

## 7. 验证方法

```bash
.venv/bin/python -m pytest tests/unit/test_service_contract_negative.py tests/unit/test_service_contract_contract.py -q
.venv/bin/python -m ruff check --select E501,D105,D107,I001,F,B memory_tool/service_contract tests/unit/test_service_contract_*.py
.venv/bin/python -m pytest tests/unit -m "not e2e" -q
```

## 8. 回退

删掉 `memory_tool/service_contract/`、两个测试文件与 `pyproject.toml` 的两行登记即可，爆炸半径为 0：没有别的模块导入它，没有数据库文件、没有服务、没有客户端入口、没有迁移。实验数据（若 P2-02 起建）在独立库里，可整体导出或丢弃。

## 9. 仍然归用户的边界（设计 §7.6，未被授权评估）

- 真实只读切片**导入哪些项目 / 是否含敏感类别**。
- HTTPS 触发条件命中后的**部署位置与信任域**（由 P6 承接）。

## 10. P2-01 明确不做、留给 P2-02/P2-03 的事

`service.sqlite3` 与迁移执行、WAL 下的真实事务与写租约、FTS/向量/图投影、缓存失效的真实期限、outbox 重放的真实网络与设备、跨设备 cursor 流、HTTPS 适配器、三客户端真实写入。契约层只交付"一个循环"和它的可判定语义；这些能力都以本文件的契约与测试为对照物。
