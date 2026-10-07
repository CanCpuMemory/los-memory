# 双轨记忆运行手册

适用范围：M1 客户端、M3 影子服务、既有 Nowledge 主库。主库的拓扑和凭证以实际配置为准。

## 日常使用

正式保存、默认搜索、跨工具会话续接仍使用 Nowledge。需要比较时调用 `los-memory-shadow` 的 `shadow_status`，再调用 `shadow_search(query, limit, project?, kind?)` 或 `shadow_get(source_id)`。查询为中文/英文**子串**匹配，多个空格分隔词要求同时命中；不提供模糊语义保证。

`shadow_search` 返回 `{results, meta}`，不是裸数组：

- `meta.mode`：`index+filter`（索引已预筛）或 `scan`（全表扫描）。
- `meta.paths`：每个词实际走的路径——`trigram`（≥3 字符）、`bigram`（2 字符中日韩）、`scan`（索引无法表达，例如 2 字符 ASCII 的 `M3`）。
- `meta.scan_terms`：未能被索引覆盖的词；非空即表示该词仍靠扫描。
- `meta.coverage`：只要带了 `project` 或 `kind` 过滤就会出现，说明过滤能覆盖多少记录。

**`project` 过滤的真实语义（2026-10-07 实测）**：Nowledge 没有 project 字段——2,048 条记录里 `metadata.project` 出现 **0 次**。project 只在满足以下任一条件时才有值：① 记录带注册过的 label（见 `memory_tool/shadow_registry.py`）；② 记录显式声明 `metadata.project`。两者都没有时是 `unassigned`，**不从目录名、线程标题或宿主 app 猜测**。

`project` **不是单值相等**，而是**标签集合成员关系**。一条记录可能真的同时属于两个项目（实测 19 / 2,048 条，例如同时带 `label_cantool` 与 `label_lot2extension`）：

- 这类记录在 `record_facets.project` 里标成 **`multi`**，而不是"按 label 出现顺序取第一个"——顺序没有语义，那样等于猜。
- 但**过滤不受影响**：`project=cantool` 与 `project=lot2extension` 都能召回它。过滤走 `record_labels` 的成员判定，不是投影列相等。
- 显式声明 `metadata.project` 且没有注册 label 的记录仍按相等匹配。

当前覆盖：`project_assigned` **557 / 2,048（27.2%）**，`project_multi` 19，其余 `unassigned`。因此：

> `project` 过滤后的空结果**不等于**"这个项目没有记忆"，只等于"该项目的可归属子集里没有"。看 `meta.coverage` 再下结论。

`kind` 来自源字段 `unit_type`（`fact`/`event`/`context`/`learning`/`decision`/`procedure`/`plan`/`preference`），未知值为 `unknown`，不映射成 `fact`。`claim_status` 缺失时读作 `undeclared`（当前 1,350 / 2,048），**不默认当作 `asserted`**。

## 检索索引

影子库里的索引都是**派生结构**，权威正文始终是 `records.snapshot`；索引只决定"哪些记录被加载并做子串检查"，所以它坏掉或没建时只会变慢，不会丢召回。

| 结构 | 用途 | 备注 |
| --- | --- | --- |
| `records_fts`（FTS5 trigram） | ≥3 字符的词（含中文） | trigram 支持中段子串；默认分词器会把 `uthentication` 查空，属语义退化 |
| `cjk_bigrams` | 2 字符中日韩词 | trigram 无法匹配 2 字符（实测 `迁移门` 命中、`记忆` 不命中） |
| `record_facets` / `record_labels` | `kind` / `project` / `claim_status` / `source_app` 过滤 | 可从 `records` 重建 |

未建索引时 `status()["search_index"]["state"]` 报 `not_built` 并给 `hint`；某个索引路径不可用时该词自动回退扫描，并在 `meta.scan_terms` 里暴露，不会把"索引没建"答成"没有相关记忆"。

实测（M3，2,048 条，2026-10-07）：`记忆`(bigram) 1.5–2.2 ms、`迁移门槛`(trigram) 1.1 ms、零命中的 trigram 查询 1.1–1.3 ms；`M3`（2 字符 ASCII）无索引路径，仍为**全表扫描约 27 ms**，随正文规模线性增长——这是已知限制，属于路线图 P3 的范畴，不要在影子层用词表索引硬凑（2 字符 ASCII 子串需要每字符对建索引，约百万行，代价远超收益）。

## 结果字段

结果携带 `shadow`、原始 `source_id`、`space_id`、`digest`、`verified_at` 与完整源快照。`verified_at` 表示最近一次向主库验证的时间，并非事实发生日期。记录内部的 claim_status、source_grounding、trust_warnings 保留，不能因为进入影子库就升级为已验证事实。

