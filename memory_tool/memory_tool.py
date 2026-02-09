#!/usr/bin/env python3
"""Standalone memory tool for logging and retrieving observations."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
SCHEMA_VERSION = 2
DEFAULT_DB = os.path.expanduser("~/.codex_memory/memory.db")
DEFAULT_LLM_HOOK = os.environ.get("MEMORY_LLM_HOOK", "")
TAG_BLACKLIST = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "over",
    "that",
    "the",
    "their",
    "this",
    "to",
    "under",
    "was",
    "were",
    "with",
}


@dataclass
class Observation:
    id: int
    timestamp: str
    project: str
    kind: str
    title: str
    summary: str
    tags: List[str]
    raw: str


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


def connect_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            project TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            tags TEXT NOT NULL,
            tags_text TEXT NOT NULL,
            raw TEXT NOT NULL
        )
        """
    )
    migrate_schema(conn)
    conn.commit()


def ensure_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def get_schema_version(conn: sqlite3.Connection) -> int:
    ensure_meta_table(conn)
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'",
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    ensure_meta_table(conn)
    conn.execute(
        """
        INSERT INTO meta (key, value)
        VALUES ('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(version),),
    )


def migrate_schema(conn: sqlite3.Connection) -> None:
    version = get_schema_version(conn)
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {version} is newer than this tool supports "
            f"(max {SCHEMA_VERSION})."
        )
    if version < 1:
        # Version 1 initializes the observations table and FTS setup.
        set_schema_version(conn, 1)
        version = 1
    if version < 2:
        try:
            conn.execute(
                "ALTER TABLE observations ADD COLUMN tags_text TEXT NOT NULL DEFAULT ''",
            )
        except sqlite3.OperationalError:
            pass
        rows = conn.execute("SELECT id, tags FROM observations").fetchall()
        for row in rows:
            tags_list = normalize_tags_list(row["tags"])
            tags_json = tags_to_json(tags_list)
            tags_text = tags_to_text(tags_list)
            conn.execute(
                "UPDATE observations SET tags = ?, tags_text = ? WHERE id = ?",
                (tags_json, tags_text, row["id"]),
            )
        rebuild_fts(conn)
        set_schema_version(conn, 2)


def ensure_fts(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts
            USING fts5(title, summary, tags_text, raw, content='observations', content_rowid='id')
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_ai
            AFTER INSERT ON observations BEGIN
                INSERT INTO observations_fts(rowid, title, summary, tags_text, raw)
                VALUES (new.id, new.title, new.summary, new.tags_text, new.raw);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_ad
            AFTER DELETE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, title, summary, tags_text, raw)
                VALUES ('delete', old.id, old.title, old.summary, old.tags_text, old.raw);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_au
            AFTER UPDATE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, title, summary, tags_text, raw)
                VALUES ('delete', old.id, old.title, old.summary, old.tags_text, old.raw);
                INSERT INTO observations_fts(rowid, title, summary, tags_text, raw)
                VALUES (new.id, new.title, new.summary, new.tags_text, new.raw);
            END;
            """
        )
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False


def rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER IF EXISTS observations_ai")
    conn.execute("DROP TRIGGER IF EXISTS observations_ad")
    conn.execute("DROP TRIGGER IF EXISTS observations_au")
    conn.execute("DROP TABLE IF EXISTS observations_fts")
    ensure_fts(conn)


def init_db(path: str) -> None:
    conn = connect_db(path)
    ensure_schema(conn)
    ensure_fts(conn)
    conn.close()


def add_observation(
    conn: sqlite3.Connection,
    timestamp: str,
    project: str,
    kind: str,
    title: str,
    summary: str,
    tags: str,
    tags_text: str,
    raw: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (timestamp, project, kind, title, summary, tags, tags_text, raw),
    )
    conn.commit()
    return int(cursor.lastrowid)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def stem_token(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            return token[: -len(suffix)]
    return token


def normalize_tags_list(tags: object) -> List[str]:
    if tags is None:
        return []
    if isinstance(tags, list):
        candidates = [str(tag) for tag in tags]
    elif isinstance(tags, str):
        raw = tags.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, list):
                    candidates = [str(tag) for tag in loaded]
                else:
                    candidates = [str(loaded)]
            except json.JSONDecodeError:
                candidates = [part.strip() for part in raw.split(",")]
        else:
            candidates = [part.strip() for part in raw.split(",")]
    else:
        candidates = [str(tags)]

    normalized: List[str] = []
    seen: set[str] = set()
    for token in candidates:
        clean = normalize_text(token).lower()
        if not clean:
            continue
        clean = stem_token(clean)
        if clean in TAG_BLACKLIST:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


def tags_to_json(tags_list: List[str]) -> str:
    return json.dumps(tags_list, ensure_ascii=False)


def tags_to_text(tags_list: List[str]) -> str:
    return " ".join(tags_list)


