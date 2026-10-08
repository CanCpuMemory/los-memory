"""Private, read-only Nowledge shadow and bounded canonical refresh."""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from .shadow_registry import MULTI, UNASSIGNED, labels_for, project_for
from .utils import like_pattern


DEFAULT_DB = Path.home() / ".local/share/los-memory-shadow/shadow.sqlite3"

# Trigram-indexed text cap per record. The index is a derived artefact; the
# authoritative text stays in records.snapshot.
FTS_TEXT_CAP = 32000
# The trigram tokenizer cannot match patterns shorter than 3 characters.
FTS_MIN_TERM = 3
# Error-ledger bound per space, and the metering window reported by status().
ERROR_LEDGER_CAP = 500
METERING_WINDOW_HOURS = 24
# How often a deactivated (404) record is re-probed. Deletions are discovered by
# the manifest anyway; this is only a safety net for an upstream restore, so it
# runs on a slow cycle instead of every rotation (which inflated `missing`).
TOMBSTONE_RECHECK_SECONDS = 24 * 3600

# CJK ranges whose 2-character terms trigram cannot serve (measured 2026-10-07
# on SQLite 3.53: trigram matches 迁移门 but not 记忆).
CJK_CHAR = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")


def fts_rowid(space, source_id):
    """Stable rowid so an update is a primary-key delete, not a scan.

    records_fts keeps its own copy of the searchable text, so replacing a record
    means removing its old index row first; deriving the rowid from the record
    identity makes that O(1) instead of a scan over every indexed record.
    """
    digest = hashlib.sha256(f"{space}\x00{source_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def fts_text(record):
    """Index title + content — the same two fields the substring search reads."""
    title = str(record.get("title") or "")
    content = str(record.get("content") or "")
    return (title + "\n" + content)[:FTS_TEXT_CAP]


def connect(path):
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    os.chmod(path, 0o600)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS records (
            space TEXT NOT NULL, source_id TEXT NOT NULL,
            snapshot TEXT NOT NULL, digest TEXT NOT NULL,
            verified_at REAL NOT NULL, active INTEGER NOT NULL,
            PRIMARY KEY(space, source_id));
        CREATE TABLE IF NOT EXISTS revisions (
            space TEXT NOT NULL, source_id TEXT NOT NULL,
            digest TEXT NOT NULL, snapshot TEXT NOT NULL, received_at REAL NOT NULL,
            PRIMARY KEY(space, source_id, digest));
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (
            space TEXT NOT NULL, source_id TEXT NOT NULL, attempted_at REAL NOT NULL,
            error TEXT, PRIMARY KEY(space, source_id));
        -- Search index. `trigram` is what makes FTS5 usable here: the shadow's
        -- documented semantics are *substring* matching (the existing tests pin
        -- it), and trigram matches substrings inside a token, not just whole
        -- tokens. It is a derived structure: records stays authoritative and
        -- `reindex` can always rebuild it.
        CREATE VIRTUAL TABLE IF NOT EXISTS records_fts
            USING fts5(source_id UNINDEXED, space UNINDEXED, text, tokenize='trigram');
        -- Derived contract facets. Nowledge has no project field (0 of 2,048
        -- records carry metadata.project) and its `unit_type` / `claim_status`
        -- are not the `kind` / asserted-vs-proposed vocabulary the design docs
        -- assume. Mapping is explicit and lossless-enough to audit; see
        -- shadow_registry.py and docs/design/nowledge-replacement-readiness.md
        -- §5.2. Derived, so `reindex` rebuilds it from records.
        CREATE TABLE IF NOT EXISTS record_facets (
            space TEXT NOT NULL, source_id TEXT NOT NULL,
            kind TEXT NOT NULL, claim_status TEXT NOT NULL, project TEXT NOT NULL,
            source_app TEXT NOT NULL, thread_source TEXT NOT NULL,
            PRIMARY KEY(space, source_id));
        CREATE INDEX IF NOT EXISTS record_facets_project ON record_facets(space, project);
        CREATE INDEX IF NOT EXISTS record_facets_kind ON record_facets(space, kind);
        CREATE TABLE IF NOT EXISTS record_labels (
            label TEXT NOT NULL, space TEXT NOT NULL, source_id TEXT NOT NULL,
            PRIMARY KEY(label, space, source_id)) WITHOUT ROWID;
        -- 2-character CJK auxiliary index. trigram cannot serve these terms, and
        -- falling back to a full scan for them is exactly what keeps the p95 of
        -- a Chinese query at scan latency. Bigrams cover every adjacent pair of
        -- a CJK run, so an indexed lookup is exact for 2-character terms.
        CREATE TABLE IF NOT EXISTS cjk_bigrams (
            term TEXT NOT NULL, space TEXT NOT NULL, source_id TEXT NOT NULL,
            PRIMARY KEY(term, space, source_id)) WITHOUT ROWID;
        -- Existence probe for "is the bigram index built for this space" must not
        -- scan ~150k index entries on every query.
        CREATE INDEX IF NOT EXISTS cjk_bigrams_space ON cjk_bigrams(space, term);
        -- Durable metering. `state['sync:<space>']` only ever holds the latest
        -- run, so P1 ("collect 24h of real traffic") had no data to read.
        CREATE TABLE IF NOT EXISTS sync_runs (
            started_at REAL PRIMARY KEY, space TEXT NOT NULL, checked INTEGER NOT NULL,
            changed INTEGER NOT NULL, missing INTEGER NOT NULL, errors INTEGER NOT NULL,
            requests INTEGER NOT NULL, bytes INTEGER NOT NULL, duration REAL NOT NULL);
        -- attempts is keyed by record and is overwritten every rotation, so it
        -- can only ever answer "did the latest attempt fail". This is the bounded
        -- audit trail: what failed, when, and how often.
        CREATE TABLE IF NOT EXISTS sync_errors (
            space TEXT NOT NULL, source_id TEXT NOT NULL,
            failed_at REAL NOT NULL, error TEXT NOT NULL);
    """)
    return conn


def cjk_bigrams(text):
    """Every adjacent 2-character CJK pair in ``text``.

    Runs shorter than 2 characters contribute nothing: a single CJK character
    has no index path at all and must be reported as a scan, not answered by an
    index that cannot represent it.
    """
    found = set()
    run = []
    for character in text:
        if CJK_CHAR.match(character):
            run.append(character)
            continue
        if len(run) >= 2:
            found.update("".join(run[index:index + 2]) for index in range(len(run) - 1))
        run = []
    if len(run) >= 2:
        found.update("".join(run[index:index + 2]) for index in range(len(run) - 1))
    return found


def facets(record):
    """Map a canonical record onto the internal contract, conservatively.

    Precedence for ``project``: a registered label wins, then an explicit
    ``metadata.project`` declaration, else ``unassigned``. Nothing is inferred
    from directory names, thread titles or the host app — the architecture design
    forbids guessing a project, and guessing is how cross-project isolation
    silently breaks. A record carrying several registered projects becomes
    ``multi`` rather than being resolved by label order.
    """
    metadata = record.get("metadata") or {}
    labels = [item for item in (record.get("label_ids") or []) if isinstance(item, str)]
    if not labels:
        labels = [item for item in (metadata.get("label_ids") or []) if isinstance(item, str)]
    thread = record.get("source_thread")
    thread_source = metadata.get("thread_source") or (
        thread.get("source") if isinstance(thread, dict) else "") or ""
    source_app = metadata.get("source_app") or ""
    project = project_for(labels)
    if project == UNASSIGNED:
        declared = metadata.get("project")
        if isinstance(declared, str) and declared.strip():
            project = declared.strip()
    claim = record.get("claim_status")
    if claim is None or claim == "":
        # 1,350 of 2,048 records carry no claim status. Defaulting to `asserted`
        # would upgrade unlabelled records into verified facts.
        claim = "undeclared"
    return {"kind": record.get("unit_type") or "unknown", "claim_status": claim,
            "project": project, "source_app": source_app, "thread_source": thread_source,
            "labels": labels}


def index_record(conn, space, record):
    """(Re)write one record's derived rows. Caller owns the transaction.

    Covers the trigram search index plus the contract facets, label rows and CJK
    bigrams, so `put` and `reindex` cannot drift apart.
    """
    rowid = fts_rowid(space, record["id"])
    conn.execute("DELETE FROM records_fts WHERE rowid=?", (rowid,))
    conn.execute("INSERT INTO records_fts(rowid, source_id, space, text) VALUES(?,?,?,?)",
                 (rowid, record["id"], space, fts_text(record)))
    derived = facets(record)
    conn.execute(
        "INSERT INTO record_facets VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(space,source_id) DO UPDATE SET kind=excluded.kind,"
        "claim_status=excluded.claim_status,project=excluded.project,"
        "source_app=excluded.source_app,thread_source=excluded.thread_source",
        (space, record["id"], derived["kind"], derived["claim_status"], derived["project"],
         derived["source_app"], derived["thread_source"]))
    conn.execute("DELETE FROM record_labels WHERE space=? AND source_id=?", (space, record["id"]))
    conn.executemany("INSERT OR IGNORE INTO record_labels VALUES(?,?,?)",
                     [(label, space, record["id"]) for label in derived["labels"]])
    conn.execute("DELETE FROM cjk_bigrams WHERE space=? AND source_id=?", (space, record["id"]))
    terms = cjk_bigrams(fts_text(record))
    conn.executemany("INSERT OR IGNORE INTO cjk_bigrams VALUES(?,?,?)",
                     [(term, space, record["id"]) for term in terms])
    return derived


def reindex(conn, space=None):
    """Rebuild every derived structure from records. Idempotent; the migration
    path for databases written before the index and facets existed."""
    where, params = ("WHERE space=?", (space,)) if space else ("", ())
    rows = conn.execute(f"SELECT space, snapshot FROM records {where}", params).fetchall()
    with conn:
        if space:
            for table in ("records_fts", "record_facets", "record_labels", "cjk_bigrams"):
                conn.execute(f"DELETE FROM {table} WHERE space=?", (space,))
        else:
            for table in ("records_fts", "record_facets", "record_labels", "cjk_bigrams"):
                conn.execute(f"DELETE FROM {table}")
        for row in rows:
            record = json.loads(row["snapshot"])
            index_record(conn, row["space"], record)
    return len(rows)


def fts_docs(conn, space=None):
    """Documents the index actually holds.

    Not `count(*) FROM records_fts`: for external-content tables that reads the
    content table, and an empty index then looks full (measured on the session
    projection). `records_fts_docsize` is the truthful count, and the caller uses
    it to refuse to report \"no matches\" over an index that was never built.
    """
    try:
        if space:
            return conn.execute("SELECT count(*) FROM records_fts_docsize d JOIN records_fts f "
                                "ON f.rowid = d.id WHERE f.space=?", (space,)).fetchone()[0]
        return conn.execute("SELECT count(*) FROM records_fts_docsize").fetchone()[0]
    except sqlite3.Error:
        return None


def _fts_query(terms):
    """AND of quoted phrases — with the trigram tokenizer a phrase is a substring
    pattern, which is exactly the semantics the scan implements."""
    return " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _fts_candidates(conn, terms, space):
    sql = "SELECT f.source_id FROM records_fts f JOIN records r ON r.space=f.space AND r.source_id=f.source_id WHERE f.records_fts MATCH ? AND r.space=? AND r.active=1"
    rows = conn.execute(sql, (_fts_query(terms), space)).fetchall()
    return {row["source_id"] for row in rows}


def _bigram_candidates(conn, terms, space):
    """Exact candidate set for 2-character CJK terms, which trigram cannot serve."""
    candidates = None
    for term in terms:
        rows = {row[0] for row in conn.execute(
            "SELECT source_id FROM cjk_bigrams WHERE term=? AND space=?", (term, space))}
        candidates = rows if candidates is None else candidates & rows
    return candidates or set()


def bigram_docs(conn, space):
    """Whether the bigram index holds any row for this space.

    Existence, not count: counting ~150k index entries per query would cost more
    than the scan it is meant to avoid. This is what lets an empty result be
    trusted (the index really has nothing) instead of falling back to a full scan
    on every zero-hit query — while still refusing to answer from an index that
    was never built.
    """
    try:
        return conn.execute("SELECT 1 FROM cjk_bigrams WHERE space=? LIMIT 1", (space,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _term_path(term):
    """Which index path can serve this term, or ``scan`` when none can."""
    if CJK_CHAR.search(term):
        if len(term) >= FTS_MIN_TERM:
            return "trigram"
        return "bigram" if len(term) == 2 else "scan"
    return "trigram" if len(term) >= FTS_MIN_TERM else "scan"


def coverage(conn, space="default"):
    """How much of the corpus actually carries each contract dimension.

    Reported by status() and attached to any filtered search. Without it an empty
    project-filtered result reads as "this project has no memories" instead of
    "a project is only assignable on the labelled subset".
    """
    total = conn.execute("SELECT count(*) FROM records WHERE space=? AND active=1",
                         (space,)).fetchone()[0]
    if not total:
        return {"records": 0, "project_assigned": 0, "project_unassigned": 0, "project_multi": 0,
                "claim_undeclared": 0, "kind_unknown": 0, "project_coverage": 0.0}
    row = conn.execute(
        "SELECT sum(f.project NOT IN ('unassigned','multi')) assigned, "
        "sum(f.project='multi') multi, sum(f.claim_status='undeclared') undeclared, "
        "sum(f.kind='unknown') unknown FROM record_facets f JOIN records r "
        "ON r.space=f.space AND r.source_id=f.source_id WHERE f.space=? AND r.active=1",
        (space,)).fetchone()
    assigned = row["assigned"] or 0
    return {"records": total, "project_assigned": assigned,
            "project_unassigned": total - assigned - (row["multi"] or 0),
            "project_multi": row["multi"] or 0,
            "claim_undeclared": row["undeclared"] or 0,
            "kind_unknown": row["unknown"] or 0, "project_coverage": round(assigned / total, 4)}


def search_detailed(conn, query, limit=10, project=None, kind=None, space="default"):
    """Substring search, accelerated by the derived indexes.

    Semantics are unchanged and the Python filter below stays authoritative: the
    indexes only narrow *which* records get loaded and substring-checked, so
    recall can never be worse than a full scan. Index path is chosen per term:
    trigram at >= 3 characters, CJK bigrams at exactly 2, and a scan for anything
    the indexes cannot represent. Narrowing by a subset of the terms is always
    safe, because every true match must match each term.

    Returns (results, meta). meta names the path each term took and reports the
    coverage behind any filter, so "no matches" is distinguishable from "this
    filter can only ever match a labelled subset".
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError("query must contain 1..500 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
    terms = query.casefold().split()
    paths = {term: _term_path(term) for term in terms}
    indexed = fts_docs(conn, space)
    usable = {"trigram": bool(indexed), "bigram": bigram_docs(conn, space)}

    meta = {"mode": "scan", "candidates": None, "indexed": indexed, "paths": paths,
            "usable": usable, "filters": {"project": project, "kind": kind}, "scan_terms": None}
    source_filter = None
    # A term is *covered* only when the index that can serve it is actually built.
    # Covered terms alone are enough to trust an empty result; a single uncovered
    # term means absence cannot be proven from the index, so scan for it.
    tri_terms = [term for term in terms if paths[term] == "trigram" and usable["trigram"]]
    big_terms = [term for term in terms if paths[term] == "bigram" and usable["bigram"]]
    uncovered = [term for term in terms
                 if paths[term] == "scan"
                 or (paths[term] == "trigram" and not usable["trigram"])
                 or (paths[term] == "bigram" and not usable["bigram"])]
    if tri_terms or big_terms or not uncovered:
        try:
            candidates = _fts_candidates(conn, tri_terms, space) if tri_terms else None
            if big_terms:
                found = _bigram_candidates(conn, big_terms, space)
                candidates = found if candidates is None else candidates & found
            source_filter = candidates if candidates is not None else set()
            meta["mode"] = "index+filter"
            meta["candidates"] = len(source_filter)
        except sqlite3.Error as exc:  # index unusable -> scan, never fail the query
            meta["index_error"] = str(exc)
            source_filter = None
    meta["scan_terms"] = uncovered or None

    found = []
    if source_filter is None or source_filter:
        sql = ("SELECT r.* FROM records r LEFT JOIN record_facets f "
               "ON f.space=r.space AND f.source_id=r.source_id WHERE r.space=? AND r.active=1")
        params = [space]
        if project is not None:
            # A project filter is set membership, not equality on one projected
            # column: a record labelled both cantool and lot2extension must be
            # findable under either. Records that declared `metadata.project`
            # without a registered label are still matched by equality.
            registered = labels_for(project)
            if registered:
                marks = ",".join("?" for _ in registered)
                sql += (" AND (EXISTS (SELECT 1 FROM record_labels rl WHERE rl.space=r.space "
                        f"AND rl.source_id=r.source_id AND rl.label IN ({marks})) OR f.project=?)")
                params.extend(registered + [project])
            else:
                sql += " AND f.project=?"
                params.append(project)
        if kind is not None:
            sql += " AND f.kind=?"
            params.append(kind)
        if source_filter is not None:
            placeholders = ",".join("?" * len(source_filter))
            sql += f" AND r.source_id IN ({placeholders})"
            params.extend(sorted(source_filter))

        for row in conn.execute(sql, params):
            record = json.loads(row["snapshot"])
            title = str(record.get("title") or "").casefold()
            body = str(record.get("content") or "").casefold()
            if all(term in title or term in body for term in terms):
                found.append((sum(term in title for term in terms), row["verified_at"], envelope(row)))
        found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if project is not None or kind is not None:
        meta["coverage"] = coverage(conn, space)
    return [item[2] for item in found[:limit]], meta


def search(conn, query, limit=10, project=None, kind=None, space="default"):
    return search_detailed(conn, query, limit=limit, project=project, kind=kind, space=space)[0]


def put(conn, space, record):
    if record.get("space_id") != space or not isinstance(record.get("id"), str):
        raise ValueError("Canonical record identity or space mismatch")
    snapshot = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    canonical = {key: value for key, value in record.items() if key != "time"}
    digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    active = record.get("lifecycle_state") == "active" and record.get("is_latest") is not False
    previous = conn.execute("SELECT digest FROM records WHERE space=? AND source_id=?",
                            (space, record["id"])).fetchone()
    now = time.time()
    with conn:
        conn.execute("INSERT OR IGNORE INTO revisions VALUES(?,?,?,?,?)",
                     (space, record["id"], digest, snapshot, now))
        conn.execute("INSERT INTO records VALUES(?,?,?,?,?,?) ON CONFLICT(space,source_id) "
                     "DO UPDATE SET snapshot=excluded.snapshot,digest=excluded.digest,"
                     "verified_at=excluded.verified_at,active=excluded.active",
                     (space, record["id"], snapshot, digest, now, int(active)))
        index_record(conn, space, record)
    return previous is None or previous["digest"] != digest


def mark_missing(conn, space, source_id):
    with conn:
        conn.execute("UPDATE records SET active=0,verified_at=? WHERE space=? AND source_id=?",
                     (time.time(), space, source_id))


def envelope(row):
    return {"shadow": True, "source": "nowledge-mem", "source_id": row["source_id"],
            "space_id": row["space"], "digest": row["digest"],
            "verified_at": row["verified_at"], "active": bool(row["active"]),
            "memory": json.loads(row["snapshot"])}


def get(conn, source_id, space="default"):
    row = conn.execute("SELECT * FROM records WHERE space=? AND source_id=? AND active=1",
                       (space, source_id)).fetchone()
    return envelope(row) if row else None


def metering(conn, space="default", hours=METERING_WINDOW_HOURS):
    """Real traffic over a rolling window.

    P1 requires 24 hours of measured traffic before a cadence change may claim an
    improvement; `state['sync:<space>']` only holds the latest run, so this reads
    the durable ledger instead.
    """
    since = time.time() - hours * 3600
    row = conn.execute(
        "SELECT count(*) runs, coalesce(sum(bytes),0) bytes, coalesce(sum(requests),0) requests, "
        "coalesce(sum(errors),0) errors, coalesce(sum(changed),0) changed, "
        "coalesce(sum(checked),0) checked, max(started_at) last_started_at "
        "FROM sync_runs WHERE space=? AND started_at>=?", (space, since)).fetchone()
    runs = row["runs"] or 0
    span = None
    interval = None
    if runs >= 2:
        first, last = conn.execute(
            "SELECT min(started_at), max(started_at) FROM sync_runs WHERE space=? AND started_at>=?",
            (space, since)).fetchone()
        if last and first and last > first:
            span = round(last - first, 3)
            # N runs span N-1 intervals. Dividing the byte total by the span
            # itself would overstate the rate by N/(N-1) — a factor of 2 when only
            # two runs are in the window, which is exactly when it reads as a
            # headline number.
            interval = span / (runs - 1)
    # Prefer the observed interval; fall back to the documented 300 s cadence.
    per_run = row["bytes"] / runs if runs else None
    runs_per_day = 86400 / interval if interval else 86400 / 300
    per_day = round(per_run * runs_per_day, 1) if per_run else None
    return {"window_hours": hours, "runs": runs, "bytes": row["bytes"], "requests": row["requests"],
            "errors": row["errors"], "changed": row["changed"], "checked": row["checked"],
            "last_started_at": row["last_started_at"], "span_seconds": span,
            "observed_interval_seconds": round(interval, 1) if interval else None,
            "bytes_per_run": round(per_run, 1) if per_run else None,
            "projected_bytes_per_day": per_day,
            "projected_runs_per_day": round(runs_per_day, 1),
            "ledger_window": conn.execute("SELECT count(*) FROM sync_runs WHERE space=?",
                                          (space,)).fetchone()[0]}


def status(conn, space="default"):
    row = conn.execute("SELECT count(*) total, sum(active) active, min(verified_at) oldest_verified_at "
                       "FROM records WHERE space=?", (space,)).fetchone()
    saved = conn.execute("SELECT value FROM state WHERE key=?", ("sync:" + space,)).fetchone()
    errors = conn.execute("SELECT count(*) FROM attempts WHERE space=? AND error IS NOT NULL",
                          (space,)).fetchone()[0]
    indexed = fts_docs(conn, space)
    total = row["total"] or 0
    # Fail loud, not silent: an index that was never built (or was dropped) would
    # otherwise look like "no matching memories".
    index_state = "empty" if not indexed else "ready"
    if total and not indexed:
        index_state = "not_built"
    recent = conn.execute(
        "SELECT source_id, failed_at, error FROM sync_errors WHERE space=? "
        "ORDER BY failed_at DESC LIMIT 5", (space,)).fetchall()
    return {"shadow": True, "primary": "nowledge-mem", "space_id": space,
            **dict(row), "unresolved_errors": errors, "sync": json.loads(saved[0]) if saved else None,
            "search_index": {"indexed": indexed, "records": total, "state": index_state,
                             "hint": "run `python3 -m memory_tool.shadow reindex`"
                                     if index_state == "not_built" else None},
            "contract": coverage(conn, space),
            "metering": metering(conn, space),
            "error_ledger": {"size": conn.execute("SELECT count(*) FROM sync_errors WHERE space=?",
                                                  (space,)).fetchone()[0],
                             "recent": [{"source_id": item["source_id"],
                                         "failed_at": item["failed_at"],
                                         "error": item["error"]} for item in recent]}}


class Nowledge:
    def __init__(self, config):
        values = json.loads(Path(config).expanduser().read_text())
        self.url = values["apiUrl"].rstrip("/")
        self.key = values["apiKey"]
        self.requests = 0
        self.bytes = 0

    def request(self, path, **params):
        url = self.url + path + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"X-NMEM-API-Key": self.key})
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
        # Counted after a successful read so failed attempts do not inflate traffic.
        self.requests += 1
        self.bytes += len(body)
        return json.loads(body)

    def ids(self, space):
        found = set()
        for offset in range(0, 100000, 100):
            page = self.request("/memories", limit=100, offset=offset, space_id=space, state="active")
            for record in page["memories"]:
                if record.get("space_id") != space:
                    raise ValueError("Manifest space mismatch")
                found.add(record["id"])
            if not page["pagination"]["has_more"]:
                return found
        raise ValueError("Manifest exceeds bounded scan")

    def get(self, source_id, space):
        return self.request("/memories/" + urllib.parse.quote(source_id, safe=""), space_id=space)


