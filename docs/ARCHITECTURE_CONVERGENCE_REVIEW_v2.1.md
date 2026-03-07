# los-memory 架构偏差评审与收敛建议 v2.1

**文档类型**: 架构治理评审报告
**评审日期**: 2026-03-07
**评审范围**: los-memory 项目实现与 ARCHITECTURE_BOUNDARY_SPEC.md / IMPLEMENTATION_PLAN.md 一致性检查
**建议有效期**: 本报告结论建议在 14 日内完成边界裁定，逾期将形成事实兼容性负担

---

## 执行摘要

### 核心发现

当前 los-memory 实现与架构文档在**技术约束层面总体一致**（CLI-first、Profile 隔离、非服务化、JSON-first），但在**能力边界层面存在显著漂移**。

代码库已出现 5 项文档未声明的正式实现能力，其中 3 项（approval/incident/recovery）构成**跨域边界扩张**，与项目原始定位产生结构性冲突。

### 关键数据

| 指标 | 数值 |
|-----|------|
| 文档声明核心能力 | 6 项（Observation/Session/Checkpoint/Feedback/ToolCall/Link） |
| 实际实现正式能力 | 11 项（+incident/recovery/approval/knowledge/attribution） |
| 文档未声明的正式能力 | 5 项 |
| 跨域扩张能力 | 3 项（approval/incident/recovery） |
| 已进入主 CLI 暴露面 | 4 项（incident/recovery/approval/knowledge） |

### 风险评级

| 风险类型 | 等级 | 说明 |
|---------|------|------|
| 对外契约冲突 | 🔴 高 | approval 等能力已进入主 CLI，将形成事实兼容性承诺 |
| 跨域边界漂移 | 🔴 高 | 从记忆域向治理/运维执行域扩张，侵蚀其他项目边界 |
| 文档失效风险 | 🟡 中 | 文档无法准确描述系统真实能力范围 |
| 架构拆分困难 | 🟡 中 | 能力耦合度增加，未来拆分成本上升 |

### 建议动作（摘要）

**立即执行（3 日内）**: 冻结新增非核心能力进入主 CLI 入口
**短期执行（14 日内）**: 完成边界裁定，明确核心/扩展/迁出清单
**中期执行（30 日内）**: 实施代码结构调整，隔离扩展能力

---

## 一、评审方法论

### 1.1 两步判定框架

为避免"看到文件即定性"，采用以下严谨判定流程：

#### 第一步：实现成熟度判定

| 判定等级 | 判定标准 | 处理策略 |
|---------|---------|---------|
| **正式实现能力** | 满足 4 项及以上判定维度 | 必须给出边界归属裁定 |
| **已实现能力** | 满足 2-3 项判定维度 | 需评估是否提升至正式 |
| **实验/内部支撑** | 满足 1 项判定维度 | 标记为 experimental |
| **残留代码** | 0 项 | 清理或归档 |

**判定维度**（按重要性排序）：
1. **入口可见性**: 是否注册为主 CLI 一级命令
2. **持久化支持**: 是否有独立数据库表及索引
3. **测试覆盖**: 是否有独立测试文件
4. **主流程依赖**: 是否被核心模块 import 调用
5. **文档声明**: 是否出现在架构文档核心对象表

#### 第二步：边界归属判定

基于以下因素裁定能力归属：

| 判定因素 | 核心层倾向 | 扩展层倾向 | 迁出倾向 |
|---------|-----------|-----------|---------|
| 符合项目主定位 | 强 | 弱 | 否 |
| 与核心对象强耦合 | 是 | 否 | 否 |
| 被主场景高频依赖 | 是 | 低频 | 否 |
| 与其他项目职责重叠 | 否 | 部分 | 强 |
| 应纳入长期兼容承诺 | 是 | 否 | 否 |

### 1.2 术语定义

**本文档统一术语**（避免概念混淆）：

