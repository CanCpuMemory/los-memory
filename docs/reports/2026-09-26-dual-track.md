# 2026-09-26 双轨部署与验证记录

## 当前结论

用户批准的双轨阶段已实现：Nowledge 为唯一正式写入和默认检索入口，M3 提供独立只读镜像。主库迁移没有发生。设计、迁移判据和操作入口分别见 `docs/design/dual-track-memory.md`、`docs/manuals/SHADOW_MEMORY.md`。

GitHub 仓库已在本机，远端为 CanCpuMemory/los-memory，基线 HEAD 为 `0ab9f9cec557b6140b660868a1b358530f1e0ee7`。实现基于工作树，未提交、未推送；用户原有 AGENTS、脚本整理和文件删除改动保持。M3 发布源摘要为 `0c1fcfd44e6f01326ce2`，数据目录不在 Git 或同步盘。

## 已验证事实

| 项目 | 证据与范围 |
| --- | --- |
| 初始镜像 | canonical 按 ID 读取 1,654 条，零错误，unhydrated=0；加入本轮实施记忆后刷新到 1,655 条 |
| 幂等刷新 | 最终发布连续全量刷新后，第二轮 checked=1655、changed=0、missing=0、errors=[]、unhydrated=0 |
| 定时任务执行 | 对专用 launchd job 执行 kickstart，runs=1、last exit code=0；本轮处理 100 条、changed=0、errors=[]。尚未以长期自然触发运行代替 14 天观察 |
| 版本一致性 | 保存 source ID、space、完整快照、版本摘要、修订历史；排除源 API 随时间变化的展示字段 time，保留 created_at |
| 客户端 Codex | 原生 mcp add 成功；app-server mcpServerStatus/list 发现影子 3 工具、Nowledge 15 工具 |
| 客户端 Grok | doctor 对影子和 Nowledge 均握手成功，分别发现 3/15 工具；Nowledge 来自现有 Claude 配置继承 |
| 客户端 Kimi | 用户级 mcp.json 保留/配置 Nowledge 与影子；真实 TUI /mcp 显示两者 connected，共 18 工具 |
| Kimi 插件 | 官方 community/main 的 Nowledge Mem 0.2.4 安装启用，/plugins info 显示 state ok；/reload 后连接正常 |
| Kimi 版本变化 | 本轮首次启动显示 0.42.0，后续自身更新后显示 2.1.1；最终插件与 MCP 检查发生在 2.1.1 |
| 基础修复 | 中文自动搜索无命中回退、跨项目/类型去重隔离、metadata 排名过滤、混合 embedding 覆盖、编辑失效 hash/embedding |
| 测试 | 89 项定向测试通过；time 展示字段修复后影子 6 项重跑通过；adapter stats/delete dry-run/review dry-run 通过；diff check 与新增模块编译通过 |
| MCP 协议 | 真实 SSH initialize、tools/call status/get 成功，按源 ID 读回本轮批准决策 |
| 搜索延迟 | 同一 SSH 连接 20 次字面查询，median 30.54ms、p95 36.98ms；只代表本轮网络和数据规模，不是长期 SLA |
| 备份恢复 | SQLite backup API 生成私有副本，integrity_check=ok；最终 1,655 条 source ID/space/digest/active 全表摘要一致；直接从恢复副本 get 本轮实施记忆成功、双轨 search 返回 6 条 |

本轮实施事实已保存到 Nowledge：`422f1aaf-da0e-4620-8326-5e841cdd96e1`；批准决策为 `6d24e90a-de0d-4eb5-9a9d-1ec801ad6cf2`。搜索索引此前存在旧内容投影，因此镜像使用 canonical by-ID REST，不使用搜索摘要作为正文。

## 判断与限制

M3 适合作为当前影子服务：私有 SSH 接入、独立存储，故障不影响主库。launchd 属于登录用户，重启登录、休眠恢复、离线最长时间尚未经过 14 天实测。当前轮转每 300 秒最多 100 条，不能宣传全库每 5 分钟更新。

没有调用模型做完整三客户端检索任务；已验证的是配置、真实 MCP 握手/工具发现，以及直接协议调用。Kimi 官方会话生命周期 hooks 已配置，未用新的实际用户对话验证自动入库。原始会话不会镜像到 los-memory。

没有运行完整测试套件。前一轮审计已发现 Python 3.9 下 adapter authentication error 清理路径的既有失败；本轮聚焦改动相关测试，未修改无关适配器。

迁移门槛仍未满足：14 天可用性/刷新证据、40 个人工标注真实问题、跨机恢复、正式写入与撤回 API、会话索引、Working Memory，以及用户确认切换窗口。现有 hash embedding 不是学习式语义模型，当前影子字面搜索不等于完整 Nowledge 替代能力。

## 配置与回退边界

只增加 `los-memory-shadow` MCP，保留 Nowledge 主入口。Kimi 补齐官方插件与远程主库 MCP。私有配置备份保存在本机数据目录，没有放进仓库。回退按运行手册移除同名 MCP 和 M3 的专用 launchd job；不整文件回滚客户端配置，不删除主库记忆。
