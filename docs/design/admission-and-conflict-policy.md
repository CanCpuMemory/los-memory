# 准入与冲突裁决：写入自由，只有冲突才判

状态：**已实现**（2026-11-14；`memory_tool/ledger/admission.py` + `mem add` 的
`--actor/--asserted-at/--on-conflict` + `mem conflicts` / `mem resolve`；25 条测试，
含 6 条负向控制）。写侧接线（宿主插件）仍待 P5 原子批，见 §6。

## 1. 为什么改

旧模型把"要不要记"当成需要人拍板的事：每次写入都进确认队列。实测 87 条建议
**87 采纳、0 拒绝**——逐条确认长期零信息量，只贡献延迟与一次上下文往返。

真正需要判断的不是"这条要不要记"，而是**"这条与已有的某条是不是同一个事实、
该谁说了算"**。于是把判定对象换掉：

| | 旧 | 新 |
| --- | --- | --- |
| 默认路径 | 入队等人 | **直接落盘** |
| 人做什么 | 逐条审"要不要记" | 只在**裁决不了**的冲突上选 A/B |
| 判定依据 | 无（纯人工） | 结构（slot / digest / supersede）+ 权威 + 事件时间 |

## 2. 能检测的冲突（只有结构化的那几类）

| 信号 | 含义 | 为什么只有它 |
| --- | --- | --- |
| 同 `slot` | 调用方**显式声明**"这是同一事实的新值" | 唯一的显式同义通道；`[slot:port]` 早已是 evolve 的约定 |
| 正文 digest 相同 | 逐字节重复 | 不依赖任何语义猜测 |
| `mem supersede <id>` | 调用方显式指定取代目标 | 声明优先于推断 |

**不做文本相似度。** 2026-11-01 的回测定论：相似度既漏（改写、合并、别名、跨语言）
又误（同主题不同事实），把"看起来像"当冲突等于让系统长期在噪声里问人。

推论，写进契约而**不是缺陷**：没有 slot、没有 supersede 的语义矛盾，本层**不判**。
两条互相矛盾但都无结构声明的条目会并存（`append`），由周期性 review/整合或人处理。
宁可并存，也不猜。

## 3. 裁决只看两维

```
order_key = (authority_rank(actor), asserted_at or recorded_at)
```

比较字典序，大者胜。**落库时间不参与比较**（只在事件时间缺失时充当替身）：库内写入
顺序回答的是"谁先到"，不是"谁更新"；把它当第三维会让"同权威、同事件时间、不同正文"
这种真冲突永远判成"后写的赢"——而那一刻恰恰是唯一该问人的。这条是**实测踩出来的**
（`test_write_time_does_not_break_a_real_tie`）。

| actor 前缀 | rank | 典型来源 |
| --- | --- | --- |
| `owner` / `human` | 30 | 用户显式拍板 |
| `cli` / `agent:main` | 20 | 主会话、人在终端里手写 |
| `agent:subagent` / `job` | 10 | 子代理、定时作业 |
| `import` | 0 | 历史存量导入（时间是导入顺序，不代表事件顺序） |
| 未知 | 10 | 保守：不替未知来源主张权威 |

同一 principal（actor 字符串相同）往自己占的槽位再写一次**不是分歧**，是更新
（写序即意图）⇒ 直接 `supersede`，不升级。

## 4. 六种处置

| action | 触发 | 后果 | 需要人？ |
| --- | --- | --- | --- |
| `append` | 无结构重叠 | 直接写 | 否 |
| `dedupe` | 同 scope 同 digest | 返回已有 id，不新增 | 否 |
| `supersede` | 同 slot 且新者胜（或同 principal 自更新） | 新条 active，旧条 `deprecated` + `superseded_by` 边 | 否 |
| `outranked` | 同 slot 但旧持有者胜（如 owner 昨天的断言 vs 今天 subagent 的写入） | **不覆盖**；新内容进 append-only 事件台账备查，返回理由与 `existing_id` | 否 |
| `escalate` | 同 slot + 同权威 + 同事件时间 + 不同正文，**且不同 principal** | 入 `conflict_escalated` 事件（含正文与 scope），`mem conflicts` 可见 | **是**（唯一一种） |
| `block` | 密钥形态（私钥块 / `sk-` / AKIA / `ghp_` / `xox?-` / `密码: 值`） | 拒绝，点名命中的模式 | 否 |

`block` 匹配的是**值形态**不是词表：正文里出现「密码填充」「password policy」这类
**话题**不会被拦（`test_sensitive_negative_controls_are_not_blocked`）。

`on_conflict` 可覆盖判定以控制流程：`auto`（默认）/ `escalate`（一律问）/
`reject`（任何槽位冲突都拒）/ `append`（绕过判定，历史导入用）。

## 5. 运维面

```bash
# 写（默认就够用；actor/asserted-at 只在需要权威或事件时间时给）
los-memory mem add --track key --project dsfolder --slot port --content "..." \
  --actor agent:main --asserted-at 2026-11-14

# 被拒/被压过都会 exit 1 并点名原因（outranked_by_slot / conflict_undecidable / sensitive_content_refused），
# 审计事件已落账，不丢内容。
los-memory mem conflicts                     # 未裁决的冲突（附正文摘录、双方 id、理由）
los-memory mem resolve conflict-2 --action replace --note "B 更符合当前口径"
los-memory mem resolve conflict-2 --action drop        # 保留已有条目
los-memory mem resolve conflict-2 --action keep_both   # 两条都留（新条不占槽位）
```

退出码沿用三态：`0` 写入成功/RESOLVE 成功；`1` 这条没写成（有原因，可裁决）；
`2` 判不了（库缺失/参数非法）。`--dry-run` 在 savepoint 里真跑一遍再回滚。

## 6. 落地状态与迁移

| 面 | 状态 |
| --- | --- |
| 账本（`mem add` / `conflicts` / `resolve`） | **已实现并测通**（`tests/unit/test_ledger_admission.py`，25 条） |
| evolve（当前唯一写者） | **过渡态**：`decisionAutoApply.keyMaxChars` 600→2000，让正常长度的 key 写入直接 `auto_applied`（注入已按 80 字摘要渲染，长 key 不再贵）；`denyPatterns` 不变。实测一条 ~1,100 字 key 写入 → `auto_applied`、未入队 |
| 宿主写侧插件（`dsh-memory-tools`） | 仍 disabled。`ctx.tools.register` 对重名**直接抛**（`tool "memory" is already registered`），所以它必须与 evolve 的 memory 工具**同批**切换（P5 原子批）；本模块就是它的准入层，接线时只需 `--actor`/`--on-conflict auto` 透传 |

**evolve 与账本的行为差异（过渡期必须知道）**：evolve 没有本文的冲突裁决（只有
slot 自动弃旧 + 长度/密钥闸）；账本有。两者在 P5 前并存，因此**不要**假设 evolve 会
把 `outranked` / `escalate` 判出来——它拿不到 authority 这条信息。

## 7. 未覆盖（明确留白）

1. **未声明的语义矛盾**（无 slot / 无 supersede）：不判，允许并存。要靠 review/整合
   降噪，或人在需要时手动 `mem supersede`。
2. **跨 scope 的同一事实**（如 memory 轨与 key 轨各写一份）：scope 隔离是有意的，
   本层不跨轨判冲突。
3. **时间语义质量**：`asserted_at` 由调用方给（正文里的日期或显式参数）。给错就按错的
   判——这是"把判定依据摊开可审计"的代价；`import` 轨 rank=0 使得存量数据不会因此压掉
   新的事实。
