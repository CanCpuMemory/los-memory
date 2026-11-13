"""Write-path semantics of the ledger: slots, revisions, lifecycle, search.

Why these tests look like this: the writable ledger is the first component in this
repo that can *change* a memory, so the failures that matter are the quiet ones —
a slot that leaves two facts active at once, a replayed write that appends a second
revision, an archived entry that still comes back from the default read. Every
check therefore has a negative control that must stay green for the right reason.

Nothing here touches ``~/.dsh/memories`` or a profile database: each test builds a
temporary ledger under ``tmp_path``.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_tool.ledger import schema  # noqa: E402
from memory_tool.ledger.schema import LedgerRefusedError, LedgerUnavailableError  # noqa: E402
from memory_tool.ledger.store import (  # noqa: E402
    Ledger,
    canonical_content,
    content_digest,
    searchable_text,
)


def open_ledger(path: Path) -> tuple[sqlite3.Connection, Ledger]:
    """Open an initialised ledger in a temp file."""
    conn = schema.connect(str(path), create=True)
    return conn, Ledger(conn)


def same_slot_multi_active(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return the (scope, slot) pairs that have more than one active unit."""
    return conn.execute(
        "SELECT scope_key, slot, COUNT(*) AS active FROM units "
        "WHERE state='active' AND slot IS NOT NULL "
        "GROUP BY scope_key, slot HAVING active > 1"
    ).fetchall()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts for the tables a write may touch."""
    return {
        "units": conn.execute("SELECT COUNT(*) FROM units").fetchone()[0],
        "revisions": conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0],
        "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
    }


def test_slot_handoff_deprecates_the_previous_active_unit(tmp_path: Path) -> None:
    """A slot holds one active fact: the newer add deprecates the older one."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            first = ledger.add("key", "gateway port 9090", project="dsfolder", branch="main",
                               slot="gateway-port")
        with ledger.transaction():
            second = ledger.add("key", "gateway port 10090", project="dsfolder", branch="main",
                                slot="gateway-port")

        assert first["id"] != second["id"]
        assert second["deprecated"] == [first["id"]]
        assert same_slot_multi_active(conn) == []

        old = ledger.get_unit(first["id"])
        assert old is not None
        assert old.state == "deprecated"
        assert old.superseded_by == second["id"]
        # History is preserved, not rewritten: the retired text is still readable.
        assert old.content == "gateway port 9090"
    finally:
        conn.close()


def test_slot_is_scoped_so_another_project_is_untouched(tmp_path: Path) -> None:
    """Negative control for the slot rule: the same slot in another project survives."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            other = ledger.add("key", "port 8080", project="cankey", branch="main",
                               slot="gateway-port")
        with ledger.transaction():
            ledger.add("key", "port 9090", project="dsfolder", branch="main",
                       slot="gateway-port")
        with ledger.transaction():
            ledger.add("key", "port 10090", project="dsfolder", branch="main",
                       slot="gateway-port")

        survived = ledger.get_unit(other["id"])
        assert survived is not None
        assert survived.state == "active", "a slot in a different project must not be deprecated"
        assert conn.execute(
            "SELECT COUNT(*) FROM units WHERE state='active' AND slot='gateway-port'"
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_repeated_add_is_idempotent(tmp_path: Path) -> None:
    """Identical content in the same scope returns the same unit, no second revision."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            first = ledger.add("key", "same fact", project="dsfolder", branch="main")
        before = counts(conn)
        with ledger.transaction():
            again = ledger.add("key", "same fact", project="dsfolder", branch="main")

        assert again["id"] == first["id"]
        assert again["created"] is False and again["deduped"] is True
        assert counts(conn)["units"] == before["units"]
        assert counts(conn)["revisions"] == before["revisions"], "no second revision"
        # The replay is recorded, so "nothing happened" is still auditable.
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind='add_deduped'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_supersede_leaves_exactly_one_active_head(tmp_path: Path) -> None:
    """A supersede chain has one active head; the rest are deprecated and linked."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            first = ledger.add("key", "v1 fact", project="dsfolder", branch="main")
        with ledger.transaction():
            second = ledger.supersede(first["id"], "v2 fact")
        with ledger.transaction():
            third = ledger.supersede(second["id"], "v3 fact")

        active = conn.execute("SELECT id FROM units WHERE state='active'").fetchall()
        assert [row[0] for row in active] == [third["id"]]

        old = ledger.get_unit(first["id"])
        middle = ledger.get_unit(second["id"])
        assert old is not None and middle is not None
        assert old.superseded_by == second["id"]
        assert middle.superseded_by == third["id"]
        assert ledger.expand(first["id"])["content"] == "v1 fact"
    finally:
        conn.close()


def test_replace_appends_a_revision_and_keeps_one_content(tmp_path: Path) -> None:
    """A replace is a new revision, not a second unit, and a no-op replace writes nothing."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            unit = ledger.add("user", "prefers evidence first")
        with ledger.transaction():
            changed = ledger.replace(unit["id"], "prefers evidence first, then options")
        assert changed["changed"] is True and changed["revision"] == 2

        before = counts(conn)
        with ledger.transaction():
            same = ledger.replace(unit["id"], "prefers evidence first, then options")
        assert same["changed"] is False and same["revision"] == 2
        assert counts(conn) == before, "an identical replace must not append a revision"

        expanded = ledger.expand(unit["id"])
        assert [item["revision"] for item in expanded["revisions"]] == [1, 2]
        assert expanded["content"] == "prefers evidence first, then options"
    finally:
        conn.close()


