# 自迭代自优化故障恢复系统架构设计 v1.0

**评估日期**: 2026-03-07
**评估方式**: 多角色协作（架构师 + 后端工程师 + CLI专家）
**状态**: 已评估，待实施

---

## 1. 架构评估结论

### 1.1 总体评价

用户提出的"自迭代自优化故障恢复系统"是一个**概念先进的闭环自治系统**，与现有四项目架构存在**显著的边界冲突和职责重叠**，需要**分层解耦和职责重新分配**后再实施。

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构合理性 | ★★★☆☆ | 概念先进，但与现有边界冲突 |
| 技术可行性 | ★★★★☆ | 技术方案可行，工作量中等 |
| 安全性 | ★★☆☆☆ | 自动恢复存在高风险，需人工审批 |
| 可扩展性 | ★★★★☆ | 模块化设计，便于独立演进 |
| 可维护性 | ★★★☆☆ | 复杂度较高，需要清晰文档 |

### 1.2 主要问题

1. **自动恢复执行者缺失**: 四项目中无任何项目被设计为"执行自动恢复操作"的角色
2. **los-ast 与 los-memory 直接耦合**: 违反现有架构单向依赖原则
3. **自优化与热重载风险**: 自我修改系统难以审计和回滚
4. **循环依赖风险**: 闭环设计可能形成反馈循环

### 1.3 关键调整建议

| 原设计 | 调整后 | 理由 |
|--------|--------|------|
| 自动恢复直接执行 | 分级执行：L1自动/L2审批/L3建议 | 安全性优先 |
| los-ast <-> los-memory 直接耦合 | 通过 VPS Agent Web 编排聚合 | 遵守架构边界 |
| 自优化直接修改策略 | 生成策略建议提案，人工审批后执行 | 可控的自我改进 |
| 运行时热重载 | 滚动重启 + 配置中心推送 | 简单可靠，易于回滚 |

---

## 2. 调整后架构

### 2.1 职责重新分配

```
┌─────────────────────────────────────────────────────────────────┐
│                    Recovery Orchestrator                        │
│                    (VPS Agent Web 扩展)                          │
│  - 接收故障通知 (Trigger)                                        │
│  - 调用 Analyzer 进行归因 (聚合 los-ast + los-memory)            │
│  - 根据分级决定：自动执行(L1) / 提交审批(L2/L3)                  │
│  - 记录 RecoveryAction 到 los-memory                             │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   L1 执行     │    │   审批队列       │    │   经验沉淀      │
│  (自动执行)   │    │   (L2/L3)        │    │  (los-memory)   │
│               │    │                 │    │                 │
│ - 缓存刷新    │    │ - 人工审批       │    │ - Incident      │
│ - 健康检查    │    │ - 影响面确认     │    │ - Recipe        │
│ - 状态查询    │    │ - 灰度验证       │    │ - Hypothesis    │
└───────────────┘    └─────────────────┘    └─────────────────┘
```

### 2.2 数据模型映射

| 用户概念 | 映射方案 | 优先级 |
|----------|----------|--------|
| **Incident** | `Observation(kind="incident")` | P0 |
| **RecoveryRecipe** | `Observation(kind="recovery_recipe")` + tags | P1 |
| **RecoveryAction** | `ToolCall` 扩展或 ObservationLink | P1 |
| **Hypothesis** | `Observation` + `ObservationLink` | P2 |
| **PreventionRule** | `Observation(kind="prevention_rule")` | P2 |
| **OptimizationItem** | `Feedback(action_type="optimize")` | P3 |

**建议**: Phase 1-2 复用现有模型快速验证，Phase 3+ 根据数据量决定是否独立建表。

---

## 3. 子系统详细设计

### 3.1 观测与触发器系统

**职责**: 采集故障信号，触发故障恢复流程

