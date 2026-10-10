"""`compact` must return space without deleting anything.

The measurement that motivated it (2026-10-10, live mirror, 174.7 MB file):
**58.9 MiB was freelist** — pages freed by reindexes and never reused — while all
118 inactive records live inside the 9.1 MB `records` table. So the space a
"retention policy" is usually asked to win back is an order of magnitude smaller
than what a rewrite returns, and the rewrite costs no auditability at all.
"""
from __future__ import annotations

import pytest

from memory_tool.shadow import compact, connect, page_stats, put, search, status


def record(source_id, content):
    return {"id": source_id, "space_id": "default", "title": f"title {source_id}",
            "content": content, "lifecycle_state": "active", "is_latest": True,
            "metadata": {"project": "alpha"}}


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "private" / "shadow.db")
    yield connection
    connection.close()


def seed(conn, count=60):
    for index in range(count):
        put(conn, "default", record(f"rec-{index}",
                                    f"中文记忆 {index} " + "padding " * 40 + f"needle-{index}"))
    conn.commit()


def make_freelist(conn):
    """Free a real amount of space the way a reindex does, then measure it."""
    seed(conn)
    with conn:
        conn.execute("DELETE FROM cjk_bigrams")
        conn.execute("DELETE FROM records_fts")
    conn.commit()
    return page_stats(conn)


def test_page_stats_accounts_for_the_file(conn):
    seed(conn, 5)

    stats = page_stats(conn)

    assert stats["page_size"] > 0
    assert stats["file_bytes"] == stats["page_size"] * stats["page_count"]
    assert stats["freelist_bytes"] == stats["page_size"] * stats["freelist_pages"]


def test_compact_reclaims_freelist_and_keeps_every_record(conn):
    before = make_freelist(conn)
    assert before["freelist_pages"] > 0, "fixture must actually free pages"

    result = compact(conn, vacuum=True)

    assert result["vacuumed"] is True
    assert result["freelist_bytes_after"] < before["freelist_bytes"]
    assert result["reclaimed_bytes"] > 0
    assert status(conn)["total"] == 60, "no record may be dropped by a space operation"
    assert status(conn)["active"] == 60


def test_compact_without_vacuum_does_not_rewrite_the_file(conn):
    before = make_freelist(conn)

    result = compact(conn, vacuum=False)

    assert result["vacuumed"] is False
    assert result["file_bytes_after"] == before["file_bytes"], (
        "only the FTS segments may be merged without an explicit --vacuum; a caller "
        "that did not ask for a rewrite must not get one")
    assert status(conn)["total"] == 60


def test_compact_keeps_the_projection_consistent(conn):
    """`optimize` merges FTS segments; it must not leave the projection short.

    The fixture empties the derived tables, and `search` still answers that from
    its documented full-scan fallback — so asserting "search finds nothing" would
    be testing the fallback, not the index. The assertion has to be about the
    index state and the rebuilt projection.
    """
    from memory_tool.shadow import fts_docs, reindex

    make_freelist(conn)
    assert fts_docs(conn) == 0
    assert status(conn)["search_index"]["state"] == "not_built", (
        "an emptied index must report not_built rather than answer 'no memories'")

    reindex(conn)
    assert fts_docs(conn) == 60

    compact(conn, vacuum=True)

    assert fts_docs(conn) == 60
    assert status(conn)["search_index"]["state"] == "ready"
    assert len(search(conn, "needle-7")) == 1
    assert len(search(conn, "中文记忆")) > 0, "CJK bigrams survive the rewrite"


def test_compact_is_idempotent(conn):
    make_freelist(conn)

    first = compact(conn, vacuum=True)
    second = compact(conn, vacuum=True)

    assert second["reclaimed_bytes"] == 0, "a second pass has nothing left to return"
    assert first["file_bytes_after"] == second["file_bytes_after"]
    assert status(conn)["total"] == 60


def test_compact_on_an_empty_database_is_a_no_op(conn):
    result = compact(conn, vacuum=True)

    assert result["reclaimed_bytes"] == 0
    assert status(conn)["total"] == 0
