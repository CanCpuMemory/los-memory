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
    assert [t["name"] for t in tools] == ["shadow_search", "shadow_get", "shadow_status",
                                         "shadow_compare"]
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools), \
        "every shadow tool must be read-only, including the compare instrumentation"
    result = dispatch(conn, {**base, "method": "tools/call", "params": {
        "name": "shadow_search", "arguments": {"query": "中文"}}})
    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload["results"][0]["source_id"] == "alpha"
    assert payload["meta"]["mode"] in ("index+filter", "scan")
    result = dispatch(conn, {**base, "method": "tools/call", "params": {
        "name": "shadow_search", "arguments": {"query": "中文", "space": "other"}}})
    assert result["error"]["code"] == -32602
    result = dispatch(conn, {**base, "method": "tools/call", "params": {
        "name": "shadow_search", "arguments": {"query": "中文", "project": "unassigned"}}})
    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload["meta"]["coverage"]["records"] == 1
    assert payload["meta"]["coverage"]["project_assigned"] == 1, "explicit metadata.project is honoured"


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


# --- contract facets: kind / claim_status / project / source_app ---------------
# Nowledge has no project field and its own vocabulary differs from the design
# docs, so the mapping is explicit. These tests pin the conservative defaults:
# nothing is promoted into a stronger claim and nothing is guessed.

def canonical(source_id, **fields):
    record = {"id": source_id, "space_id": "default", "title": "t", "content": "c",
              "lifecycle_state": "active", "is_latest": True}
    record.update(fields)
    return record


def test_facets_preserve_unknown_values_instead_of_promoting_them(conn):
    from memory_tool.shadow import facets
    assert facets(canonical("a"))["kind"] == "unknown"
    # 1,350 of 2,048 real records carry no claim status; `asserted` would be a lie.
    assert facets(canonical("a"))["claim_status"] == "undeclared"
    assert facets(canonical("a", unit_type="decision", claim_status="proposed")) == {
        "kind": "decision", "claim_status": "proposed", "project": "unassigned",
        "source_app": "", "thread_source": "", "labels": []}


def test_project_comes_from_registry_then_declaration_then_unassigned(conn):
    from memory_tool.shadow import facets
    registered = canonical("a", label_ids=["label_cankey", "label_daily"],
                           metadata={"source_app": "kimi-code"})
    assert facets(registered)["project"] == "cankey"
    assert facets(registered)["source_app"] == "kimi-code"
    # An explicit declaration is honoured; a host app or thread id never becomes one.
    assert facets(canonical("b", metadata={"project": "alpha"}))["project"] == "alpha"
    assert facets(canonical("c", source_thread={"id": "codex-1", "source": "codex"},
                            metadata={"source_app": "codex"}))["project"] == "unassigned"


def test_two_character_cjk_uses_the_bigram_index(conn):
    """trigram cannot match 2 characters, so without bigrams this is a full scan."""
    from memory_tool.shadow import search_detailed
    put(conn, "default", indexed_record("a", "中文记忆", "保留了完整的原文"))
    rows, meta = search_detailed(conn, "记忆")
    assert [r["source_id"] for r in rows] == ["a"]
    assert meta["paths"]["记忆"] == "bigram", meta
    assert meta["mode"] == "index+filter", meta
    # A single CJK character has no index path and must be reported as a scan.
    rows, meta = search_detailed(conn, "中")
    assert [r["source_id"] for r in rows] == ["a"]
    assert meta["mode"] == "scan", meta
    assert meta["scan_terms"] == ["中"], meta


def test_project_filter_reports_its_own_coverage(conn):
    """An empty filtered result must not read as 'this project has no memories'."""
    from memory_tool.shadow import search_detailed
    put(conn, "default", indexed_record("a", "shared wording", "body", project=None))
    conn.execute("UPDATE record_facets SET project='cankey' WHERE source_id='a'")
    put(conn, "default", indexed_record("b", "shared wording", "body", project=None))
    rows, meta = search_detailed(conn, "shared", project="cankey")
    assert [r["source_id"] for r in rows] == ["a"]
    assert meta["coverage"]["project_assigned"] == 1
    assert meta["coverage"]["records"] == 2
    assert meta["coverage"]["project_coverage"] == 0.5
    rows, meta = search_detailed(conn, "shared", project="nothing-registered")
    assert rows == []
    assert meta["coverage"]["project_coverage"] == 0.5, "empty result still explains itself"