| 术语 | 定义 | 示例 |
|-----|------|------|
| **正式实现能力** | 已满足成熟度判定，具备完整实现的功能 | observation, incident, approval |
| **核心能力** | 属于项目主定位范畴的正式实现能力 | observation, session, checkpoint |
| **扩展能力** | 正式实现但不属于核心定位的能力 | 待裁定 |
| **边界漂移信号** | 发现超范围能力的迹象 | 发现 approval 相关文件 |
| **边界漂移事实** | 已进入主入口/主流程/事实兼容承诺 | approval 已注册为 CLI 一级命令 |
| **跨域扩张** | 从原领域向不同领域扩张 | memory → approval（记忆→治理） |
| **邻域扩展** | 在原领域邻近范围扩展 | memory → knowledge（记忆→知识） |

---

## 二、文档声明能力 vs 实际能力对比

### 2.1 文档已声明且已实现 ✅ 符合

| 能力 | 文档章节 | CLI 入口 | 数据库表 | 测试文件 | 成熟度判定 |
|-----|---------|---------|---------|---------|-----------|
| Observation | 2.2 核心对象 | `memory`/`observation` | observations | test_memory_tool.py | 正式实现 |
| Session | 2.2 核心对象 | `session` | sessions | test_sessions_bdd.py | 正式实现 |
| Checkpoint | 2.2 核心对象 | `checkpoint` | checkpoints | test_checkpoints_bdd.py | 正式实现 |
| Feedback | 2.2 核心对象 | `review` | feedback_log | test_feedback_bdd.py | 正式实现 |
| ToolCall | 2.2 核心对象 | `tool` | (analytics) | test_tool_memory_bdd.py | 正式实现 |
| Link | 2.2 核心对象 | `memory link` | observation_links | test_observation_links_bdd.py | 正式实现 |
| Doctor | 4.1 检查项 | `admin doctor` | meta | test_doctor.py | 正式实现 |

**结论**: 文档声明的核心能力均已正式实现，符合预期。

### 2.2 文档未声明但已正式实现的能力

**判定过程**（逐项验证成熟度判定维度）：

| 能力 | CLI 入口 | 数据库表 | 索引数 | 测试文件 | 主流程 import | 成熟度判定 |
|-----|---------|---------|--------|---------|--------------|-----------|
| **incident** | ✅ 一级命令 | ✅ 2 表 | 5 | ✅ test_incidents.py | ❌ | **正式实现** |
| **recovery** | ✅ 一级命令 | ✅ 3 表 | 3 | ✅ test_recovery.py, test_recovery_schema.py | ❌ | **正式实现** |
| **approval** | ✅ 一级命令 | ✅ 4 表 | 6 | ✅ test_approval_api.py, test_approval_system.py | ❌ | **正式实现** |
| **knowledge** | ✅ 一级命令 | ✅ 2 表 | 3 | ✅ test_knowledge_base.py | ❌ | **正式实现** |
| **attribution** | ❌ 无独立 CLI | ✅ 1 表 | 2 | ✅ test_attribution.py | ✅ incidents.py | **内部支撑** |

**关键证据链**:

1. **CLI 入口证据**:
   ```bash
   $ los-memory --help
   可用命令: {init,memory,observation,session,checkpoint,project,tool,admin,review,
             incident,recovery,approval,knowledge}
   ```

2. **数据库 Schema 证据**:
   ```sql
   -- incident: 2 表 + 5 索引
   CREATE TABLE incidents (...);
   CREATE TABLE incident_observations (...);
   CREATE INDEX idx_incidents_status ON incidents(status);

   -- recovery: 3 表 + 3 索引
   CREATE TABLE recovery_actions (...);
   CREATE TABLE recovery_executions (...);
   CREATE TABLE recovery_policies (...);

   -- approval: 4 表 + 6 索引（含安全相关）
   CREATE TABLE approval_requests (...);  -- 含 HMAC 签名字段
   CREATE TABLE approval_audit_log (...);
   CREATE TABLE approval_events (...);    -- SSE 事件
   CREATE TABLE approval_nonces (...);    -- 重放攻击防护
   ```

