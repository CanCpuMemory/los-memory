"""Admission policy — 「写入自由，冲突才判」的纯判定层（零 IO）。

## 问题

旧模型的每一次写入都在问人（`proposed` → 人工 approve）。实测 85→87 条建议
**全部被采纳、0 条被拒**：逐条确认长期零信息量，只贡献延迟。但"全自动落盘"
又会静默覆盖掉人明确拍板过的结论。

真正需要判断的对象不是"这条要不要记"，而是**"这条与已有的某条是不是同一个事实、
该谁说了算"**。所以本模块只回答一件事：

    given 一条待写入 + 同 scope 的现存 active 条目 ⇒ 该怎么处置

## 处置动作（六选一，全部机械化）

| action | 触发 | 后果 |
| --- | --- | --- |
| `block` | 命中敏感形态（私钥/sk-/AKIA/密码赋值…） | 拒绝写入，点名命中的模式 |
| `dedupe` | 同 scope 内已有**完全相同**正文（digest 相等） | 不新增，返回已有 id |
| `supersede` | 占同一 `slot`，且新条目按 (权威, 事件时间, 落库时间) 胜出 | 新条目 active，旧条目 deprecated + `superseded_by` 边 |
| `outranked` | 占同一 `slot`，但**现存条目胜出**（例：owner 昨天的断言 vs 今天 agent 的写入） | **不覆盖**；把新内容记进事件台账备查，返回被压过的理由 |
| `escalate` | 占同一 `slot`，双方**权威相同且事件时间相同**、正文不同 | 不进默认召回集；落 `conflict_escalated` 事件，等人裁 |
| `append` | 无结构重叠 | 直接写（这就是"写入不需要确认"） |

## 为什么只有这几种冲突能被发现

冲突检测**只用结构信号**：`slot`（"同一事实的新值"的唯一显式通道）、正文 digest
（完全相同）、以及 `mem supersede` 的显式声明。**不做文本相似度**——2026-11-01
的回测定论：文本相似度既漏（改写/合并/别名）又误（同主题不同事实），把
"看起来像"当冲突会让系统长期在噪声里问人。

推论（写进契约，别当缺陷）：**没有 slot、没有 supersede 的语义矛盾，本层不判**。
两条互相矛盾但都无结构声明的条目会并存（append），由周期性 review/整合或人处理。
这是有意的：宁可并存，也不猜。

## 权威阶梯（"agent 即可判断"的那一维）

| actor 前缀 | rank | 典型来源 |
| --- | --- | --- |
| `owner` / `human` | 30 | 用户显式拍板 |
| `cli` / `agent:main` | 20 | 主会话、人在终端里手动写 |
| `agent:subagent` / `job` | 10 | 子代理、定时作业 |
| `import` | 0 | 历史存量导入（时间=导入顺序，不代表事件顺序） |
| 其它/未知 | 10 | 保守：不替未知来源主张权威 |

比较用三元组 `(rank, asserted_at, recorded_at)`，字典序大者胜；完全相等 ⇒ `escalate`。
`asserted_at` 是**事件时间**（正文里的日期或调用方显式给），缺失时退化为落库时间。
`import` 的 rank=0 是有意的：历史存量不能因为"导入得晚"就压掉新的事实。

## 三态纪律

本模块是纯函数、无 IO、不抛业务异常：调用方（store）负责把 `block`/`outranked`/
`escalate` 落成对应的拒绝/事件。判定不出来（字段缺失、类型不对）一律当
`escalate`（判不了 ≠ 可以覆盖）。

@module memory_tool.ledger.admission
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

#: 敏感形态（**值形态**，不是词表）。刻意要求"有具体值"：正文里出现
#: 「密码填充」「password policy」这类**话题**不该被拦，只有 `密码: xxx` 这种
#: 赋值形态才算泄漏面。2026-11-02 的教训：用词表会把正常内容也咬掉。
DENY_PATTERNS = (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bsk-[A-Za-z0-9_-]{12,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}",
    r"(?:密码|口令|passwd|password)\s*[:：=]\s*\S{4,}",
)
_DENY_COMPILED = tuple(re.compile(p, re.IGNORECASE | re.MULTILINE) for p in DENY_PATTERNS)

#: 权威阶梯（见模块 docstring 的表）。
RANK_OWNER = 30
RANK_MAIN = 20
RANK_SUB = 10
RANK_IMPORT = 0

_ACTOR_PREFIXES = (
    ("owner", RANK_OWNER),
    ("human", RANK_OWNER),
    ("cli", RANK_MAIN),
    ("agent:main", RANK_MAIN),
    ("agent:subagent", RANK_SUB),
    ("job", RANK_SUB),
    ("import", RANK_IMPORT),
)

#: 每个动作的稳定字符串（CLI/日志/测试共用，别改成同义词）。
ACTIONS = ("append", "dedupe", "supersede", "outranked", "escalate", "block")


def authority_rank(actor: Any) -> int:
    """Map an actor id onto the authority ladder (unknown ⇒ sub-agent level).

    Longer prefixes win, so ``agent:subagent`` is not read as ``agent``.
    """
    value = str(actor or "").strip().lower()
    best: tuple[int, int] | None = None
    for prefix, rank in _ACTOR_PREFIXES:
        if value.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), rank)
    return RANK_SUB if best is None else best[1]


def _same_principal(left: Any, right: Any) -> bool:
    """True when two actor ids name the same principal (case/space insensitive)."""
    return str(left or "").strip().lower() == str(right or "").strip().lower()


def matched_sensitive_pattern(content: str) -> str | None:
    """Return the first deny pattern that matches, or ``None``."""
    for raw, compiled in zip(DENY_PATTERNS, _DENY_COMPILED):
        if compiled.search(content or ""):
            return raw
    return None


@dataclass(frozen=True)
class Incoming:
    """One write being admitted (already canonicalised by the caller)."""

    track: str
    scope_key: str
    digest: str
    content: str = ""
    slot: str | None = None
    actor: str = "cli"
    asserted_at: str | None = None
    recorded_at: str | None = None

    @property
    def order_key(self) -> tuple[int, str]:
        """Sort key used for "who wins": ``(authority rank, event time)``.

        **落库时间不参与比较**（只在事件时间缺失时充当它的替身）。理由：库内写入
        顺序是"谁先到"，不是"谁更新"；把它当第三维会让"同一权威、同一事件时间、
        不同正文"这种真冲突永远判成"后写的赢"，而那一刻恰恰是最需要人裁决的。
        """
        return (authority_rank(self.actor), self.asserted_at or self.recorded_at or "")


@dataclass(frozen=True)
class Existing:
    """One active unit in the same scope that the incoming write may collide with."""

    id: str
    digest: str
    slot: str | None = None
    actor: str = "import"
    asserted_at: str | None = None
    recorded_at: str | None = None

    @property
    def order_key(self) -> tuple[int, str]:
        """Sort key used for "who wins": ``(authority rank, event time)``.

        **落库时间不参与比较**（只在事件时间缺失时充当它的替身）。理由：库内写入
        顺序是"谁先到"，不是"谁更新"；把它当第三维会让"同一权威、同一事件时间、
        不同正文"这种真冲突永远判成"后写的赢"，而那一刻恰恰是最需要人裁决的。
        """
        return (authority_rank(self.actor), self.asserted_at or self.recorded_at or "")


@dataclass(frozen=True)
class Decision:
    """What the caller must do with this write."""

    action: str
    reason: str
    existing_id: str | None = None
    detail: Mapping[str, Any] | None = None

    @property
    def writes(self) -> bool:
        """True when the caller should create/activate the unit."""
        return self.action in ("append", "supersede")


def decide(
    incoming: Incoming,
    actives: Iterable[Existing],
    *,
    force: str | None = None,
) -> Decision:
    """Decide how to admit ``incoming`` against the scope's active units.

    Args:
        incoming: the write being admitted.
        actives: **same scope** active units (caller already filtered by scope).
        force: optional override for flow control — ``"append"`` (caller explicitly
            bypasses conflict handling, e.g. an import of historical data) or
            ``"escalate"`` (always ask a human). Unknown values are ignored.

    Returns:
        A :class:`Decision`; never raises for a business reason.
    """
    sensitive = matched_sensitive_pattern(incoming.content)
    if sensitive is not None:
        return Decision("block", "sensitive_content", detail={"pattern": sensitive})

    if force == "append":
        return Decision("append", "forced_append")

    candidates = list(actives)
    if not candidates:
        return Decision("append", "no_overlap")

    for existing in candidates:
        if existing.digest == incoming.digest:
            return Decision(
                "dedupe",
                "identical_content",
                existing_id=existing.id,
                detail={"digest": incoming.digest},
            )

    if force == "escalate":
        return Decision("escalate", "forced_escalate")

    if incoming.slot is None:
        # 没有 slot 就没有"同一事实"的显式声明 ⇒ 不做冲突判定（见模块 docstring）。
        return Decision("append", "no_slot_declared")

    for existing in candidates:
        if existing.slot != incoming.slot:
            continue
        ours, theirs = incoming.order_key, existing.order_key
        if ours == theirs:
            # 同一权威、同一事件时间：**先问"是不是同一个主体"**。
            # 同一个 principal 往自己的槽位再写一次不是分歧，是更新（写序即意图）；
            # 两个不同 principal 在同一时刻给出不同正文才是真冲突 —— 那才该问人。
            if _same_principal(incoming.actor, existing.actor):
                return Decision(
                    "supersede",
                    "self_update_same_principal",
                    existing_id=existing.id,
                    detail={"slot": incoming.slot, "actor": incoming.actor},
                )
            return Decision(
                "escalate",
                "tie_same_authority_and_time",
                existing_id=existing.id,
                detail={"slot": incoming.slot, "actor": incoming.actor,
                        "other_actor": existing.actor},
            )
        if ours > theirs:
            return Decision(
                "supersede",
                "newer_or_higher_authority",
                existing_id=existing.id,
                detail={
                    "slot": incoming.slot,
                    "incoming_rank": ours[0],
                    "existing_rank": theirs[0],
                },
            )
        return Decision(
            "outranked",
            "existing_wins",
            existing_id=existing.id,
            detail={
                "slot": incoming.slot,
                "incoming_rank": ours[0],
                "existing_rank": theirs[0],
            },
        )

    return Decision("append", "no_slot_collision")
