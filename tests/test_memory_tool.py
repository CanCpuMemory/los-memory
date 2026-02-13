from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = ROOT / "memory_tool"
sys.path.append(str(MEMORY_DIR))

import memory_tool as mem  # noqa: E402


def test_schema_version_set(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    conn = mem.connect_db(str(db_path))
    mem.ensure_schema(conn)
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'",
    ).fetchone()
    conn.close()
    assert row is not None
    assert int(row["value"]) == mem.SCHEMA_VERSION


def test_add_search_timeline_get(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    conn = mem.connect_db(str(db_path))
    mem.ensure_schema(conn)
    mem.ensure_fts(conn)

    ts1 = "2024-01-01T00:00:00Z"
    ts2 = "2024-01-01T01:00:00Z"
    first_id = mem.add_observation(
        conn,
        ts1,
        "proj",
        "note",
        "First title",
        "Hello world",
        mem.tags_to_json(["alpha", "beta"]),
        mem.tags_to_text(["alpha", "beta"]),
        "raw",
    )
    second_id = mem.add_observation(
        conn,
        ts2,
        "proj",
        "note",
        "Second title",
        "Another summary",
        mem.tags_to_json(["gamma"]),
        mem.tags_to_text(["gamma"]),
        "raw2",
    )

    results = mem.run_search(conn, "hello", limit=10)
    assert any(item["id"] == first_id for item in results)

    timeline = mem.run_timeline(conn, start=ts1, end=ts2, around_id=None, window_minutes=60, limit=10)
    assert [item.id for item in timeline] == [second_id, first_id]

    fetched = mem.run_get(conn, [second_id, first_id])
    assert [item.id for item in fetched] == [second_id, first_id]
    conn.close()


def test_ingest_helper(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    raw_path = tmp_path / "raw.txt"
    raw_path.write_text("Line one\nLine two", encoding="utf-8")

    cmd = [
        sys.executable,
        str(MEMORY_DIR / "ingest.py"),
        "--db",
        str(db_path),
        "--raw-file",
        str(raw_path),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    assert result.returncode == 0

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    count = conn.execute("SELECT COUNT(*) AS c FROM observations").fetchone()["c"]
    conn.close()
    assert count == 1


def test_export_json_csv(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    conn = mem.connect_db(str(db_path))
    mem.ensure_schema(conn)
    mem.ensure_fts(conn)
    mem.add_observation(
        conn,
        "2024-01-01T00:00:00Z",
        "proj",
        "note",
        "Title",
        "Summary",
        mem.tags_to_json(["alpha"]),
        mem.tags_to_text(["alpha"]),
        "raw",
        session_id=1,
    )
    conn.close()

    json_out = tmp_path / "export.json"
    csv_out = tmp_path / "export.csv"
    subprocess.run(
        [
            sys.executable,
            str(MEMORY_DIR / "memory_tool.py"),
            "--db",
            str(db_path),
            "export",
            "--format",
            "json",
            "--output",
            str(json_out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(MEMORY_DIR / "memory_tool.py"),
            "--db",
            str(db_path),
            "export",
            "--format",
            "csv",
            "--output",
            str(csv_out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    exported = json.loads(json_out.read_text(encoding="utf-8"))
    assert len(exported) == 1
    assert exported[0]["title"] == "Title"

    csv_text = csv_out.read_text(encoding="utf-8")
    csv_header = csv_text.splitlines()[0]
    assert "session_id" in csv_header
    assert "Title" in csv_text


def test_pagination_offsets(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    conn = mem.connect_db(str(db_path))
    mem.ensure_schema(conn)
    mem.ensure_fts(conn)
    for idx in range(5):
        mem.add_observation(
            conn,
            f"2024-01-01T0{idx}:00:00Z",
            "proj",
            "note",
            f"Title {idx}",
            "Summary",
            mem.tags_to_json(["alpha"]),
            mem.tags_to_text(["alpha"]),
            "raw",
        )

    first_page = mem.run_list(conn, limit=2, offset=0)
    second_page = mem.run_list(conn, limit=2, offset=2)
    assert first_page[0].id != second_page[0].id

    search_page = mem.run_search(conn, "Title", limit=2, offset=2)
    assert len(search_page) == 2

    timeline_page = mem.run_timeline(
        conn,
        start=None,
        end=None,
        around_id=None,
        window_minutes=120,
        limit=2,
        offset=2,
    )
    assert len(timeline_page) == 2
    conn.close()


def test_required_tags_filter_for_search_and_list(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    conn = mem.connect_db(str(db_path))
    mem.ensure_schema(conn)
    mem.ensure_fts(conn)
    mem.add_observation(
        conn,
        "2026-01-01T00:00:00Z",
        "tenant-a",
        "note",
        "Scoped note",
        "contains migration details",
        mem.tags_to_json(["tenant:a", "user:alice", "migration"]),
        mem.tags_to_text(["tenant:a", "user:alice", "migration"]),
        "raw",
    )
    mem.add_observation(
        conn,
        "2026-01-01T00:01:00Z",
        "tenant-b",
        "note",
        "Other tenant note",
        "contains migration details",
        mem.tags_to_json(["tenant:b", "user:bob", "migration"]),
        mem.tags_to_text(["tenant:b", "user:bob", "migration"]),
        "raw",
    )

    scoped_search = mem.run_search(
        conn,
        "migration",
        limit=10,
        required_tags=["tenant:a", "user:alice"],
    )
    assert len(scoped_search) == 1
    assert scoped_search[0]["title"] == "Scoped note"

    scoped_list = mem.run_list(conn, limit=10, required_tags=["tenant:a", "user:alice"])
    assert len(scoped_list) == 1
    assert scoped_list[0].title == "Scoped note"

    no_match = mem.run_search(conn, "migration", limit=10, required_tags=["tenant:a", "user:bob"])
    assert no_match == []
    conn.close()


def test_profile_resolution() -> None:
    codex_path = mem.resolve_db_path("codex", None)
    claude_path = mem.resolve_db_path("claude", None)
    shared_path = mem.resolve_db_path("shared", None)
    explicit = mem.resolve_db_path("codex", "~/custom-memory.db")

    assert codex_path.endswith("/.codex_memory/memory.db")
    assert claude_path.endswith("/.claude_memory/memory.db")
    assert shared_path.endswith("/.local/share/llm-memory/memory.db")
    assert explicit.endswith("/custom-memory.db")


def test_clean_and_manage(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    conn = mem.connect_db(str(db_path))
    mem.ensure_schema(conn)
    mem.ensure_fts(conn)
    mem.add_observation(
        conn,
        "2020-01-01T00:00:00Z",
        "ops",
        "note",
        "Old entry",
        "to be cleaned",
        mem.tags_to_json(["temp"]),
        mem.tags_to_text(["temp"]),
        "",
    )
    mem.add_observation(
        conn,
        "2026-01-01T00:00:00Z",
        "ops",
        "decision",
        "New entry",
        "to keep",
        mem.tags_to_json(["keep"]),
        mem.tags_to_text(["keep"]),
        "",
    )

    dry = mem.run_clean(
        conn,
        before="2021-01-01T00:00:00Z",
        older_than_days=None,
        project=None,
        kind=None,
        tag=None,
        delete_all=False,
        dry_run=True,
        vacuum=False,
    )
    assert dry["matched"] == 1
    assert dry["deleted"] == 0

    cleaned = mem.run_clean(
        conn,
        before="2021-01-01T00:00:00Z",
        older_than_days=None,
        project=None,
        kind=None,
        tag=None,
        delete_all=False,
        dry_run=False,
        vacuum=False,
    )
    assert cleaned["deleted"] == 1

    stats = mem.run_manage(conn, "stats", 10)
    assert stats["total"] == 1
    projects = mem.run_manage(conn, "projects", 10)
    assert projects["projects"][0]["project"] == "ops"
    tags = mem.run_manage(conn, "tags", 10)
    assert tags["tags"][0]["tag"] == "keep"
    conn.close()


def test_edit_and_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    conn = mem.connect_db(str(db_path))
    mem.ensure_schema(conn)
    mem.ensure_fts(conn)
    first_id = mem.add_observation(
        conn,
        "2026-01-01T00:00:00Z",
        "app",
        "note",
        "Original",
        "Before",
        mem.tags_to_json(["alpha"]),
        mem.tags_to_text(["alpha"]),
        "raw",
    )
    second_id = mem.add_observation(
        conn,
        "2026-01-01T00:10:00Z",
        "app",
        "note",
        "Second",
        "Before2",
        mem.tags_to_json(["beta"]),
        mem.tags_to_text(["beta"]),
        "raw2",
    )

    edited = mem.run_edit(
        conn,
        obs_id=first_id,
        project=None,
        kind="decision",
        title="Changed",
        summary="After",
        tags="gamma,delta",
        raw=None,
        timestamp=None,
        auto_tags=False,
    )
    assert edited["updated"]["title"] == "Changed"
    assert edited["updated"]["kind"] == "decision"
    assert "gamma" in edited["updated"]["tags"]

    preview = mem.run_delete(conn, [second_id], dry_run=True)
    assert preview["matched"] == 1
    assert preview["deleted"] == 0

    deleted = mem.run_delete(conn, [second_id], dry_run=False)
    assert deleted["deleted"] == 1
    remaining = mem.run_list(conn, limit=10)
    assert len(remaining) == 1
    assert remaining[0].id == first_id


def test_log_agent_transition(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    conn = mem.connect_db(str(db_path))
    mem.ensure_schema(conn)
    mem.ensure_fts(conn)

    from memory_tool.analytics import log_agent_transition

    obs_id = log_agent_transition(
        conn,
        phase="review",
        action="check-regression",
        transition_input={"files": ["a.py"]},
        transition_output={"ok": True, "issues": 0},
        status="success",
        reward=1.0,
        project="proj",
        session_id=None,
    )

    rows = conn.execute("SELECT kind, title, summary, raw, tags FROM observations WHERE id = ?", (obs_id,)).fetchone()
    assert rows is not None
    assert rows["kind"] == "agent_transition"
    assert rows["title"] == "Transition: review/check-regression"
    assert "Reward: 1.0" in rows["summary"]
    raw = json.loads(rows["raw"])
    assert raw["phase"] == "review"
    assert raw["action"] == "check-regression"
    conn.close()


def test_apply_review_feedback_batch(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    conn = mem.connect_db(str(db_path))
    mem.ensure_schema(conn)
    mem.ensure_fts(conn)
    first_id = mem.add_observation(
        conn,
        "2026-01-01T00:00:00Z",
        "proj",
        "note",
        "A",
        "Old summary",
        mem.tags_to_json(["x"]),
        mem.tags_to_text(["x"]),
        "raw",
    )
    second_id = mem.add_observation(
        conn,
        "2026-01-01T00:01:00Z",
        "proj",
        "note",
        "B",
        "Another summary",
        mem.tags_to_json(["y"]),
        mem.tags_to_text(["y"]),
        "raw",
    )

    from memory_tool.review_feedback import apply_review_feedback

    report = apply_review_feedback(
        conn,
        items=[
            {"observation_id": first_id, "feedback": "修正: New summary"},
            {"id": second_id, "text": "补充: add context"},
            {"observation_id": "bad-id", "feedback": "补充: skipped"},
        ],
        auto_apply=True,
    )

    assert report["total"] == 3
    assert report["applied"] == 2
    assert report["failed"] == 1

    first = mem.run_get(conn, [first_id])[0]
    second = mem.run_get(conn, [second_id])[0]
    assert first.summary == "New summary"
    assert "[补充] add context" in second.summary
    conn.close()


def test_apply_review_feedback_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    conn = mem.connect_db(str(db_path))
    mem.ensure_schema(conn)
    mem.ensure_fts(conn)
    obs_id = mem.add_observation(
        conn,
        "2026-01-01T00:00:00Z",
        "proj",
        "note",
        "Dry run",
        "Keep me",
        mem.tags_to_json(["z"]),
        mem.tags_to_text(["z"]),
        "raw",
    )

    from memory_tool.review_feedback import apply_review_feedback

    report = apply_review_feedback(
        conn,
        items=[{"observation_id": obs_id, "feedback": "修正: Changed"}],
        auto_apply=False,
    )
    assert report["dry_run"] is True
    current = mem.run_get(conn, [obs_id])[0]
    assert current.summary == "Keep me"
    conn.close()
    conn.close()