3. **代码实现证据**:
   - `approval_api.py`: 提供 HMAC-signed POST /api/v1/jobs/approval
   - `approval_events.py`: 提供 SSE /api/v1/events/stream
   - `approval_security.py`: HMAC 签名验证、nonce 管理
   - `approval_store.py`: 乐观锁、版本控制、48h 自动拒绝调度器

**事实陈述**:

上述 5 项能力已超出 `ARCHITECTURE_BOUNDARY_SPEC.md` 定义的范围。其中 `incident/recovery/approval/knowledge` 已进入主 CLI 暴露面，形成**边界漂移事实**。

---

## 三、风险分析与治理影响

### 3.1 🔴 高风险：对外契约冲突

#### 问题：approval 系统与文档边界直接冲突

**文档声明**（ARCHITECTURE_BOUNDARY_SPEC.md 2.2 节）:
> "❌ 审批系统或 proposal/commit 完整治理" 是当前阶段非目标

**实际状态**:
- `approval` 已注册为主 CLI 一级命令
- 具备完整的 P2 Approval Workflow 实现
- 包含 HMAC 安全、SSE 事件流、乐观锁、自动拒绝调度器

**治理风险**:

| 风险类型 | 具体表现 | 影响程度 |
|---------|---------|---------|
| **暴露面风险** | 用户可通过 `los-memory approval` 发现并使用该能力 | 高 |
| **兼容性风险** | 一旦使用，JSON 输出、表结构、CLI 行为将形成事实兼容承诺 | 高 |
| **文档失效风险** | 文档声明与实际能力不符，集成方无法信任文档 | 中 |
| **职责侵蚀风险** | 审批属于治理/执行域，与 VPS Agent Web 职责重叠 | 高 |

**关键判断**:

`approval` 的问题不只是"代码存在但未文档化"，而是它已经成为主 CLI 暴露面的**正式实现能力**。一旦被使用，就会形成事实上的兼容承诺，并对项目边界产生反向塑形作用，使文档声明逐渐失效。

### 3.2 🔴 高风险：跨域边界漂移

#### 漂移方向分析

**项目原始定位**:
> "CLI-first 个人/代理记忆工具"

**实际能力集**:

| 能力 | 所属领域 | 与原定位关系 |
|-----|---------|-------------|
| Observation/Session/Checkpoint | 记忆域 | 核心定位 ✅ |
| Feedback/ToolCall | 记忆治理域 | 邻域扩展 ✅ |
| Knowledge | 知识管理域 | 邻域扩展 ⚠️ |
| Incident | 运维治理域 | 跨域扩张 ❌ |
| Recovery | 运维执行域 | 跨域扩张 ❌ |
| Approval | 流程治理域 | 跨域扩张 ❌ |
| Attribution | 根因分析域 | 支撑能力 ⚠️ |

**漂移性质判定**:

- **邻域扩展**（可接受）: memory → knowledge，概念邻近，数据模型相似
- **跨域扩张**（需治理）: memory → approval/incident/recovery，属于从"认知/记忆域"向"治理/运维执行域"的跨域扩张

**治理影响**:

跨域扩张会导致：
1. 与 VPS Agent Web（运维控制台）职责重叠
2. 与 lsclaw（治理层）功能边界模糊
3. 项目身份从"工具"向"平台"漂移
4. 后续拆分成本随耦合度增加而上升

### 3.3 🟡 中风险：计划项未交付

