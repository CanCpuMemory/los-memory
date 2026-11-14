"""One-shot render payload for the memory injection surface (P2).

The injection plugin runs once per turn, so it must not spawn one subprocess per
track. This module composes the whole payload in a single read: five tracks, slot
health and an explicit degradation list — nothing that requires a second call
except ``expand --id`` for a full body.

Contract notes that matter for the renderer:

* ``count`` is the **natural** number of active units in the track; ``truncated`` is
  how many of them the payload dropped. ``count - truncated == len(entries)``.
* ``mode`` is a judgement derived from the two thresholds (``full`` only when the
  track fits both), reported so the renderer can decide how to present it. It does
  **not** change the caps — those are the explicit ``--max-entries``/``--max-chars``.
* Bodies are never included: a body is fetched deliberately with ``expand --id``.
* A missing branch is **fail-open** (no branch filter) with a named degradation, the
  same policy ``evolve`` uses; a missing ledger is exit ``2`` (cannot judge).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

from .evolve_md import parse_entry
from .registry import project_hash
from .store import Ledger, today_local

__all__ = ["SnapshotOptions", "build_snapshot", "resolve_git_branch", "SNAPSHOT_SCHEMA"]

#: Bumped when the payload shape changes in a way a renderer must notice.
SNAPSHOT_SCHEMA = 1

#: Tracks that render an entry list. ``daily`` and ``project_log`` render counts only.
LISTED_TRACKS = ("memory", "user", "key")

#: One rendered entry line. The budget charges for this prefix plus the text that is
#: actually emitted, because a budget that measures something other than the payload
#: is how 340 short summaries once "exceeded" a 200k limit worth ~1MB of bodies.
RENDER_PREFIX_TEMPLATE = "- [{unit_id}] "

#: Calibrated on the live corpus 2026-11-13: `memory` 340 + `user` 4 + `key` 451
#: summaries ≈ 54k emitted characters in total, so these defaults leave all three
#: tracks untruncated with headroom (the P2 renderer passes its own caps too).
DEFAULT_MAX_ENTRIES = 500
DEFAULT_MAX_CHARS = 120_000


@dataclass
class SnapshotOptions:
    """Everything the payload depends on, so tests can pin it."""

    cwd: str = field(default_factory=os.getcwd)
    branch: str | None = None
    project: str | None = None
    max_entries: int = DEFAULT_MAX_ENTRIES
    max_chars: int = DEFAULT_MAX_CHARS
    full_threshold_entries: int = 200
    full_threshold_chars: int = 60_000
    summary_chars: int = 160
    date: str | None = None
    project_slugs: dict[str, str] = field(default_factory=dict)
    project_map_info: dict[str, Any] = field(default_factory=dict)


def resolve_git_branch(cwd: str) -> str | None:
    """Resolve the current branch of ``cwd``; ``None`` when it cannot be known.

    A detached HEAD (``HEAD``) is unresolved on purpose: there is no branch to filter
    by, and inventing one would silently hide branch-scoped facts.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    name = (result.stdout or "").strip()
    if not name or name == "HEAD":
        return None
    return name


def build_snapshot(ledger: Ledger, options: SnapshotOptions) -> dict[str, Any]:
    """Compose the render payload.

    Args:
        ledger: Open ledger (the caller owns the connection and its lifecycle).
        options: Resolved options; see :class:`SnapshotOptions`.

    Returns:
        The payload described in ``docs/design/m1-writable-ledger.md`` §2.1.
    """
    degraded: list[str] = []
    cwd = os.path.abspath(os.path.expanduser(options.cwd or os.getcwd()))

    branch = options.branch
    if branch is None:
        branch = resolve_git_branch(cwd)
        if branch is None:
            degraded.append("branch_unresolved")

    project = options.project or project_hash(cwd)
    project_slug = options.project_slugs.get(project)
    if options.project_map_info.get("available", True) is False:
        degraded.append("project_map_unavailable")

    tracks: dict[str, Any] = {}
    for track in LISTED_TRACKS:
        branch_filtered = False
        kwargs: dict[str, Any] = {"track": track, "state": "active", "limit": None}
        if track == "key":
            kwargs["project"] = project
            if branch is not None:
                kwargs["branch"] = branch
                branch_filtered = True
        rows = ledger.list_units(**kwargs)
        bucket = _render_track(rows, options)
        if track == "key":
            bucket["branch_filtered"] = branch_filtered
        tracks[track] = bucket

    day = options.date or today_local()
    daily_count = len(ledger.list_units(track="daily", day=day, state="active", limit=None))
    project_log_count = len(
        ledger.list_units(track="project", project=project, state="active", limit=None)
    )

    return {
        "ok": True,
        "schema": SNAPSHOT_SCHEMA,
        "cwd": cwd,
        "branch": branch,
        "project": project,
        "project_slug": project_slug,
        "tracks": tracks,
        "daily": {"date": day, "count": daily_count},
        "project_log": {"count": project_log_count},
        "slots": _slot_health(ledger),
        # 待裁决冲突：判据必须有人消费 —— 注入面是模型唯一能"自己看见"的地方，
        # 所以未裁决冲突以**计数 + id** 出现在这里（正文不进 payload，按 id 走
        # `mem conflicts` / `mem resolve`）。0 时仍给出结构，便于渲染层判断"真空态"
        # 与"这个版本不认识该字段"的区别。
        "conflicts": _conflict_health(ledger),
        "degraded": degraded,
    }


