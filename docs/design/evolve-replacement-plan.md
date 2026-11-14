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

## 5. 命名与三条待决项（2026-11-13 定稿，Lead 自决）

**命名（定稿，不再讨论）**

| 层 | 名字 | 说明 |
| --- | --- | --- |
| 写运行时 | `los-memory mem ...`（模块 `memory_tool/ledger/`，交付名 **M1**） | 唯一写者 + 检索真源 |
| 注入面 | 插件 `dsh-memory-inject`，段名 `memory:snapshot`，台账 `~/.dsh/memory-inject/renders-YYYY-MM.jsonl` | 段名沿用 evolve 以便机械对照 |
| 宿主能力 | 插件 `dsh-host-tools`，工具名 `dtodo` / `skill_manage` / `de_channel_send` **一字不改** | 名字是契约，改名会连带改 AGENTS.md 与 quick-actions |

**三条待决项（已裁）**

1. **同名工具切换** → 不做"同时启用"，走**原子批**：先装 host-tools 但三开关全 `false`（不注册）→ 同一批 patch 里关 evolve 三条 + 开 host-tools 三条 → **一次** reload → 用宿主 tools 清单验证「每个工具只注册一次」。
   为什么不靠"重名注册时谁覆盖谁"：那是宿主未文档化行为，验它要扰动在跑的会话；原子批根本不需要这个知识。
2. **`skill_manage` 采纳面** → host-tools 默认 `skillReviewEnabled: true`（直写 `~/.agents/skills`），保留 `false` 可配；切换前清空 `pending-skills/`（当前为空）。理由：卸载后没有采纳 UI，队列只会堆积。
3. **cwd 别名规范化** → **搬**（evolve `lib/path-alias.js` 的最小必要部分 + 只读 `~/.dsh/storages/path-alias-map.json`）。理由：写进旧 hash 的"死"项目目录是静默错误，代价高于 150 行；别名表缺失/坏 JSON ⇒ fail-open 退化为不归一化 + warn。

**事实补充**：Nowledge 镜像是一次性安全副本（P0 已补 KEY/全局/用户三轨，共 1,111 条）；此后以 M1 库为权威，P5 停 `mem-sync-evolve` 后不再新增镜像。`nmem m add` 每条约 5–8 s（后端带蒸馏），**不要**为低价值轨（project 日志/daily 增量）再跑批量补镜像。

## 6. P5 前置：冷备与依赖清点（2026-11-14 执行）

### 6.1 冷备（两份，各自带对账基线 —— 恢复后必须能证明"一致"）

| 产物 | 路径 | 大小 | sha256（前 20） | 对账基线 |
| --- | --- | --- | --- | --- |
| 记忆语料 | `~/.dsh/backups/memories-20261114T105225.tar.gz` | 5.1 MB（源 64 MB，`events/` 54 MB 压缩比高） | `d431baea753c860a111e` | 315 条目；MEMORY 340 / USER 4 / KEY(唯一项目 39 个，去 alias 后) 785 / daily 72 文件 / decisions 749 行 |
| 可写账本 | `~/.dsh/backups/service-ledger-20261114T105229.sqlite3` | 130 MB | `c9c3140ca4beda89020d` | units 6,781 / revisions 6,781 / events 6,819 |

⚠️ 账本备份是 WAL 库快照：**只读打开必须 `file:…?immutable=1`**，用 `?mode=ro` 会因缺 `-shm`
报 `unable to open database file (14)`（本仓记录过的坑，今天又踩了一次）。

### 6.2 依赖清点（P5 要同批改的六处）

