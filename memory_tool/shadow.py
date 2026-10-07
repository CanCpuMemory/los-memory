"""Private, read-only Nowledge shadow and bounded canonical refresh."""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_DB = Path.home() / ".local/share/los-memory-shadow/shadow.sqlite3"

# Trigram-indexed text cap per record. The index is a derived artefact; the
# authoritative text stays in records.snapshot.
FTS_TEXT_CAP = 32000
# The trigram tokenizer cannot match patterns shorter than 3 characters.
FTS_MIN_TERM = 3


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
    """)
    return conn


def index_record(conn, space, record):
    """(Re)write one record's search-index row. Caller owns the transaction."""
    rowid = fts_rowid(space, record["id"])
    conn.execute("DELETE FROM records_fts WHERE rowid=?", (rowid,))
    conn.execute("INSERT INTO records_fts(rowid, source_id, space, text) VALUES(?,?,?,?)",
                 (rowid, record["id"], space, fts_text(record)))


def reindex(conn, space=None):
    """Rebuild the search index from records. Idempotent; the migration path for
    databases written before the index existed."""
    where, params = ("WHERE space=?", (space,)) if space else ("", ())
    rows = conn.execute(f"SELECT space, snapshot FROM records {where}", params).fetchall()
    with conn:
        if space:
            conn.execute("DELETE FROM records_fts WHERE space=?", (space,))
        else:
            conn.execute("DELETE FROM records_fts")
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


def search_detailed(conn, query, limit=10, project=None, space="default"):
    """Substring search, accelerated by the trigram index.

    Semantics are unchanged and the Python filter below stays authoritative: the
    index only narrows *which* records get loaded and substring-checked. Recall
    can therefore never be worse than the full scan — when the index cannot
    answer (short terms, empty index, FTS error) this falls back to scanning.

    Returns (results, meta) where meta records which path ran, so a caller can
    see when the index did not help instead of guessing.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError("query must contain 1..500 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
    terms = query.casefold().split()

    meta = {"mode": "scan", "candidates": None, "indexed": fts_docs(conn, space)}
    source_filter = None
    if len(query.strip()) >= FTS_MIN_TERM and all(len(t) >= FTS_MIN_TERM for t in terms):
        try:
            candidates = _fts_candidates(conn, terms, space)
            if candidates:
                source_filter = candidates
                meta = {"mode": "index+filter", "candidates": len(candidates),
                        "indexed": meta["indexed"]}
        except sqlite3.Error as exc:  # index unusable -> scan, never fail the query
            meta["index_error"] = str(exc)

    sql = "SELECT * FROM records WHERE space=? AND active=1"
    params = [space]
    if source_filter is not None:
        placeholders = ",".join("?" * len(source_filter))
        sql += f" AND source_id IN ({placeholders})"
        params.extend(sorted(source_filter))

    found = []
    for row in conn.execute(sql, params):
        record = json.loads(row["snapshot"])
        metadata = record.get("metadata") or {}
        if project is not None and metadata.get("project", "unassigned") != project:
            continue
        title = str(record.get("title") or "").casefold()
        body = str(record.get("content") or "").casefold()
        if all(term in title or term in body for term in terms):
            found.append((sum(term in title for term in terms), row["verified_at"], envelope(row)))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in found[:limit]], meta


def search(conn, query, limit=10, project=None, space="default"):
    return search_detailed(conn, query, limit, project, space)[0]


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
    return {"shadow": True, "primary": "nowledge-mem", "space_id": space,
            **dict(row), "unresolved_errors": errors, "sync": json.loads(saved[0]) if saved else None,
            "search_index": {"indexed": indexed, "records": total, "state": index_state,
                             "hint": "run `python3 -m memory_tool.shadow reindex`"
                                     if index_state == "not_built" else None}}


class Nowledge:
    def __init__(self, config):
        values = json.loads(Path(config).expanduser().read_text())
        self.url = values["apiUrl"].rstrip("/")
        self.key = values["apiKey"]

    def request(self, path, **params):
        url = self.url + path + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"X-NMEM-API-Key": self.key})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)

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


def sync(conn, client, batch=100, space="default"):
    report = {"started_at": time.time(), "checked": 0, "changed": 0, "missing": 0, "errors": []}
    try:
        manifest = client.ids(space)
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
    with conn:
        conn.execute("INSERT OR REPLACE INTO state VALUES(?,?)", ("sync:" + space, json.dumps(report)))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["sync", "status", "reindex"])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--config", default=str(Path.home() / ".nowledge-mem/config.json"))
    parser.add_argument("--batch", type=int, default=100)
    args = parser.parse_args()
    os.umask(0o077)
    if not 1 <= args.batch <= 5000:
        parser.error("batch must be 1..5000")
    with contextlib.closing(connect(args.db)) as conn:
        if args.action == "status":
            result = status(conn)
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
            result = {"reindexed": count, "search_index": status(conn)["search_index"]}
        else:
            import fcntl
            with open(str(Path(args.db).expanduser()) + ".lock", "a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise SystemExit("Another shadow sync is running")
                result = sync(conn, Nowledge(args.config), args.batch)
        print(json.dumps(result, ensure_ascii=False))
        if result.get("errors"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