def test_kind_filter_is_served_by_facets_not_by_metadata(conn):
    put(conn, "default", canonical("a", unit_type="decision", title="pick one", content="we chose x"))
    put(conn, "default", canonical("b", unit_type="fact", title="pick one", content="we chose y"))
    assert [r["source_id"] for r in search(conn, "pick one", kind="decision")] == ["a"]
    assert [r["source_id"] for r in search(conn, "pick one", kind="fact")] == ["b"]
    assert sorted(r["source_id"] for r in search(conn, "pick one")) == ["a", "b"]


def test_reindex_rebuilds_facets_and_bigrams(conn):
    from memory_tool.shadow import coverage, reindex
    put(conn, "default", canonical("a", unit_type="learning", title="中文记忆", content="正文",
                                    label_ids=["label_los"]))
    conn.execute("DELETE FROM record_facets")
    conn.execute("DELETE FROM record_labels")
    conn.execute("DELETE FROM cjk_bigrams")
    conn.commit()
    assert coverage(conn)["project_assigned"] == 0
    assert reindex(conn) == 1
    assert coverage(conn)["project_assigned"] == 1
    assert [r["source_id"] for r in search(conn, "记忆")] == ["a"]


# --- metering: durable traffic ledger (P1 needs 24h of real numbers) -----------

class Metered:
    def __init__(self, records):
        self.records = records
        self.requests = 0
        self.bytes = 0

    def ids(self, space):
        self.requests += 1
        self.bytes += 100
        return set(self.records)

    def get(self, source_id, space):
        self.requests += 1
        self.bytes += 200
        return self.records[source_id]


def test_metering_records_real_traffic_per_run(conn):
    report = sync(conn, Metered({"a": record("a")}))
    assert report["requests"] == 2 and report["bytes"] == 300
    assert report["duration"] >= 0
    metered = status(conn)["metering"]
    assert metered["runs"] == 1
    assert metered["bytes"] == 300 and metered["requests"] == 2
    assert status(conn)["error_ledger"]["size"] == 0


def test_error_ledger_survives_later_success(conn):
    """`attempts` is overwritten every rotation, so it cannot answer 'what failed'."""
    class Flaky:
        requests = 0
        bytes = 0

        def __init__(self):
            self.fail = True

        def ids(self, space):
            return {"alpha"}

        def get(self, source_id, space):
            if self.fail:
                raise OSError("unavailable")
            return record(source_id)

    client = Flaky()
    assert sync(conn, client)["errors"]
    assert status(conn)["error_ledger"]["size"] == 1
    client.fail = False
    sync(conn, client)
    assert status(conn)["error_ledger"]["size"] == 1, "history is not erased by a later success"
    assert status(conn)["unresolved_errors"] == 0, "latest attempt succeeded"


def test_summary_digest_detects_content_drift(conn):
    from memory_tool.shadow import summary
    put(conn, "default", record("a", content="one"))
    first = summary(conn)["identity_digest"]
    put(conn, "default", record("a", content="two"))
    assert summary(conn)["identity_digest"] != first
    assert summary(conn)["records"] == 1


def test_manifest_cache_decouples_listing_from_refresh(conn):
    """The listing is the traffic; the per-ID refresh is the freshness. A cache
    window lets the refresh keep its cadence while the listing runs hourly."""
    client = Metered({"a": record("a")})
    first = sync(conn, client, manifest_cache_seconds=3600)
    assert first["manifest_cached"] is False
    assert (first["requests"], first["bytes"]) == (2, 300), "1 listing + 1 get"
    second = sync(conn, client, manifest_cache_seconds=3600)
    assert second["manifest_cached"] is True
    assert second["manifest_count"] == 1
    assert (second["requests"], second["bytes"]) == (1, 200), \
        "listing reused: this run costs one get, not listing+get"
    # cache 0 is the unchanged production behaviour: list every run
    third = sync(conn, client, manifest_cache_seconds=0)
    assert third["manifest_cached"] is False
    assert third["requests"] == 2, "listing + get"


