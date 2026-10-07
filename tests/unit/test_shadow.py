import json
import urllib.error

import pytest

from memory_tool.shadow import connect, get, put, search, status, sync
from memory_tool.shadow_mcp import dispatch


def record(source_id="alpha", content="中文记忆", space="default"):
    return {"id": source_id, "space_id": space, "title": "test", "content": content,
            "lifecycle_state": "active", "is_latest": True, "metadata": {"project": "alpha"}}


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "private/shadow.db")
    yield connection
    connection.close()


def test_revision_idempotency_and_scope(conn):
    assert put(conn, "default", record())
    assert not put(conn, "default", record())
    assert not put(conn, "default", {**record(), "time": "2 minutes ago"})
    assert put(conn, "default", record(content="新版记忆"))
    assert conn.execute("SELECT count(*) FROM revisions").fetchone()[0] == 2
    assert not search(conn, "中文")
    assert len(search(conn, "新版", project="alpha")) == 1
    assert not search(conn, "新版", project="beta")
    assert get(conn, "alpha", "other") is None
    with pytest.raises(ValueError):
        put(conn, "default", record(space="other"))


def test_sync_failure_keeps_last_good_and_reports_error(conn):
    put(conn, "default", record())

    class Offline:
        def ids(self, space):
            raise OSError("offline")

    report = sync(conn, Offline())
    assert report["errors"]
    assert get(conn, "alpha")["memory"]["content"] == "中文记忆"
    assert status(conn)["sync"]["errors"]


def test_manifest_absence_requires_canonical_confirmation(conn):
    put(conn, "default", record())

    class Source:
        missing = False

        def ids(self, space):
            return set()

        def get(self, source_id, space):
            if self.missing:
                raise urllib.error.HTTPError("redacted", 404, "not found", {}, None)
            return record()

    source = Source()
    sync(conn, source)
    assert get(conn, "alpha")
    source.missing = True
    assert sync(conn, source)["missing"] == 1
    assert get(conn, "alpha") is None
    assert conn.execute("SELECT count(*) FROM revisions").fetchone()[0] == 1


def test_sync_prioritizes_unhydrated_and_validates_identity(conn):
    put(conn, "default", record())

    class Source:
        def ids(self, space):
            return {"alpha", "beta"}

        def get(self, source_id, space):
            return record(source_id)

    report = sync(conn, Source(), batch=1)
    assert report["unhydrated"] == 0
    assert get(conn, "beta")


def test_mcp_protocol_and_read_only_surface(conn):
    put(conn, "default", record())
    base = {"jsonrpc": "2.0", "id": 1}
    result = dispatch(conn, {**base, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
    assert result["result"]["protocolVersion"] == "2025-03-26"
    assert dispatch(conn, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = dispatch(conn, {**base, "method": "tools/list"})["result"]["tools"]
    assert len(tools) == 3
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools)
    result = dispatch(conn, {**base, "method": "tools/call", "params": {
        "name": "shadow_search", "arguments": {"query": "中文"}}})
    assert json.loads(result["result"]["content"][0]["text"])[0]["source_id"] == "alpha"
    result = dispatch(conn, {**base, "method": "tools/call", "params": {
        "name": "shadow_search", "arguments": {"query": "中文", "space": "other"}}})
    assert result["error"]["code"] == -32602


def test_failed_records_do_not_starve_other_records(conn):
    class Source:
        def ids(self, space):
            return {"alpha", "beta"}

        def get(self, source_id, space):
            if source_id == "alpha":
                raise OSError("unavailable")
            return record(source_id)

    assert sync(conn, Source(), batch=1)["errors"]
    assert sync(conn, Source(), batch=1)["checked"] == 1
    assert get(conn, "beta")
    assert status(conn)["unresolved_errors"] == 1


# --- search index (trigram FTS5) ---------------------------------------------
# The index is an accelerator, not a second source of truth: every test here
# checks that the *documented* substring semantics survive it.

def indexed_record(source_id, title, content, project="alpha"):
    return {"id": source_id, "space_id": "default", "title": title, "content": content,
            "lifecycle_state": "active", "is_latest": True, "metadata": {"project": project}}


def test_index_is_built_on_put_and_reindex_is_idempotent(conn):
    from memory_tool.shadow import fts_docs, reindex
    put(conn, "default", indexed_record("a", "retry policy", "exponential backoff with jitter"))
    assert fts_docs(conn) == 1
    assert len(search(conn, "backoff")) == 1
    assert reindex(conn) == 1
    assert fts_docs(conn) == 1
    assert len(search(conn, "backoff")) == 1
    # an update rewrites the index row instead of leaving the old text behind
    put(conn, "default", indexed_record("a", "retry policy", "now uses a fixed 3s delay"))
    assert fts_docs(conn) == 1
    assert not search(conn, "jitter")
    assert len(search(conn, "fixed 3s")) == 1


def test_mid_word_substring_search_uses_the_index(conn):
    from memory_tool.shadow import search_detailed
    put(conn, "default", indexed_record("a", "auth", "the authentication flow retries twice"))
    rows, meta = search_detailed(conn, "uthentication")
    assert [r["source_id"] for r in rows] == ["a"], rows
    assert meta["mode"] == "index+filter", meta
    assert meta["candidates"] >= 1, meta


def test_short_terms_fall_back_to_scan(conn):
    from memory_tool.shadow import search_detailed
    put(conn, "default", indexed_record("a", "中文记忆", "保留了完整的原文"))
    rows, meta = search_detailed(conn, "中")
    assert [r["source_id"] for r in rows] == ["a"], rows
    assert meta["mode"] == "scan", meta


def test_unbuilt_index_falls_back_and_is_reported(conn):
    """The silent-empty trap: an index that was never built must not look like
    'no matching memories'."""
    put(conn, "default", indexed_record("a", "retry", "exponential backoff"))
    conn.execute("DELETE FROM records_fts")
    conn.commit()
    assert status(conn)["search_index"]["state"] == "not_built"
    assert status(conn)["search_index"]["hint"]
    assert [r["source_id"] for r in search(conn, "backoff")] == ["a"], "fallback scan lost recall"


def test_index_preserves_and_and_project_semantics(conn):
    from memory_tool.shadow import search_detailed
    put(conn, "default", indexed_record("a", "retry policy", "exponential backoff", project="alpha"))
    put(conn, "default", indexed_record("b", "retry policy", "exponential backoff", project="beta"))
    put(conn, "default", indexed_record("c", "retry policy", "no delay at all", project="alpha"))
    rows, meta = search_detailed(conn, "retry backoff")
    assert sorted(r["source_id"] for r in rows) == ["a", "b"], rows
    assert meta["mode"] == "index+filter", meta
    # terms may land in different fields (title vs content) — the index holds
    # both in one column, so AND still means "somewhere in this record"
    assert [r["source_id"] for r in search(conn, "retry delay")] == ["c"]
    assert [r["source_id"] for r in search(conn, "retry backoff", project="alpha")] == ["a"]