def test_replace_refuses_a_non_active_unit(tmp_path: Path) -> None:
    """Negative control: an archived unit cannot be edited back into recall."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            unit = ledger.add("memory", "archived fact")
        with ledger.transaction():
            ledger.archive(unit["id"])
        with pytest.raises(LedgerRefusedError) as excinfo, ledger.transaction():
            ledger.replace(unit["id"], "sneaky edit")
        assert "unit_not_active" in str(excinfo.value)
    finally:
        conn.close()


def test_archive_hides_from_list_but_stays_readable(tmp_path: Path) -> None:
    """Archived means out of default recall, still addressable by id."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            unit = ledger.add("memory", "archive me")
        with ledger.transaction():
            ledger.archive(unit["id"])

        active = ledger.list_units(state="active")
        assert [row.id for row in active] == []
        every = ledger.list_units(state="all")
        assert [row.id for row in every] == [unit["id"]]
        assert ledger.expand(unit["id"])["content"] == "archive me"
        # Search follows the same rule, and says why it is empty.
        result = ledger.search("archive")
        assert result["results"] == []
        result_all = ledger.search("archive", include_archived=True)
        assert [hit["id"] for hit in result_all["results"]] == [unit["id"]]
    finally:
        conn.close()


def test_unknown_id_is_a_refusal_not_an_empty_answer(tmp_path: Path) -> None:
    """A missing unit id is a real negative (refused), distinct from "no ledger"."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with pytest.raises(LedgerRefusedError):
            ledger.expand("unit_doesnotexist")
        with pytest.raises(LedgerRefusedError), ledger.transaction():
            ledger.archive("unit_doesnotexist")
    finally:
        conn.close()


def test_missing_ledger_is_unavailable_not_empty(tmp_path: Path) -> None:
    """A missing database must not read as "zero memories"."""
    missing = tmp_path / "never-created.sqlite3"
    with pytest.raises(LedgerUnavailableError) as excinfo:
        schema.connect(str(missing), create=False)
    assert "service_ledger_missing" in str(excinfo.value)
    assert not missing.exists(), "a read must not create the file it inspects"


def test_scope_validation_refuses_guessed_projects(tmp_path: Path) -> None:
    """Project scope is refused rather than defaulted, in both directions."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with pytest.raises(ValueError), ledger.transaction():
            ledger.add("key", "no project given")
        with pytest.raises(ValueError), ledger.transaction():
            ledger.add("memory", "global track with a project", project="dsfolder")
        with pytest.raises(ValueError), ledger.transaction():
            ledger.add("key", "branch on a non-key track is meaningless",
                       project="dsfolder", branch="main", day="2026-01-01")
    finally:
        conn.close()