def load_manifest(conn, client, space, cache_seconds=0):
    """Return ``(ids, cached)`` for the canonical active-ID listing.

    The listing returns full record bodies (5.18 MiB / 21 requests at 2,048
    records, measured 2026-10-07) and is by far the largest share of sync
    traffic, while the per-ID refresh is what actually keeps records fresh. A
    cache window lets the refresh run far more often than the listing, which is
    P1's whole point, without changing any default behaviour: the live job passes
    cache 0 and still lists every run.

    Returns ``cached=True`` only when a stored listing is still inside the
    window, so a caller can prove from the report which path ran.
    """
    key = "manifest:" + space
    if cache_seconds > 0:
        saved = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        if saved:
            stored = json.loads(saved[0])
            if time.time() - stored.get("fetched_at", 0) <= cache_seconds:
                return set(stored.get("ids", [])), True
    ids = client.ids(space)
    with conn:
        conn.execute("INSERT OR REPLACE INTO state VALUES(?,?)",
                     (key, json.dumps({"fetched_at": time.time(), "count": len(ids),
                                       "ids": sorted(ids)})))
    return ids, False


def sync(conn, client, batch=100, space="default", manifest_cache_seconds=0):
    report = {"started_at": time.time(), "checked": 0, "changed": 0, "missing": 0, "errors": []}
    # Traffic counters may be cumulative for the life of the client object. Meter
    # the delta for THIS run; recording the cumulative value would double-count
    # every earlier run as soon as a caller reuses the client.
    start_requests = getattr(client, "requests", 0) or 0
    start_bytes = getattr(client, "bytes", 0) or 0
    try:
        manifest, report["manifest_cached"] = load_manifest(conn, client, space, manifest_cache_seconds)
        records = {row["source_id"]: (row["verified_at"], row["active"]) for row in
                   conn.execute("SELECT source_id,verified_at,active FROM records WHERE space=?", (space,))}
        attempts = {row["source_id"]: row["attempted_at"] for row in
                    conn.execute("SELECT source_id,attempted_at FROM attempts WHERE space=?", (space,))}
        now = time.time()
        tombstone_cutoff = now - TOMBSTONE_RECHECK_SECONDS
        # A record the source reported as gone is deactivated but kept, so an
        # upstream restore is still picked up and the deletion stays auditable.
        # Re-probing it on every rotation was self-inflicted traffic: the 12
        # inactive records in the live mirror produced the 0 -> 33 -> 116 `missing`
        # counts in the daily report, which then reads as a wave of deletions.
        # Tombstones are re-checked on a slow cycle instead.
        candidates = set(manifest)
        candidates.update(
            source_id for source_id, (verified_at, active) in records.items()
            if active or attempts.get(source_id, verified_at) <= tombstone_cutoff)
        ordered = sorted(candidates, key=lambda source_id: (
            attempts.get(source_id, records.get(source_id, (0, 1))[0]), source_id))
        report["manifest_count"] = len(manifest)
        report["tombstones_deferred"] = len(records) - sum(
            1 for source_id in records if source_id in candidates)
        for source_id in ordered[:batch]:
            failure = None
            try:
                record = client.get(source_id, space)
                if record.get("id") != source_id:
                    raise ValueError("Canonical source ID mismatch")
                report["changed"] += int(put(conn, space, record))
                report["checked"] += 1
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    mark_missing(conn, space, source_id)
                    report["missing"] += 1
                else:
                    failure = "HTTP " + str(error.code)
            except (OSError, ValueError, KeyError) as error:
                failure = type(error).__name__
            if failure:
                report["errors"].append({"source_id": source_id, "error": failure})
            with conn:
                conn.execute("INSERT OR REPLACE INTO attempts VALUES(?,?,?,?)",
                             (space, source_id, time.time(), failure))
        covered = {row[0] for row in conn.execute("SELECT source_id FROM records WHERE space=?", (space,))}
        report["unhydrated"] = len(manifest - covered)
    except (OSError, ValueError, KeyError) as error:
        report["errors"].append({"phase": "manifest", "error": type(error).__name__})
    report["finished_at"] = time.time()
    report["duration"] = round(report["finished_at"] - report["started_at"], 3)
    # Test doubles have no counters; a missing meter is 0, never a fabricated number.
    report["requests"] = (getattr(client, "requests", 0) or 0) - start_requests
    report["bytes"] = (getattr(client, "bytes", 0) or 0) - start_bytes
    with conn:
        conn.execute("INSERT OR REPLACE INTO sync_runs VALUES(?,?,?,?,?,?,?,?,?)",
                     (report["started_at"], space, report["checked"], report["changed"],
                      report["missing"], len(report["errors"]), report["requests"],
                      report["bytes"], report["duration"]))
        for item in report["errors"][:ERROR_LEDGER_CAP]:
            conn.execute("INSERT INTO sync_errors VALUES(?,?,?,?)",
                         (space, item.get("source_id", "<manifest>"), report["finished_at"],
                          item.get("error", "")))
        conn.execute(
            "DELETE FROM sync_errors WHERE space=? AND rowid NOT IN "
            "(SELECT rowid FROM sync_errors WHERE space=? ORDER BY failed_at DESC LIMIT ?)",
            (space, space, ERROR_LEDGER_CAP))
        conn.execute("INSERT OR REPLACE INTO state VALUES(?,?)", ("sync:" + space, json.dumps(report)))
    return report


