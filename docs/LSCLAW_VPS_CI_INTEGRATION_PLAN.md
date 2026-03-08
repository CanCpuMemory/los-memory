# lsclaw × VPS Agent Web × los-memory 对接与持续集成方案

**版本**: 1.0  
**状态**: 可执行方案  
**更新时间**: 2026-03-08

---

## 1. 目标与边界

### 1.1 目标

1. 建立 `lsclaw` 与 `los-memory` 的稳定检索/写入契约，保障多租户隔离与可审计性。
2. 完成 `approval` 能力向 `VPS Agent Web` 的可控迁移，保留回滚路径。
3. 将对接契约纳入 CI，形成“改动即验证”的持续保障。

### 1.2 职责边界

- `los-memory`: 记忆账本与检索能力、对接适配层。
- `lsclaw`: 路由治理与执行编排，调用 memory 能力但不持有记忆真相副本。
- `VPS Agent Web`: 控制平面与审批工作流主域（approval Source of Truth）。

---

## 2. 对接总体架构

```text
lsclaw (control-plane)
   ├─ memory search/list/get/timeline  ---> los-memory CLI/API
   ├─ observation add/feedback/link    ---> los-memory CLI/API
   └─ tool transition/review apply     ---> los-memory CLI/API

VPS Agent Web (approval 主域)
   ├─ create/approve/reject/list/get   ---> approval domain API
   └─ callback/events (HMAC/SSE)       ---> los-memory migrate_out adapter

los-memory
   ├─ memory/observation/tool/review/admin
   └─ migrate_out.approval (dual-write, remote-only, local-only)
```

---

## 3. lsclaw 对接方案

### 3.1 接口契约

1. 检索接口统一使用：
   - `memory search`
   - `memory list`
   - `memory get`
2. 写入与修订接口统一使用：
   - `observation add`
   - `observation feedback`
   - `observation link/unlink/related`
3. 编排轨迹与评审闭环：
   - `tool transition`
   - `review apply --file <json>`

### 3.2 隔离策略

1. `search/list` 强制传递 `--require-tags`。
2. 标签最小集合：`tenant:<id>,user:<id>`；会话场景追加 `session:<id>`。
3. recall 降级只允许：
   - 第一层：tenant+user+session
   - 第二层：tenant+user
   - 禁止跨 tenant 放宽

### 3.3 落地步骤

1. `integrations-memory-adapter` 增加 `requiredTags` 参数透传。
2. `server` 路由统一注入 scope tags。
3. `team-agent-orchestrator` 阶段末写 `tool transition`。
4. reviewer 产出 `review-feedback.json` 并执行 `review apply`。

### 3.4 验收标准

1. tenant/user 隔离测试全部通过。
2. 每个 team stage 至少一条 `agent_transition` 记录。
3. review 回写后目标 observation 内容可见更新。

---

## 4. VPS Agent Web 对接方案

### 4.1 迁移阶段与策略

1. `local-only`: 本地审批，冻结新增功能。
2. `dual-write`: 本地+远端双写，开启一致性核对。
3. `remote-only`: 远端为主，本地仅兜底/审计。
4. `removed`: 本地 approval 彻底下线。

### 4.2 关键能力对齐

1. HMAC 兼容：
   - 统一签名算法、时间窗、nonce 校验策略。
2. SSE 事件：
   - 维持迁移期事件语义稳定，事件字段不破坏兼容。
3. 错误语义：
   - 远端错误代码映射到本地统一错误结构，保证调用方处理逻辑一致。

### 4.3 数据一致性保障

1. 双写阶段引入“写后比对”：
   - 关键字段：`job_id`, `status`, `version`, `updated_at`。
2. 日终核对任务：
   - 对比本地与远端审批状态，输出差异清单。
3. 差异处置策略：
   - 先告警再人工确认，禁止自动覆盖高风险状态。

---

## 5. 持续集成对接方案

### 5.1 CI 流水线分层

1. **文档一致性层**
   - 校验 docs 中命令示例是否使用现行分组命令。
2. **契约单元层**
   - 校验 CLI 输出结构、错误结构、退出码行为。
3. **集成回归层**
   - 覆盖 `lsclaw -> los-memory` 的 search/list/review/transition 关键路径。
4. **迁移回归层**
   - 覆盖 `local-only/dual-write/remote-only` 三种迁移模式。

### 5.2 推荐 CI Job 设计

1. `docs-command-lint`
   - 基于正则或脚本扫描旧命令样式，发现即失败。
2. `cli-contract-test`
   - 运行核心命令矩阵，验证输出字段与退出码。
3. `lsclaw-adapter-e2e`
   - 模拟多租户多用户检索，验证 scope tags 生效。
4. `approval-migration-e2e`
   - 模拟双写、回退、远端失败场景，验证补偿逻辑。

### 5.3 发布闸门

1. 主干合并前必须通过：`docs-command-lint` + `cli-contract-test`。
2. 发布分支必须额外通过：`lsclaw-adapter-e2e` + `approval-migration-e2e`。
3. 任一迁移回归失败时，禁止切换到 `remote-only`。

---

## 6. 实施里程碑

### M1（基础契约）

1. 文档口径统一。
2. lsclaw adapter/server 接入 `requiredTags`。
3. CI 接入 `docs-command-lint` 与 `cli-contract-test`。

### M2（闭环与迁移）

1. team orchestrator 接入 `tool transition`。
2. reviewer 接入 `review apply`。
3. dual-write 一致性核对任务上线。

### M3（稳定与切换）

1. 迁移回归全绿并连续稳定。
2. 分批切到 `remote-only`。
3. 完成本地 approval 清理窗口评估。

---

## 7. 风险与缓解

1. **风险**: 文档与实现再次漂移  
   **缓解**: 增加 docs 命令 lint，纳入主干必跑。
2. **风险**: 双写一致性问题难定位  
   **缓解**: 增加写后比对日志与差异报表。
3. **风险**: 多租户召回越界  
   **缓解**: 强制 `requiredTags`，并提供越界用例为回归基线。
