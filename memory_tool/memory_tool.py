#!/usr/bin/env python3
"""Standalone memory tool for logging and retrieving observations."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_DB = os.path.expanduser("~/.codex_memory/memory.db")
DEFAULT_LLM_HOOK = os.environ.get("MEMORY_LLM_HOOK", "")
TAG_BLACKLIST = {"the", "and", "for", "with", "from", "this", "that", "into", "over", "under"}


@dataclass
class Observation:
    id: int
    timestamp: str
    project: str
    kind: str
    title: str
    summary: str
    tags: str
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
            raw TEXT NOT NULL
        )
        """
    )
    conn.commit()


def ensure_fts(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts
            USING fts5(title, summary, tags, raw, content='observations', content_rowid='id')
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_ai
            AFTER INSERT ON observations BEGIN
                INSERT INTO observations_fts(rowid, title, summary, tags, raw)
                VALUES (new.id, new.title, new.summary, new.tags, new.raw);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_ad
            AFTER DELETE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, title, summary, tags, raw)
                VALUES ('delete', old.id, old.title, old.summary, old.tags, old.raw);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_au
            AFTER UPDATE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, title, summary, tags, raw)
                VALUES ('delete', old.id, old.title, old.summary, old.tags, old.raw);
                INSERT INTO observations_fts(rowid, title, summary, tags, raw)
                VALUES (new.id, new.title, new.summary, new.tags, new.raw);
            END;
            """
        )
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False


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
    raw: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO observations (timestamp, project, kind, title, summary, tags, raw)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (timestamp, project, kind, title, summary, tags, raw),
    )
    conn.commit()
    return int(cursor.lastrowid)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def auto_tags_from_text(title: str, summary: str, limit: int = 6) -> List[str]:
    text = normalize_text(f"{title} {summary}").lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9\\-]{2,}", text)
    counts: dict[str, int] = {}
    for token in tokens:
        if token in TAG_BLACKLIST:
            continue
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [tag for tag, _ in ranked[:limit]]


def run_llm_hook(payload: dict, hook_cmd: str) -> dict:
    if not hook_cmd:
        return {}
    try:
        proc = subprocess.run(
            hook_cmd,
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
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
                tags=row["tags"],
                raw=row["raw"],
            )
        )
    return results


def run_search(conn: sqlite3.Connection, query: str, limit: int) -> List[dict]:
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
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "project": row["project"],
                "kind": row["kind"],
                "title": row["title"],
                "summary": row["summary"],
                "tags": row["tags"],
                "score": row["score"],
            }
            for row in rows
        ]
    except sqlite3.OperationalError:
        rows = conn.execute(
            """
            SELECT id, timestamp, project, kind, title, summary, tags, raw
            FROM observations
            WHERE title LIKE ? OR summary LIKE ? OR tags LIKE ? OR raw LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            tuple([f"%{query}%"] * 4 + [limit]),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "project": row["project"],
                "kind": row["kind"],
                "title": row["title"],
                "summary": row["summary"],
                "tags": row["tags"],
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
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(str(limit))
    rows = conn.execute(query, params).fetchall()
    return normalize_rows(rows)


def run_get(conn: sqlite3.Connection, ids: List[int]) -> List[Observation]:
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM observations WHERE id IN ({placeholders}) ORDER BY timestamp DESC",
        ids,
    ).fetchall()
    return normalize_rows(rows)


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

    timeline_parser = subparsers.add_parser("timeline", help="Timeline query")
    timeline_parser.add_argument("--start")
    timeline_parser.add_argument("--end")
    timeline_parser.add_argument("--around-id", type=int)
    timeline_parser.add_argument("--window-minutes", type=int, default=120)
    timeline_parser.add_argument("--limit", type=int, default=20)

    get_parser = subparsers.add_parser("get", help="Fetch observations by id")
    get_parser.add_argument("ids", help="Comma-separated observation ids")

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
        tags = normalize_text(args.tags)
        raw = args.raw
        if args.llm_hook:
            hook_payload = {
                "title": title,
                "summary": summary,
                "raw": raw,
                "project": args.project,
                "kind": args.kind,
                "tags": tags,
            }
            hook_result = run_llm_hook(hook_payload, args.llm_hook)
            title = normalize_text(hook_result.get("title", title))
            summary = normalize_text(hook_result.get("summary", summary))
            tags = normalize_text(hook_result.get("tags", tags))
        if args.auto_tags and not tags:
            tags = ",".join(auto_tags_from_text(title, summary))
        obs_id = add_observation(
            conn,
            args.timestamp,
            args.project,
            args.kind,
            title,
            summary,
            tags,
            raw,
        )
        print(json.dumps({"ok": True, "id": obs_id}, indent=2))
        return

    if args.command == "search":
        results = run_search(conn, args.query, args.limit)
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


if __name__ == "__main__":
    main()