| 功能 | 文档章节 | 计划优先级 | 实现状态 | 影响 |
|-----|---------|-----------|---------|------|
| 多语言 SDK (Go/Rust/Node) | 4.5 SDK 设计 | P1 | ❌ 未实现 | 影响易用性，但不破坏边界 |
| Shell 补全 | 4.3 CLI 改进 | P1 | ❌ 未实现 | 影响易用性 |
| 交互式模式 (`-i`) | 4.3 CLI 改进 | P1 | ❌ 未实现 | 影响易用性 |
| 测试目录命名 | - | - | `tests/cli/` vs `tests/e2e/` | 低影响 |

**结论**: 计划项未交付属于执行层面问题，不影响架构边界。

---

## 四、能力归属裁定建议

### 4.1 逐项裁定分析

#### incident / recovery

| 判定因素 | 评估 | 备注 |
|---------|------|------|
| 符合项目主定位 | ❌ 否 | 事故管理与自愈不属于记忆工具范畴 |
| 与核心对象强耦合 | ⚠️ 弱 | 通过 incident_observations 关联，可解耦 |
| 被主场景高频依赖 | ❌ 否 | 非记忆核心流程 |
| 与其他项目职责重叠 | ✅ 强 | 与 VPS Agent Web 运维控制台重叠 |
| 应纳入长期兼容承诺 | ❌ 否 | 不应由记忆工具承诺 |

**建议归属**: **扩展层**（短期）或 **迁出**（中长期）

#### approval

| 判定因素 | 评估 | 备注 |
|---------|------|------|
| 符合项目主定位 | ❌ 否 | 审批工作流与记忆无关 |
| 与核心对象强耦合 | ❌ 否 | 独立表结构，无强制关联 |
| 被主场景高频依赖 | ❌ 否 | 非记忆核心流程 |
| 与其他项目职责重叠 | ✅ 强 | 与 VPS Agent Web 审批流程重叠，与 lsclaw 治理层边界模糊 |
| 应纳入长期兼容承诺 | ❌ 否 | 不应由记忆工具承诺 |

**建议归属**: **扩展层**（临时）或 **独立模块/迁出**（推荐）

#### knowledge

| 判定因素 | 评估 | 备注 |
|---------|------|------|
| 符合项目主定位 | ⚠️ 部分 | 知识管理与记忆概念邻近 |
| 与核心对象强耦合 | ⚠️ 中等 | 可关联 observation，也可独立 |
| 被主场景高频依赖 | ⚠️ 待观察 | 当前使用频率未知 |
| 与其他项目职责重叠 | ❌ 弱 | 暂无明确重叠 |
| 应纳入长期兼容承诺 | ⚠️ 待定 | 取决于裁定结果 |

**建议归属**: **待裁定核心候选**，需明确以下判定条件：

- **纳入核心条件**: 主要服务于 observation/session 的组织检索，不引入独立治理流程，不形成独立产品面
- **归为扩展条件**: 有自己的生命周期、关系建模、管理命令，与 memory 操作模型明显不同

#### attribution

| 判定因素 | 评估 | 备注 |
|---------|------|------|
| 符合项目主定位 | ⚠️ 弱 | 根因分析属于支撑能力 |
| 与核心对象强耦合 | ✅ 强 | 依赖 incident 表 |
| 被主场景高频依赖 | ❌ 否 | 仅 incident 分析使用 |
| 与其他项目职责重叠 | ⚠️ 中等 | 可能与其他分析系统重叠 |
| 应纳入长期兼容承诺 | ❌ 否 | 内部支撑能力 |

**建议归属**: **扩展层**（与 incident 绑定）

### 4.2 归属建议总表

| 能力 | 成熟度 | 建议归属 | 判定依据 |
|-----|-------|---------|---------|
| Observation | 正式实现 | **核心层** | 项目原始定位 |
| Session | 正式实现 | **核心层** | 项目原始定位 |
| Checkpoint | 正式实现 | **核心层** | 项目原始定位 |
| Feedback | 正式实现 | **核心层** | 记忆治理机制 |
| ToolCall | 正式实现 | **核心层** | 记忆追踪机制 |
| Link | 正式实现 | **核心层** | 记忆关联机制 |
| **Knowledge** | 正式实现 | **核心候选/扩展** | 概念邻近但模型不同，待裁定 |
| **Attribution** | 内部支撑 | **扩展层** | 支撑能力，非核心 |
| **Incident** | 正式实现 | **扩展层/迁出** | 跨域扩张，职责重叠 |
| **Recovery** | 正式实现 | **扩展层/迁出** | 跨域扩张，职责重叠 |
| **Approval** | 正式实现 | **扩展层/独立模块** | 跨域扩张，强职责重叠 |