源 API 的 `time` 是“几分钟前”一类展示文字，会随读取时间变化。版本摘要排除这个字段，保留 `created_at` 及全部其他事实字段；快照仍保留原响应，避免把相对时间变化误记为事实修订。

## 路径与入口

- M3 数据：`~/.local/share/los-memory-shadow/shadow.sqlite3`，私有目录与权限。
- 冻结发布：同目录 `releases/<source-hash>/`；`serve` 启动当前发布的只读 MCP。
- 定时任务：`~/Library/LaunchAgents/co.los.memory-shadow.plist`，每 300 秒处理最多 100 条。
- 凭证：M3 自己的 `~/.nowledge-mem/config.json`，部署不复制客户端密钥。
- 日志：数据目录下 `sync.out.log`、`sync.err.log`；状态工具报告最近同步及未解决错误。
- 客户端备份：M1 `~/.local/share/los-memory-shadow/client-backups/<timestamp>/`，含私有配置，禁止提交。

## 部署与验证

在本仓执行：

```sh
python3 scripts/deploy_shadow.py --host m3-t
python3 scripts/configure_shadow_clients.py
grok mcp doctor los-memory-shadow
codex mcp get los-memory-shadow
```

部署要求 M3 SSH alias、Python 3.13+、已配置的 nmem 凭证和登录用户 launchd domain。安装器打包当前工作树的 Python 源码并按内容摘要发布，包含未提交改动；它不会把代码提交或推送。重部署替换本任务自己的 launchd 配置，新 MCP 连接使用新发布，既有连接须重启。

Kimi：用户级 `~/.kimi-code/mcp.json` 中生成 Nowledge 配置并加入影子 MCP，保留已有条目。使用 `/mcp` 验证；官方 Nowledge 插件通过 `/plugins install https://github.com/nowledge-co/community/tree/main` 安装，再 `/reload`。不要同时安装旧 hooks fallback。新增 MCP 需要新会话。Codex/Grok 保留原有 Nowledge 插件与主库入口。

首次建立基线可在部署报告指明的 M3 release 目录执行 `~/.local/bin/python3 -m memory_tool.shadow sync --batch 2000`。常规刷新持有进程锁，不与手工同步同时写入；失败退出非零。`status` 的 `unhydrated=0` 只代表清单 ID 已有副本，仍须检查 `unresolved_errors`、最旧验证时间及主库变化。

**升级后必须回填派生结构**（新增索引或契约字段时；可从 `records` 重建，不需要重新访问主库）：

```sh
cd ~/.local/share/los-memory-shadow/releases/<release>
~/.local/bin/python3 -m memory_tool.shadow reindex      # 重建索引 + 契约字段
~/.local/bin/python3 -m memory_tool.shadow summary      # (space, source_id, digest, active) 身份摘要
```

`reindex` 复用 `sync` 的 flock 单写者锁，与同步并发会交错写索引行。2,048 条实测约 6.6 秒。

**`status` 现在回答四件事**：`search_index`（索引是否真的建了）、`contract`（`project`/`claim_status` 覆盖率）、`metering`（滚动 24 小时的真实请求数、响应字节、错误数、预计日流量）、`error_ledger`（有界失败台账，最近 5 条）。`attempts` 表每轮覆盖，只能回答"最近一次是否失败"，所以另建了 `sync_errors` 台账。

**同步周期实验**：`sync --manifest-cache-seconds 3600` 会在窗口内复用上一次活动 ID 清单，只做按 ID 刷新。清单返回完整正文（2,048 条实测 21 请求 / 5.18 MiB），是流量的大头；按 ID 刷新才是新鲜度的来源。默认 `0`（每轮都拉清单）是现网行为，**改周期前先看 `status.metering` 攒够 24 小时基线**。

清单缺失不等于删除。镜像继续按 ID 验证已有记录，只有明确 404 或非活动/非最新状态才从检索隐藏，历史快照保留。失败不会覆盖旧快照；失败记录进入轮转，不阻塞其他记录。404 的旧正文只能用于审计，不返回到正常 get/search。

## 回退

1. M1 执行 `codex mcp remove los-memory-shadow`、`grok mcp remove los-memory-shadow`；Kimi 仅删除或禁用同名 MCP 条目。不要整文件覆盖恢复，以免抹掉后续配置。
2. M3 执行 `launchctl bootout gui/$(id -u)/co.los.memory-shadow`，将该 plist 移出 LaunchAgents 后保留备份。停止影子 MCP 会话。
3. 保留 SQLite 与 release 供审计，Nowledge 继续正常使用。恢复时重新部署并使用同一数据库，无需往主库回写。

SQLite 应使用 backup API 生成一致快照，再在副本上检查 integrity 与 `(space, source_id, digest, active)`。不能在 WAL 写入中只复制主数据库文件。本轮完成本机副本校验；异机加密备份和整机恢复演练尚待迁移门槛验证。
