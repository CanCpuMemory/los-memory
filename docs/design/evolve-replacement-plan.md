# dsh-memory-evolve → los-memory 替换计划（P0–P5）

状态：P0 已落地（2026-11-13），M1 已完成（本地 commit 2690aae），P2 进行中，P3 已完成，P4–P5 待做。
背景与影响半径见会话审计结论（2026-11-13 三路只读盘点）：evolve 是 `~/.dsh/memories` 的**唯一写者**
+ system prompt 的**唯一记忆注入面**；los-memory 此前是**只读影子 + 只读尺子**（`service_contract/`
自述无 server/socket/db/CLI，且 `grep service_contract` 零外部 importer）。

## 0. 为什么必须分阶段（而不是"卸了再说"）

| 事实 | 证据 | 后果 |
| --- | --- | --- |
| KEY 轨 / 全局 MEMORY 轨 / USER 轨 **从未进过 Nowledge** | `mem-sync-evolve.py` 原先只 glob `projects/*/MEMORY.md` + `daily/*.md`；对 KEY 独特短语 `shadow_search` 0 命中 | 直接卸载 = 这三轨（本文档项目 KEY 455 条 / 全局 339 条）**零副本丢失** |
| 注入是 push，检索是 pull | evolve `systemPrompt.context('memory:snapshot')` 每回合 77k 字符；影子检索 1–2 ms 但需 agent 主动查 | 少了注入，模型"不知道自己记得什么" |
| evolve 手写 insert 行挂载 | `~/.dsh/profiles/web/cordis.patch.yml:61-82` | 卸载顺序错会留下 failed 行 + 插件树 failed |

## 1. 阶段划分

| 阶段 | 内容 | 落点 | 验收 | 状态 |
| --- | --- | --- | --- | --- |
| **P0 扩桥** | `mem-sync-evolve.py` 覆盖五轨（项目日志/KEY/全局/用户/daily），修 label 语义、realpath 去重、待办显式跳过 | `~/.dsh/scripts/mem-sync-evolve.py` | ①10 条 pytest 全绿（含 4 条负向/不变量）②`--dry-run` 每轨条数可复现 ③Nowledge 主库能搜到 KEY/全局轨独特短语（脚本实测） | ✅ |
| **M1 写运行时（原计划里的 P1）** | `service.sqlite3`（units/revisions/events）+ `mem add/replace/supersede/archive/list/expand/search` + `import-evolve` 导入器 | 本仓 `memory_tool/ledger/` | ①同 slot 两次 add ⇒ 旧条 deprecated 且 `same_slot_multi_active==0` ②导入幂等（跑两遍第二遍 created=0）③`mem search` 空命中返回 `[]` 而非报错 | ✅ |
| **P2 注入面** | 新 DSH host 插件，`systemPrompt.context` 渲染五轨快照：全局+用户+项目 KEY（按 cwd + git 分支过滤）、渐进披露摘要、收尾两步骤、审查到期提醒 | `dsplugins/dsh-memory-inject` | ①与 evolve 快照做 **diff**（字符数/条数/段名一一对应，差异表解释） ②分支过滤在 main 与非 main 上各验证一次 ③注入体积对比（目标 ≤ 现 77k 字符） | ⬜ |
| **P3 宿主能力** | 把 `dtodo` / `skill_manage` / `de_channel_send` 抽成薄插件（`lib/{todo,skills,notify}.js` 共 ~2,000 行，只依赖 `ctx.tools` + 文件） | `dsplugins/dsh-host-tools` | ①6 个工具名与 schema 与原版逐字一致 ②quick-actions 的 `tools.get('dtodo')` 硬依赖不再抛错 ③待办 4 轨 + 167 条现状数据可读 | ⬜ |
| **P4 影子期 A/B** | 双注入并存 1–2 周，比对注入文本与检索命中；达标判据 = 差异表逐条解释 + 无「该注入没注入」 | — | 每会话可复算：注入账本新增字段（source=inject v2）与实际段落 diff | ⬜ |
| **P5 卸载** | 冷备 → 规则改写（`~/.dsh/AGENTS.md:59-64`）→ 停 `com.echerlos.memory-lifecycle-probe` → 修 quick-actions → 删 patch insert（保留同块 dsh-los-ops）→ `dsh plugin remove` → 数据最后动 | 见审计清单 | ①`/memory-evolve/api/pending-skills` 由 200 变 404 ②插件树无 failed ③`check-scheduler-registry --host` 绿 | ⬜ |

## 2. 边界（不做的事）

- **不写 Nowledge**：P0 之后 `~/.dsh/memories` 仍由 evolve 写，桥只负责镜像；P1 的库是**独立** service 库，
  不导入 shadow 库、不改 profile 库。
- **不动 shadow 读写路径**：影子仍是「Nowledge 的只读镜像」，P1 不改变它的语义（可在 P4 后再讨论读切换 B6）。
- **待办/技能不进记忆库**：它们是宿主能力，进记忆库只会污染语义检索（P0 已显式跳过并计数）。

## 3. 已知坑（P0 实测，后续阶段会再遇到）

1. **alias 软链**：`projects/faab4b6033b2 -> 2d4994d462af` ⇒ 按 glob 枚举会把同一份 1,800+ 条推两遍。必须 `realpath` 去重。
2. **缺 `§` 的粘连条目**：实测 2 处（`projects/0e6891157526/MEMORY.md` 88↔89、`projects/2d4994d462af/MEMORY.md` 1808↔1809），
   已插入分隔符修复并留 `.bak-20261113-glued-entry`；不修会让两条记忆变成一条（切分与日期头计数不等）。
3. **KEY 轨头部形态有四种**：`[id:8hex] [YYYY-MM-DD]`、`[id:8hex] [deprecated:<id>] [YYYY-MM-DD] [slot:slug]`、`[YYYY-MM-DD HH:MM]`、`[YYYY-MM-DD]`。
   日期正则**不能锚行首**，否则 191 条带生命周期标记的条目会被判成"无日期"。
4. **label 历史包袱**：`key-<hash>` 实际是**项目日志轨**（不是 KEY 轨）。改 label = 8k+ 条按新 id 重复创建，
   故保留原名，KEY 轨另用 `keymem-<hash>`，两轨不可混读。

## 4. 回滚

- P0：`git`/无版本——脚本备份在 `.bak` 副本；数据修复有 `.bak-20261113-glued-entry`；`--dry-run` 可随时看差异，不写则零副作用。
- P1：service 库是新增文件，删除即回滚（不动既有库）。
- P2：插件未加载即无影响；加载后在 profile patch 删一行即可回退。
- P5：见审计清单的 Step 0 冷备（63M tar + 三个 profile 文件）。
