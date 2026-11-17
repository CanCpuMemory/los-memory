# dsh-memory-evolve → los-memory 替换计划（P0–P5）

状态（2026-11-16 对齐）：P0 ✅ / M1 ✅ / **P2 ✅ 已上线并在观测窗内**（2026-11-14 起 `mode=inject`，§7.0 有实测数）/ **P3 🟡 已实现但未接线**（`dsh-host-tools` 安装后 `disabled: true`）/ **P4 改为「观测窗 + 三面判据」✅ 进行中**（2026-11-14→11-28，双注入不可能：宿主同名 context 会抛）/ **P5 ⬜ 未做**。
路线决定（2026-11-16，用户拍板）：**当前先双写 → 补完 los-memory 缺口 → 再切**；执行口径全部收在 **§7**。
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
| **P2 注入面** | 新 DSH host 插件，`systemPrompt.context` 渲染五轨快照：全局+用户+项目 KEY（按 cwd + git 分支过滤）、渐进披露摘要、收尾两步骤、审查到期提醒 | `dsplugins/dsh-memory-inject` | ①与 evolve 快照做 **diff**（字符数/条数/段名一一对应，差异表解释） ②分支过滤在 main 与非 main 上各验证一次 ③注入体积对比（目标 ≤ 现 77k 字符） | ✅ 2026-11-14 上线（`mode=inject`；`diff-ledgers` 13/18 共享会话在容差内、5 条已定性）。见 §7.0 |
| **P3 宿主能力** | 把 `dtodo` / `skill_manage` / `de_channel_send` 抽成薄插件（`lib/{todo,skills,notify}.js` 共 ~2,000 行，只依赖 `ctx.tools` + 文件） | `dsplugins/dsh-host-tools` | ①6 个工具名与 schema 与原版逐字一致 ②quick-actions 的 `tools.get('dtodo')` 硬依赖不再抛错 ③待办 4 轨 + 167 条现状数据可读 | 🟡 三工具已实现并有 parity 夹具，但插件 `disabled: true`（原子批才开）。见 §7.5/§7.6 |
| **P4 影子期 A/B** | 双注入并存 1–2 周，比对注入文本与检索命中；达标判据 = 差异表逐条解释 + 无「该注入没注入」 | — | 每会话可复算：注入账本新增字段（source=inject v2）与实际段落 diff | ✅ 改为观测窗 2026-11-14→11-28（`observe.mjs` 9 判据 + 每日 09:00 体检 `schedule-920d0e15`）。见 §7.4 |
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

## 7. 2026-11-16 路线决定：先双写 → 补缺口 → 切 los-memory

### 7.0 现状基线（2026-11-16 实测，两路只读审计）

- **注入面已换且判据绿**：`dsh-memory-inject` 自 2026-11-14T02:15Z `mode=inject`；渲染台账 **619 条 inject / 46 会话 / 全 ok**；
  最近一次 **82.2k 字符**（key 46,392×465 / memory 34,798×342 / user 571×5），对 `maxChars=100000` 是 82%，`truncated=0`。
- **账本对齐**：units **6,980**（daily 2,927 / project 2,888 / key 816 / memory 344 / user 5），
  `origin=import:evolve` 6,974 + `origin=cli` 6；小时导入幂等（连续多轮 `created=0`）。
- **写侧仍是 evolve**：`memory` / `memory_suggest` / `memory_review_status` 只有它提供；`dsh-memory-tools` `disabled: true`。
- **旧的「镜像 project 4,639 vs 源 2,807（165%）」已不成立**：实测 project 2,888 vs 源 2,890，仓库内无定因记录 ⇒ 判为过期/误测。

### 7.1 决定与理由

用户 2026-11-16 拍板：**当前先双写**（evolve 继续写 markdown，los-memory 账本保持镜像同步）；**补完 los-memory 侧缺口后再切**；
切换前必须先有一份可机械判定的可观测方案。

理由不是偏好：停写 evolve 会**同时冻结三条下游**——①本仓每小时 `mem import-evolve`（`DEFAULT_CORPUS=~/.dsh/memories`）
②`~/.dsh/scripts/mem-sync-evolve.py` 的 Nowledge 沉淀 ③`dsplugins/dsh-os-seams` 的 `/os-seams/memory`、`/os-seams/memory-search`
与 `dsh-memory-console` 读数。在"账本还不是真源"时切，等于把三条链一起停。

