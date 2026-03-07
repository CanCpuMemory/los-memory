# 四项目架构边界说明书 v1 - 评审报告

**评审日期**: 2026-03-07
**评审项目**: los-memory
**评审人**: Claude Code
**原说明书版本**: Architecture Boundary Spec v1 (草案)

---

## 1. 执行摘要

原《四项目架构边界说明书 v1》为 los-ast、los-memory、lsclaw、VPS Agent Web 四个项目定义了清晰的职责边界和协作模式。经与 los-memory 实际代码库对比分析，说明书整体架构设计合理，但**存在定位偏差、模型映射不匹配、接口定义超前**等问题。建议进行针对性修订后再正式采纳。

---

## 2. 当前 los-memory 项目实际情况

### 2.1 项目定位

**实际定位**: 个人/代理级本地 SQLite 记忆工具
**目标用户**: 单个 AI 代理（Claude、Codex）或开发者
**部署形态**: 本地 CLI 工具 + Python API
**数据存储**: 本地 SQLite 文件（`~/.claude_memory/memory.db`）

### 2.2 核心数据模型

```python
# 实际已实现模型 (memory_tool/models.py)
- Observation       # 观察记录（decision/incident/note/meeting/todo/snippet/tool_call）
- Session           # 工作会话
- Checkpoint        # 检查点/里程碑
- Feedback          # 反馈与修正
- ObservationLink   # 观察记录间的关联
- ToolCall          # 工具调用追踪
```

### 2.3 核心功能集

| 功能 | 实现状态 | 说明 |
|------|----------|------|
| 记忆增删改查 | ✅ 已实现 | CLI + Python API |
| 多 Profile 隔离 | ✅ 已实现 | claude/codex/shared |
| 反馈与修正 | ✅ 已实现 | feedback 命令 |
| 工具调用追踪 | ✅ 已实现 | tool-log/tool-stats |
| 记忆关联 | ✅ 已实现 | link/related/unlink |
| Session 管理 | ✅ 已实现 | start/end 会话 |
| Checkpoint | ✅ 已实现 | create/resume 检查点 |
| Web Viewer | ✅ 已实现 | 本地浏览界面 |
| 导出导入 | ✅ 已实现 | JSON/CSV 格式 |

### 2.4 与外部项目关系

当前 los-memory **无直接服务依赖**，是独立工具：
- 不依赖 los-ast（无代码分析能力）
- 不依赖 lsclaw（无 LLM 路由需求）
- 不依赖 VPS Agent Web（本身就是本地工具）

**可选集成方式**: 其他项目通过 CLI 调用或 Python API 集成

---

## 3. 说明书与实际对比分析

### 3.1 定位偏差

| 维度 | 说明书描述 | 实际现状 | 偏差度 |
|------|-----------|---------|--------|
| **用户范围** | 项目级/团队级 | 个人/代理级 | 中等 |
| **部署形态** | 服务/组件 | CLI 工具 | 中等 |
| **数据归属** | 项目长期知识账本 | 个人工作记忆 | 中等 |
| **系统边界** | 四项目体系中的一环 | 独立工具 | 低 |

**分析**: 说明书将 los-memory 定位为"项目长期知识账本"，而实际实现更接近"个人工作记忆辅助工具"。这种差异影响了接口设计和服务依赖假设。

### 3.2 数据模型映射分析

说明书提到的模型 vs 实际模型：

| 说明书模型 | 实际对应 | 匹配度 | 说明 |
|-----------|---------|--------|------|
| `CorrectedFact` | `Observation` + `Feedback` | 部分 | 需组合实现 |
| `IncidentLesson` | `Observation(kind=incident)` | 高 | 概念一致 |
| `MemoryEntry` | `Observation` | 高 | 基本等同 |
| `RejectedHypothesis` | `Feedback(action_type=correct)` | 低 | 无明确对应 |
| `MemorySummary` | ❌ 无 | 缺失 | 未实现 |
| `TemporalEdge` | ❌ 无 | 缺失 | 时间线通过 timestamp 实现 |
| `KnowledgeSourceRef` | ❌ 无 | 缺失 | 未实现来源追溯 |
| `FactStatus` | `Observation` + `Feedback` 状态 | 部分 | 隐含状态 |