def emitted_cost(mode: str, unit_id: str, summary: str, content_len: int) -> int:
    """Characters one entry actually costs in the payload.

    ``full`` mode emits the body, so it is charged the body; ``summary`` mode emits
    only the title, so it is charged the title. Charging the body in summary mode was
    the defect that silently dropped 130 of 340 entries while emitting 32k characters
    under a 200k budget.
    """
    overhead = len(RENDER_PREFIX_TEMPLATE.format(unit_id=unit_id))
    if mode == "full":
        return overhead + content_len
    return overhead + len(summary)


def _render_track(rows: list[Any], options: SnapshotOptions) -> dict[str, Any]:
    """Render one track's entries under the caps, with the mode judgement.

    Two different quantities are involved on purpose:

    * ``mode`` is judged on **stored body size** (``full_threshold_*``): it answers
      "is this track small enough to show in full?".
    * the cap is charged for **emitted characters** (``emitted_cost``): it answers
      "does what I am about to print fit?".

    Entries are dropped whole — a half summary would be worse than a missing one —
    and ``count - truncated == len(entries)`` always holds. When a single entry cannot
    fit an empty budget, one entry is still emitted (better a short answer than an
    empty track) and the shortfall stays visible in ``truncated``.
    """
    total = len(rows)
    total_chars = sum(len(row.content) for row in rows)
    mode = (
        "full"
        if total <= options.full_threshold_entries and total_chars <= options.full_threshold_chars
        else "summary"
    )

    entries: list[dict[str, Any]] = []
    emitted = 0
    for row in rows:
        if len(entries) >= options.max_entries:
            break
        summary = parse_entry(row.content).summary_text(options.summary_chars)
        cost = emitted_cost(mode, row.id, summary, len(row.content))
        if entries and emitted + cost > options.max_chars:
            break
        entries.append({"id": row.id, "summary": summary, "content_len": len(row.content)})
        emitted += cost
    return {
        "mode": mode,
        "count": total,
        "entries": entries,
        "truncated": total - len(entries),
    }


def _conflict_health(ledger: Ledger) -> dict[str, Any]:
    """Open (un-adjudicated) conflicts, as a count plus stable ids.

    Source of truth is the append-only event ledger, same derivation as
    :meth:`Ledger.list_conflicts` — the snapshot never invents a second queue.
    """
    try:
        queue = ledger.list_conflicts()
    except Exception:  # noqa: BLE001 - 快照不因审计面缺失而整段失败
        return {"open": 0, "ids": [], "known": False}
    items = queue.get("open") or []
    ids = [str(item.get("conflict_id")) for item in items if item.get("conflict_id")]
    return {"open": len(items), "ids": ids[:10], "known": True}


def _slot_health(ledger: Ledger) -> dict[str, int]:
    """Slot counters: active/deprecated unit counts and the multi-active invariant.

    ``same_slot_multi_active`` counts (scope, slot) pairs that hold more than one
    active unit — it must stay ``0``, and it is reported rather than asserted so a
    caller can see the invariant instead of trusting it.
    """
    conn = ledger.conn
    active = int(
        conn.execute(
            "SELECT COUNT(*) FROM units WHERE state='active' AND slot IS NOT NULL"
        ).fetchone()[0]
    )
    deprecated = int(
        conn.execute(
            "SELECT COUNT(*) FROM units WHERE state='deprecated' AND slot IS NOT NULL"
        ).fetchone()[0]
    )
    conflicts = int(
        conn.execute(
            "SELECT COUNT(*) FROM (SELECT scope_key, slot FROM units "
            "WHERE state='active' AND slot IS NOT NULL "
            "GROUP BY scope_key, slot HAVING COUNT(*) > 1)"
        ).fetchone()[0]
    )
    return {
        "active": active,
        "deprecated": deprecated,
        "same_slot_multi_active": conflicts,
    }