### 7.2 双写口径（本阶段唯一合同）

- **方向**：evolve（写者/真源）→ 账本（镜像/读侧真源）。**本阶段不反向写 markdown**。
- **滞后预算**：单次 ≤300 s、P95 ≤300 s（导入间隔 300 s + 单轮耗时）；超 900 s 判红，不叫"稍微慢"。
- **禁止直写**：agent 一律用 `memory` 工具（evolve）写；`mem add/replace/supersede/archive/resolve` 仅限维护/演示，
  且必须同轮把内容补进 markdown。2026-11-16 实测已有 5 条 `origin=cli` 只在账本
  （`home-inventory-baseline` / `home-cleanup-candidates`），正是本规则要消灭的形态。
- **身份**：账本 `project` 列恒为 evolve 目录 hash（`sha1(字面 cwd)[:12]`）；slug 只用于报表，不做地址。

### 7.3 双写机制

| 环节 | 现状（2026-11-16） | 本阶段改成 |
| --- | --- | --- |
| 导入 | launchd `com.echerlos.los-memory-import`，`StartInterval=3600`，幂等（created 恒 0） | ✅ `StartInterval` → **300**（2026-11-16 已执行；plist 与 `.scheduler/registry.json` 的 triggers 均已对齐） |
| 写后即时 | 无 | 不建文件监听：day/project 文件是就地追加，**目录 mtime 不变 ⇒ `WatchPaths` 抓不到**；靠 300 s 轮询达成滞后预算 |
| 直写防护 | 无 | 判据 `reverse_divergence`（§7.4）+ §7.2 规则 |
| 变更可见性 | 只有 `los-memory-import.out.log` | ✅ 每轮追加一行到 `~/.dsh/memory-inject/dw-YYYY-MM.jsonl`（2026-11-16 起） |

### 7.4 可观测方案（9 项判据 / 三态 / 台账 / 告警）

判据脚本：`scripts/dual_write_observe.py`（本仓，只读，`--json` / `--self-test` / 三态退出）。
承载：launchd `com.echerlos.los-memory-dw-observe`（`StartInterval=900`，2026-11-16 已安装并登记 registry+curation）+ 壳 `scripts/dw-observe-run.sh`
（落账 + 失败按小时幂等推飞书，沿用 `dsh-memory-inject/scripts/observe-run.sh` 的键模式）。

| # | 判据 | 判什么 | 失败含义 |
| --- | --- | --- | --- |
| 1 | `corpus_reachable` | `~/.dsh/memories` 可读、五轨文件数与字节数可算 | **判不了(2)**，不当 0 |
| 2 | `parity_tracks` | memory/user 差 ≤1；key/project/daily 按文件逐项比，差 ≤0.2% | 导入漏条或有人直写 |
| 3 | `import_lag` | 源文件最新 mtime vs 账本 `updated_at` 的最大差 ≤300 s（单次 >900 s 红） | 导入停摆/滞后 |
| 4 | `reverse_divergence` | `origin='cli'` 且 markdown 无对应的 active units 必须 0（`origin_key` 前缀 `dw-exempt:` 可豁免） | 有人绕过 markdown 直写 |
| 5 | `expand_path` | 取 1 条 memory unit 跑 `mem expand --id` 必须返回正文；并断言注入文案给出的取全文方式可用 | 全文不可达（2026-11-16 已修，见 §7.5-1；现绿） |
| 6 | `alias_cwd` | `mem snapshot --cwd <旧路径>` 的 key 条数 == 新路径 | 别名归一化未生效（2026-11-16 已修，见 §7.5-2；现绿） |
| 7 | `volume` | 复用 `dsh-memory-inject/scripts/observe.mjs`：total ≤ `maxChars`、`truncated=0` | 注入被截断 |
| 8 | `importer_alive` | 导入作业自己的心跳（`~/.dsh/logs/los-memory-import.out.log` 的 mtime）≤ 3 h（36× 周期，吸收睡眠不补跑） | 导入作业停摆。**与 `import_lag` 互补**：lag 在"安静语料"时刻意 pass，看不见停摆 ⇒ 只有这条能分开"没写"与"没导" |
| 9 | `render_health` | 最近 24 h 内 `mode=inject` 的渲染行 **`ok` 必须全为 true**；窗口内零渲染 ⇒ na 具名 | 有整段记忆没注入的回合（`volume` 只读最新行，看不见历史上的失败）。2026-11-17 首跑即抓到 1 次（`01:01:30Z timeout`） |

