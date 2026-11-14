"""Admission policy: writes are free, only structural conflicts are judged.

The rule under test (see ``memory_tool/ledger/admission.py``): a write that overlaps
nothing lands immediately; a same-``slot`` collision is decided mechanically by
``(authority rank, event time)``; the only case that reaches a human is an exact tie
(same authority **and** same event time with different bodies); sensitive shapes are
refused outright.

Every non-trivial assertion has a negative control, because the two ways this layer
can fail are both quiet: **over-refusing** (writer starts needing a human again) and
**over-writing** (an agent silently replaces an owner's decision). Nothing here
touches ``~/.dsh/memories`` — each test builds a temporary ledger.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_tool.ledger import admission, schema  # noqa: E402
from memory_tool.ledger.store import Ledger  # noqa: E402

PROJECT = "2d4994d462af"  # evolve's directory hash for dsfolder (stable identity)


def open_ledger(path: Path) -> tuple[object, Ledger]:
    """Open an initialised ledger in a temp file."""
    conn = schema.connect(str(path), create=True)
    return conn, Ledger(conn)


def active_in_slot(conn, slot: str) -> list:
    """Active units currently occupying one slot."""
    return conn.execute(
        "SELECT id FROM units WHERE state='active' AND slot=? ORDER BY created_at", (slot,)
    ).fetchall()


# --------------------------------------------------------------- pure decision


def test_append_when_nothing_overlaps():
    decision = admission.decide(
        admission.Incoming(track="memory", scope_key="memory|||", digest="d1", content="x"),
        [],
    )
    assert decision.action == "append"


def test_no_slot_declared_means_no_conflict_detection():
    """Documented limit: without a slot there is no structural claim of "same fact".

    Negative control for the tempting alternative (text similarity): two contradictory
    bodies without slots must both land, because guessing sameness is what the
    2026-11-01 backtest rejected.
    """
    existing = [
        admission.Existing(id="u1", digest="d1", slot=None, actor="owner", asserted_at="2026-01-01")
    ]
    decision = admission.decide(
        admission.Incoming(
            track="memory",
            scope_key="memory|||",
            digest="d2",
            content="完全相反的结论",
            actor="agent:main",
            asserted_at="2026-11-14",
        ),
        existing,
    )
    assert decision.action == "append"


def test_identical_content_dedupes():
    existing = [admission.Existing(id="u1", digest="same", slot="s")]
    decision = admission.decide(
        admission.Incoming(track="key", scope_key="k", digest="same", content="x", slot="s"),
        existing,
    )
    assert decision.action == "dedupe"
    assert decision.existing_id == "u1"


def test_newer_event_time_wins_the_slot():
    existing = [
        admission.Existing(id="old", digest="d1", slot="port", actor="agent:main", asserted_at="2026-11-01")
    ]
    decision = admission.decide(
        admission.Incoming(
            track="key",
            scope_key="k",
            digest="d2",
            content="9090",
            slot="port",
            actor="agent:main",
            asserted_at="2026-11-14",
        ),
        existing,
    )
    assert decision.action == "supersede"
    assert decision.existing_id == "old"


def test_older_event_time_loses_the_slot():
    """Out-of-order writes must not win: a back-filled older fact is outranked."""
    existing = [
        admission.Existing(id="new", digest="d1", slot="port", actor="agent:main", asserted_at="2026-11-14")
    ]
    decision = admission.decide(
        admission.Incoming(
            track="key",
            scope_key="k",
            digest="d2",
            content="8080",
            slot="port",
            actor="agent:main",
            asserted_at="2026-11-01",
        ),
        existing,
    )
    assert decision.action == "outranked"
    assert decision.existing_id == "new"


def test_authority_beats_recency():
    """An owner assertion is not overwritten by a newer sub-agent write."""
    existing = [
        admission.Existing(id="owner", digest="d1", slot="deploy", actor="owner", asserted_at="2026-11-10")
    ]
    decision = admission.decide(
        admission.Incoming(
            track="key",
            scope_key="k",
            digest="d2",
            content="改成 systemd",
            slot="deploy",
            actor="agent:subagent",
            asserted_at="2026-11-14",
        ),
        existing,
    )
    assert decision.action == "outranked"


def test_import_rank_cannot_outrank_agents():
    """Historical imports (rank 0) never displace a live agent assertion."""
    existing = [
        admission.Existing(id="live", digest="d1", slot="s", actor="agent:main", asserted_at="2026-01-01")
    ]
    decision = admission.decide(
        admission.Incoming(
            track="key",
            scope_key="k",
            digest="d2",
            content="imported",
            slot="s",
            actor="import:evolve",
            asserted_at="2026-11-14",
        ),
        existing,
    )
    assert decision.action == "outranked"


def test_exact_tie_escalates_instead_of_guessing():
    existing = [
        admission.Existing(id="a", digest="d1", slot="naming", actor="agent:main", asserted_at="2026-11-14")
    ]
    decision = admission.decide(
        admission.Incoming(
            track="key",
            scope_key="k",
            digest="d2",
            content="B",
            slot="naming",
            actor="cli",  # 不同 principal、同等权威（cli 与 agent:main 同为 rank 20）
            asserted_at="2026-11-14",
        ),
        existing,
    )
    assert decision.action == "escalate"


def test_write_time_does_not_break_a_real_tie():
    """Same authority + same event time must escalate even though rows were written apart.

    Negative control for the bug this file caught: using ``recorded_at`` as a third
    comparison key made every "real" conflict look decided ("whoever wrote last wins"),
    i.e. the one case that needs a human was silently auto-resolved.
    """
    existing = [
        admission.Existing(
            id="a",
            digest="d1",
            slot="naming",
            actor="agent:main",
            asserted_at="2026-11-14",
            recorded_at="2026-11-14T00:00:00Z",
        )
    ]
    decision = admission.decide(
        admission.Incoming(
            track="key",
            scope_key="k",
            digest="d2",
            content="B",
            slot="naming",
            actor="cli",
            asserted_at="2026-11-14",
            recorded_at="2026-11-14T09:00:00Z",
        ),
        existing,
    )
    assert decision.action == "escalate"


def test_same_principal_same_time_is_an_update_not_a_conflict():
    """同一个 agent 往自己占的槽位再写一次：写序即意图，不该升级给人。"""
    existing = [
        admission.Existing(id="a", digest="d1", slot="port", actor="agent:main", asserted_at="2026-11-14")
    ]
    decision = admission.decide(
        admission.Incoming(
            track="key",
            scope_key="k",
            digest="d2",
            content="9090",
            slot="port",
            actor="agent:main",
            asserted_at="2026-11-14",
        ),
        existing,
    )
    assert decision.action == "supersede"


@pytest.mark.parametrize(
    "content",
    [
        "OPENAI_API_KEY 是 sk-abcdef1234567890abcdef",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE...",
        "aws AKIAIOSFODNN7EXAMPLE",
        "密码: hunter2hunter2",
    ],
)
def test_sensitive_shapes_are_blocked(content):
    decision = admission.decide(
        admission.Incoming(track="memory", scope_key="m", digest="d", content=content), []
    )
    assert decision.action == "block"


@pytest.mark.parametrize(
    "content",
    [
        "密码填充与记忆能力归属：两者强依赖（话题词，不是赋值）",
        "password policy 讨论：需要轮换周期",
        "sk- 前缀只是举例，没有具体值",
    ],
)
def test_sensitive_negative_controls_are_not_blocked(content):
    """Word-level matching would refuse ordinary prose — that is why the deny list
    matches *value shapes* (assignment/known token formats), not words."""
    decision = admission.decide(
        admission.Incoming(track="memory", scope_key="m", digest="d", content=content), []
    )
    assert decision.action == "append"


def test_unknown_actor_is_conservative():
    assert admission.authority_rank("weird-agent") == admission.RANK_SUB
    assert admission.authority_rank("agent:subagent:x") == admission.RANK_SUB
    assert admission.authority_rank("agent:main") == admission.RANK_MAIN


# ------------------------------------------------------------ store integration


def test_store_supersede_keeps_exactly_one_active(tmp_path):
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    first = ledger.add("key", "端口是 8080", project=PROJECT, slot="port",
                       actor="agent:main", asserted_at="2026-11-01")
    second = ledger.add("key", "端口改成 9090", project=PROJECT, slot="port",
                        actor="agent:main", asserted_at="2026-11-14")
    assert first["created"] is True and second["created"] is True
    assert first["id"] in second["deprecated"]
    assert len(active_in_slot(conn, "port")) == 1


def test_store_owner_assertion_is_not_overwritten(tmp_path):
    conn, ledger = open_ledger(tmp_path / "o.sqlite3")
    ledger.add("key", "部署走 launchd（owner 拍板）", project=PROJECT, slot="deploy",
               actor="owner", asserted_at="2026-11-10")
    refused = ledger.add("key", "部署改成 systemd", project=PROJECT, slot="deploy",
                         actor="agent:subagent", asserted_at="2026-11-14")
    assert refused["admitted"] is False
    assert refused["reason"] == "outranked_by_slot"
    assert refused["existing_id"] is not None
    assert len(active_in_slot(conn, "deploy")) == 1
    # 审计面：被压过的内容不是"消失"，而是进了 append-only 事件台账
    kinds = [row["kind"] for row in conn.execute("SELECT kind FROM events ORDER BY id")]
    assert "add_outranked" in kinds


def test_store_tie_lands_in_the_conflict_queue_and_resolves(tmp_path):
    conn, ledger = open_ledger(tmp_path / "c.sqlite3")
    ledger.add("key", "命名用 A", project=PROJECT, slot="naming",
               actor="agent:main", asserted_at="2026-11-14")
    refused = ledger.add("key", "命名用 B", project=PROJECT, slot="naming",
                         actor="cli", asserted_at="2026-11-14")
    assert refused["admitted"] is False and refused["action"] == "escalate"
    queue = ledger.list_conflicts()
    assert len(queue["open"]) == 1
    conflict_id = queue["open"][0]["conflict_id"]
    assert queue["open"][0]["existing_id"] is not None

    result = ledger.resolve_conflict(conflict_id, "replace", actor="owner", note="B 更符合口径")
    assert result["changed"] is True and result["written"]
    assert ledger.list_conflicts()["open"] == []
    actives = active_in_slot(conn, "naming")
    assert len(actives) == 1 and actives[0]["id"] == result["written"]

    # 幂等：同一条冲突再裁一次不重复写
    again = ledger.resolve_conflict(conflict_id, "replace", actor="owner")
    assert again["changed"] is False


def test_store_drop_keeps_the_existing_entry(tmp_path):
    conn, ledger = open_ledger(tmp_path / "d.sqlite3")
    first = ledger.add("key", "A", project=PROJECT, slot="s",
                       actor="agent:main", asserted_at="2026-11-14")
    ledger.add("key", "B", project=PROJECT, slot="s",
               actor="cli", asserted_at="2026-11-14")
    conflict_id = ledger.list_conflicts()["open"][0]["conflict_id"]
    ledger.resolve_conflict(conflict_id, "drop", actor="owner")
    actives = active_in_slot(conn, "s")
    assert len(actives) == 1 and actives[0]["id"] == first["id"]


def test_store_keep_both_appends_without_taking_the_slot(tmp_path):
    conn, ledger = open_ledger(tmp_path / "k.sqlite3")
    ledger.add("key", "A", project=PROJECT, slot="s",
               actor="agent:main", asserted_at="2026-11-14")
    ledger.add("key", "B", project=PROJECT, slot="s",
               actor="cli", asserted_at="2026-11-14")
    conflict_id = ledger.list_conflicts()["open"][0]["conflict_id"]
    ledger.resolve_conflict(conflict_id, "keep_both", actor="owner")
    assert len(active_in_slot(conn, "s")) == 1  # 槽位仍只有一条
    total_active = conn.execute("SELECT COUNT(*) FROM units WHERE state='active'").fetchone()[0]
    assert total_active == 2  # 两条内容都保留


def test_store_blocks_secrets_and_negative_control(tmp_path):
    _conn, ledger = open_ledger(tmp_path / "sec.sqlite3")
    blocked = ledger.add("memory", "token: sk-abcdef1234567890abcdef", actor="agent:main")
    assert blocked["admitted"] is False and blocked["action"] == "block"
    ok = ledger.add("memory", "密码填充与记忆能力归属：盘点结论", actor="agent:main")
    assert ok["created"] is True


def test_store_on_conflict_append_bypasses_judgement(tmp_path):
    """Historical imports pass ``on_conflict='append'``: provenance wins over novelty."""
    _conn, ledger = open_ledger(tmp_path / "i.sqlite3")
    ledger.add("key", "端口 8080", project=PROJECT, slot="port",
               actor="import:evolve", asserted_at="2026-11-01")
    forced = ledger.add("key", "端口 8080（旧副本）", project=PROJECT, slot="port",
                        actor="import:evolve", asserted_at="2026-11-01", on_conflict="append")
    assert forced["created"] is True
    assert forced["deprecated"]  # 槽位依旧只留一条 active


def test_store_unknown_on_conflict_is_rejected(tmp_path):
    _conn, ledger = open_ledger(tmp_path / "u.sqlite3")
    with pytest.raises(ValueError):
        ledger.add("memory", "x", on_conflict="whatever")