def rotate_log(path, max_bytes=8 * 1024 * 1024, keep=5):
    """Cap an append-only log that launchd writes to.

    Copy-then-truncate, never rename: launchd holds the file descriptor open
    across runs, so renaming would leave the live writer appending to the
    archived inode and the visible log frozen. Truncating in place keeps the
    inode, so the next write lands in a fresh empty file.
    """
    path = Path(path).expanduser()
    if not path.exists():
        return {"path": str(path), "rotated": False, "reason": "missing"}
    size = path.stat().st_size
    if size <= max_bytes:
        return {"path": str(path), "rotated": False, "bytes": size}
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    archive = path.with_name(path.name + "." + stamp)
    # Second granularity is not unique: several rotations inside one second (a
    # test, or a manual run right after the scheduled one) would overwrite each
    # other and silently lose an archive.
    suffix = 0
    while archive.exists():
        suffix += 1
        archive = path.with_name(f"{path.name}.{stamp}.{suffix}")
    shutil.copyfile(path, archive)
    with open(path, "r+b") as handle:
        handle.truncate(0)
    archives = sorted(path.parent.glob(path.name + ".*"))
    removed = []
    for old in archives[:-keep] if keep > 0 else archives:
        old.unlink()
        removed.append(old.name)
    return {"path": str(path), "rotated": True, "bytes": size, "archive": archive.name,
            "kept": min(len(archives), keep), "removed": removed}


