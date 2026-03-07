# 四项目架构边界说明书 v1.2

**适用范围**: los-ast、los-memory、lsclaw、VPS Agent Web
**版本**: v1.2 (正式版)
**修订日期**: 2026-03-07

**关键修订**（本次最重要的修正）：
- **明确区分 los-memory "当前态"与"目标态"**：当前是 CLI-first 的个人/代理记忆工具，不是服务化的中心知识服务
- **架构图调整**：memory 当前作为"辅助能力层"而非与 los-ast 对称的"底层能力层"
- **增加非目标清单**：明确当前阶段不追求的能力
- **定义服务化触发条件**：提供可判断的演进门槛
- **接口修正**：将错误归属到 `los-memory` 的 discover/validate/enumerate 接口移除；这些接口仅保留在 `los-ast` 能力定义中，补充 los-memory 实际 CLI 接口

---

## 1. 总体架构原则

### 1.1 分层原则

#### 当前架构（Current Form）

```
┌──────────────────────────────────────────────────────────┐
│                    上层控制与执行层                       │
│                    VPS Agent Web                         │
│            (入口、编排、审批、审计、展示)                  │
├──────────────────────────────────────────────────────────┤
│                      中间治理层                           │
│                      lsclaw                               │
│            (LLM路由、策略、治理、标准化)                   │
├──────────────────────────────────────────────────────────┤
│                      底层能力层                           │
│                    ┌─────────────┐                       │
│                    │  los-ast    │                       │
│                    │ 代码理解内核 │                       │
│                    └─────────────┘                       │
├──────────────────────────────────────────────────────────┤
│              辅助能力层（可被各层接入）                    │
│                    los-memory                            │
│           (CLI 记忆工具 / Agent-side memory)              │
└──────────────────────────────────────────────────────────┘
```

> **说明**: 当前阶段 `los-memory` 作为旁路型本地记忆工具存在，通过 CLI/Python API 被代理、CLI 工具、控制面接入。它**不是**与 `los-ast` 对称的中心服务。

#### 目标架构（Target Form）

当服务化条件满足时，`los-memory` 可抬升为与 `los-ast` 对称的底层能力层核心组件：

```
┌─────────────────────────────────────────┐
│         上层控制与执行层                  │
│         VPS Agent Web                   │
├─────────────────────────────────────────┤
│           中间治理层                      │
│           lsclaw                         │
├─────────────────────────────────────────┤
│           底层能力层                      │
│  ┌─────────────┐    ┌─────────────┐     │
│  │  los-ast    │    │ los-memory  │     │
│  │ 代码理解内核 │    │ 知识组件     │     │
│  └─────────────┘    └─────────────┘     │
└─────────────────────────────────────────┘
```

#### 职责对照

| 层级 | 项目 | 核心职责 | 当前形态 |
|------|------|----------|----------|
| 上层控制层 | VPS Agent Web | 任务编排、HITL审批、审计追踪、dashboard | 服务 |
| 中间治理层 | lsclaw | provider适配、路由决策、预算治理、熔断降级 | 服务 |
| 底层能力层 | los-ast | 代码解析、AST建模、影响面分析、证据输出 | 服务/库 |
| 辅助能力层 | los-memory | 工作记忆持久化、经验沉淀、工具追踪 | **CLI 工具** |

### 1.2 单向依赖原则

**允许的依赖方向**:
- `VPS Agent Web` → `lsclaw`
- `VPS Agent Web` → `los-ast`
- `VPS Agent Web` → `los-memory`
- `lsclaw` 可被上层调用
- `los-ast` 与 `los-memory` 可被上层调用

**禁止的依赖方向**:
- `los-ast` → `VPS Agent Web`
- `los-memory` → `VPS Agent Web`
- `los-ast` → `lsclaw`
- `los-memory` → `lsclaw`
- `los-ast` ↔ `los-memory` 直接强耦合
- 任意项目反向依赖调用方内部状态

**一句话概括**: 上层编排下层，下层不感知上层。

### 1.3 所有权原则

边界不靠"感觉"定义，而靠以下三条：

1. **谁拥有核心长期状态**
2. **谁对该状态有最终写权限**
3. **谁是该对象的 Source of Truth**

