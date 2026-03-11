# lsclaw 对接设计评审与可持续演进说明

> 状态说明：本文档是 2026-03-08 的设计评审记录，用于保留对接方案的设计判断，不是当前实现状态真相源。
> 当前已落地能力与主实现路径请以 `README.md` 和 `docs/current/CURRENT_STATE.md` 为准。

更新时间：2026-03-08

## 1. 范围

本评审覆盖以下落地项：

1. GitHub Actions 基础 CI
2. 文档命令口径校验
3. lsclaw 集成 smoke 测试
4. 对接契约最小集

## 2. 设计结论

### 2.1 合理性

1. 采用“文档门禁 + 契约回归 + 集成烟测”的三层结构，符合跨仓协作的最小稳定闭环。
2. 优先固化 `require-tags`、`tool transition`、`review apply`，与 lsclaw 当前对接需求一致。
3. 将旧命令回流阻断前置到 CI，降低文档漂移引发的联调失败概率。

### 2.2 可维护性

1. CI 职责按 job 分层，便于定位失败范围。
2. 契约基线通过独立文档沉淀，降低口头约定依赖。
3. smoke 测试走 CLI 实际调用路径，可覆盖参数解析与输出序列化边界。

### 2.3 可扩展性

1. 契约文档可自然扩展到 approval 迁移链路与更多输出字段约束。
2. smoke 测试可按场景追加 tenant/session/taskType 降级路径。
3. docs lint 规则可继续扩展到命令分组、全局参数、退出码说明一致性。

## 3. 当前风险与改进方向

### 3.1 风险

1. docs lint 仍是正则方案，复杂代码块上下文下可能出现漏检。
2. 契约测试目前覆盖核心路径，尚未覆盖全部错误码与失败语义矩阵。

### 3.2 改进方向

1. 新增 `approval-migration-e2e`，对齐迁移阶段回归。
2. 增加 `@pytest.mark.contract` 与按 marker 执行策略。
3. 增加 CI 缓存与并发取消，降低重复构建开销。
4. 增强 `admin doctor` 的 degraded/unhealthy 契约矩阵覆盖。

## 4. 可追溯进度

### M1（已完成）

1. 建立 CI workflow：docs lint / contract / unit+integration / bdd smoke。
2. 建立 lsclaw 契约最小集文档。
3. 建立 lsclaw 集成 smoke 自动化用例。

### M2（下一步）

1. 契约测试覆盖 `admin doctor` 退化语义与关键输出字段。（已完成）
2. 完成 skill 文档命令口径对齐。（已完成）
3. 增加 CI 侧 marker 维度执行策略。（已完成）
4. 增加 `approval-migration-e2e` 作业。（已完成）
5. 抽取 CI 复合动作并启用 pip 缓存。（已完成）
6. 增加 `admin doctor` degraded 契约用例。（已完成）

### M3（中期）

1. 扩展契约版本化治理与升级门禁。