# ---------------------------------------------------------------------------
# Divergence instrumentation (phase 1 of the read-path rollover)
#
# Rolling reads onto the shadow cannot start before we know *where the two
# backends disagree*, and that data has to come from real queries rather than
# from a corpus written against the corpus (see docs/reports/2026-10-07-eval-baseline.md
# §6 on the circularity). So `shadow_compare` instruments real lookups.
#
# It must not repeat the mistake the DSH profile already made once: prompt-time
# recall was switched off because `nmem memories search` costs a measured
# 12.8-13.0 s per call. So the compare path is split — the shadow side is
# answered immediately (indexed terms are ~1-2 ms) and the query is only
# *queued*; `drain_compare` runs the slow Nowledge side later, off the critical
# path, deduplicated and capped.
# ---------------------------------------------------------------------------

COMPARE_PENDING_CAP = 5000
COMPARE_CACHE_TTL_HOURS = 24

# Deliberately regex-only: routing must be deterministic, auditable and free.
# An LLM in this decision would make the phase-1 divergence data unexplainable.
LITERAL_ANCHOR = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}"                       # uuid
    r"|[0-9a-f]{12,}"                                # long hash (incl. sha256: prefix body)
    r"|\b[0-9a-f]{8,11}\b"                           # short hash / abbreviated id
    r"|\b\d+\.\d+(?:\.\d+)?\b"                       # version
    r"|(?:~|/)[\w./-]{3,}"                           # path
    r"|\b[A-Z][A-Z0-9_]{3,}\b"                       # ERROR_CODE / CONSTANT
    r"|\b[\w-]+\.(?:md|py|json|sh|toml|ya?ml|db|sqlite3?|log|mjs|ts)\b"   # filename
    r"|\b\w+(?:-\w+){2,}\b")                         # 3+ hyphenated segments: mcp-los-memory-shadow