如果一个项目只是消费结果，而不拥有该对象真相，它就不是该对象的归属方。

---

## 2. 项目定义

### 2.1 los-ast

#### 定位
`los-ast` 是 **Code Intelligence Kernel**。负责将代码库转化为可查询、可验证、可改写的结构化表示，并对代码变更提供证据与影响面分析。

它回答的问题是：
- 代码结构是什么？
- 这次改动会影响哪里？
- 哪些位置满足某类规则？
- 如何基于结构安全地进行 rewrite？
- 哪些证据支持这次分析结论？

#### 核心职责
- 代码解析（多语言 AST/CST）
- 符号、引用、调用关系建模
- 影响面分析
- 规则扫描与违规检测
- 批量 rewrite / patch candidate 生成
- 证据输出与引用
- 代码图谱生成与增量更新

#### 拥有对象
| 对象 | 说明 |
|------|------|
| `CodeGraph` | 代码结构图 |
| `AstNodeIndex` | AST 节点索引 |
| `SymbolIndex` | 符号索引 |
| `ReferenceGraph` | 引用关系图 |
| `ImpactReport` | 影响面分析报告 |
| `RewriteCandidate` | 改写候选 |
| `EvidenceBundle` | 证据包 |
| `GraphDelta` | 图谱变更集 |

#### 不负责的内容
- 长期记忆管理
- corrected facts 账本
- 任务调度与执行编排
- 审批流程
- 模型路由与 provider 策略
- 用户会话管理
- dashboard 展示逻辑

#### 对外接口
**类型 1: discover** - 发现代码结构事实
- `discover_symbols(repo, file_pattern)`
- `discover_callers(repo, symbol_id)`
- `discover_tenant_boundaries(repo)`
- `discover_sql_builders(repo)`

**类型 2: validate** - 验证结论是否被代码事实支持
- `validate_isolation_enforced(repo, module)`
- `validate_symbol_usage(repo, symbol_id, context)`
- `validate_patch_safety(repo, patch)`

**类型 3: enumerate** - 枚举目标或证据
- `enumerate_candidate_files(repo, criteria)`
- `enumerate_unsafe_patterns(repo, pattern)`
- `enumerate_impacted_modules(repo, change_set)`

#### 输出约束
- 必须包含 object id、repo/revision、scope
- 必须包含 evidence references
- 必须包含 confidence 分数
- 必须包含 schema version

#### 禁止越界
- 禁止写入 corrected facts 账本
- 禁止做"记住这次教训"的 ledger 写入
- 禁止决定"下一步该用哪个模型"
- 禁止判断"这个任务该谁审批"

---

### 2.2 los-memory

#### 定位（两层定义）

**当前形态（Current Form）**

> 一句话：CLI-first 辅助记忆组件，提供长期知识账本的最小子集能力。

`los-memory` 当前是 **个人/代理级本地记忆工具**（CLI + Python API 形态）。

它服务于：
- Claude/Codex 这类代理的持续工作记忆
- 开发者的本地上下文沉淀
- 任务过程中的事实记录、反馈修正、检查点保存、关联追踪

它回答的问题是：
- 我之前记录过什么？
- 这个代理上次做到哪？
- 某个结论后来被纠正了吗？
- 某个工具调用留下了什么痕迹？
- 这组观察之间有什么关联？

**桥接表述：当前态如何体现"账本"属性**

当前阶段，`los-memory` 以 CLI-first 的个人/代理记忆工具形态实现"长期知识与纠错账本"的**最小子集能力**。

其"账本"属性当前主要体现在：
- **Observation** - 可追溯的事实记录（支持多态：decision/incident/note/meeting/todo/snippet/tool_call）
- **Feedback** - 事后修正机制（correct/supplement/delete）
- **Checkpoint** - 工作现场的可恢复快照
- **ToolCall** - 工具调用历史的完整追踪
- **ObservationLink** - 事实间的关联关系

而非服务化的项目级治理、正式审批流程或多租户隔离。

**目标形态（Target Form）**

> 一句话：必要时演进为项目级知识组件，支持正式治理与多项目共享。