def parse_tags_json(value: str) -> List[str]:
    return normalize_tags_list(value)


def auto_tags_from_text(title: str, summary: str, limit: int = 6) -> List[str]:
    text = normalize_text(f"{title} {summary}").lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9\\-]{2,}", text)
    counts: dict[str, int] = {}
    for token in tokens:
        token = stem_token(token)
        if token in TAG_BLACKLIST:
            continue
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [tag for tag, _ in ranked[:limit]]


def run_llm_hook(payload: dict, hook_cmd: str | List[str]) -> dict:
    if not hook_cmd:
        return {}
    if isinstance(hook_cmd, str):
        try:
            cmd_parts = shlex.split(hook_cmd)
        except ValueError:
            return {}
    else:
        cmd_parts = list(hook_cmd)
    if not cmd_parts:
        return {}
    try:
        proc = subprocess.run(
            cmd_parts,
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return {}
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def normalize_rows(rows: Iterable[sqlite3.Row]) -> List[Observation]:
    results: List[Observation] = []
    for row in rows:
        results.append(
            Observation(
                id=row["id"],
                timestamp=row["timestamp"],
                project=row["project"],
                kind=row["kind"],
                title=row["title"],
                summary=row["summary"],
                tags=parse_tags_json(row["tags"]),
                raw=row["raw"],
            )
        )
    return results


def quote_fts_query(query: str) -> str:
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


def run_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    offset: int = 0,
    mode: str = "auto",
    quote: bool = False,
) -> List[dict]:
    query = query.strip()
    if not query:
        return []
    fts_query = quote_fts_query(query) if quote else query
    if mode != "like":
        try:
            rows = conn.execute(
                """
                SELECT observations.id, observations.timestamp, observations.project,
                       observations.kind, observations.title, observations.summary,
                       observations.tags, observations.raw,
                       bm25(observations_fts) AS score
                FROM observations_fts
                JOIN observations ON observations_fts.rowid = observations.id
                WHERE observations_fts MATCH ?
                ORDER BY score
                LIMIT ? OFFSET ?
                """,
                (fts_query, limit, offset),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "project": row["project"],
                    "kind": row["kind"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "tags": parse_tags_json(row["tags"]),
                    "score": row["score"],
                }
                for row in rows
            ]
        except sqlite3.OperationalError:
            if mode == "fts":
                raise

    rows = conn.execute(
        """
        SELECT id, timestamp, project, kind, title, summary, tags, raw
        FROM observations
        WHERE title LIKE ? OR summary LIKE ? OR tags_text LIKE ? OR raw LIKE ?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        tuple([f"%{query}%"] * 4 + [limit, offset]),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "project": row["project"],
            "kind": row["kind"],
            "title": row["title"],
            "summary": row["summary"],
            "tags": parse_tags_json(row["tags"]),
            "score": None,
        }
        for row in rows
    ]


def run_timeline(
    conn: sqlite3.Connection,
    start: Optional[str],
    end: Optional[str],
    around_id: Optional[int],
    window_minutes: int,
    limit: int,
    offset: int = 0,
) -> List[Observation]:
    if around_id is not None:
        row = conn.execute(
            "SELECT timestamp FROM observations WHERE id = ?",
            (around_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Observation {around_id} not found")
        ts = datetime.strptime(row["timestamp"], ISO_FORMAT).replace(tzinfo=timezone.utc)
        start_dt = ts - timedelta(minutes=window_minutes)
        end_dt = ts + timedelta(minutes=window_minutes)
        start = start_dt.strftime(ISO_FORMAT)
        end = end_dt.strftime(ISO_FORMAT)

    query = "SELECT * FROM observations"
    params: List[str] = []
    if start or end:
        query += " WHERE 1=1"
        if start:
            query += " AND timestamp >= ?"
            params.append(start)
        if end:
            query += " AND timestamp <= ?"
            params.append(end)
    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.append(str(limit))
    params.append(str(offset))
    rows = conn.execute(query, params).fetchall()
    return normalize_rows(rows)


def run_get(conn: sqlite3.Connection, ids: List[int]) -> List[Observation]:
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM observations WHERE id IN ({placeholders}) ORDER BY timestamp DESC",
        ids,
    ).fetchall()
    return normalize_rows(rows)


def run_list(conn: sqlite3.Connection, limit: int, offset: int = 0) -> List[Observation]:
    rows = conn.execute(
        "SELECT * FROM observations ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return normalize_rows(rows)


def run_export(conn: sqlite3.Connection, limit: int, offset: int = 0) -> List[Observation]:
    return run_list(conn, limit, offset)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone memory tool")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to SQLite database")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize the database")

    add_parser = subparsers.add_parser("add", help="Add an observation")
    add_parser.add_argument("--timestamp", default=utc_now())
    add_parser.add_argument("--project", default="cantool")
    add_parser.add_argument("--kind", default="note")
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--summary", required=True)
    add_parser.add_argument("--tags", default="")
    add_parser.add_argument("--raw", default="")
    add_parser.add_argument(
        "--auto-tags",
        action="store_true",
        help="Generate tags from title/summary when tags is empty",
    )
    add_parser.add_argument(
        "--llm-hook",
        default=DEFAULT_LLM_HOOK,
        help="Shell command to run for enrichment (reads JSON from stdin)",
    )

    search_parser = subparsers.add_parser("search", help="Search observations")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--offset", type=int, default=0)
    search_parser.add_argument(
        "--mode",
        choices=["auto", "fts", "like"],
        default="auto",
        help="Search mode: auto (fts then LIKE), fts, or like",
    )
    search_parser.add_argument(
        "--fts-quote",
        action="store_true",
        help="Quote the query to make FTS parsing safer",
    )

    timeline_parser = subparsers.add_parser("timeline", help="Timeline query")
    timeline_parser.add_argument("--start")
    timeline_parser.add_argument("--end")
    timeline_parser.add_argument("--around-id", type=int)
    timeline_parser.add_argument("--window-minutes", type=int, default=120)
    timeline_parser.add_argument("--limit", type=int, default=20)
    timeline_parser.add_argument("--offset", type=int, default=0)

    get_parser = subparsers.add_parser("get", help="Fetch observations by id")
    get_parser.add_argument("ids", help="Comma-separated observation ids")

    list_parser = subparsers.add_parser("list", help="List latest observations")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--offset", type=int, default=0)

    export_parser = subparsers.add_parser("export", help="Export observations")
    export_parser.add_argument("--format", choices=["json", "csv"], default="json")
    export_parser.add_argument("--output", default=None, help="Write output to a file")
    export_parser.add_argument("--limit", type=int, default=1000)
    export_parser.add_argument("--offset", type=int, default=0)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "init":
        init_db(args.db)
        print(json.dumps({"ok": True, "db": args.db}, indent=2))
        return

    conn = connect_db(args.db)
    ensure_schema(conn)
    ensure_fts(conn)

    if args.command == "add":
        title = normalize_text(args.title)
        summary = normalize_text(args.summary)
        tags_list = normalize_tags_list(args.tags)
        raw = args.raw
        if args.llm_hook:
            hook_payload = {
                "title": title,
                "summary": summary,
                "raw": raw,
                "project": args.project,
                "kind": args.kind,
                "tags": tags_list,
            }
            hook_result = run_llm_hook(hook_payload, args.llm_hook)
            title = normalize_text(hook_result.get("title", title))
            summary = normalize_text(hook_result.get("summary", summary))
            if "tags" in hook_result:
                tags_list = normalize_tags_list(hook_result.get("tags"))
        if args.auto_tags and not tags_list:
            tags_list = auto_tags_from_text(title, summary)
        tags_json = tags_to_json(tags_list)
        tags_text = tags_to_text(tags_list)
        obs_id = add_observation(
            conn,
            args.timestamp,
            args.project,
            args.kind,
            title,
            summary,
            tags_json,
            tags_text,
            raw,
        )
        print(json.dumps({"ok": True, "id": obs_id}, indent=2))
        return

    if args.command == "search":
        results = run_search(
            conn,
            args.query,
            args.limit,
            offset=args.offset,
            mode=args.mode,
            quote=args.fts_quote,
        )
        print(json.dumps({"ok": True, "results": results}, indent=2))
        return

    if args.command == "timeline":
        try:
            results = run_timeline(
                conn,
                args.start,
                args.end,
                args.around_id,
                args.window_minutes,
                args.limit,
                offset=args.offset,
            )
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            sys.exit(1)
        print(json.dumps({"ok": True, "results": [asdict(r) for r in results]}, indent=2))
        return

    if args.command == "get":
        ids = [int(part.strip()) for part in args.ids.split(",") if part.strip()]
        results = run_get(conn, ids)
        print(json.dumps({"ok": True, "results": [asdict(r) for r in results]}, indent=2))
        return

    if args.command == "list":
        results = run_list(conn, args.limit, offset=args.offset)
        print(json.dumps({"ok": True, "results": [asdict(r) for r in results]}, indent=2))
        return

    if args.command == "export":
        results = run_export(conn, args.limit, offset=args.offset)
        output = sys.stdout
        if args.output:
            output = open(args.output, "w", encoding="utf-8", newline="")
        try:
            if args.format == "json":
                json.dump([asdict(r) for r in results], output, indent=2)
                if output is sys.stdout:
                    output.write("\n")
            else:
                writer = csv.DictWriter(
                    output,
                    fieldnames=["id", "timestamp", "project", "kind", "title", "summary", "tags", "raw"],
                )
                writer.writeheader()
                for item in results:
                    row = asdict(item)
                    row["tags"] = tags_to_json(item.tags)
                    writer.writerow(row)
        finally:
            if output is not sys.stdout:
                output.close()
        return


if __name__ == "__main__":
    main()
