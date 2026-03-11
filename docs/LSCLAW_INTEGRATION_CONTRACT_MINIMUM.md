# lsclaw 对接契约最小集

本文档定义 `lsclaw` 对接 `los-memory` 的最小稳定契约，用于：

- 保证跨仓调用在版本升级时可回归
- 明确“必须稳定”的命令、输出与失败语义
- 给 CI 的 `cli-contract-test` 和 `lsclaw-adapter-e2e` 提供判定基线

---

## 1. 命令面最小集

`lsclaw` 至少依赖以下命令：

1. 初始化与健康：
   - `init`
   - `admin doctor`
2. 检索与读取：
   - `memory search`
   - `memory list`
   - `memory get`
3. 写入与修订：
   - `observation add`
   - `observation feedback`
4. 编排闭环：
   - `tool transition`
   - `review apply`

说明：

- 旧扁平命令（如 `review-feedback`、`transition-log`）仅作为兼容层，不作为新集成契约。

---

## 2. 参数契约最小集

### 2.1 全局参数

所有命令应支持：

- `--db`：显式数据库路径
- `--output`：结构化输出格式（至少 `json`）
- `--human`：人类可读输出切换

### 2.2 检索隔离参数

`memory search` / `memory list` 应支持：

- `--require-tags "tenant:<id>,user:<id>[,session:<id>]"`（AND 语义）

约束：

- `lsclaw` 在多租户场景必须传递 `tenant` + `user` 标签。
- session recall 场景建议额外传递 `session` 标签。

---

## 3. 输出契约最小集（JSON）

### 3.1 通用成功响应

至少包含：

- `ok: true`

### 3.2 通用失败响应

至少包含：

- `ok: false`
- `error`（字符串或对象）

### 3.3 关键命令字段

1. `memory search`
   - `results`（数组）
2. `memory list`
   - `results`（数组）
3. `review apply`
   - `total` / `applied` / `failed` / `errors` / `dry_run`
4. `tool transition`
   - `id` / `phase` / `action` / `status`
5. `admin doctor`
   - `status`（`healthy`/`degraded`/`unhealthy`）
   - `capabilities`（`can_read/can_write/can_search/can_migrate`）

---

## 4. 退出码契约最小集

为保证脚本化稳定，最小约束如下：

- `0`：命令执行成功
- `1`：命令执行失败

说明：

- `admin doctor` 在 `healthy`/`degraded` 时返回 `0`。
- `admin doctor` 在 `unhealthy` 时返回 `1`，用于上游阻断或触发故障处理。

---

## 5. 兼容与变更策略

1. 兼容层保留原则
   - 旧命令可短期保留，但必须在文档中标记为 deprecated。
2. 变更发布原则
   - 先更新契约文档与测试，再发布命令行为变更。
3. 升级原则
   - `lsclaw` 通过 pinned version 升级，禁止直接追外部 HEAD。

---

## 6. CI 验证最小集

`los-memory` 仓内应至少提供以下验证：

1. `docs-command-lint`
   - 阻断旧命令示例回流。
2. `cli-contract-test`
   - 校验最小命令面的输出与退出码。
3. `lsclaw-integration-smoke`
   - 校验 `require-tags`、`tool transition`、`review apply --dry-run`。

---

## 7. 失败处理建议

1. `admin doctor` 失败
   - 上游标记 `degraded`，暂停写入类流程，保留只读检索。
2. `review apply` 部分失败
   - 以 `failed/errors` 回传到编排层，进入人工复核队列。
3. `tool transition` 失败
   - 不阻断主任务，但必须写告警日志并上报指标。
