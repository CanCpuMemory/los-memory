# 通用 memory 工具调研与 los-memory 取舍

日期：2026-09-26。方法：Exa 检索并读取上游官方文档与仓库；下列为当日文档观察，不是安装、源码审计或本机性能复现。无发布时间的页面不推断发布日期。产品云服务与开源版本分别判断。

本报告支持 [总体设计](../design/memory-service-architecture.md)、[检索处理方案](../design/memory-retrieval-pipeline.md) 和 [迭代路线](../design/memory-roadmap.md)。本机现状以 [资源实测](2026-09-26-resource-assessment.md) 为准。

## 参考工具解决了什么问题

| 工具 | 官方资料已核实的机制 | 借鉴到 los-memory | 不直接照搬的部分 |
| --- | --- | --- | --- |
| Nowledge Mem | 本地已验证跨客户端 MCP、durable memory、thread、canonical by-ID；搜索投影曾出现旧正文 | 主库与原始证据分开、工具中立接入、按 ID 复核、Working Memory | 不能把搜索结果摘要当规范正文；不依赖私有内部数据库结构作为同步协议 |
| Mem0 | add/search 生命周期；用户、agent、app、run 标识；SQL 事实与向量/实体检索分层 [S1–S3] | 统一采集协议、结构化范围、写入去重与检索前置 | 不让自动抽取直接把建议变成批准；不把标识过滤当完整鉴权 |
| Graphiti / Zep | Graphiti 为开放的时间知识图框架，episode 来源、实体、事实关系有效期、增量构图、语义/关键词/图遍历混合；Zep 是不同部署形态的托管服务 [S4] | 双时间、关系证据、旧事实失效而非无痕覆盖、局部关系扩展 | 不因需要关系就立即增加独立图数据库；不把 Zep 厂商延迟承诺写成本机 SLA |
| Letta | 长期存在于上下文的 memory blocks，可跨 agent 共享或只读；块更新可整体替换，存在并发覆盖风险 [S5] | 小而稳定的 Working Memory，按读者生成、版本引用、只读共享块 | 不接管 Kimi/Codex/Grok 的 agent 执行循环；不把自由编辑大文本块当规范事实库 |
| LangMem | 显式工具写入与后台抽取分离；namespace 隔离；延迟处理可 debounce；InMemoryStore 不提供持久化 [S6–S7] | 后台归纳、安静窗口合并任务、模型适配器、命名空间 | 不为记忆功能强制引入 LangGraph；不将后台抽取视为天然可靠或等价事实 |
| Microsoft GraphRAG | local、global、DRIFT、basic 查询；global 对 community reports 做 map-reduce，官方明确其资源开销 [S8] | 项目阶段回顾、设计历史专题汇总可做离线任务 | 不在每次 agent 提问上执行全图总结，不把研究问答管线当基础记忆存取 API |

## 必须保留的版本区别

本轮 Mem0 当前官方文档将 Platform 描述为内置实体共现图，并把图信号融合到检索分数；图的连接不等于带类型的事实关系。其当前核心概念页同时描述 OSS 依赖配置的向量存储及实体重叠信号，没有与 Platform 等价的图功能。[S1–S2]

搜索也返回了 `v1.0.10` 的历史开源 graph-memory 文档，其中有外部图数据库和独立 relations 返回值。不能把历史 tagged 文档、当前 OSS 和当前 Platform 拼成一套可部署能力。本设计只借鉴机制；如后续采用库，须对锁定版本源码、许可证、迁移行为重新验证。

Mem0 当前 entity-scoped 文档还说明默认抽取会按 speaker 分配 user_id 或 agent_id，并不保证两者同时存在。[S3] los-memory 因而将所有者、发言者、执行 agent、读取授权拆成独立字段，不沿用这种隐含归属行为。

## 存储和检索工具选择