def classify_query(query):
    """Route a query to a backend class. Deterministic and side-effect free."""
    text = query or ""
    if LITERAL_ANCHOR.search(text):
        return "literal_anchor"
    if any(CJK_CHAR.search(term) and len(term) <= 4 for term in text.split()):
        return "short_cjk"
    return "conceptual"


def database_path(conn):
    """Return the file behind an open connection, or None for in-memory databases.

    The compare ledger is derived state and must live beside the mirror it
    describes. Resolving it from the connection (rather than from the process's
    default path) is what stops a run against a fixture database from writing
    entries into the operator's production state directory.
    """
    for row in conn.execute("PRAGMA database_list"):
        # Positional: a caller may hand us a connection without a row_factory.
        # PRAGMA database_list yields (seq, name, file).
        name, path = row[1], row[2]
        if name == "main" and path:
            return Path(path)
    return None


def compare_paths(state_dir=None, conn=None):
    """Resolve the compare ledger paths for a mirror.

    ``state_dir`` wins when given; otherwise the directory of ``conn``'s database
    file is used, so the ledger always sits next to the mirror it belongs to.
    Falls back to the historical default only when neither is available (an
    in-memory connection), which keeps existing callers working.
    """
    if state_dir:
        base = Path(state_dir)
    else:
        db_path = database_path(conn) if conn is not None else None
        base = db_path.parent if db_path is not None else DEFAULT_DB.expanduser().parent
    return {"pending": base / "compare-pending.jsonl",
            "results": base / "compare-results.jsonl",
            "cache": base / "compare-cache.json"}


def compare_shadow(conn, query, limit=10, space="default"):
    """The shadow side of a comparison. No network, no writes to the mirror."""
    started = time.perf_counter()
    results, meta = search_detailed(conn, query, limit=limit, space=space)
    return {"query": query, "class": classify_query(query),
            "ids": [item["source_id"] for item in results],
            "results": results, "meta": meta,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2)}


