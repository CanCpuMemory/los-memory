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
PROFILE_DB_PATHS = {
    "codex": "~/.codex_memory/memory.db",
    "claude": "~/.claude_memory/memory.db",
    "shared": "~/.local/share/llm-memory/memory.db",
}
PROFILE_CHOICES = tuple(PROFILE_DB_PATHS.keys())
DEFAULT_PROFILE = os.environ.get("MEMORY_PROFILE", "codex").strip().lower() or "codex"
if DEFAULT_PROFILE not in PROFILE_DB_PATHS:
    DEFAULT_PROFILE = "codex"
DEFAULT_DB = os.path.expanduser(PROFILE_DB_PATHS[DEFAULT_PROFILE])
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


def resolve_db_path(profile: str, explicit_db: Optional[str]) -> str:
    if explicit_db:
        return os.path.expanduser(explicit_db)
    profile_name = (profile or DEFAULT_PROFILE).strip().lower()
    if profile_name not in PROFILE_DB_PATHS:
        raise ValueError(f"Unknown profile '{profile_name}'. Expected one of: {', '.join(PROFILE_CHOICES)}")
    return os.path.expanduser(PROFILE_DB_PATHS[profile_name])


def connect_db(path: str) -> sqlite3.Connection:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
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


def parse_ids(ids_raw: str) -> List[int]:
    ids = [int(part.strip()) for part in ids_raw.split(",") if part.strip()]
    unique_ids: List[int] = []
    seen: set[int] = set()
    for item in ids:
        if item in seen:
            continue
        seen.add(item)
        unique_ids.append(item)
    return unique_ids


def run_edit(
    conn: sqlite3.Connection,
    obs_id: int,
    project: Optional[str],
    kind: Optional[str],
    title: Optional[str],
    summary: Optional[str],
    tags: Optional[str],
    raw: Optional[str],
    timestamp: Optional[str],
    auto_tags: bool,
) -> dict:
    row = conn.execute("SELECT * FROM observations WHERE id = ?", (obs_id,)).fetchone()
    if row is None:
        raise ValueError(f"Observation {obs_id} not found")

    current_title = row["title"]
    current_summary = row["summary"]
    current_tags = parse_tags_json(row["tags"])

    updates: dict[str, object] = {}
    if project is not None:
        updates["project"] = project
    if kind is not None:
        updates["kind"] = kind
    if title is not None:
        updates["title"] = normalize_text(title)
        current_title = str(updates["title"])
    if summary is not None:
        updates["summary"] = normalize_text(summary)
        current_summary = str(updates["summary"])
    if raw is not None:
        updates["raw"] = raw
    if timestamp is not None:
        updates["timestamp"] = timestamp

    if tags is not None:
        current_tags = normalize_tags_list(tags)
    if auto_tags and tags is None:
        current_tags = auto_tags_from_text(current_title, current_summary)
    if tags is not None or auto_tags:
        updates["tags"] = tags_to_json(current_tags)
        updates["tags_text"] = tags_to_text(current_tags)

    if not updates:
        raise ValueError("No changes requested. Provide at least one editable field.")

    set_clause = ", ".join(f"{column} = ?" for column in updates.keys())
    params = list(updates.values()) + [obs_id]
    conn.execute(f"UPDATE observations SET {set_clause} WHERE id = ?", params)
    conn.commit()

    updated = conn.execute("SELECT * FROM observations WHERE id = ?", (obs_id,)).fetchone()
    result = normalize_rows([updated])[0]
    return {"ok": True, "updated": asdict(result)}


def run_delete(conn: sqlite3.Connection, ids: List[int], dry_run: bool) -> dict:
    if not ids:
        raise ValueError("No ids provided")
    placeholders = ",".join("?" for _ in ids)
    matched = int(
        conn.execute(
            f"SELECT COUNT(*) AS c FROM observations WHERE id IN ({placeholders})",
            ids,
        ).fetchone()["c"]
    )
    deleted = 0
    if not dry_run and matched:
        cursor = conn.execute(f"DELETE FROM observations WHERE id IN ({placeholders})", ids)
        deleted = int(cursor.rowcount)
        conn.commit()
    elif dry_run:
        conn.rollback()
    return {
        "ok": True,
        "ids": ids,
        "matched": matched,
        "deleted": deleted,
        "dry_run": dry_run,
    }