def test_zero_hit_query_trusts_a_built_index_instead_of_rescanning(conn):
    """Scanning on every empty result costs latency and hides which path ran."""
    from memory_tool.shadow import search_detailed
    put(conn, "default", indexed_record("a", "retry policy", "exponential backoff"))
    rows, meta = search_detailed(conn, "nonexistentterm")
    assert rows == []
    assert meta["mode"] == "index+filter", meta
    assert meta["candidates"] == 0, meta
    assert meta["scan_terms"] is None


def test_unbuilt_bigram_index_falls_back_to_scan_not_to_a_wrong_empty(conn):
    """The silent-empty trap, one level down: an upgrade that added the bigram
    table must not answer 2-character CJK queries as 'no memories' before the
    backfill has run."""
    from memory_tool.shadow import search_detailed
    put(conn, "default", indexed_record("a", "中文记忆", "保留了完整的原文"))
    conn.execute("DELETE FROM cjk_bigrams")
    conn.commit()
    rows, meta = search_detailed(conn, "记忆")
    assert [r["source_id"] for r in rows] == ["a"], "recall must survive the missing index"
    assert meta["mode"] == "scan", meta
    assert meta["scan_terms"] == ["记忆"], meta
    assert meta["usable"]["bigram"] is False


def test_rotate_log_truncates_in_place_so_the_live_writer_keeps_working(conn, tmp_path):
    """launchd holds the log fd open across runs: renaming would leave it
    appending to the archived inode and the visible log frozen."""
    from memory_tool.shadow import rotate_log
    log = tmp_path / "sync.out.log"
    log.write_bytes(b"x" * 100)
    old_inode = log.stat().st_ino
    quiet = rotate_log(log, max_bytes=1000, keep=3)
    assert quiet["rotated"] is False and quiet["bytes"] == 100
    assert not list(tmp_path.glob("sync.out.log.*")), "must not archive below the cap"

    result = rotate_log(log, max_bytes=50, keep=3)
    assert result["rotated"] is True
    assert log.stat().st_size == 0, "live file is emptied in place"
    assert log.stat().st_ino == old_inode, "same inode: the writer's fd still points at it"
    assert (tmp_path / result["archive"]).stat().st_size == 100

    for _ in range(4):
        log.write_bytes(b"y" * 100)
        rotate_log(log, max_bytes=50, keep=3)
    assert len(list(tmp_path.glob("sync.out.log.*"))) == 3, "archives are bounded"


def test_projected_traffic_uses_intervals_not_run_count(conn):
    """N runs span N-1 intervals; dividing bytes by the span overstates the rate
    by N/(N-1), which is a factor of two when only two runs are in the window."""
    from memory_tool.shadow import metering
    client = Metered({"a": record("a")})
    sync(conn, client)                       # 300 bytes
    base = conn.execute("SELECT started_at FROM sync_runs").fetchone()[0]
    conn.execute("UPDATE sync_runs SET started_at=?", (base - 300,))
    conn.commit()
    sync(conn, client)                       # +300 bytes, one true 300 s interval
    metered = metering(conn, hours=24)
    assert metered["runs"] == 2
    assert metered["bytes"] == 600
    assert metered["observed_interval_seconds"] == 300
    assert metered["projected_runs_per_day"] == 288
    # 300 B/run * 288 runs. Tolerance absorbs sub-second jitter in the timestamps;
    # the bug this pins would show up as ~172800 (a factor of two).
    assert abs(metered["projected_bytes_per_day"] - 86400) < 500


# --- multi-project records: a real property, not a label-order coin flip --------