口径纪律：**2 不折 1**（判不了既不是失败也不是通过）；na 不计红但要具名原因；判活看台账最新行与 `checks[]` 齐全，
**不看 out.log**（沿用 `com.echerlos.memory-inject-observe` 的既有教训）。

### 7.5 切换前必须补的 los-memory 缺口

| # | 缺口 | 证据（2026-11-16） | 完成判据 |
| --- | --- | --- | --- |
| 1 | 注入文案的 `expand` 手柄解析不了 | 文案让 `memory action=expand+id`，id 是 `unit_<12hex>`；在册 `memory` 是 evolve，按 `[id:8hex]` 在 markdown 里 `find`（evolve `lib/index.js:1693`）⇒ 必 `not_found`；能吃 `unit_…` 的 `dsh-memory-tools` 是 disabled | ✅ 2026-11-16：文案改指 `mem expand --id`（`dsh-memory-inject/lib/text.js` + render 测试同步），`expand_path` 已绿。**注意：文案改动在宿主进程里生效需要一次插件重载**（磁盘判据先绿，模型看到的仍是旧文案直到重载） |
| 2 | cwd 别名未归一 | evolve `cwdCanonicalization: true`（`lib/sync/index.js:113/127`）；本仓 `memory_tool/ledger/snapshot.py:110` 直接 `project_hash(cwd)` ⇒ 旧路径会话 key 轨静默 0 | ✅ 2026-11-16：新增 `memory_tool/ledger/path_alias.py`（与 evolve 同表同规则：exact→最长前缀→`/` 边界→单跳→丢自映射；坏表 fail-open 并具名降级），`snapshot` 在算 project hash 前归一化并回传 `cwd/cwd_raw/cwd_aliased_from`；`alias_cwd` 已绿（旧路径 cantool → 32 条 key） |
| 3 | 归档文件未导入 | **✅ 2026-11-16 已修**：`evolve_md` 认 `MEMORY/USER/KEY-archive.md`（根 + `projects/<h>/`）并以 `state=archived` 导入；`TODOS-archive.md` 与 `TODOS.md` 同样计入跳过（host 插件 schema）。实测 dry-run：198 文件 / 6,993 条目 / archived_imported 10，账本 `key · archived = 10`，注入面 key 条数不变（cantool 32） | 归档条目数 == 源归档条目数（已入 `parity_tracks` 覆盖面：parity 用 `origin=import:evolve` 全状态计数，归档行天然在分母里） |
| 4 | 语义检索未接线 | `search_hybrid` 零非测试调用者、env 未设、无模型/索引；后端还是 shadow 库不是账本 | **不阻塞切换**，切后单独立项（另一条轴：替换 Nowledge） |
| 5 | host-tools 已知功能差 | 无 `web` 站内通知渠道、无 `de_notify` / `de_session_images`（`dsh-host-tools/README.md` §差异 #5/#6） | **决策（2026-11-16，接受，不补）**：①`notifyEnabled=false` 且 web 铃铛无消费者 ⇒ 缺 web 渠道与生产一致；②`de_session_images` 当前**本身也未注册**（`sessionImageQueryEnabled=false`），`de_channel_send` 的 `attachmentId` 分支不可达，描述与开关态本就有漂移。**复活条件**（任一成立再立项）：恢复网页右上角铃铛 / 启用 `sessionImageQueryEnabled` / 出现按 attachmentId 取图的需求。补的成本不在工具本身：`notify-web.js` 依赖 `coi/broadcast.js`（65 KB），属另一件事 |
| 6 | 三个新插件无 remote | `dsh-memory-inject` / `dsh-memory-tools` / `dsh-host-tools` 均无 origin | 建仓并推（**需人操作**：新建 GitHub 仓） |

### 7.6 切换门（9 条全绿才动 P5）

