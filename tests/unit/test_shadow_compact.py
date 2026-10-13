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


def test_reindex_merges_the_segments_it_invalidates(conn):
    """A full rebuild must not leave the previous index generation behind.

    `reindex` deletes every FTS row and re-inserts it, which FTS5 keeps as
    tombstoned terms until segments are merged — the same garbage `compact`
    exists to clear. On the live mirror two rebuilds left ~90 MiB of dead
    segments (measured 2026-10-14: 136.2 MiB of FTS+bigram pages fell to
    46.4 MiB on optimize, and the file went 194.8 -> 70.5 MiB on the VACUUM
    after it).

    This asserts the *call*, not a byte count, and that is deliberate: at
    fixture scale FTS5's automerge already merges nearly everything, so a size
    assertion here would measure noise (3 pages of difference before the fix)
    rather than the mechanism. The byte effect is measured on the live mirror in
    the report; what a unit test can hold is that `reindex` asks for the merge
    and that the projection still answers afterwards.
    """
    from memory_tool.shadow import fts_docs, reindex

    seed(conn)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        reindex(conn)
    finally:
        conn.set_trace_callback(None)

    assert any("'optimize'" in statement for statement in statements), (
        "reindex must merge the segments its own DELETE+INSERT invalidated")

    assert fts_docs(conn) == 60, "merging must not drop indexed documents"
    assert status(conn)["search_index"]["state"] == "ready"
    assert len(search(conn, "needle-7")) == 1
    assert len(search(conn, "中文记忆")) > 0, "CJK bigrams survive the merge"


def test_repeated_rebuilds_do_not_grow_the_live_index(conn):
    """The merge must bound repeated rebuilds, not just happen once.

    Measured 2026-10-14 on this fixture (60 records, default `automerge=4`),
    live pages (page_count - freelist_pages) over four rebuilds:
    pre-fix 72 / 71 / 87 / 87 — the dead segments stay inside the index and are
    never returned — versus 71 / 71 / 71 / 71 with the merge. Forcing
    `automerge=0` separates the two monotonically (pre-fix 72 / 87 / 99 / 112),
    which is the shape the live mirror showed: automerge keeps up at fixture
    scale but not on a 2,457-record index.
    """
    from memory_tool.shadow import page_stats, reindex

    seed(conn)
    live_pages = []
    for _ in range(4):
        reindex(conn)
        stats = page_stats(conn)
        live_pages.append(stats["page_count"] - stats["freelist_pages"])

    assert live_pages[-1] <= live_pages[0] + 4, (
        f"the live index grew across rebuilds, so the merge is not bounding it: {live_pages}")
    assert status(conn)["total"] == 60


def test_status_reports_index_bloat_that_the_freelist_hides(conn):
    """`status.storage.fts_index_bytes` must answer "how big is the index".

    The point of the field is that `freelist_bytes` cannot: an unmerged rebuild
    leaves dead segments inside the live index, so the freelist does not move
    while the index grows. Measured on this fixture, the payload roughly doubles
    on an unmerged rebuild and returns on `optimize`.
    """
    import json

    from memory_tool.shadow import index_record, reindex

    seed(conn)
    reindex(conn)
    merged = status(conn)["storage"]["fts_index_bytes"]
    assert merged and merged > 0, "a built index must report a non-zero size"

    rows = conn.execute("SELECT space, snapshot FROM records").fetchall()
    with conn:
        for table in ("records_fts", "record_facets", "record_labels", "cjk_bigrams"):
            conn.execute(f"DELETE FROM {table}")
        for row in rows:
            index_record(conn, row["space"], json.loads(row["snapshot"]))
    bloat = status(conn)["storage"]["fts_index_bytes"]
    assert bloat > merged, (
        "an unmerged rebuild must show up as index growth — if this fails, the "
        f"field cannot see the waste it exists to expose (merged {merged}, now {bloat})")

    optimize_only = compact(conn, vacuum=False)

    assert optimize_only["vacuumed"] is False
    after = status(conn)["storage"]["fts_index_bytes"]
    # Not exact equality: merging is not byte-identical to the first build (5 bytes
    # of segment structure on this fixture), so the assertion is "back to the same
    # size class", which is what separates merged from bloated.
    assert abs(after - merged) <= max(64, merged // 100), (
        f"merging must return the reported index size to where it started "
        f"(was {merged}, now {after})")
    assert status(conn)["total"] == 60