def run_clean(
    conn: sqlite3.Connection,
    before: Optional[str],
    older_than_days: Optional[int],
    project: Optional[str],
    kind: Optional[str],
    tag: Optional[str],
    delete_all: bool,
    dry_run: bool,
    vacuum: bool,
) -> dict:
    if before and older_than_days is not None:
        raise ValueError("Use either --before or --older-than-days, not both")
    filters: List[str] = []
    params: List[object] = []

    cutoff = before
    if older_than_days is not None:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        cutoff = cutoff_dt.strftime(ISO_FORMAT)
    if cutoff:
        filters.append("timestamp < ?")
        params.append(cutoff)
    if project:
        filters.append("project = ?")
        params.append(project)
    if kind:
        filters.append("kind = ?")
        params.append(kind)

    tag_values = normalize_tags_list(tag) if tag else []
    if tag_values:
        tag_filters: List[str] = []
        for item in tag_values:
            tag_filters.append("tags_text LIKE ?")
            params.append(f"%{item}%")
        filters.append(f"({' OR '.join(tag_filters)})")

    if not filters and not delete_all:
        raise ValueError("Refusing to clean without filters. Use --all to delete everything.")

    where_clause = " AND ".join(filters) if filters else "1=1"
    matched = int(
        conn.execute(
            f"SELECT COUNT(*) AS c FROM observations WHERE {where_clause}",
            params,
        ).fetchone()["c"]
    )

    deleted = 0
    if not dry_run and matched:
        cursor = conn.execute(f"DELETE FROM observations WHERE {where_clause}", params)
        deleted = int(cursor.rowcount)
        conn.commit()
    elif dry_run:
        conn.rollback()

    if vacuum and not dry_run:
        conn.execute("VACUUM")

    return {
        "ok": True,
        "matched": matched,
        "deleted": deleted,
        "dry_run": dry_run,
        "before": cutoff,
        "vacuum": bool(vacuum and not dry_run),
    }