### 3.3 接口定义差异

说明书建议接口：
- `discover` - 发现代码结构事实 ❌（属于 los-ast）
- `validate` - 验证结论 ❌（属于 los-ast）
- `enumerate` - 枚举目标或证据 ❌（属于 los-ast）

实际 los-memory 接口：
- `add/ingest` - 写入记忆 ✅
- `search/timeline/get/list` - 检索记忆 ✅
- `edit/delete/clean` - 维护记忆 ✅
- `feedback` - 反馈修正 ✅
- `link/related` - 关联管理 ✅
- `checkpoint` - 检查点管理 ✅

**结论**: 说明书错误地将 los-ast 的接口类型归给了 los-memory。

### 3.4 职责边界问题

说明书对 los-memory 的约束：

| 约束 | 实际符合度 | 说明 |
|------|-----------|------|
| 不负责代码扫描 | ✅ 符合 | 确实无此能力 |
| 不负责 AST 解析 | ✅ 符合 | 确实无此能力 |
| 不负责 provider 路由 | ✅ 符合 | 确实无此能力 |
| 不负责实时任务执行 | ✅ 符合 | 确实无此能力 |
| 不负责审批 UI | ✅ 符合 | 确实是 CLI 工具 |
| **不负责原始 trace 主存储** | ⚠️ 部分 | 实际存储 ToolCall trace |
| **不存成全量代码图谱** | ✅ 符合 | 确实无此能力 |

### 3.5 写入模型差异

说明书建议：
- `proposal` - 候选知识提交
- `commit` - 正式入账

实际实现：
- 直接写入 `Observation`（无 proposal/commit 区分）
- `Feedback` 机制用于事后修正

**建议**: 当前简化模型适合 CLI 工具场景，但如需升级为服务，proposal/commit 模式值得考虑。

---

## 4. 需要补充的内容

### 4.1 los-memory 自身能力缺口

基于跨项目接入复盘（2026-02-25），当前 los-memory 需要补充：

1. **官方 launcher/doctor 命令**
   - 检查 Python/SQLite/DB 路径/profile 可用性
   - 输出可复制的修复命令
   - 降低项目方接入成本

2. **JSON-first 输出模式**
   - `list/search/manage` 支持稳定 JSON schema
   - 便于其他项目程序化集成

3. **多语言 SDK 模板**
   - Go/Rust/Node 最小集成示例
   - 封装 CLI 调用 + 重试 + 错误分类

4. **标签策略标准化**
   - 自动补全结构化标签（`tenant:<id>`, `module:<name>`）
   - 标签质量评分与提醒

5. **来源追溯字段**
   - 补充 `KnowledgeSourceRef` 能力
   - 记录记忆来源（人工/工具/系统）

### 4.2 说明书需补充的内容

1. **CLI 工具 vs 服务两种形态**的差异化描述
2. **Profile 机制**的说明（claude/codex/shared 隔离）
3. **Feedback 机制**作为"事后修正"模式的说明
4. **ToolCall 追踪**作为 los-memory 实际承担的能力
5. **跨项目集成模式**（CLI 调用 vs API 调用）

---

## 5. 需要调整的内容

### 5.1 接口定义调整

**原文（建议删除或迁移到 los-ast）**:
```
### 1. discover - 用于发现代码结构事实
### 2. validate - 用于验证某个结论是否被代码事实支持
### 3. enumerate - 用于枚举某类目标或证据
```

**建议替换为 los-memory 实际接口**:
```
### 1. memory CRUD
- add/ingest: 写入记忆
- get/search/timeline: 检索记忆
- edit/delete: 修改删除

### 2. memory governance
- feedback: 反馈与修正
- link/unlink/related: 关联管理
- clean/manage: 维护管理

### 3. session & checkpoint
- session: 工作会话管理
- checkpoint: 检查点保存与恢复
```

### 5.2 数据模型调整

**原文模型列表（需修订）**:
```
- CorrectedFact
- RejectedHypothesis
- IncidentLesson
- MemoryEntry
- MemorySummary
- TemporalEdge
- KnowledgeSourceRef
- FactStatus
```