`los-memory` 在未来可演进为 **项目级长期知识组件**，承接：
- 正式的 corrected facts ledger
- 多源 knowledge source refs
- proposal / commit 审批流程
- 服务化接口（HTTP/gRPC）
- 项目级共享与治理

> **关键判断**: 当前阶段是 CLI-first 的本地工具，不是服务化的中心知识服务。CLI 工具将保持向后兼容，未来服务化时会作为客户端保留。

#### 当前非目标（Non-Goals for Current Phase）

`los-memory` 当前**不是**也**不追求**成为：

- ❌ 项目级共享知识服务
- ❌ 多租户/多项目正式隔离中心
- ❌ 审批系统或 proposal/commit 完整治理
- ❌ 全局事实真相源（Source of Truth for organization）
- ❌ 分布式 trace 中心
- ❌ 替代审计系统
- ❌ HTTP/gRPC 服务（当前阶段）

> 这些能力可能在未来服务化阶段引入，但当前阶段明确不追求。

#### 服务化触发条件

只有当以下条件满足 **2~3 条**时，才启动服务化演进：

| 条件 | 当前状态 | 阈值 |
|------|----------|------|
| 多个系统需要并发共享同一记忆库 | 否 | 有 3+ 项目同时依赖 |
| 需要正式 proposal/commit 审批流程 | 否 | 存在合规/审批需求 |
| 需要项目级权限与治理 | 否 | 存在多租户隔离需求 |
| 需要跨机器共享记忆 | 否 | 分布式团队场景 |
| CLI 调用延迟/可靠性成为瓶颈 | 否 | P99 延迟 > 500ms 或失败率 > 1% |

> 在当前阶段，上述条件均不满足，因此保持 CLI-first 是正确选择。

#### 部署形态

**当前形态（MVP）**:
- 本地 CLI 工具 (`python -m memory_tool`)
- Python API (`from memory_tool import ...`)
- 本地 SQLite 存储

**Profile 机制**（多代理隔离）:
- `claude`: `~/.claude_memory/memory.db`
- `codex`: `~/.codex_memory/memory.db`
- `shared`: `~/.local/share/llm-memory/memory.db`

**演进方向（可选）**:
- 本地服务化（HTTP/gRPC API）
- CLI 保持作为客户端工具
- 可选 PostgreSQL 后端

#### 核心职责
- 工作记忆持久化（Observation、Session、Checkpoint）
- 事后反馈与修正（Feedback 机制）
- 工具调用追踪（ToolCall）
- 记忆关联管理（ObservationLink）
- 记忆检索与时间线查询
- 数据导出/导入/清理

#### 拥有对象
| 对象 | 类型 | 说明 |
|------|------|------|
| `Observation` | 核心 | 记忆基本单元，支持多态（decision/incident/note/meeting/todo/snippet/tool_call） |
| `Session` | 核心 | 工作会话，组织相关 Observation |
| `Checkpoint` | 核心 | 检查点，保存工作现场 |
| `Feedback` | 治理 | 对已有记忆的反馈与修正 |
| `ObservationLink` | 治理 | 记忆间的关联关系 |
| `ToolCall` | 追踪 | 工具调用记录 |

#### 不负责的内容
- 代码扫描（属于 los-ast）
- AST 解析（属于 los-ast）
- provider 路由（属于 lsclaw）
- 实时任务执行（属于 VPS Agent Web）
- 审批 UI（上层控制层职责）
- 全量代码图谱维护（属于 los-ast）

#### 对外接口

**类型 1: Memory CRUD**
| 接口 | 说明 | 对应 CLI |
|------|------|----------|
| `add` | 写入观察记录 | `add` |
| `ingest` | 从文件/stdin 摄入 | `ingest` |
| `search` | 全文检索 | `search` |
| `timeline` | 时间线查询 | `timeline` |
| `get` | 按 ID 获取 | `get` |
| `list` | 列表查询 | `list` |
| `edit` | 编辑记忆 | `edit` |
| `delete` | 删除记忆 | `delete` |