def record_compare(conn, query, limit=10, space="default", state_dir=None):
    """Answer the shadow side now and queue the query for the slow side later.

    Returns what the caller needs to answer immediately; the Nowledge half and
    the divergence record are produced by `drain_compare`.
    """
    payload = compare_shadow(conn, query, limit=limit, space=space)
    paths = compare_paths(state_dir, conn=conn)
    paths["pending"].parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = time.time()
    row = {"ts": stamp, "query": query, "limit": limit, "space": space,
           "class": payload["class"], "shadow_ids": payload["ids"],
           "shadow_mode": payload["meta"].get("mode"),
           "scan_terms": payload["meta"].get("scan_terms"),
           "shadow_latency_ms": payload["latency_ms"]}
    with open(paths["pending"], "a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.chmod(paths["pending"], 0o600)
    pending = [line for line in paths["pending"].read_text().splitlines() if line.strip()]
    if len(pending) > COMPARE_PENDING_CAP:
        paths["pending"].write_text("\n".join(pending[-COMPARE_PENDING_CAP:]) + "\n")
        os.chmod(paths["pending"], 0o600)
    return payload


def _resolve_command(command):
    """Find the primary's search CLI even under launchd's minimal PATH.

    launchd starts jobs with PATH=/usr/bin:/bin:/usr/sbin:/sbin, so a bare
    `nmem` raised FileNotFoundError in the scheduled drain. Resolving known
    install locations here means the caller does not have to remember.
    """
    if os.path.sep in command:
        return command
    found = shutil.which(command)
    if found:
        return found
    for candidate in (Path.home() / ".local/bin" / command,
                      Path("/usr/local/bin") / command,
                      Path("/opt/homebrew/bin") / command):
        if candidate.exists():
            return str(candidate)
    return command


def _nowledge_ids(query, limit, command, timeout):
    """Run the primary's own search CLI. Measured at ~13 s, hence the queue.

    Never raises: a missing binary, a timeout and unparseable output all come back
    through the third element. Callers get one error channel instead of two, which
    is what a scheduled job needs — an exception here would take down the whole run
    rather than record one failed lookup.
    """
    started = time.perf_counter()
    try:
        result = subprocess.run([command, "memories", "search", query, "-n", str(limit), "-j"],
                                capture_output=True, text=True, timeout=timeout)
        elapsed = time.perf_counter() - started
        if result.returncode != 0:
            return None, elapsed, (result.stderr or "").strip()[:200]
        payload = json.loads(result.stdout)
        items = payload.get("memories", payload) if isinstance(payload, dict) else payload
        return ([item["id"] for item in items if isinstance(item, dict) and item.get("id")],
                elapsed, None)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return None, time.perf_counter() - started, type(error).__name__


def drain_compare(state_dir=None, max_queries=10, cache_ttl_hours=COMPARE_CACHE_TTL_HOURS,
                  command=None, timeout=180, limit=None):
    """Fill in the Nowledge half for queued queries and log the divergence.

    Deduplicated against a short-lived cache and capped per run, because each
    uncached query costs the primary ~13 s. Re-running is safe: the pending
    queue is only truncated for the queries actually resolved.
    """
    command = _resolve_command(command or os.environ.get("SHADOW_NMEM_BIN") or "nmem")
    paths = compare_paths(state_dir)
    if not paths["pending"].exists():
        return {"pending": 0, "resolved": 0, "results": str(paths["results"])}
    queued = []
    for line in paths["pending"].read_text().splitlines():
        if line.strip():
            try:
                queued.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not queued:
        return {"pending": 0, "resolved": 0, "results": str(paths["results"])}

    cache = {}
    if paths["cache"].exists():
        try:
            cache = json.loads(paths["cache"].read_text())
        except json.JSONDecodeError:
            cache = {}
    # Newest occurrence per query wins; older duplicates collapse into it.
    latest = {}
    for row in queued:
        latest[row["query"]] = row

    now = time.time()
    resolved, deferred, errors = [], 0, []
    for query, row in sorted(latest.items(), key=lambda item: -item[1]["ts"]):
        depth = limit or row.get("limit") or 10
        entry = cache.get(query)
        if entry and now - entry.get("ts", 0) <= cache_ttl_hours * 3600 and entry.get("limit") == depth:
            nowledge_ids, elapsed, error = entry["ids"], entry.get("elapsed"), None
        else:
            if len(resolved) >= max_queries:
                deferred += 1
                continue
            try:
                nowledge_ids, elapsed, error = _nowledge_ids(query, depth, command, timeout)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                nowledge_ids, elapsed, error = None, None, type(exc).__name__
            if error:
                errors.append({"query": query, "error": error})
                continue
            cache[query] = {"ids": nowledge_ids, "ts": now, "elapsed": elapsed, "limit": depth}
        shadow_ids = row.get("shadow_ids") or []
        shadow_set, nowledge_set = set(shadow_ids), set(nowledge_ids or [])
        union = shadow_set | nowledge_set
        with open(paths["results"], "a") as handle:
            handle.write(json.dumps({
                "ts": now, "query": query, "class": row.get("class"),
                "limit": depth, "shadow_ids": shadow_ids, "nowledge_ids": list(nowledge_ids or []),
                "shadow_only": sorted(shadow_set - nowledge_set),
                "nowledge_only": sorted(nowledge_set - shadow_set),
                "overlap": sorted(shadow_set & nowledge_set),
                "jaccard": round(len(shadow_set & nowledge_set) / len(union), 4) if union else None,
                "shadow_mode": row.get("shadow_mode"), "scan_terms": row.get("scan_terms"),
                "shadow_latency_ms": row.get("shadow_latency_ms"),
                "nowledge_latency_s": round(elapsed, 2) if elapsed else None,
            }, ensure_ascii=False) + "\n")
        resolved.append(query)
    # Only touch the results file if this run actually wrote a record: an
    # all-failed drain must not blow up (or create a misleading empty file).
    if os.path.exists(paths["results"]):
        os.chmod(paths["results"], 0o600)
    paths["cache"].write_text(json.dumps(cache, ensure_ascii=False))
    os.chmod(paths["cache"], 0o600)
    # Keep only what could not be resolved this run.
    keep = [row for row in queued if row["query"] not in set(resolved)]
    paths["pending"].write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep))
    os.chmod(paths["pending"], 0o600)
    return {"pending": len(queued), "resolved": len(resolved), "deferred": deferred,
            "errors": errors, "results": str(paths["results"])}


def compare_report(state_dir=None):
    """Aggregate divergence by query class — the input to the category boundary.

    This is the number the rollover decision actually needs: not a headline
    score, but "on which class of query do the two backends disagree, and in
    whose favour".
    """
    paths = compare_paths(state_dir)
    if not paths["results"].exists():
        return {"records": 0, "by_class": {}, "note": "no drained comparisons yet"}
    per_class = {}
    records = 0
    for line in paths["results"].read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        records += 1
        bucket = per_class.setdefault(row.get("class") or "conceptual",
                                      {"n": 0, "jaccard": [], "shadow_only": 0,
                                       "nowledge_only": 0, "agree_empty": 0,
                                       "shadow_only_queries": 0, "nowledge_only_queries": 0})
        bucket["n"] += 1
        if row.get("jaccard") is not None:
            bucket["jaccard"].append(row["jaccard"])
        bucket["shadow_only"] += len(row.get("shadow_only") or [])
        bucket["nowledge_only"] += len(row.get("nowledge_only") or [])
        if row.get("shadow_only"):
            bucket["shadow_only_queries"] += 1
        if row.get("nowledge_only"):
            bucket["nowledge_only_queries"] += 1
        if not row.get("shadow_ids") and not row.get("nowledge_ids"):
            bucket["agree_empty"] += 1
    for bucket in per_class.values():
        values = bucket.pop("jaccard")
        bucket["mean_jaccard"] = round(sum(values) / len(values), 4) if values else None
    return {"records": records, "by_class": per_class,
            "reading": "mean_jaccard near 0 with shadow_only>0 means the shadow adds "
                       "candidates the primary's default search misses; nowledge_only>0 "
                       "is the risk side — raise the limit before drawing conclusions."}