| 组件 | 当前建议 | 启用或升级的依据 |
| --- | --- | --- |
| SQLite + FTS5 | 保持事实事务与全文搜索的起点；分词、trigram、短词召回需分别评测 [S9] | 单机容量、写入队列、恢复测试满足目标时继续使用 |
| sqlite-vec | 首选向量实验后端；可用 metadata/partition 约束，纯 C、支持多平台；上游明确 pre-v1 [S10–S11] | 锁定版本，先验证过滤前召回、加载 ABI、恢复和精确检索成本；不是默认已有 ANN 能力 |
| Qdrant | 达到向量规模或并发瓶颈后的候选；支持多路 prefetch、RRF、分阶段检索及磁盘/量化取舍 [S12–S13] | 同语料同硬件的精度/延迟/内存对比证明收益，且 NAS 能容纳额外常驻服务 |
| PostgreSQL + pgvector | 如果规范数据未来确实需要多写入者、并发事务或已有合适的数据库运维，再评估 [S14] | ANN 后过滤可能不足 K 条；需要迭代扫描/分区并验证范围召回。不会仅为向量新增整套数据库 |
| SQLite 关系表 | 首期实体、别名、边、来源引用与一至二跳有界遍历 | 关系规模和路径延迟未超预算时无需外置图数据库 |
| Graphiti + 独立图后端 | 作为后续关系抽取/时间图实验，独立于规范事实存储 [S4] | 多跳题获得明确增益，且版本回撤、边删除、权限和资源验证通过 |

sqlite-vec 文档说明 metadata 支持的过滤操作有边界，partition 过细会形成低效分片。不能把 owner/device/provider/model/project 全部做物理分区。[S11]

FTS5 trigram 是三个字符一组，少于三个 Unicode 字符的 MATCH 无命中；LIKE 在缺乏可索引片段时可能退化扫描。[S9] 因此“记忆”这样的双字查询需要单独的中文分词/双字索引或受限子串路径，不能声称开启 trigram 就解决全部中文检索。

Qdrant 当前文档包含随版本变化的存储参数，实施时按实际安装版本验证；本文不复制配置参数作为运行配置。[S13] pgvector 的 ANN 召回后过滤问题也提醒我们：权限约束必须贯穿候选生成与返回，不能拿到全局 top-K 再随便过滤后声称召回完整。[S14]

## 结论

选择“可追溯事实库 + 可重建检索投影 + 有预算的上下文组装”。向量解决改写和语义相关，图解决实体关系与时间，全文解决中文、标识符及精确证据。三者均不能代替规范版本、范围鉴权和来源引用。

先构建自己的小型领域协议与评测，再按瓶颈引入成熟库。不会完整复制某个 agent 框架，也不同时部署向量数据库、图数据库、消息队列和大型模型。

## 来源索引

- S1：[Mem0 — How it works](https://docs.mem0.ai/core-concepts/how-it-works)
- S2：[Mem0 Platform — Graph Memory](https://docs.mem0.ai/platform/features/graph-memory)
- S3：[Mem0 — Entity-Scoped Memory](https://docs.mem0.ai/platform/features/entity-scoped-memory)
- S4：[Graphiti 官方仓库](https://github.com/getzep/graphiti)
- S5：[Letta — Memory blocks](https://docs.letta.com/guides/agents/memory-blocks/)
- S6：[LangMem — Background quickstart](https://langchain-ai.github.io/langmem/background_quickstart/)
- S7：[LangMem — Dynamic namespaces](https://langchain-ai.github.io/langmem/guides/dynamically_configure_namespaces/)
- S8：[Microsoft GraphRAG — Query overview](https://microsoft.github.io/graphrag/query/overview/)
- S9：[SQLite FTS5，包括 Trigram Tokenizer](https://www.sqlite.org/fts5.html)
- S10：[sqlite-vec 官方仓库](https://github.com/asg017/sqlite-vec)
- S11：[sqlite-vec vec0 metadata / partition 文档](https://github.com/asg017/sqlite-vec/blob/main/site/features/vec0.md)
- S12：[Qdrant — Hybrid queries](https://qdrant.tech/documentation/search/hybrid-queries/)
- S13：[Qdrant — Optimize performance](https://qdrant.tech/documentation/ops-optimization/optimize/)
- S14：[pgvector 官方仓库：Filtering / Iterative index scans](https://github.com/pgvector/pgvector)

历史对照，非当前能力保证：[Mem0 v1.0.10 graph-memory](https://github.com/mem0ai/mem0/blob/v1.0.10/docs/open-source/features/graph-memory.mdx)。Nowledge 外部产品入口：[Start here](https://mem.nowledge.co/zh/docs/start-here)；本轮 Nowledge 判断主要来自已经完成的本地 API 和资源实测，而不是重新采集全部官网页面。