**类型 2: Memory Governance**
| 接口 | 说明 | 对应 CLI |
|------|------|----------|
| `feedback` | 反馈修正 | `feedback` |
| `link` | 创建关联 | `link` |
| `unlink` | 移除关联 | `unlink` |
| `related` | 查询关联 | `related` |
| `clean` | 清理记忆 | `clean` |
| `manage` | 维护管理 | `manage` |

**类型 3: Session & Checkpoint**
| 接口 | 说明 | 对应 CLI |
|------|------|----------|
| `session_start` | 开始会话 | `session` |
| `session_end` | 结束会话 | `session` |
| `checkpoint_create` | 创建检查点 | `checkpoint` |
| `checkpoint_resume` | 恢复检查点 | `checkpoint` |

**类型 4: Tool Tracking**
| 接口 | 说明 | 对应 CLI |
|------|------|----------|
| `tool_log` | 记录工具调用 | `tool-log` |
| `tool_stats` | 工具统计 | `tool-stats` |
| `tool_suggest` | 工具推荐 | `tool-suggest` |

**类型 5: Utility**
| 接口 | 说明 | 对应 CLI |
|------|------|----------|
| `doctor` | 环境检查 | `doctor` |
| `export` | 导出数据 | `export` |
| `import` | 导入数据 | `import` |
| `init` | 初始化数据库 | `init` |

#### 集成契约

**CLI 调用规范**:
```bash
python -m memory_tool [command] [options] [--json]

# 全局选项
--profile {claude,codex,shared}  # 选择 profile
--json                           # JSON 输出
--db-path PATH                   # 指定数据库路径
```

**JSON 输出 Schema**:
```json
// 成功响应
{
  "ok": true,
  "data": { ... },
  "meta": {
    "profile": "claude",
    "db_path": "...",
    "timestamp": "2026-03-07T10:00:00Z"
  }
}

// 错误响应
{
  "ok": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "Observation 123 not found",
    "suggestion": "Use 'search' to find valid IDs"
  }
}
```

**退出码规范**:
| 退出码 | 含义 | 处理建议 |
|--------|------|----------|
| 0 | 成功 | - |
| 1 | 参数错误或业务错误 | 查看 JSON 错误消息 |
| 2 | 配置错误 | 运行 `doctor` 命令 |
| 3 | 数据库错误 | 检查 DB 路径和权限 |
| 127 | 命令未找到 | 检查 Python 环境 |

**Python API 调用**:
```python
from memory_tool.database import connect_db, ensure_schema
from memory_tool.operations import add_observation, run_search
from memory_tool.sessions import start_session, end_session
from memory_tool.checkpoints import create_checkpoint
from memory_tool.feedback import apply_feedback
from memory_tool.links import create_link, get_related_observations
from memory_tool.analytics import log_tool_call, get_tool_stats

# 连接数据库
conn = connect_db("~/.claude_memory/memory.db")
ensure_schema(conn)

# 搜索记忆
results = run_search(conn, "API design", limit=10)
```

#### 禁止越界
- 禁止直接扫描代码库（属于 los-ast）
- 禁止直接修改 CodeGraph（属于 los-ast）
- 禁止直接管理 provider policy（属于 lsclaw）
- 禁止直接作为任务状态机（属于 VPS Agent Web）
- 禁止存成"什么都往里塞"的大杂烩
- 禁止直接替代审计系统

---

### 2.3 lsclaw

#### 定位
`lsclaw` 是 **LLM Gateway / Routing / Governance Layer**。负责统一多模型 provider 访问、请求标准化、策略执行、成本与风控治理。

它回答的问题是：
- 这次请求应该发给哪个 provider / model？
- 遇到失败该如何 fallback？
- 当前预算和策略是否允许调用？
- 如何把不同模型接口统一成标准格式？
- 如何记录跨 provider 的一致 trace？

#### 核心职责
- provider 适配与统一接口
- 请求/响应标准化
- 路由决策（model selection）
- fallback / retry / timeout / 熔断
- budget / quota / cost policy
- 模型能力画像
- policy enforcement
- 请求级 tracing / metrics