1. `dw-observe` **连续 7 天 `rc=0`**（na 率 ≤10%，每次 na 有具名原因）
   - **口径（2026-11-17 补）**：按台账 `dw-YYYY-MM.jsonl` 逐行判，不按日历天补数；**漏拍必须具名**
     （11-17 实测 10:14→11:36 有 82 min 空档 = 睡眠期 interval 作业不补跑）；空档 >3 h（curation 的
     freshness 阈值）才算该天缺失、不计入连续数。**把"没跑"读成"通过"是这条门最容易自欺的地方。**
2. `import_lag` P95 ≤300 s 且最大 ≤900 s
3. `reverse_divergence` 连续 7 天 = 0
4. `expand_path` 绿，且宿主 `memory` 工具能吃 `unit_…`（= `dsh-memory-tools` 上线那一批）
5. `alias_cwd` 绿
6. 归档 parity 绿
7. `importer_alive` 连续 7 天不红 —— 它回答"**没导**"，`import_lag` 回答"**没写**是否超预算"，两条不可互相顶替
8. `render_health` 连续 7 天不红（窗口内 `ok:false` = 0；零渲染 ⇒ na，不计红但必须有具名原因）
9. §6.2 六处依赖改动 + 一次原子批已验证：`memory`/`dtodo`/`skill_manage`/`de_channel_send` 各只注册一次、
   插件树无 failed、**本作业条目**在 `check-scheduler-registry --host` 里绿（**不要求全表绿**：2026-11-17
   实测全表有 3 条无关漂移（session-index / ai-node-reputation / surge-mesh-observe），拿全表当门等于把切换
   挂在别人家的欠账上）

### 7.7 本阶段不做的事

- 不卸 evolve、不改 `mode: inject`、不动 `dsh-memory-tools` / `dsh-host-tools` 的 `disabled`。
- 不反向写 `~/.dsh/memories`（单向镜像）；不迁 todo 轨（归宿主 `dtodo`）。
- 不追语义检索质量门禁（那是替换 Nowledge 的 B2/B6，不是本计划）。

### 7.8 执行记录（2026-11-16）

| 动作 | 落点 | 证据 |
| --- | --- | --- |
| 双写节奏 3600→300 s | `~/Library/LaunchAgents/com.echerlos.los-memory-import.plist`（模板 `scripts/deploy/`）+ `.scheduler/registry.json` | `triggers[0].seconds=300`；`launchctl print` runs 正常；`import_lag` 判据：pending=0 时 pass，pending>0 且 >300 s 时 fail |
| 双写判据上线 | 本仓 `scripts/dual_write_observe.py`（7 项 + 20 条自检含 6 条负向控制）、`scripts/dw-observe-run.sh`（落账 + 按小时幂等推飞书） | `--self-test` 20/20；实跑 rc=0，7 项全 pass |
| 定时承载 | `com.echerlos.los-memory-dw-observe`（900 s） | registry 82 作业、curation 已登记、`check-scheduler-registry --host` = ok（真漂移 0 / 判不了 0）；台账首行 rc=0 |
| expand 手柄修复 | `dsplugins/dsh-memory-inject/lib/text.js`（5 个标题 + 读取说明） | inject 测试 92/92；`expand_path` 绿（CLI 取正文 1,757 字符）；**宿主重载后模型才看到新文案** |
| cwd 别名归一化 | `memory_tool/ledger/path_alias.py`、`snapshot.py`（新增 `cwd_raw`/`cwd_aliased_from`）、`cli.py`（`--alias-map` / `--no-alias-map`） | `pytest tests/unit/test_ledger_*.py` 130 passed；`alias_cwd` 绿（`~/Downloads/projects/cantool` → 32 条 key，与 live 路径相等） |
| 直写白名单 | `~/.dsh/memory-inject/dw-allowlist.json` | 4 条 2026-11-16 直写（memory×2 / daily×2）各带 reason；`reverse_divergence` 由红转绿，新增未登记 id 立即红 |
| 归档导入 | `evolve_md.discover`（`*-archive.md` → `archived`）+ `import_evolve`（add 后 archive，一条码路）+ `TODOS-archive` 计入跳过 | dry-run：198 文件 / 6,993 条目 / archived_imported 10；账本 `key·archived=10`；重跑 `created=0`；注入面不变（cantool key 仍 32）；测试 `test_archive_files_import_as_archived_and_never_go_live`（含「归档不得进 payload」「重跑 created=0」） |
| host-tools 功能差决策 | 本文 §7.5-5 | 接受（无 web 铃铛/无 `de_session_images`），复活条件与补齐成本已写明 |
| 判据 7 → 9 项（2026-11-17） | `scripts/dual_write_observe.py`（新增 `importer_alive` / `render_health` + 7 条自检）、`~/.dsh/profiles/web/cordis.patch.yml`（`timeoutMs: 6000`） | `--self-test` **27/27**（新增 4 条 importer、3 条 render，含 2 条负向控制）；实跑首行即 `fail render_health: 1 of 89 inject render(s) failed (2026-11-17T01:01:30 timeout)` —— 判据抓到的是真事，不是误报 |