**建议调整为实际模型**:
```
核心实体:
- Observation (多态: decision/incident/note/meeting/todo/snippet/tool_call)
- Session (工作会话)
- Checkpoint (检查点)

治理实体:
- Feedback (反馈修正)
- ObservationLink (记忆关联)
- ToolCall (工具调用追踪)

待扩展:
- KnowledgeSourceRef (来源追溯)
- MemorySummary (知识摘要 - 规划中)
```

### 5.3 协作链路调整

**原文示例**（假设 los-memory 可被上层服务调用）：
```
VPS Agent Web -> los-memory (检索/写入)
```

**实际应为**（CLI 调用模式）：
```
VPS Agent Web --(CLI)--> los-memory
或其他项目 --(Python API)--> los-memory
```

需要明确 los-memory 的**调用方集成责任**：
- 调用方需确保 Python 环境
- 调用方需处理 CLI 返回值解析
- 调用方需管理 Profile 选择

---

## 6. 修订建议

### 6.1 短期建议（MVP 阶段）

1. **接受 CLI 工具定位**：说明书应明确 los-memory 当前是 CLI 工具，而非服务
2. **修正接口描述**：将 los-ast 的接口类型从 los-memory 章节移除
3. **补充 Profile 机制**：说明 claude/codex/shared 的设计意图
4. **明确集成契约**：定义 JSON 输出格式和退出码规范

### 6.2 中期建议（服务化演进）

如需将 los-memory 演进为服务组件：

1. **保持 CLI 能力**：向后兼容，CLI 变为客户端
2. **引入 proposal/commit 模式**：支持审批流程
3. **添加 HTTP/gRPC 接口**：支持服务间直接调用
4. **实现缺失模型**：KnowledgeSourceRef、MemorySummary 等

### 6.3 架构边界澄清

**关键决策点**：

| 问题 | 选项 A（保持现状） | 选项 B（服务化） | 建议 |
|------|------------------|-----------------|------|
| 部署形态 | CLI 工具 | 本地服务 | A（短期） |
| 调用方式 | 子进程/本地 API | HTTP/gRPC | A（短期） |
| 数据范围 | 个人/代理级 | 项目级 | A（短期） |
| 用户隔离 | Profile 机制 | 多租户 | 两者结合 |

---

## 7. 结论

### 7.1 说明书整体评价

- **架构分层合理**：底层能力/中间治理/上层控制的分层清晰
- **单向依赖原则正确**：上层编排下层，下层不感知上层
- **所有权原则重要**：谁拥有长期状态，谁拥有功能归属

### 7.2 主要修订点

1. **定位校准**：从"项目级知识账本"调整为"个人/代理级记忆工具"
2. **接口修正**：移除 los-ast 类型接口，补充实际记忆管理接口
3. **模型映射**：建立说明书模型与实际模型的对应关系
4. **集成模式**：明确 CLI 调用模式的服务契约

### 7.3 下一步行动

1. 根据本评审报告修订《架构边界说明书 v1.1》
2. 在 los-memory 项目实现 P0 优先级功能（doctor、JSON 输出）
3. 建立 los-memory 与其他项目的实际集成验证
4. 定期回顾架构边界，防止职责漂移

---

## 附录：功能归属速查表修订

| 问题/功能 | 归属 | 说明 |
|----------|------|------|
| 查询某函数有哪些调用方 | los-ast | 代码分析能力 |
| 检查某 patch 影响哪些模块 | los-ast | 影响面分析 |
| 保存"这个项目已确认必须加 tenant_id 过滤" | los-memory | 作为 Observation(kind=fact) |
| 保存"上次猜测 root cause 是缓存污染，但被证明错误" | los-memory | 作为 Feedback(action_type=correct) |
| 决定这次请求走 Claude 还是 Gemini | lsclaw | 路由决策 |
| 控制超预算时是否降级模型 | lsclaw | 预算策略 |
| 展示任务进度、审批记录、执行链路 | VPS Agent Web | 控制面展示 |
| 批量调度节点执行任务 | VPS Agent Web | 编排执行 |
| **记录工具调用历史** | **los-memory** | **实际承担** |
| **保存工作检查点** | **los-memory** | **实际承担** |
| **建立记忆关联** | **los-memory** | **实际承担** |

---

**评审完成日期**: 2026-03-07
**评审报告版本**: v1.0