#### 拥有对象
| 对象 | 说明 |
|------|------|
| `ProviderProfile` | Provider 配置画像 |
| `ModelProfile` | 模型能力画像 |
| `RoutingPolicy` | 路由策略规则 |
| `BudgetPolicy` | 预算策略规则 |
| `CircuitState` | 熔断器状态 |
| `RequestTrace` | 标准化请求追踪 |
| `NormalizedRequest` | 统一请求格式 |
| `NormalizedResponse` | 统一响应格式 |
| `ToolPolicy` | 工具调用策略 |
| `GuardrailRule` | 安全护栏规则 |

#### 不负责的内容
- 长期项目知识账本（属于 los-memory）
- 代码图谱（属于 los-ast）
- AST rewrite（属于 los-ast）
- 业务任务状态机（属于 VPS Agent Web）
- 审批 UI（属于 VPS Agent Web）

#### 对外接口
- `route(request)` - 路由决策
- `invoke(request)` - 执行调用
- `get_budget_status(tenant)` - 预算查询
- `get_circuit_state(provider)` - 熔断状态

#### 禁止越界
- 禁止长期记忆 ledger
- 禁止代码 AST 扫描
- 禁止任务调度引擎
- 禁止审批流引擎

---

### 2.4 VPS Agent Web

#### 定位
`VPS Agent Web` 是 **Execution Fabric + Control Plane**。用户使用系统的总入口，负责任务编排、执行、审批、审计、会话、追踪展示。

它回答的问题是：
- 用户发起了什么任务？
- 这个任务如何编排执行？
- 哪些步骤需要审批？
- 谁执行了什么动作？
- 如何把多个底层系统组合成可操作产品？

#### 核心职责
- 用户入口（chat / web UI）
- task / run / workflow 编排
- HITL（Human-in-the-loop）
- approval / rejection 工作流
- audit view
- dashboard 与 trace 可视化
- trigger / scheduler

#### 拥有对象
| 对象 | 说明 |
|------|------|
| `Task` | 用户任务定义 |
| `Run` | 任务执行实例 |
| `ExecutionStep` | 执行步骤 |
| `ApprovalRequest` | 审批请求 |
| `ApprovalDecision` | 审批决策 |
| `AuditRecord` | 审计记录 |
| `Session` | 用户会话 |
| `TraceView` | 追踪视图 |

#### 不负责的内容
- 重新实现 provider 路由（使用 lsclaw）
- 内嵌 AST 图谱维护（使用 los-ast）
- 内嵌长期知识账本（使用 los-memory）
- 以"缓存"为名长期持有底层真相副本

#### 编排职责
**可以做的**:
- 调 `lsclaw` 发起模型调用
- 调 `los-ast` 做分析与 rewrite 建议
- 调 `los-memory` 检索历史记忆或写入新记忆
- 聚合结果后交给用户审批
- 批准后继续执行后续步骤

**禁止做的**:
- 在本地偷偷复制 provider policy
- 在本地偷偷保存代码图谱
- 在本地偷偷维护 corrected facts 真相

---

## 3. 核心对象归属总表

| 对象 | Source of Truth | 所属项目 | 说明 |
|------|-----------------|----------|------|
| `CodeGraph` | los-ast | los-ast | 代码结构真相 |
| `SymbolIndex` | los-ast | los-ast | 符号级索引 |
| `EvidenceBundle` | los-ast | los-ast | 代码证据输出 |
| `Observation` | los-memory | los-memory | 观察记录（多态） |
| `Session` | los-memory | los-memory | 工作会话 |
| `Checkpoint` | los-memory | los-memory | 检查点 |
| `Feedback` | los-memory | los-memory | 反馈修正记录 |
| `ToolCall` | los-memory | los-memory | 工具调用追踪 |
| `RoutingPolicy` | lsclaw | lsclaw | 模型路由规则 |
| `BudgetPolicy` | lsclaw | lsclaw | 成本预算规则 |
| `RequestTrace` | lsclaw | lsclaw | 模型调用标准 trace |
| `Task` | VPS Agent Web | VPS Agent Web | 用户任务对象 |
| `Run` | VPS Agent Web | VPS Agent Web | 执行实例 |
| `ApprovalRequest` | VPS Agent Web | VPS Agent Web | 审批对象 |
| `AuditRecord` | VPS Agent Web | VPS Agent Web | 审计记录 |