# ---------------------------------------------------------------------------
# Primary recall probe
#
# The primary can report `Search Index: Ready` while everything written after some
# point is not retrievable at all. That is exactly what happened on 2026-09-24: the
# projection writer stalled, `nmem models status` kept answering Ready, and the only
# reason we know is a matched-pair probe run by hand two weeks later. The alert we
# have (`index_not_ready`) keys off `search_index.state`, so it structurally cannot
# fire for this failure mode.
#
# The mirror is the only read-only source of "records the primary ought to be able
# to find". So: take anchors that exist in the mirror, ask the primary for them, and
# report which ones come back. The probes are grouped by job — `recent` is the
# signal, `control` (oldest records) proves the primary answers at all, and `span`
# locates the boundary. A bare "recent records are missing" reading cannot tell a
# stale projection apart from an unreachable primary; the control group is what
# separates them. Neither side is written to.
# ---------------------------------------------------------------------------

# An anchor shared by many records proves nothing about retrieval, so only anchors
# that appear in at most this many mirror records are used.
RECALL_PROBE_MAX_MIRROR_HITS = 2
# A retrieval rate at or above this, on the control set, means the primary answers
# at all — which is what makes a low recent rate evidence of a stale projection.
RECALL_PROBE_CONTROL_FLOOR = 0.5
RECALL_PROBE_CJK_WINDOW = 4

# Identifiers with a separator are the most distinctive anchors in this corpus
# (filenames, `snake_case`, `mcp-los-memory-shadow`, `sha256:…`).
_ANCHOR_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[_./:-][A-Za-z0-9]+)+")


def _anchor_candidates(record):
    """Distinctive literal anchors for a mirrored snapshot, best first.

    Title before body: a token in the title is both more likely to be unique and
    more likely to be what a real query would use.
    """
    title = record.get("title") or ""
    body = record.get("content") or ""
    candidates = []
    for text in (title, body):
        candidates.extend(match.group(0) for match in _ANCHOR_TOKEN.finditer(text))
    for text in (title, body):
        window = RECALL_PROBE_CJK_WINDOW
        for index in range(max(0, len(text) - window + 1)):
            chunk = text[index:index + window]
            if all(CJK_CHAR.match(char) for char in chunk):
                candidates.append(chunk)
                break
    seen = set()
    ordered = []
    for candidate in candidates:
        if len(candidate) >= 3 and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _mirror_hits(conn, space, anchor):
    """How many mirrored records contain this anchor (rarity, checked on the mirror)."""
    return conn.execute(
        "SELECT count(*) FROM records WHERE space=? AND snapshot LIKE ? ESCAPE '\\'",
        (space, like_pattern(anchor))).fetchone()[0]


def select_recall_probes(conn, space="default", recent=6, control=3, span=3,
                         max_mirror_hits=RECALL_PROBE_MAX_MIRROR_HITS):
    """Choose probe records in three groups, each with one job.

    ``recent`` (newest N) is the signal: "can the primary still retrieve what was
    written lately".

    ``control`` (oldest N) is the calibration: records from a region we already
    trust, so that "the primary lost my recent content" cannot be confused with "the
    primary cannot answer at all". They must *not* be drawn from near the recent
    bucket — a same-day record is exactly what is under suspicion, and letting it
    into the control rate would let a stale index masquerade as a dead primary.

    ``span`` (evenly spaced over the middle) locates the boundary. Without it the
    bracket is "somewhere between the oldest record and today": measured three
    months wide on the live mirror, which answers nothing. Evenly spaced probes cost
    the same per lookup and narrow it a lot.

    Only ``recent`` and ``control`` feed the verdict; all three feed the bracket.
    """
    rows = conn.execute("SELECT source_id, snapshot FROM records WHERE space=? AND active=1",
                        (space,)).fetchall()
    entries = []
    for row in rows:
        try:
            record = json.loads(row["snapshot"])
        except (TypeError, ValueError):
            continue
        entries.append((record.get("created_at") or "", row["source_id"], record))
    entries.sort(key=lambda item: (item[0], item[1]))
    if len(entries) < 2:
        return {"probes": [], "recent": 0, "control": 0, "span": 0,
                "reason": "the mirror holds too few records to probe"}

    recent_bucket = entries[-recent:] if recent > 0 else []
    remainder = entries[:len(entries) - len(recent_bucket)]
    control_bucket = remainder[:control] if control > 0 else []
    middle = remainder[len(control_bucket):]

    if span <= 0 or not middle:
        span_bucket = []
    elif len(middle) <= span:
        span_bucket = middle
    else:
        step = (len(middle) - 1) / (span - 1) if span > 1 else 0
        span_bucket = [middle[min(len(middle) - 1, round(index * step))]
                       for index in range(span)]

    wanted = ([("recent", created_at, source_id, record)
               for created_at, source_id, record in recent_bucket]
              + [("control", created_at, source_id, record)
                 for created_at, source_id, record in control_bucket]
              + [("span", created_at, source_id, record)
                 for created_at, source_id, record in span_bucket])

    probes = []
    for label, created_at, source_id, record in wanted:
        for anchor in _anchor_candidates(record):
            hits = _mirror_hits(conn, space, anchor)
            if hits <= max_mirror_hits:
                probes.append({"group": label, "source_id": source_id,
                               "created_at": created_at, "anchor": anchor,
                               "mirror_hits": hits})
                break
    counts = {label: sum(1 for probe in probes if probe["group"] == label)
              for label in ("recent", "control", "span")}
    return {"probes": probes, **counts, "candidates": len(entries)}


def _recall_verdict(recent_rate, control_rate, recent_count, control_count):
    """Turn the two hit rates into a bounded set of named outcomes.

    Naming them matters: "the primary is down" and "the primary's projection is
    stale" need opposite responses, and an operator reading a bare percentage
    would treat them the same.
    """
    if not recent_count:
        return "no_recent_probes"
    if not control_count:
        return "inconclusive_no_control"
    if control_rate < RECALL_PROBE_CONTROL_FLOOR:
        return "primary_not_answering"
    if recent_rate < RECALL_PROBE_CONTROL_FLOOR:
        return "stale_projection_suspected"
    return "ok"