**触发器类型**:
- **规则触发**: 5分钟内某接口5xx超过阈值
- **语义触发**: 日志中出现同类panic模式
- **经验触发**: memory中已有"这类错误必须先回滚"

**技术选型**:
- 触发器: Redis + Celery (生产) / APScheduler (开发)
- 事件流: SSE (Server-Sent Events)

**API设计**:
```typescript
// POST /api/v1/recovery/trigger
interface TriggerRequest {
  incident_id: string;
  severity: "p0" | "p1" | "p2";
  symptoms: {
    metric: string;
    value: number;
    threshold: number;
    duration_sec: number;
  }[];
  context: {
    service: string;
    environment: string;
    trace_id?: string;
  };
}
```

### 3.2 故障归因系统

**职责**: 分析故障根因，生成归因假设

**流程**:
1. VPS Agent Web 收集上下文（trace、logs、deployment diff）
2. 调用 los-memory 检索历史相似 incident
3. 调用 los-ast 分析最近代码变更影响面
4. 调用 lsclaw 进行多模型归因分析
5. 输出结构化归因报告

**输出格式**:
```json
{
  "incident_id": "...",
  "suspected_root_causes": [...],
  "supporting_evidence": [...],
  "rejected_hypotheses": [...],
  "blast_radius": [...],
  "recommended_actions": [...],
  "auto_recoverable": true/false
}
```

### 3.3 自动恢复系统

**分级恢复策略**:

| 级别 | 操作类型 | 执行方式 | 示例 |
|------|----------|----------|------|
| **L1** | 无害恢复 | 自动执行 | 重试、切换provider、清缓存、健康检查 |
| **L2** | 受控恢复 | 审批后执行 | 回滚版本、灰度关闭功能、重建索引 |
| **L3** | 代码级恢复 | 生成候选，人工审批 | 代码修复、配置变更、数据修复 |

**安全约束**:
- 全局恢复速率限制（每项目每分钟最多 N 次）
- 恢复操作依赖图检测（禁止循环依赖的恢复链）
- L2/L3 必须人工审批

### 3.4 经验沉淀系统

**职责**: 记录故障事实、恢复经验、错误假设

**沉淀对象**:

1. **IncidentFact**: 确认发生了什么
   - 时间、范围、影响、触发条件、最终根因

2. **RejectedHypothesis**: 哪些判断被证明是错的
   - 误判根因、无效恢复动作、误导性指标

3. **RecoveryRecipe**: 有效恢复步骤
   - 先做什么、禁止做什么、成功条件、回滚条件

4. **PreventionRule**: 下次怎么提前防
   - 新告警规则、新校验规则、新测试样例

### 3.5 自迭代优化系统

**职责**: 持续优化策略、规则、恢复流程

**优化范围**:
- 策略: 路由策略、预算策略、熔断阈值
- 规则: los-ast 扫描规则、发布门禁
- 恢复手册: 故障A的恢复动作优化
- 测试: 为incident增加golden case

**实现方式**:
```
Optimizer -> 生成策略建议提案
         -> 提交 VPS Agent Web 审批
         -> 批准后由对应系统执行
         -> 记录到 los-memory (Feedback)
```

### 3.6 热重载系统

**可热重载对象**:
- 路由策略、budget policy、guardrail rule
- 任务模板、恢复剧本、扫描规则
- 告警阈值、provider profile

**发布流程**:
1. 生成新bundle（带版本号）
2. 预验证
3. 小范围热加载（canary）
4. 观测
5. 全量生效或回退

---

## 4. API 接口契约

### 4.1 los-memory 新增接口

```bash
# 故障记录
memory incident create --severity critical --title "..." --summary "..."
memory incident list --status detected --project "vps-agent"
memory incident resolve <id> --root-cause "..." --recipe-id <id>

# 恢复方案管理
memory recipe create --trigger '{"error_code": "E1001"}' --steps '[...]'
memory recipe search --error-code E1001 --output json
memory recipe apply <recipe_id> --incident-id <id> --dry-run

# 假设验证记录
memory hypothesis create --incident-id <id> --description "..."
memory hypothesis reject <id> --reason "..."
memory hypothesis list --incident-id <id>

# 预防规则
memory rule create --condition "..." --action "..." --severity high
memory rule enable/disable <rule_id>
```