def test_multi_project_record_is_marked_not_guessed(conn):
    """19 of 2,048 real records carry two registered project labels. Picking
    whichever came first in `label_ids` is guessing; the order is not meaningful."""
    from memory_tool.shadow import facets
    both = canonical("a", label_ids=["label_cantool", "label_lot2extension"])
    assert facets(both)["project"] == "multi"
    single = canonical("b", label_ids=["label_cantool", "label_architecture"])
    assert facets(single)["project"] == "cantool", "a non-project label must not create ambiguity"
    # Reordered labels must not change the answer — that was the bug.
    assert facets(canonical("c", label_ids=["label_lot2extension", "label_cantool"]))["project"] == "multi"


def test_multi_project_record_is_findable_under_each_of_its_projects(conn):
    """Marking it `multi` must not cost recall: the filter is label membership."""
    from memory_tool.shadow import search_detailed
    put(conn, "default", canonical("multi1", title="shared body words",
                                   content="shared body words",
                                   label_ids=["label_cantool", "label_lot2extension"]))
    put(conn, "default", canonical("onlytool", title="shared body words",
                                   content="shared body words",
                                   label_ids=["label_cantool"]))
    assert sorted(r["source_id"] for r in search(conn, "shared body", project="cantool")) == \
        ["multi1", "onlytool"]
    assert [r["source_id"] for r in search(conn, "shared body", project="lot2extension")] == ["multi1"]
    coverage = search_detailed(conn, "shared body", project="cantool")[1]["coverage"]
    assert coverage["project_multi"] == 1
    assert coverage["project_assigned"] == 1, "only the unambiguous record counts as assigned"


# --- divergence instrumentation (phase 1 of the read-path rollover) -------------

def test_query_classifier_is_deterministic_and_explains_itself():
    from memory_tool.shadow import classify_query
    literal = ["SQLITE_IOERR_TRUNCATE", "6d24e90a-de0d-4eb5-9a9d-1ec801ad6cf2",
               "~/.local/state/agent-history/queue.json", "0.10.86", "db.syncthing.sqlite3",
               "mcp-los-memory-shadow", "sha256:c6ac069fa1cefe2d552c5be0"]
    short_cjk = ["冷层 热数据", "磁盘满 阶段化", "影子库"]
    conceptual = ["有些功能明明做完了却像没接上是什么问题", "对比两份媒体清单时怎么让结论站得住"]
    for query in literal:
        assert classify_query(query) == "literal_anchor", query
    for query in short_cjk:
        assert classify_query(query) == "short_cjk", query
    for query in conceptual:
        assert classify_query(query) == "conceptual", query
    # Same input, same class — routing must not depend on anything else.
    assert classify_query("冷层 热数据") == classify_query("冷层 热数据")


def test_compare_records_the_shadow_side_without_calling_the_primary(conn, tmp_path):
    """The primary costs ~13 s/query, so the tool must not call it inline."""
    from memory_tool.shadow import compare_paths, record_compare
    put(conn, "default", indexed_record("a", "冷层 与热数据", "冻结副本迁移"))
    payload = record_compare(conn, "冷层 热数据", limit=5, state_dir=tmp_path)
    assert payload["class"] == "short_cjk"
    assert payload["ids"] == ["a"]
    assert payload["latency_ms"] < 2000, "the shadow side must be answered immediately"
    pending = [json.loads(line) for line in compare_paths(tmp_path)["pending"].read_text().splitlines()]
    assert len(pending) == 1
    assert pending[0]["query"] == "冷层 热数据" and pending[0]["shadow_ids"] == ["a"]


def test_mcp_compare_queues_and_says_answer_from_the_primary(conn):
    from memory_tool.shadow_mcp import dispatch
    put(conn, "default", indexed_record("a", "冷层 与热数据", "冻结副本迁移"))
    result = dispatch(conn, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "shadow_compare", "arguments": {"query": "冷层 热数据"}}})
    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload["comparison"]["queued"] is True
    assert "answer from the primary" in payload["comparison"]["note"]
    assert payload["results"][0]["source_id"] == "a"


