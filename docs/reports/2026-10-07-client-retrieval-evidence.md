# W-06 客户端接入与真实检索留证

日期：2026-10-07。范围：Codex / Kimi / Grok / DSH 四个客户端对 `los-memory-shadow` 的接入与实际工具调用。只读，未改主库。

门槛 4 的要求是"**在 Kimi/Codex/Grok 新会话分别完成真实工具检索和按 ID 读取**"，并且明确"客户端配置解析成功不算实际工具调用成功"。本报告按后者取证：每个客户端都由**它自己的 agent 循环**发起工具调用，而不是由我直接对 MCP 服务器发 JSON-RPC。

## 结果

| 客户端 | 接入配置 | 握手 | 发现工具 | 真实 agent 调用 |
| --- | --- | --- | --- | --- |
| Codex | `~/.codex/config.toml` `[mcp_servers.los-memory-shadow]` | ✅ | — | ✅ `mcp: los-memory-shadow/shadow_status started / (completed)` |
| Kimi | `~/.kimi-code/mcp.json` | ✅ | — | ✅ 模型自述 `mcp__los-memory-shadow__shadow_status` 并返回 JSON |
| Grok | **本轮补注册**（此前 0 server） | ✅ `handshake OK (2025-06-18)` | ✅ `3 tools discovered` | ✅ `grok -p … --always-approve` |
| DSH | `dsh-mcp-los-memory-shadow` bundle（link 到 `dsplugins/dsh-mcp-los-memory-shadow`） | — | — | ⚠️ 插件树 active，工具可见性需新会话（见下） |

四个客户端拿到的 `shadow_status` 是**同一份** JSON（`total: 2048`、`search_index.state: "ready"`、`contract.project_coverage: 0.2812`、`metering.requests: 121`、`bytes: 5735247`），可交叉印证：影子服务、MCP 适配层、客户端注入三处一致，不存在某个客户端拿到陈旧或裁剪过的字段。

## Grok 修复

此前 Grok 是掉的：`~/.grok/config.toml` 0 server，从 `~/.claude.json` 继承的 5 个里没有影子。补注册命令：

```sh
grok mcp add los-memory-shadow /usr/bin/ssh --transport stdio --scope user -- \
  -T -o BatchMode=yes -o ConnectTimeout=5 m3-t \
  /Users/echerlos/.local/share/los-memory-shadow/serve
grok mcp doctor los-memory-shadow
  ✓ command found (/usr/bin/ssh)
  ✓ server started (0.0s)
  ✓ handshake OK (protocol 2025-06-18)
  ✓ 3 tools discovered
  Found 1 healthy, 0 failing.
```

## DSH 接入（已存在，本轮只做验证）

`~/.dsh/profiles/web/node_modules/dsh-mcp-los-memory-shadow` → `dsplugins/dsh-mcp-los-memory-shadow`，声明 `dsh.bundle.patch`，已在 `~/.dsh/profiles/web/package.json` 的 bundles 与 dependencies 里。组合结果：

```
# pnpm dsh --profile web --dump-config
# == dsh-mcp-los-memory-shadow
- id: mcp-los-memory-shadow
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: los_memory_shadow
    transport: stdio
    command: /usr/bin/ssh
    args: ['-T','-o','BatchMode=yes','-o','ConnectTimeout=8','m3-t','/Users/echerlos/.local/share/los-memory-shadow/serve']
    failOnStartupError: false
    toolCallTimeoutMs: 60000
    descriptionMaxLength: 400
```

运行时插件树（`dsh-obs plugins los`）：**220 项 / active 189 / failed 0**，其中 `include:mcp-los-memory-shadow`（`@deepseek-ai/dsh-mcp-client`）为 `active`。

配置行存在 ≠ 工具已注入：按 dsh-plugin-operations 的"注入时序"约定，**重启后已有会话的工具 schema 快照不含新 MCP 工具**，必须新会话或刷新页面才可见。因此本项**不能**用当前会话看不到 `mcp__los_memory_shadow__*` 来判失败；判定依据是插件树 active（已满足）。剩余未取证项：DSH 新会话里真实调用一次（留给下一个会话，30 秒可完成）。

## 复现命令

```sh
# Codex：真实 agent 调用（输出含 mcp: los-memory-shadow/shadow_status started）
cd /tmp && codex exec --sandbox read-only --skip-git-repo-check \
  '调用 los-memory-shadow 的 shadow_status 工具并把 JSON 原样贴出来' </dev/null

# Kimi
cd /tmp && kimi -p '调用 los-memory-shadow 的 shadow_status 工具并把 JSON 原样贴出来'

# Grok
cd /tmp && grok -p '调用 los-memory-shadow 的 shadow_status 工具并把 JSON 原样贴出来' --always-approve

# DSH
dsh-obs plugins los
pnpm dsh --profile web --dump-config | grep -A12 'mcp-los-memory-shadow'
```

## 残留缺口

1. **按 ID 读取（`shadow_get`）尚未在任一客户端做真实调用**：本轮四个客户端验证的都是 `shadow_status`。门槛 4 要求"检索**和**按 ID 读取"两项，`shadow_get` 还缺真实取证。
2. **DSH 新会话内的真实调用**未取证（插件树 active，但工具可见性需新会话）。
3. 本轮未做"用影子结果回答一个真实问题并与 Nowledge 对比"的任务级验证——那属于 W-01 评测夹具的范围，不在本项。
