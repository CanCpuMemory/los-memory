# lsclaw 改造执行清单（逐文件）

本文档给出在 `lsclaw` 仓库可直接执行的改造步骤，目标：

1. 检索强隔离（`tenant/user/session`）
2. 结构化轨迹写入（`transition-log`）
3. review 结果自动回写（`review-feedback`）

前置：`los-memory` 已升级，支持：
- `search/list --require-tags`
- `transition-log`
- `review-feedback --file`

## 0. 环境变量（建议先补）

在 `lsclaw` `control-plane` 的环境配置中新增：

- `MEMORY_ENFORCE_SCOPE_TAGS=1`（默认启用）
- `MEMORY_RECALL_USE_REQUIRED_TAGS=1`（默认启用）
- `TEAM_MEMORY_TRANSITION_LOG=1`（默认启用）
- `TEAM_MEMORY_REVIEW_FEEDBACK=1`（默认关闭，先灰度）

## 1. `control-plane/src/m1/integrations-memory-adapter.mjs`

## 1.1 `searchMemoryObservations(input)` 增加参数

新增入参：
- `requiredTags?: string | string[]`

拼参逻辑：
- 将 `requiredTags` 归一化为逗号分隔字符串
- 非空时追加 CLI 参数：`--require-tags <tags>`

示例（伪代码）：

```js
const requiredTags = normalizeText(input.requiredTags, "");
if (requiredTags) {
  args.push("--require-tags", requiredTags);
}
```

## 1.2 `listMemoryObservations(input)` 同步支持 `requiredTags`

同上，追加：
- `--require-tags <tags>`

## 1.3 新增两个便捷包装（可选）

- `buildScopeTags({ tenantId, userId, sessionId })`
- `joinTags(...parts)`（过滤空值并逗号拼接）

用于 server 路由统一复用，避免多处重复字符串拼接。

## 2. `control-plane/src/m1/server.mjs`

## 2.1 `/api/users/:userId/memory/search` 强制作用域标签

在调用 `searchMemoryObservations` 时，补充：

- `requiredTags: "tenant:<tenantId>,user:<userId>"`
- 有 session 参数时再加 `session:<sessionId>`

注意：
- query 继续保留语义文本（`q`）
- 不再依赖把 scope 塞入 query 文本

## 2.2 `/api/users/:userId/memory/list` 同样强制作用域标签

调用 `listMemoryObservations` 追加相同 `requiredTags`。

## 2.3 chat recall（`/api/chat` 主链路）改为硬过滤

当前 recall 常见写法是 query 拼 scope。改为：

- query：仅用户消息
- requiredTags：tenant/user/session

降级策略：
- 第一轮：tenant+user+session
- 第二轮：tenant+user（放宽 session）
- 不做 tenant 放宽

## 2.4 自动写入 chat memory 保持 scope 标签一致

写入时 tags 保持：
- `tenant:<tenantId>,user:<userId>,session:<sessionId>,chat`

确保召回和写入使用同一标签规范。

## 3. `control-plane/scripts/team-agent-orchestrator.mjs`

## 3.1 阶段完成后追加 `transition-log`

在每个 stage 执行后，除原 `POST /api/users/:id/memory` 外，新增本地 memory_tool 调用（或经控制平面代理）：

- command: `transition-log`
- phase: `team_stage`
- action: `${stage.id}:${stage.role}:${stage.agent}`
- input: 阶段 prompt 摘要
- output: `{ ok, exitCode, summary }`
- status: `success|error`
- reward: 可选，建议
  - 验证通过：`1`
  - 有失败：`0`

## 3.2 review 阶段后自动 `review-feedback`

约定 reviewer 输出产物：`review-feedback.json`，格式：

```json
{
  "items": [
    { "observation_id": 101, "feedback": "修正: ..." },
    { "observation_id": 102, "feedback": "补充: ..." }
  ]
}
```

执行：

```bash
python3 /path/to/los-memory/memory_tool/memory_tool.py \
  --profile shared review-feedback --file review-feedback.json
```

灰度期先用：

```bash
python3 /path/to/los-memory/memory_tool/memory_tool.py \
  --profile shared review-feedback --file review-feedback.json --dry-run
```

## 4. `control-plane/test/*` 建议新增用例

## 4.1 隔离测试

新增测试点：
- tenant A 写入 + tenant B 查询同关键词 => 0 结果
- same tenant 不同 user 查询 => 仅本人结果
- session 作用域召回优先级正确

## 4.2 recall 路径测试

验证：
- recall 请求携带 `requiredTags`
- fallback 只放宽到 tenant+user，不跨 tenant

## 4.3 编排闭环测试

在 `team-orchestrator-sync` 扩展：
- 阶段结束后有 transition 记录
- review-feedback dry-run/apply 都能返回结构化结果

## 5. 实施顺序（建议照此执行）

1. adapter 支持 `requiredTags`
2. server search/list/chat-recall 接入 `requiredTags`
3. 补隔离测试（先测再改或边改边测）
4. orchestrator 增加 `transition-log`
5. orchestrator 接入 `review-feedback`（先 dry-run）
6. 灰度开关放量

## 6. 发布验收清单

- `GET /api/users/:userId/memory/search` 已受 tenant/user 强过滤
- `GET /api/users/:userId/memory/list` 已受 tenant/user 强过滤
- chat recall 日志可看到 requiredTags 生效
- team stage 有 `agent_transition` 记录
- review-feedback 执行报告包含 `applied/failed/errors`
- 无新增高优先级回归
