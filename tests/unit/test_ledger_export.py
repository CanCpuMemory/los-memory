"""Reverse export (`mem export-evolve`): the rollback surface for the cutover.

The tests are written around the only question that matters: **if we flip to the
ledger and then need to go back, does the corpus get everything back exactly once?**
So: round trip through the real importer, idempotency on a second run, archive
routing, and corpus-wide deduplication (the same text must not land in two files).
"""
from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_tool.ledger import schema  # noqa: E402
from memory_tool.ledger.evolve_md import parse_entry, split_entries  # noqa: E402
from memory_tool.ledger.export_evolve import (  # noqa: E402
    corpus_digests,
    entry_text,
    existing_digests,
    export_units,
    target_path,
)
from memory_tool.ledger.import_evolve import import_evolve  # noqa: E402
from memory_tool.ledger.store import Ledger, content_digest, canonical_content  # noqa: E402


def open_ledger(path: Path) -> tuple[object, Ledger]:
    conn = schema.connect(str(path), create=True)
    return conn, Ledger(conn)


def units_of(ledger: Ledger) -> list[dict]:
    rows = ledger.list_units(state="all", limit=None)
    return [
        {
            "id": row.id,
            "track": row.track,
            "project": row.project,
            "branch": row.branch,
            "slot": row.slot,
            "day": row.day,
            "state": row.state,
            "created_at": row.created_at,
            "content": row.content,
        }
        for row in rows
    ]


def test_round_trip_returns_every_unit_once(tmp_path: Path) -> None:
    """Ledger → corpus → ledger: same entries, same content, no duplicates."""
    conn, ledger = open_ledger(tmp_path / "a.sqlite3")
    try:
        with ledger.transaction():
            ledger.add("memory", "第一条：盘点基线")
        with ledger.transaction():
            ledger.add("key", "第二条：接缝改账本", project="abc123abc123", slot="seam-source")
        units = units_of(ledger)
    finally:
        conn.close()

    corpus = tmp_path / "corpus"
    report = export_units(units, root=str(corpus), dry_run=False)
    assert report["appended"] == 2, report
    assert (corpus / "MEMORY.md").exists()
    key_file = corpus / "projects" / "abc123abc123" / "KEY.md"
    assert key_file.exists()

    # Read the corpus back through the real importer, not a second parser.
    conn2, ledger2 = open_ledger(tmp_path / "b.sqlite3")
    try:
        import_evolve(ledger2, corpus)
        rows = ledger2.list_units(state="all", limit=None)
        bodies = {parse_entry(row.content).body for row in rows}
        assert bodies == {"第一条：盘点基线", "第二条：接缝改账本"}, bodies
        key = [r for r in rows if r.track == "key"][0]
        assert key.slot == "seam-source", "slot 必须能往返"
    finally:
        conn2.close()


def test_second_run_appends_nothing(tmp_path: Path) -> None:
    """Idempotency is the property that makes this safe to run on every rollback drill."""
    conn, ledger = open_ledger(tmp_path / "a.sqlite3")
    try:
        with ledger.transaction():
            ledger.add("memory", "只此一条")
        units = units_of(ledger)
    finally:
        conn.close()
    corpus = tmp_path / "corpus"
    first = export_units(units, root=str(corpus), dry_run=False)
    second = export_units(units, root=str(corpus), dry_run=False)
    assert first["appended"] == 1
    assert second["appended"] == 0, second
    assert second["skipped_existing"] == 1


def test_archived_goes_to_the_archive_file_not_the_main_one(tmp_path: Path) -> None:
    conn, ledger = open_ledger(tmp_path / "a.sqlite3")
    try:
        with ledger.transaction():
            added = ledger.add("memory", "会被归档的一条")
        with ledger.transaction():
            ledger.archive(added["id"], reason="test")
        units = units_of(ledger)
    finally:
        conn.close()
    corpus = tmp_path / "corpus"
    export_units(units, root=str(corpus), dry_run=False)
    assert (corpus / "MEMORY-archive.md").exists()
    assert not (corpus / "MEMORY.md").exists()


def test_dedupe_is_corpus_wide_so_one_text_lives_in_one_file(tmp_path: Path) -> None:
    """A body already in MEMORY.md must not be appended to MEMORY-archive.md.

    evolve reads both files; the same text in two of them is the memory showing up
    twice after a rollback. Per-file dedupe would miss exactly this case.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "MEMORY.md").write_text("§\n重复正文\n", encoding="utf-8")
    assert corpus_digests(corpus) == {content_digest(canonical_content("重复正文"))}

    unit = {
        "id": "unit_x", "track": "memory", "project": None, "branch": None, "slot": None,
        "day": "2026-11-18", "state": "deprecated", "created_at": "2026-11-18T00:00:00Z",
        "content": "重复正文",
    }
    report = export_units([unit], root=str(corpus), dry_run=False)
    assert report["appended"] == 0
    assert not (corpus / "MEMORY-archive.md").exists()


def test_date_token_falls_back_to_created_at_and_never_invents_a_day(tmp_path: Path) -> None:
    unit = {"track": "memory", "content": "x", "created_at": "2026-11-02T10:00:00Z", "day": None}
    assert entry_text(unit).startswith("[2026-11-02] ")
    daily = {"track": "daily", "content": "y", "created_at": "2026-11-03T23:30:00Z", "day": None}
    path = target_path(tmp_path, "daily", None, "2026-11-03")
    assert path == tmp_path / "daily" / "2026-11-03.md"
    assert parse_entry(entry_text(daily)).body == "y"


def test_existing_digests_ignores_empty_blocks(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    path.write_text("a\n§\n\n§\nb\n", encoding="utf-8")
    assert len(split_entries(path.read_text(encoding="utf-8"))) == 2
    assert len(existing_digests(path)) == 2
