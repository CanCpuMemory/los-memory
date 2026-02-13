# lsclaw 接入升级指南（基于 los-memory 新能力）

本文档用于指导你在 `lsclaw` 仓库执行后续改造，目标是：

- 检索强隔离：避免跨 tenant/user/session 误召回
- 结构化轨迹：为训练与回放准备 transition 数据
- review 闭环：把评审结论自动回写到记忆

更新时间：2026-02-13

## 1. 本次 los-memory 已新增能力

1. `search/list` 新增 `--require-tags`（AND 语义）
   - 示例：`--require-tags "tenant:tenant-a,user:alice,session:s1"`
2. 新增 `transition-log`
   - 写入 `kind=agent_transition` 的结构化轨迹
3. 新增 `review-feedback`
   - 批量读取 review JSON，对 observation 自动执行 `feedback` 修订
4. 修复 `export --format csv` 对 `session_id` 的兼容

## 2. lsclaw 需要改的核心点

## 2.1 memory adapter：补强隔离参数

建议修改文件：`control-plane/src/m1/integrations-memory-adapter.mjs`

新增参数：

- `requiredTags?: string[]`

`searchMemoryObservations` 调用时：

- 把 `requiredTags` 拼成 `tenant:...,user:...,session:...`
- 透传为 CLI 参数：`--require-tags "tenant:x,user:y,session:z"`

`listMemoryObservations` 同理透传 `--require-tags`。

## 2.2 server 路由：默认强约束，不允许裸检索

建议修改文件：`control-plane/src/m1/server.mjs`

对于：

- `GET /api/users/:userId/memory/search`
- `GET /api/users/:userId/memory/list`

调用 adapter 时默认注入：

- `tenant:${tenantId}`
- `user:${userId}`
- 可选：`session:${sessionId}`（有会话上下文时）

这样即使 query 很宽泛，也不会出租户或跨用户结果。

## 2.3 chat 自动回忆：从 query 拼接，升级为硬过滤

当前做法是 query 里拼 `tenant:user:session` 字符串。  
建议升级为：

- query 只保留语义文本（用户消息）
- 过滤交给 `requiredTags`

收益：可解释、可审计、可测。

## 2.4 团队编排：阶段结果写 transition-log

建议修改文件：`control-plane/scripts/team-agent-orchestrator.mjs`

每个 stage 执行后新增一条 transition：

- `phase`: `team_stage`
- `action`: `${stage.id}:${stage.role}:${stage.agent}`
- `input`: 阶段 prompt 摘要
- `output`: `ok/exitCode/summary`
- `status`: `success|error`
- `reward`: 可选（如验证通过=1，不通过=0）

## 2.5 review 闭环：落地 review-feedback

建议在 reviewer 阶段后生成 `review-feedback.json`，格式：

```json
{
  "items": [
    { "observation_id": 101, "feedback": "修正: ..." },
    { "observation_id": 102, "feedback": "补充: ..." }
  ]
}
```

然后执行：

```bash
python3 /path/to/los-memory/memory_tool/memory_tool.py \
  --profile shared review-feedback --file review-feedback.json
```

预检用：

```bash
python3 /path/to/los-memory/memory_tool/memory_tool.py \
  --profile shared review-feedback --file review-feedback.json --dry-run
```

## 3. 验收标准（建议）

1. 隔离
   - tenant A + user alice 写入后，tenant B 或 user bob 无法通过 search/list 命中
2. 轨迹
   - 每个 team stage 至少有一条 `agent_transition`
3. 闭环
   - review-feedback 执行后，目标 observation 的 summary/title 可见更新
4. 导出
   - CSV 里包含 `session_id` 列

## 4. 推荐落地顺序

1. adapter + server 强隔离（最优先）
2. chat recall 改为 `requiredTags` 硬过滤
3. team orchestrator 增加 `transition-log`
4. reviewer 增加 `review-feedback` 自动回写
5. 增补 e2e 测试与运维手册