def test_drain_writes_divergence_and_is_idempotent(conn, tmp_path, monkeypatch):
    from memory_tool import shadow
    put(conn, "default", indexed_record("a", "冷层 与热数据", "冻结副本迁移"))
    shadow.record_compare(conn, "冷层 热数据", limit=5, state_dir=tmp_path)
    calls = []

    class FakeRun:
        returncode = 0
        stderr = ""
        stdout = json.dumps({"memories": [{"id": "a"}, {"id": "other"}]})

    def fake_subprocess(*args, **kwargs):
        calls.append(args[0])
        return FakeRun()

    monkeypatch.setattr(shadow.subprocess, "run", fake_subprocess)
    first = shadow.drain_compare(state_dir=tmp_path, command="nmem")
    assert first["resolved"] == 1 and not first["errors"]
    assert len(calls) == 1, "one primary lookup for one query"
    results = [json.loads(l) for l in shadow.compare_paths(tmp_path)["results"].read_text().splitlines()]
    assert results[0]["overlap"] == ["a"] and results[0]["nowledge_only"] == ["other"]
    assert results[0]["jaccard"] == 0.5
    # Second drain: nothing pending, and the cache prevents another lookup.
    second = shadow.drain_compare(state_dir=tmp_path, command="nmem")
    assert second["pending"] == 0
    assert len(calls) == 1, "the query cache must prevent repeat lookups"

    report = shadow.compare_report(state_dir=tmp_path)
    assert report["records"] == 1
    bucket = report["by_class"]["short_cjk"]
    assert bucket["n"] == 1 and bucket["mean_jaccard"] == 0.5
    assert bucket["shadow_only_queries"] == 0 and bucket["nowledge_only_queries"] == 1


def test_drain_caps_uncached_lookups_and_keeps_the_rest_queued(conn, tmp_path, monkeypatch):
    """Each uncached query costs ~13 s, so a drain must be bounded."""
    from memory_tool import shadow
    for index in range(3):
        put(conn, "default", indexed_record(f"q{index}", f"term{index} body", "x"))
        shadow.record_compare(conn, f"term{index}", limit=5, state_dir=tmp_path)

    class FakeRun:
        returncode = 0
        stderr = ""
        stdout = json.dumps({"memories": [{"id": "q0"}]})

    monkeypatch.setattr(shadow.subprocess, "run", lambda *a, **k: FakeRun())
    out = shadow.drain_compare(state_dir=tmp_path, max_queries=2)
    assert out["resolved"] == 2 and out["deferred"] == 1
    still_queued = [json.loads(l) for l in
                    shadow.compare_paths(tmp_path)["pending"].read_text().splitlines() if l.strip()]
    assert len(still_queued) == 1, "the deferred query stays queued for the next drain"
    assert still_queued[0]["query"] == "term0", "and it is the one the cap skipped"


def test_drain_reports_a_primary_failure_instead_of_faking_agreement(conn, tmp_path, monkeypatch):
    from memory_tool import shadow
    put(conn, "default", indexed_record("a", "冷层", "x"))
    shadow.record_compare(conn, "冷层", limit=5, state_dir=tmp_path)

    class Failing:
        returncode = 1
        stdout = ""
        stderr = "connection refused"

    monkeypatch.setattr(shadow.subprocess, "run", lambda *a, **k: Failing())
    out = shadow.drain_compare(state_dir=tmp_path, command="nmem")
    assert out["resolved"] == 0 and out["errors"], "a failed primary lookup must be visible"
    assert not shadow.compare_paths(tmp_path)["results"].exists(), \
        "no divergence record may be written from a failed lookup"


def test_drain_finds_the_primary_cli_under_a_minimal_path(monkeypatch, tmp_path):
    """launchd starts jobs with PATH=/usr/bin:/bin:/usr/sbin:/sbin, so a bare
    `nmem` failed with FileNotFoundError in the scheduled drain. Resolving the
    known install locations is what makes the job work unattended."""
    from memory_tool import shadow
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(shadow.shutil, "which", lambda name: None)
    fake_home = tmp_path / "home"
    (fake_home / ".local/bin").mkdir(parents=True)
    binary = fake_home / ".local/bin/nmem"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(shadow.Path, "home", classmethod(lambda cls: fake_home))
    assert shadow._resolve_command("nmem") == str(binary)
    # An explicit path is never second-guessed.
    assert shadow._resolve_command("/custom/nmem") == "/custom/nmem"