def run_manage(conn: sqlite3.Connection, action: str, limit: int) -> dict:
    if action == "stats":
        row = conn.execute(
            """
            SELECT COUNT(*) AS total, MIN(timestamp) AS earliest, MAX(timestamp) AS latest
            FROM observations
            """
        ).fetchone()
        projects = [
            {"project": item["project"], "count": item["count"]}
            for item in conn.execute(
                """
                SELECT project, COUNT(*) AS count
                FROM observations
                GROUP BY project
                ORDER BY count DESC, project ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        kinds = [
            {"kind": item["kind"], "count": item["count"]}
            for item in conn.execute(
                """
                SELECT kind, COUNT(*) AS count
                FROM observations
                GROUP BY kind
                ORDER BY count DESC, kind ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        return {
            "ok": True,
            "action": action,
            "total": int(row["total"]),
            "earliest": row["earliest"],
            "latest": row["latest"],
            "projects": projects,
            "kinds": kinds,
        }

    if action == "projects":
        rows = conn.execute(
            """
            SELECT project, COUNT(*) AS count
            FROM observations
            GROUP BY project
            ORDER BY count DESC, project ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {
            "ok": True,
            "action": action,
            "projects": [{"project": row["project"], "count": row["count"]} for row in rows],
        }

    if action == "tags":
        counts: dict[str, int] = {}
        for row in conn.execute("SELECT tags FROM observations").fetchall():
            for tag in parse_tags_json(row["tags"]):
                counts[tag] = counts.get(tag, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return {
            "ok": True,
            "action": action,
            "tags": [{"tag": tag, "count": count} for tag, count in ranked],
        }

    if action == "vacuum":
        conn.execute("VACUUM")
        return {"ok": True, "action": action, "vacuumed": True}

    raise ValueError(f"Unsupported manage action: {action}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone memory tool")
    parser.add_argument(
        "--profile",
        choices=PROFILE_CHOICES,
        default=DEFAULT_PROFILE,
        help="Memory profile to select default DB path",
    )
    parser.add_argument("--db", default=None, help="Path to SQLite database (overrides --profile)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize the database")

    add_parser = subparsers.add_parser("add", help="Add an observation")
    add_parser.add_argument("--timestamp", default=utc_now())
    add_parser.add_argument("--project", default="general")
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

    edit_parser = subparsers.add_parser("edit", help="Edit an observation by id")
    edit_parser.add_argument("--id", type=int, required=True)
    edit_parser.add_argument("--timestamp", default=None)
    edit_parser.add_argument("--project", default=None)
    edit_parser.add_argument("--kind", default=None)
    edit_parser.add_argument("--title", default=None)
    edit_parser.add_argument("--summary", default=None)
    edit_parser.add_argument("--tags", default=None)
    edit_parser.add_argument("--raw", default=None)
    edit_parser.add_argument("--auto-tags", action="store_true")

    delete_parser = subparsers.add_parser("delete", help="Delete observations by ids")
    delete_parser.add_argument("ids", help="Comma-separated observation ids")
    delete_parser.add_argument("--dry-run", action="store_true", help="Show matches without deleting")

    list_parser = subparsers.add_parser("list", help="List latest observations")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--offset", type=int, default=0)

    export_parser = subparsers.add_parser("export", help="Export observations")
    export_parser.add_argument("--format", choices=["json", "csv"], default="json")
    export_parser.add_argument("--output", default=None, help="Write output to a file")
    export_parser.add_argument("--limit", type=int, default=1000)
    export_parser.add_argument("--offset", type=int, default=0)

    clean_parser = subparsers.add_parser("clean", help="Delete old or filtered observations")
    clean_parser.add_argument("--before", default=None, help="Delete rows before ISO UTC timestamp")
    clean_parser.add_argument(
        "--older-than-days",
        type=int,
        default=None,
        help="Delete rows older than N days",
    )
    clean_parser.add_argument("--project", default=None)
    clean_parser.add_argument("--kind", default=None)
    clean_parser.add_argument("--tag", default=None, help="Tag filter (comma-separated)")
    clean_parser.add_argument("--all", action="store_true", help="Delete all observations")
    clean_parser.add_argument("--dry-run", action="store_true", help="Show matched rows without deleting")
    clean_parser.add_argument("--vacuum", action="store_true", help="VACUUM database after cleanup")

    manage_parser = subparsers.add_parser("manage", help="Manage and inspect memory database")
    manage_parser.add_argument("action", choices=["stats", "projects", "tags", "vacuum"])
    manage_parser.add_argument("--limit", type=int, default=20)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = resolve_db_path(args.profile, args.db)
    if args.command == "init":
        init_db(db_path)
        print(json.dumps({"ok": True, "db": db_path, "profile": args.profile}, indent=2))
        return

    conn = connect_db(db_path)
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
        ids = parse_ids(args.ids)
        results = run_get(conn, ids)
        print(json.dumps({"ok": True, "results": [asdict(r) for r in results]}, indent=2))
        return

    if args.command == "edit":
        try:
            result = run_edit(
                conn,
                obs_id=args.id,
                project=args.project,
                kind=args.kind,
                title=args.title,
                summary=args.summary,
                tags=args.tags,
                raw=args.raw,
                timestamp=args.timestamp,
                auto_tags=args.auto_tags,
            )
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            sys.exit(1)
        result["db"] = db_path
        result["profile"] = args.profile
        print(json.dumps(result, indent=2))
        return

    if args.command == "delete":
        try:
            result = run_delete(conn, parse_ids(args.ids), args.dry_run)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            sys.exit(1)
        result["db"] = db_path
        result["profile"] = args.profile
        print(json.dumps(result, indent=2))
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

    if args.command == "clean":
        try:
            result = run_clean(
                conn,
                before=args.before,
                older_than_days=args.older_than_days,
                project=args.project,
                kind=args.kind,
                tag=args.tag,
                delete_all=args.all,
                dry_run=args.dry_run,
                vacuum=args.vacuum,
            )
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            sys.exit(1)
        result["db"] = db_path
        result["profile"] = args.profile
        print(json.dumps(result, indent=2))
        return

    if args.command == "manage":
        try:
            result = run_manage(conn, args.action, args.limit)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            sys.exit(1)
        result["db"] = db_path
        result["profile"] = args.profile
        print(json.dumps(result, indent=2))
        return


if __name__ == "__main__":
    main()