**概念对象映射说明**:

> 概念层中的抽象对象（如 `CorrectedFact`, `RejectedHypothesis`, `IncidentLesson`）当前分别通过 `Observation`, `Feedback` 等具体对象组合承载。详见下表映射关系。

---

## 4. 数据模型映射（说明书概念 → 实际模型）

| 说明书概念 | los-memory 实际模型 | 映射说明 |
|------------|---------------------|----------|
| `MemoryEntry` | `Observation` | 基本等同 |
| `IncidentLesson` | `Observation(kind=incident)` | 通过 kind 字段区分 |
| `CorrectedFact` | `Observation` + `Feedback` | 组合实现，Feedback 用于事后修正 |
| `RejectedHypothesis` | `Feedback(action_type=correct)` | 需通过 Feedback 语义推断 |
| `MemorySummary` | — | 未实现（未来扩展） |
| `TemporalEdge` | `timestamp` 字段 | 时间线通过时间戳查询实现 |
| `KnowledgeSourceRef` | — | 未实现（未来扩展） |
| `FactStatus` | `Observation` + `Feedback` | 隐含状态 |

---

## 5. 典型协作链路

### 5.1 漏洞扫描与修复建议

```
用户 (VPS Agent Web)
    │
    ├─→ 调用 los-memory ──→ 检索历史相关记忆
    │
    ├─→ 调用 los-ast ─────→ 扫描模式、生成 evidence
    │
    ├─→ 调用 lsclaw ──────→ 选择模型、生成建议
    │
    ▼
用户审批 (VPS Agent Web)
    │
    ├─→ 批准后 ───────────→ 触发 patch generation
    │
    └─→ 提交候选事实到 los-memory ──→ 记录经验（Observation/Feedback）
```

**边界说明**:
- 代码事实来自 `los-ast`
- 历史经验来自 `los-memory`
- 模型调用来自 `lsclaw`
- 任务状态来自 `VPS Agent Web`
- **写入主权**: 由 `VPS Agent Web` 主动发起，非自动沉淀

### 5.2 多模型代码评审

```
VPS Agent Web 创建评审任务
    │
    ├─→ los-ast 提供变更结构、影响面、风险点
    │
    ├─→ los-memory 检索相关历史决策
    │
    ├─→ lsclaw 按 policy 选择模型、记录 trace
    │
    ▼
VPS Agent Web 展示结果
    │
    └─→ 提交到 los-memory 记录问题模式（Observation）
```

### 5.3 los-memory 集成模式

**模式 A: 子进程调用（通用）**:
```bash
# VPS Agent Web 或其他项目调用
result=$(python -m memory_tool search "API设计" --profile claude --json)
```

**模式 B: Python API（同进程）**:
```python
from memory_tool.operations import run_search
results = run_search(conn, "API设计", limit=5)
```

**模式 C: 封装 SDK（推荐）**:
```python
# 调用方封装 SDK，内部处理 CLI 调用和 JSON 解析
client = LosMemoryClient(profile="claude")
observations = client.search("API设计")
```

---

## 6. 接口与通信原则

### 6.1 优先同步契约

MVP 阶段优先使用：
- gRPC 或 OpenAPI（服务间）
- JSON Schema / Protobuf
- CLI 契约（los-memory 当前形态）

原因：容易调试、容易追踪、避免早期过度复杂化

### 6.2 los-memory CLI 契约

**环境要求**:
- Python 3.8+
- SQLite3

**调用方责任**:
- 确保 Python 环境可用
- 处理 JSON 输出解析
- 管理 Profile 选择
- 处理退出码和错误

**健康检查**:
```bash
python -m memory_tool doctor
# 输出: {python_ok, sqlite_ok, db_path, profile, writable}
```

### 6.3 异步事件使用原则

适合事件化的内容：
- `CodeGraphUpdated` (los-ast)
- `RewriteGenerated` (los-ast)
- `ObservationAdded` (los-memory)
- `FeedbackApplied` (los-memory)
- `RoutingPolicyChanged` (lsclaw)
- `ApprovalGranted` (VPS Agent Web)