---

## 五、收敛动作计划

### 5.1 立即执行（3 日内）

#### 动作 1: 冻结新增非核心能力进入主 CLI

**执行内容**:
- 暂停任何新的非核心能力注册为主 CLI 一级命令
- 代码审查时增加"CLI 入口合规性"检查项

**验收标准**:
- 无新的非核心能力合入主分支

### 5.2 短期执行（14 日内）

#### 动作 2: 边界裁定会议

**参与方**: los-memory 维护者、架构师、lsclaw/VPS Agent Web 代表

**议程**:
1. 逐项评审 5 项文档未声明能力的归属建议
2. 对 incident/recovery/approval/knowledge/attribution 进行最终裁定
3. 确定迁移/隔离时间表

**输出物**:
- 《能力边界裁定表》（含核心/扩展/实验/迁出最终清单）
- 《CLI 暴露面调整清单》（哪些命令保留/隐藏/移除）
- 《迁移影响评估》（数据迁移、兼容性承诺、测试调整）

#### 动作 3: 文档更新（基于裁定结果）

**待更新文档**:

1. **ARCHITECTURE_BOUNDARY_SPEC.md**:
   - 明确核心/扩展/非目标三层边界
   - 补充演进触发条件（何时从扩展升为核心）
   - 更新"当前非目标清单"，说明哪些已存在但属于扩展

2. **IMPLEMENTATION_PLAN.md**:
   - 同步 P0/P1 实际完成状态
   - 将超范围能力纳入计划或标记为实验
   - 补充扩展能力治理策略

3. **新增 EXTENSIONS.md**:
   - 说明扩展能力的使用方式
   - 声明兼容性承诺级别（核心=完整承诺，扩展=尽力而为，实验=无承诺）
   - 扩展能力启用/禁用说明

### 5.3 中期执行（30 日内）

#### 动作 4: 代码结构调整

**目标结构**（根据最终裁定调整）：

```text
los-memory/
├── core/                          # 核心能力
│   ├── __init__.py
│   ├── cli.py                     # 仅暴露核心命令
│   ├── operations.py
│   ├── sessions.py
│   ├── checkpoints.py
│   ├── feedback.py
│   ├── links.py
│   └── analytics.py
├── extensions/                    # 扩展能力（默认不加载）
│   ├── __init__.py
│   ├── incident/
│   │   ├── cli.py
│   │   ├── models.py
│   │   └── tests/
│   ├── recovery/
│   ├── approval/
│   ├── knowledge/
│   └── attribution/
├── contracts/                     # 契约定义
│   └── schemas/
└── tests/
    ├── unit/core/
    ├── unit/extensions/
    ├── integration/core/
    └── integration/extensions/
```

**CLI 加载策略**:

```python
# core/cli.py 仅注册核心命令
def register_core_commands(subparsers):
    subparsers.add_parser("memory")
    subparsers.add_parser("session")
    subparsers.add_parser("checkpoint")
    # ... 核心命令

# extensions 通过显式启用加载
EXTENSIONS = {
    "incident": "extensions.incident.cli",
    "recovery": "extensions.recovery.cli",
    "approval": "extensions.approval.cli",
    "knowledge": "extensions.knowledge.cli",
}

def load_extensions(subparsers, enabled=None):
    """加载扩展命令（默认不加载）"""
    enabled = enabled or os.getenv("MEMORY_ENABLE_EXTENSIONS", "").split(",")
    for name, module_path in EXTENSIONS.items():
        if name in enabled:
            module = importlib.import_module(module_path)
            module.register(subparsers)
```