def test_events_are_append_only(tmp_path: Path) -> None:
    """Existing events never change: every write only appends."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            unit = ledger.add("memory", "first")
        snapshot = [tuple(row) for row in conn.execute("SELECT id, kind, payload_json FROM events")]
        with ledger.transaction():
            ledger.replace(unit["id"], "second")
        with ledger.transaction():
            ledger.archive(unit["id"])

        after = [tuple(row) for row in conn.execute("SELECT id, kind, payload_json FROM events")]
        assert after[: len(snapshot)] == snapshot, "append-only: earlier rows are untouched"
        assert len(after) > len(snapshot)
        kinds = [row[1] for row in after]
        assert kinds == ["add", "replace", "archive"]
    finally:
        conn.close()


def test_empty_content_is_refused(tmp_path: Path) -> None:
    """Whitespace-only content is not a writable fact."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with pytest.raises(ValueError) as excinfo, ledger.transaction():
            ledger.add("memory", "   \n\t  ")
        assert "empty_content" in str(excinfo.value)
        assert counts(conn)["units"] == 0
    finally:
        conn.close()


def test_search_paths_and_no_match_behaviour(tmp_path: Path) -> None:
    """Search finds 2-character CJK terms (bigram) and reports an honest empty result."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            unit = ledger.add("key", "网关端口已改为 10090，§7 有记录", project="dsfolder",
                              branch="main")
        cjk = ledger.search("网关")
        assert [hit["id"] for hit in cjk["results"]] == [unit["id"]]
        assert cjk["meta"]["paths"] == {"网关": "bigram"}

        long_term = ledger.search("10090")
        assert [hit["id"] for hit in long_term["results"]] == [unit["id"]]
        assert long_term["meta"]["paths"] == {"10090": "trigram"}

        absent = ledger.search("不存在的内容")
        assert absent["results"] == [], "no match is an empty list, not an error"
        assert absent["meta"]["hits"] == 0
    finally:
        conn.close()


def test_search_matches_body_not_program_tokens(tmp_path: Path) -> None:
    """The index carries the entry body and human-facing tokens, not identity/date."""
    assert searchable_text("[id:aaaa1111] [2026-01-05] [summary:网关端口] 正文 §7") == (
        "正文 §7\nsummary:网关端口"
    )
    assert "[id:aaaa1111]" not in searchable_text("[id:aaaa1111] [2026-01-05] 正文")


def test_rebuild_index_restores_the_index_path(tmp_path: Path) -> None:
    """A wiped index degrades to a scan (not to "no hits") and the rebuild restores it.

    This is the honesty property that matters more than speed: an index that was
    never built must not turn a real match into an empty answer. ``meta`` names the
    path so the degradation is visible instead of silent.
    """
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            unit = ledger.add("memory", "rebuildable fact")
        conn.execute("DELETE FROM units_fts")
        conn.execute("DELETE FROM cjk_bigrams")
        conn.commit()

        degraded = ledger.search("rebuildable")
        assert [hit["id"] for hit in degraded["results"]] == [unit["id"]], (
            "scan fallback keeps recall"
        )
        assert degraded["meta"]["mode"] == "scan"
        assert degraded["meta"]["usable"] == {"trigram": False, "bigram": False}
        assert degraded["meta"]["scan_terms"] == ["rebuildable"]

        rebuilt = ledger.rebuild_index()
        assert rebuilt["units"] >= 1
        restored = ledger.search("rebuildable")
        assert restored["meta"]["mode"] == "index+filter"
        assert [hit["id"] for hit in restored["results"]] == [unit["id"]]
    finally:
        conn.close()


def test_scope_key_rejects_the_separator(tmp_path: Path) -> None:
    """A scope value containing the key separator is refused, not silently merged."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with pytest.raises(ValueError), ledger.transaction():
            ledger.add("key", "ambiguous scope", project="a|b")
    finally:
        conn.close()


def test_digest_is_stable_for_equivalent_whitespace(tmp_path: Path) -> None:
    """Trailing/leading whitespace does not create a second unit."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            first = ledger.add("memory", "  same body\n")
        with ledger.transaction():
            second = ledger.add("memory", "same body")
        assert first["id"] == second["id"]
        assert content_digest(canonical_content("same body")) == content_digest(
            canonical_content("  same body\n")
        )
    finally:
        conn.close()