不适合事件化：
- 需要强一致实时返回的查询
- 核心用户交互路径上的同步反馈

---

## 7. 防职责漂移规则

### 7.1 越界判断四问

新增功能进入哪个项目前，必须先回答：

1. 它的核心长期状态归谁拥有？
2. 它的最终写权限归谁？
3. 它是底层真相，还是上层消费结果？
4. 放进去后会不会让该项目开始承担第二种系统范式？

如果回答不清，则不得直接开发。

### 7.2 常见漂移示例

| 错误示例 | 问题 | 正确归属 |
|----------|------|----------|
| 在 `VPS Agent Web` 中维护 provider fallback 逻辑 | 复制了 `lsclaw` 的治理职责 | lsclaw |
| 在 `los-memory` 中保存全量运行 trace | 把知识账本做成日志垃圾场 | 日志系统 |
| 在 `lsclaw` 中做项目级任务状态机 | 从 gateway 漂成 orchestration | VPS Agent Web |
| 在 `los-ast` 中直接写入 corrected facts | 分析结论直接升级为长期真相 | los-memory |

### 7.3 允许的缓存原则

允许缓存，但缓存不得成为真相源。缓存必须满足：
- 可失效
- 可重建
- 有 TTL / version
- 不改变对象归属

---

## 8. 命名与对外展示

| 内部名 | 对外建议名 | 当前形态 |
|--------|-----------|----------|
| los-ast | Code Kernel / AST Engine | 服务/库 |
| los-memory | Memory Ledger / Project Memory | CLI 工具 |
| lsclaw | LLM Gateway / AI Gateway | 服务 |
| VPS Agent Web | Control Plane / Execution Console | Web 应用 |

---

## 9. MVP 建议

### 9.1 最小能力闭环

建议优先形成一个最小闭环：

**第一阶段**: 至少需要以下三个组件形成可运行的端到端流程
- `lsclaw`: 统一 provider 调用、trace、policy（治理基础）
- `VPS Agent Web`: 任务编排、审批、展示（控制面）
- `los-ast` 或 `los-memory` 中至少一个可被稳定消费的底层能力

**能力选择策略**:
- 对**代码分析场景**优先接入 `los-ast`（代码理解、影响面分析）
- 对**连续工作记忆场景**优先接入 `los-memory`（会话追踪、经验沉淀）

**能跑通的闭环示例**（代码分析场景）:
用户发任务 → 调用 `los-ast` 分析代码 → 调用 `lsclaw` 生成建议 → 展示给用户 → 人工审批 → 通过 `VPS Agent Web` 触发后续操作

**能跑通的闭环示例**（工作记忆场景）:
用户发任务 → 调用 `lsclaw` 生成内容 → 记录到 `los-memory` → 展示给用户 → 人工审批 → 通过 `VPS Agent Web` 继续执行 → 沉淀经验到 `los-memory`

> **重要**: `los-memory` 的写入由 `VPS Agent Web` 或受控集成层**主动发起**，而非 `los-ast` 或 `lsclaw` 自动决定沉淀内容。`los-memory` 仅提供写入接口，不感知上层业务逻辑。

### 9.2 los-memory 演进路线

```
当前（v1.x）:
├── CLI 工具完整功能
├── Python API
└── Profile 隔离

短期（v1.5）:
├── doctor 命令
├── JSON-first 输出
└── 多语言 SDK 模板

中期（v2.x）:
├── 可选本地服务化
├── HTTP/gRPC API
├── CLI 作为客户端
└── KnowledgeSourceRef

长期（v3.x）:
├── 可选 PostgreSQL 后端
├── 多租户隔离
└── MemorySummary 能力
```

---

## 10. 可观测性要求

四个项目都应统一输出可观测数据：

```json
{
  "trace_id": "...",
  "request_id": "...",
  "actor": "...",
  "tenant": "...",
  "project": "...",
  "latency_ms": 123,
  "error_type": "...",
  "version": "...",
  "causation_id": "..."
}
```

推荐统一 OpenTelemetry。VPS Agent Web 负责展示端到端链路，但不是底层 trace 的唯一存储源。

---