**关键原则**:

> 扩展能力默认关闭，不纳入核心兼容承诺；仅在显式启用时暴露 CLI 入口。

启用方式:
```bash
# 方式 1: 环境变量
export MEMORY_ENABLE_EXTENSIONS="incident,recovery,knowledge"
los-memory incident list

# 方式 2: 配置文件
# ~/.config/los-memory/config.yaml
extensions:
  - incident
  - recovery
```

#### 动作 5: 测试矩阵调整

| 能力层级 | 测试要求 | 兼容性承诺 |
|---------|---------|-----------|
| 核心 | unit + integration + contract + e2e | 完整向后兼容 |
| 扩展 | integration + smoke | 尽力而为，重大变更发通知 |
| 实验 | unit（可选） | 无承诺，随时可能移除 |

### 5.4 长期跟踪（持续）

#### 动作 6: 建立边界治理检查点

- **每季度**: 审查新增能力是否合规
- **每版本发布前**: 核对 CLI 暴露面与文档一致性
- **年度**: 评审扩展能力是否应升级为核心或迁出

---

## 六、推荐默认决策

### 6.1 临时治理立场（边界裁定完成前）

**建议默认立场**:

> 在未完成正式边界裁定前，建议默认将 `incident/recovery/approval/attribution` 视为**扩展域能力**，将 `knowledge` 视为**待裁定的核心候选能力**，避免继续扩大核心边界。

**执行策略**:

| 能力 | 当前处理 | 后续动作 |
|-----|---------|---------|
| incident | 标记为 experimental，文档警告 | 裁定后决定隔离或迁出 |
| recovery | 标记为 experimental，文档警告 | 裁定后决定隔离或迁出 |
| approval | 标记为 experimental，文档警告 | 裁定后决定隔离或独立模块 |
| knowledge | 正常支持，但文档注明"边界待裁定" | 14 日内完成裁定 |
| attribution | 标记为 internal，不暴露 CLI | 与 incident 绑定处理 |

### 6.2 若裁定会议无法按期举行

**Fallback 方案**（30 日内未达成裁定共识）:

1. 所有文档未声明能力自动归为**扩展层**
2. 实施代码结构调整，隔离到 `extensions/`
3. 默认禁用扩展能力 CLI 入口
4. 文档更新承认现状，声明扩展能力治理策略

---

## 七、附录

### 附录 A: 证据文件清单

| 能力 | 关键文件 | 证据类型 |
|-----|---------|---------|
| incident | `cli_incidents.py`, `incidents.py`, `test_incidents.py` | CLI/模型/测试 |
| recovery | `cli_recovery.py`, `recovery_executor.py`, `recovery_actions.py` | CLI/执行器/动作 |
| approval | `approval_api.py`, `approval_events.py`, `approval_security.py`, `approval_store.py` | API/事件/安全/存储 |
| knowledge | `cli_knowledge.py`, `knowledge_base.py` | CLI/模型 |
| attribution | `attribution_engine.py`, `test_attribution.py` | 引擎/测试 |

### 附录 B: 数据库 Schema 差异

**文档声明表**: observations, sessions, checkpoints, feedback_log, observation_links
**实际存在表**: +incidents, incident_observations, recovery_actions, recovery_executions, recovery_policies, approval_requests, approval_audit_log, approval_events, approval_nonces, knowledge_entries, knowledge_relationships, attribution_reports

### 附录 C: CLI 命令差异

**文档声明命令**: init, memory, observation, session, checkpoint, project, tool, admin, review
**实际存在命令**: +incident, recovery, approval, knowledge

---

**文档结束**

**下一步动作**: 建议 3 日内召集边界裁定会议，使用本报告作为评审材料，14 日内完成裁定与文档更新。