### 4.2 跨项目集成契约

**数据交换格式**:
```json
// los-memory -> VPS Agent Web (查询恢复方案)
{
  "type": "recovery_recipe_query",
  "error_signature": {
    "service": "vps-agent",
    "error_code": "E1001",
    "error_pattern": "connection timeout"
  },
  "context": {
    "project": "vps-agent",
    "recent_observations": [...]
  }
}

// VPS Agent Web -> los-memory (记录故障)
{
  "type": "incident_report",
  "incident": {
    "severity": "critical",
    "title": "Database connection pool exhausted",
    "tags": ["incident", "db", "connection-pool"],
    "related_observations": [123, 456]
  }
}
```

---

## 5. 实施计划

### 5.1 工作量估算

| 阶段 | 任务 | 人天 | 依赖 |
|------|------|------|------|
| Phase 1 | 观测 + 记录闭环 | 8 | T001-T008完成 |
| Phase 2 | L1自动恢复 | 12 | Phase 1 |
| Phase 3 | L2审批恢复 + 归因 | 20 | Phase 2 |
| Phase 4 | 经验沉淀 + 自优化 | 17 | Phase 3 |
| **总计** | | **57** | |

### 5.2 里程碑规划

**Milestone 1: 观测 + 记录闭环 (2周)**
- [ ] VPS Agent Web 集成 los-memory 记录 incident
- [ ] los-memory 新增 incident 子命令
- [ ] 基础归因分析建议（人工确认）

**Milestone 2: L1 自动恢复 (3周)**
- [ ] 分级恢复框架
- [ ] L1 恢复动作白名单实现
- [ ] 恢复结果反馈机制

**Milestone 3: L2 审批恢复 + 归因 (4周)**
- [ ] 审批工作流集成
- [ ] los-ast 影响面分析集成
- [ ] 多模型归因分析
- [ ] RecoveryRecipe 库建立

**Milestone 4: 经验沉淀 + 自优化 (3周)**
- [ ] RejectedHypothesis 记录
- [ ] PreventionRule 管理
- [ ] 策略优化建议生成
- [ ] 热重载机制

### 5.3 与现有任务衔接

```
当前任务 (T001-T008)
    │
    ▼
Phase 1 启动 (incident记录)
    │
    ▼
T011-T015 CLI重构完成后
    │
    ▼
Phase 2-3 (L1/L2恢复)
    │
    ▼
T024+ SDK模板完成后
    │
    ▼
Phase 4 (跨项目集成)
```

---

## 6. 风险评估与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| 自动恢复误操作 | 高 | L2/L3必须人工审批；L1严格限定只读操作 |
| 策略优化失控 | 高 | 设置优化边界；保留人工否决权；快速回滚 |
| 数据污染 | 中 | Feedback机制；定期审计Recipe有效性 |
| 级联故障 | 高 | 全局速率限制；依赖图检测；人工审批断路 |
| 归因准确性不足 | 中 | 置信度阈值；人工确认机制 |

---

## 7. 关键决策点

| # | 决策点 | 建议 | 影响 |
|---|--------|------|------|
| 1 | 自动恢复执行者 | VPS Agent Web 扩展 | 最小架构变更 |
| 2 | L1/L2/L3 分级 | 按操作风险分级 | 安全性直接相关 |
| 3 | 自优化范围 | 建议+审批模式 | 平衡效率与安全 |
| 4 | 热重载实现 | 滚动重启 | 简单可靠 |
| 5 | los-memory 服务化 | 暂缓，保持CLI | 避免阻塞项目 |

---

**文档结束**
