# los-memory 跨项目接入复盘与优化建议（2026-02-25）

## 1. 检查范围

- `/Users/echerlos/Downloads/projects/zeroclaw`
- `/Users/echerlos/Downloads/projects/fullstackframe`
- `/Users/echerlos/Downloads/projects/cantool`
- `/Users/echerlos/syncthing/project/lot2extension`

## 2. 接入现状

- 已接入：`cantool`（Rust/Tauri，CLI 包装）
- 未接入：`zeroclaw`、`fullstackframe`、`lot2extension`

## 3. 数据面观察

本机现有 DB（非项目内置）：

- `~/.claude_memory/memory.db`: `observations=7`
- `~/.local/share/llm-memory/memory.db`: `observations=381`
- `project='cantool'` 在两库中均为 `0`

说明：当前虽有接入代码，但目标项目未稳定沉淀业务 memory 数据。

## 4. 共性问题

1. 接入路径不统一：
   - 有的项目用自研 memory，有的计划接 los-memory，有的仅文档提及。
2. 启动依赖脆弱：
   - 对 `python3 -m memory_tool` 强依赖，环境差异容易导致不可用。
3. 结构化约束不足：
   - title/summary/tags 规范在项目间不一致，影响检索质量。
4. 缺少“健康检查 -> 降级 -> 观测”闭环：
   - 不可用时通常只有失败，没有统一告警和指导。

## 5. 可落地优化建议（对 los-memory 项目）

### P0

1. 提供稳定可执行入口（优先级最高）：
   - 新增官方 launcher（如 `los-memory` 命令或 `python3 -m memory_tool` 安装脚本）
   - 降低项目方对路径/环境差异处理成本
2. 增加 `doctor` 子命令：
   - 检查 Python、SQLite、DB 路径、profile 可用性
   - 输出可复制的修复命令
3. 提供 JSON-first 输出模式：
   - `list/search/manage` 支持稳定 JSON schema，减少文本解析

### P1

4. 提供“项目接入模板包”：
   - Go/Rust/Node 三套最小 SDK 示例（调用 + 重试 + 错误分类）
5. 提供“标签策略助手”：
   - 自动补全 `tenant:user:session:module:type` 结构化标签
6. 引入 ingestion quality score：
   - 检查 title/summary/tags 质量，低质量写入给出提醒

### P2

7. 增加跨项目对比报表：
   - 每项目写入量、检索命中率、重复标签率、冷数据比例
8. 增加“推荐清理与归并”：
   - 合并相近标签（如 `db/postgres/postgresql`）

## 6. 建议的统一写入规范（跨项目）

- `project`: 仓库名（固定）
- `kind`: `decision|incident|note|fact|transition`
- `tags`:
  - 作用域：`tenant:<id>`, `user:<id>`, `session:<id>`
  - 模块：`module:<name>`
  - 主题：`auth`, `scheduler`, `rollback`, `migration`, `adapter`

## 7. 下一步建议

1. 在 los-memory 增加 `doctor` 与 `json output`（P0）。
2. 输出 Go/Rust/Node 三个官方集成模板（P1）。
3. 建立每周接入质量巡检脚本（P2）。