遗留：`host-tools` 的 **UI 面**处置（4 tab + Advisor 面板，与 §7.5-5 的功能差不是一件事）、三个插件建 remote（**需人操作**）、`memory` 工具级 expand（原子批）、宿主重载让新注入文案生效。

### 7.9 决策记录（2026-11-17，切换前必须裁的两项 + 一处判据补全）

2026-11-16 的盘点把「`memory_suggest` / `memory_review_status` 无替身」和「`reviewReminder` 只有占位」列为
未拍板开口。以下三条是决定，依据都写在同一行里；D1/D2 只改文档与注入面契约，**不动在跑的插件**。

| # | 事项 | 决定 | 依据 | 落地动作（谁、何时） |
| --- | --- | --- | --- | --- |
| D1 | `memory_suggest`（建议 → 待确认队列） | **随 evolve 消失，不做替身** | 准入模型 2026-11-14 已取消"待确认写入"；`~/.dsh/memories/SUGGESTIONS.jsonl` 实测 **0 字节**（enforce 之后没有新条目进过队列）⇒ 队列已死，替身会重建一个没人读的入口。唯一还活着的消费者是 dtodo 描述里那句"模型自建待办请用 memory_suggest" | 原子批同批改 `dsh-host-tools/lib/todo.js:627` 的描述：「模型不要自建待办；只有用户明确要做某事才 add」 |
| D2 | `memory_review_status`（到 N 回合提醒审查） | **不做注入面自计数替身；改台账驱动** | 计数器的语义是"渲染数"，不是"回合数"（子代理与多会话都会放大），自计数会造出"没到期报到期"的假提醒——比没提醒更贵。台账驱动是三态可判的：有台账就渲染"上次审查 X / 下次 Y"，没有就不渲染 | 二期：定时作业（卫生尺子 `scripts/hygiene_scan.py` 一路）写 `~/.dsh/memory-inject/review-state.json`（`lastReviewedAt/dueAt/findings`），`render.js` 只读它。**当前 `reviewReminder` 保持 false**（先有作业再有渲染） |
| D3 | 注入超时 | `timeoutMs` **3000 → 6000 ms**（`~/.dsh/profiles/web/cordis.patch.yml`） | 11-17T01:01:30Z 实测整段丢注（`ok:false error=timeout`，chars 581）；典型 445 ms ⇒ 3 s 只有 6.7× 余量。**上限不能再抬**：snapshot 走 `spawnSync`，会阻塞宿主事件循环；复发正解是异步/预热，不是加数值 | ✅ 2026-11-17 已改（配置热重放生效，下次渲染即用新预算） |
| D4 | 判据面 | 新增 `importer_alive` + `render_health`，切换门 7 → 9 条 | 前者分开"没写"与"没导"（`import_lag` 在安静语料时刻意 pass）；后者补上 `volume` 只读最新行的盲区（一次失败 = 一轮无记忆） | ✅ 2026-11-17 已实现（`--self-test` 27/27）；首跑即抓到 11-17 那次 timeout，**故第 9 条判据自 2026-11-18T01:01Z 起才可能转绿** |

**对 §7.6 第 1 条的影响**：`render_health` 是窗口判据（默认 24 h），一次失败会让门红到该行滑出窗口。
这不是噪声，是"切换后没有回退面"的代价；要缩短就得先证明那是瞬态（异步/预热），不是把阈值放宽。