## 附录 A：功能归属速查表

| 问题/功能 | 归属 | 说明 |
|----------|------|------|
| 查询某函数有哪些调用方 | los-ast | 代码分析 |
| 检查某 patch 影响哪些模块 | los-ast | 影响面分析 |
| 保存"这个项目已确认必须加 tenant_id 过滤" | los-memory | Observation(kind=fact) |
| 保存"上次猜测 root cause 错误" | los-memory | Feedback(action_type=correct) |
| 决定这次请求走 Claude 还是 Gemini | lsclaw | 路由决策 |
| 控制超预算时是否降级模型 | lsclaw | 预算策略 |
| 展示任务进度、审批记录、执行链路 | VPS Agent Web | 控制面展示 |
| 批量调度节点执行任务 | VPS Agent Web | 编排执行 |
| 记录工具调用历史 | los-memory | ToolCall |
| 保存工作检查点 | los-memory | Checkpoint |
| 建立记忆关联 | los-memory | ObservationLink |
| provider 失败后的 fallback | lsclaw | 熔断降级 |
| 生成结构化代码证据 | los-ast | EvidenceBundle |

---

## 附录 B：一句话版决策规则

判断一个新功能放哪里，只看一句：

**谁拥有它的长期真相，谁就拥有这个功能的核心归属。**

---

## 附录 C：修订历史

| 版本 | 日期 | 修订内容 | 作者 |
|------|------|----------|------|
| v1.0 | 2026-02-xx | 初始草案 | - |
| v1.1 | 2026-03-07 | **关键修正**：<br>1. 明确区分 los-memory "当前态"（CLI 工具）与"目标态"（服务化知识组件）<br>2. 增加"桥接表述"：说明当前 CLI 工具如何实现"账本"的最小子集<br>3. 架构图调整：memory 当前作为"辅助能力层"而非与 los-ast 对称的底层能力层<br>4. 职责对照表统一为：上层控制层/中间治理层/底层能力层（los-ast）/辅助能力层（los-memory）<br>5. 增加"非目标清单"，明确当前阶段不追求的能力<br>6. 定义"服务化触发条件"，提供可判断的演进门槛<br>7. 将错误归属到 `los-memory` 的 discover/validate/enumerate 接口移除；这些接口仅保留在 `los-ast`<br>8. 补充实际 CLI 接口（add/search/feedback/link/checkpoint）<br>9. 添加数据模型映射表（说明书概念 → 实际模型）<br>10. 补充 Profile 机制详细说明<br>11. 调整 MVP 建议顺序，明确 los-memory 写入由上层主动发起<br>12. 明确 memory 写入责任主体：由 VPS Agent Web 或受控集成层发起，非自动沉淀 | Claude Code |
| v1.2 | 2026-03-07 | **正式版文案收口**：<br>1. `lsclaw` 定位从 `Governance Brain` 收紧为 `Governance Layer`<br>2. `los-memory` 定位小节直接标注"当前态/目标态"一句话<br>3. 核心对象归属表后补充概念对象映射说明<br>4. 协作链路"写入 los-memory"改为更中性表述（"提交候选事实"、"提交到 los-memory 记录"）<br>5. 确保"非目标清单"和"服务化触发条件"在正文章节显式展示<br>6. 协作链路补充"写入主权"边界说明 | Claude Code |

---

## 附录 D：术语表

| 术语 | 定义 | 所属项目 |
|------|------|----------|
| Observation | 记忆的基本单元，包含 title/summary/tags/kind 等 | los-memory |
| Session | 工作会话，用于组织和追踪一组相关的 Observation | los-memory |
| Checkpoint | 检查点，保存工作现场以便后续恢复 | los-memory |
| Feedback | 对已有 Observation 的反馈与修正机制 | los-memory |
| Profile | 数据隔离单元（claude/codex/shared） | los-memory |
| ToolCall | 工具调用追踪记录 | los-memory |
| EvidenceBundle | 代码分析证据包 | los-ast |
| RoutingPolicy | LLM 路由决策规则 | lsclaw |
| ApprovalRequest | 需要人工审批的请求对象 | VPS Agent Web |

---

**文档结束**
