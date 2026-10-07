"""Private, read-only Nowledge shadow and bounded canonical refresh."""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from .shadow_registry import UNASSIGNED, project_for


DEFAULT_DB = Path.home() / ".local/share/los-memory-shadow/shadow.sqlite3"

# Trigram-indexed text cap per record. The index is a derived artefact; the
# authoritative text stays in records.snapshot.
FTS_TEXT_CAP = 32000
# The trigram tokenizer cannot match patterns shorter than 3 characters.
FTS_MIN_TERM = 3
# Error-ledger bound per space, and the metering window reported by status().
ERROR_LEDGER_CAP = 500
METERING_WINDOW_HOURS = 24

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
    silently breaks.
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
        return {"records": 0, "project_assigned": 0, "project_unassigned": 0,
                "claim_undeclared": 0, "kind_unknown": 0, "project_coverage": 0.0}
    row = conn.execute(
        "SELECT sum(f.project<>'unassigned') assigned, sum(f.claim_status='undeclared') undeclared, "
        "sum(f.kind='unknown') unknown FROM record_facets f JOIN records r "
        "ON r.space=f.space AND r.source_id=f.source_id WHERE f.space=? AND r.active=1",
        (space,)).fetchone()
    assigned = row["assigned"] or 0
    return {"records": total, "project_assigned": assigned,
            "project_unassigned": total - assigned, "claim_undeclared": row["undeclared"] or 0,
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
    if runs >= 2:
        first, last = conn.execute(
            "SELECT min(started_at), max(started_at) FROM sync_runs WHERE space=? AND started_at>=?",
            (space, since)).fetchone()
        span = round(last - first, 3) if last and first and last > first else None
    per_day = round(row["bytes"] * 86400 / span, 1) if span else None
    return {"window_hours": hours, "runs": runs, "bytes": row["bytes"], "requests": row["requests"],
            "errors": row["errors"], "changed": row["changed"], "checked": row["checked"],
            "last_started_at": row["last_started_at"], "span_seconds": span,
            "projected_bytes_per_day": per_day,
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
    try:
        manifest, report["manifest_cached"] = load_manifest(conn, client, space, manifest_cache_seconds)
        existing = {row["source_id"]: row["verified_at"] for row in
                    conn.execute("SELECT source_id,verified_at FROM records WHERE space=?", (space,))}
        attempts = {row["source_id"]: row["attempted_at"] for row in
                    conn.execute("SELECT source_id,attempted_at FROM attempts WHERE space=?", (space,))}
        candidates = sorted(manifest | set(existing),
                            key=lambda source_id: (attempts.get(source_id, existing.get(source_id, 0)), source_id))
        report["manifest_count"] = len(manifest)
        for source_id in candidates[:batch]:
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
    report["requests"] = getattr(client, "requests", 0) or 0
    report["bytes"] = getattr(client, "bytes", 0) or 0
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
    parser.add_argument("action", choices=["sync", "status", "reindex", "summary"])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--config", default=str(Path.home() / ".nowledge-mem/config.json"))
    parser.add_argument("--batch", type=int, default=100)
    parser.add_argument("--manifest-cache-seconds", type=int, default=0,
                        help="reuse the last active-ID listing for this many seconds "
                             "(0 = list every run, the current production behaviour)")
    args = parser.parse_args()
    os.umask(0o077)
    if not 1 <= args.batch <= 5000:
        parser.error("batch must be 1..5000")
    with contextlib.closing(connect(args.db)) as conn:
        if args.action == "status":
            result = status(conn)
        elif args.action == "summary":
            result = summary(conn)
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