| 面 | 位置 | 依赖形态 | P5 动作 |
| --- | --- | --- | --- |
| launchd | `com.echerlos.memory-lifecycle-probe` | `ProgramArguments` 指向 `dsplugins/dsh-memory-evolve/scripts/lifecycle-probe.mjs`；脚本只 import node builtin，但**读 `~/.dsh/memories`** | 停用，或改读账本（`slots`/`supersede` 计数账本已有） |
| profile 依赖 | `~/.dsh/profiles/web/package.json:23` | `"dsh-memory-evolve": "link:…"` | 移除依赖 |
| profile 配置 | `~/.dsh/profiles/web/cordis.patch.yml:61` 起 | id-targeted config（`injectMemory` / `decisionPolicy` / `decisionAutoApply`） | 整行移除（与 memory-tools 原子批） |
| 插件间 | `dsplugins/dsh-quick-actions/lib/actions.mjs:72` | `tools.get('dtodo')` 硬依赖 | 切到 `dsh-host-tools` 的 `dtodo`（工具名不变，契约逐字对齐） |
| 规则 | `~/.dsh/AGENTS.md` §Memory Writes（61–64 行） | 以 `memory(...)` 工具与 markdown 槽位语义书写 | 改写成账本语义（slot / 权威 / 冲突裁决） |
| 校验器 | `dsplugins/dsh-memory-tools/{scripts,tests}` | parity-check 从 evolve **源码**重推 `memory` schema | 切换点后只保留冻结夹具，去掉对 evolve 源码路径的依赖 |

未发现其他运行时硬依赖：`dsh-feishu-outbound` 只在注释里提到 `de_channel_send`（工具名本身来自 evolve，
卸载时必须由 `dsh-host-tools` 补齐，这是 P3 的事）。

### 6.3 重载前必须先修的两个环境缺陷（2026-11-14 实测，否则"重载"这件事本身不可行）

| 缺陷 | 症状 | 修法 |
| --- | --- | --- |
| `dsh-web-activity.sh` RPC 回退分支 py3.9 语法错 | `activity.json` 缺失时（宿主刚重启后）判据永远「判不了 ⇒ 按忙」，**空闲闸门永不放行** | f-string 里禁止反斜杠 ⇒ 改 `%` 格式化（dsfolder `ff02764`，快照已复拍） |
| `check-orphan-children.mjs` 把检查器祖先当孤儿 | detached 重启是 `ppid=1`，boot-check 跑在它下面 ⇒ **重启用自己的父进程把自己拦住**（`✗ orphans 发现孤儿进程：31012`） | 排除自身祖先链（`ps` 沿 ppid 最多 32 跳，命中即 legit）；self-test 12→15 条（含"非祖先仍判 orphan"负控）（dsfolder `5982491`） |

### 6.4 观测窗内不要做的事

- 不动写侧（`dsh-memory-tools` 仍 disabled）、不卸载 evolve 的 memory 工具 —— 那会拆掉 A/B 基线。
- 唯一生效的过渡改动是 `decisionAutoApply.keyMaxChars: 600 → 2000`（注入已按 80 字摘要渲染，
  长 key 不再贵），它让正常写入直接 `auto_applied`，与新准入模型一致。

### 6.5 P5 会丢的两项"注入面附属能力"，以及替代方案

| 能力 | evolve 今天怎么给 | P5 后由谁给 | 结论 |
| --- | --- | --- | --- |
| **待确认记忆建议提示**（`pendingSuggestions`，快照里一行"有 N 条待确认"） | 读 `~/.dsh/memories/decisions.jsonl` 的 pending 集合 | **不需要**：准入模型（`docs/design/admission-and-conflict-policy.md`）不再有"待确认写入"这个词 —— 写入要么落盘、要么被压过/被拦（进事件台账）、要么成为**待裁决冲突** | **能力消失即设计目标达成**，不留替代 |
| **待裁决冲突提示** | 无（evolve 没有冲突概念） | `mem snapshot` 的 `conflicts{open,ids}` → 注入面纪律段一行可操作提示（已实现，2026-11-14） | 新增，已有端到端证据 |
| **审查到期提醒**（`reviewInterval: 10`，每 N 回合提示做记忆审查） | evolve 自带计数器 | **暂无**。`dsh-memory-inject` 的 `reviewReminder` 只是开关占位，渲染器无状态、不计数 | **待定**：①接受丢失（审查改为人工/定时作业触发）；②在注入插件里实现"渲染计数 + 到 N 提醒"（需要状态文件，属新增写面）。倾向 ②，但要等观测窗结束、确认注入面不再变动后再做 —— 现在实现等于在会变的面上加状态 |

**判据提醒**：上表的第 3 行是 P5 前必须拍板的唯一开口。若走 ②，它的验收要能回答"计的是什么"（渲染次数 ≠ 回合数，
子代理与多会话会放大），且**不得**把"没到期"渲染成"到期"（假提醒比没提醒更贵）。