def probe_primary_recall(conn, command=None, timeout=180, limit=10, space="default",
                         recent=6, control=3, span=3, probes=None):
    """Ask the primary to retrieve anchors the mirror says it holds.

    Each lookup costs the primary a measured ~13 s, so the probe count is bounded
    (the defaults are 12 probes, ~3 minutes) and this belongs on a schedule, never
    in a read path. Pass `probes` to run a pre-selected set instead.

    Read-only on both sides: the mirror is only SELECTed, and the primary is only
    asked the same `memories search` the compare drain already uses.
    """
    command = command or _resolve_command("nmem")
    selection = probes if probes is not None else select_recall_probes(
        conn, space=space, recent=recent, control=control, span=span)["probes"]
    if not selection:
        return {"verdict": "no_probes", "probes": [],
                "reading": "the mirror held no record with a rare enough anchor"}

    results = []
    for probe in selection:
        ids, elapsed, error = _nowledge_ids(probe["anchor"], limit, command, timeout)
        results.append({**probe,
                        "retrieved": bool(ids) and probe["source_id"] in ids,
                        "primary_latency_s": round(elapsed, 2),
                        "error": error})

    def rate(group):
        bucket = [item for item in results if item["group"] == group]
        if not bucket:
            return 0.0, 0
        return sum(1 for item in bucket if item["retrieved"]) / len(bucket), len(bucket)

    recent_rate, recent_count = rate("recent")
    control_rate, control_count = rate("control")
    retrieved_dates = [item["created_at"] for item in results if item["retrieved"] and item["created_at"]]
    missed_dates = [item["created_at"] for item in results if not item["retrieved"] and item["created_at"]]
    newest_retrievable = max(retrieved_dates) if retrieved_dates else None
    # Only misses *after* the newest retrievable record can bound the boundary. A
    # control probe that missed sits before it and is an anchor-style caveat, not a
    # bracket — taking the raw minimum would report a boundary interval running
    # backwards.
    after_boundary = [date for date in missed_dates
                      if newest_retrievable is None or date > newest_retrievable]
    oldest_unretrievable = min(after_boundary) if after_boundary else None
    control_misses = [item["anchor"] for item in results
                      if item["group"] == "control" and not item["retrieved"]]
    return {"verdict": _recall_verdict(recent_rate, control_rate, recent_count, control_count),
            "recent_rate": round(recent_rate, 4), "recent_probes": recent_count,
            "control_rate": round(control_rate, 4), "control_probes": control_count,
            "control_misses": control_misses,
            # These two bracket the freeze boundary *within the probed set*. They are
            # not a solved boundary: with the recent bucket fully stale, the newest
            # retrievable record is simply the newest control probe, so anything
            # between the two dates is unmeasured. Closing it would need a bisection
            # and each step costs the primary ~13 s.
            "newest_retrievable_created_at": newest_retrievable,
            "oldest_unretrievable_created_at": oldest_unretrievable,
            "oldest_probe_created_at": min((item["created_at"] for item in results if item["created_at"]),
                                           default=None),
            "errors": [{"anchor": item["anchor"], "error": item["error"]}
                       for item in results if item["error"]],
            "probes": results,
            "reading": "recent_rate low with control_rate healthy means the primary "
                       "answers but cannot retrieve newer content (a stale projection); "
                       "both low means the primary is unreachable or changed shape. "
                       "The verdict compares two populations, so it is robust to the "
                       "caveats that follow. The two *_created_at fields are indicative "
                       "only: they bracket the boundary within the probed set, only "
                       "misses after the newest retrievable record count towards them, "
                       "and `created_at` is not the order the index ingested records "
                       "(bulk imports share timestamps), so a split inside one timestamp "
                       "means the boundary is not purely temporal. A control probe that "
                       "misses may be an anchor-style artefact rather than proof, so read "
                       "control_misses alongside control_rate, and treat no single run as "
                       "proof."}


def summary(conn, space="default"):
    """Identity summary for backup/restore comparison.
    Compares content, not row counts: a restore is only proven when every
    (space, source_id, digest, active) tuple matches the source.
    """
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute("SELECT space, source_id, digest, active FROM records "
                            "ORDER BY space, source_id"):
        digest.update(f"{row['space']}\x00{row['source_id']}\x00{row['digest']}\x00{row['active']}\n"
                      .encode())
        count += 1
    return {"space": space, "records": count, "identity_digest": digest.hexdigest(),
            "active": conn.execute("SELECT count(*) FROM records WHERE active=1").fetchone()[0]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["sync", "status", "reindex", "summary", "rotatelog",
                                           "compare", "compare-drain", "compare-report",
                                           "recall-probe"])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--config", default=str(Path.home() / ".nowledge-mem/config.json"))
    parser.add_argument("--batch", type=int, default=100)
    parser.add_argument("--query", default=None, help="compare: the query to compare")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-queries", type=int, default=10,
                        help="compare-drain: cap on uncached Nowledge lookups per run "
                             "(each costs the primary ~13 s)")
    parser.add_argument("--recent", type=int, default=6,
                        help="recall-probe: newest mirror records to probe (each costs the "
                             "primary ~13 s)")
    parser.add_argument("--control", type=int, default=3,
                        help="recall-probe: oldest mirror records used as the control set, "
                             "so a dead primary is not mistaken for a stale projection")
    parser.add_argument("--span", type=int, default=3,
                        help="recall-probe: probes evenly spaced over the middle of the "
                             "timeline; they locate the retrieval boundary without "
                             "affecting the verdict")
    parser.add_argument("--nowledge-cmd", default=os.environ.get("SHADOW_NMEM_BIN", "nmem"))
    parser.add_argument("--max-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--keep", type=int, default=5)
    parser.add_argument("--manifest-cache-seconds", type=int, default=0,
                        help="reuse the last active-ID listing for this many seconds "
                             "(0 = list every run, the current production behaviour)")
    args = parser.parse_args()
    os.umask(0o077)
    if not 1 <= args.batch <= 5000:
        parser.error("batch must be 1..5000")
    for name in ("recent", "control", "span"):
        if getattr(args, name) < 0:
            parser.error(f"--{name} must not be negative")
    if args.recent + args.control + args.span > 40:
        parser.error("a recall probe costs the primary ~13 s per lookup; keep the "
                     "total probe count at 40 or below")
    with contextlib.closing(connect(args.db)) as conn:
        if args.action == "status":
            result = status(conn)
        elif args.action == "summary":
            result = summary(conn)
        elif args.action == "rotatelog":
            result = rotate_log(Path(args.db).expanduser().with_name("sync.out.log"),
                                args.max_bytes, args.keep)
        elif args.action == "compare":
            if not args.query:
                parser.error("compare requires --query")
            state_dir = Path(args.db).expanduser().parent
            result = {key: value for key, value in
                      record_compare(conn, args.query, limit=args.limit,
                                     state_dir=state_dir).items() if key != "results"}
        elif args.action == "compare-drain":
            result = drain_compare(state_dir=Path(args.db).expanduser().parent,
                                   max_queries=args.max_queries, command=args.nowledge_cmd)
        elif args.action == "compare-report":
            result = compare_report(state_dir=Path(args.db).expanduser().parent)
        elif args.action == "recall-probe":
            result = probe_primary_recall(conn, command=args.nowledge_cmd,
                                          limit=args.limit, recent=args.recent,
                                          control=args.control, span=args.span)
        elif args.action == "reindex":
            # Same single-writer lock as sync: rebuilding while a sync writes
            # would interleave index rows with record writes.
            import fcntl
            with open(str(Path(args.db).expanduser()) + ".lock", "a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise SystemExit("Another shadow sync is running")
                count = reindex(conn)
            result = {"reindexed": count, "search_index": status(conn)["search_index"],
                      "contract": status(conn)["contract"]}
        else:
            import fcntl
            with open(str(Path(args.db).expanduser()) + ".lock", "a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise SystemExit("Another shadow sync is running")
                result = sync(conn, Nowledge(args.config), args.batch,
                              manifest_cache_seconds=args.manifest_cache_seconds)
        print(json.dumps(result, ensure_ascii=False))
        if result.get("errors"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
