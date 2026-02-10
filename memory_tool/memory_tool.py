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
SCHEMA_VERSION = 3
SESSION_FILE = ".los_memory_session"
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
    session_id: Optional[int] = None


@dataclass
class Session:
    id: int
    start_time: str
    end_time: Optional[str]
    project: str
    working_dir: str
    agent_type: str
    summary: str
    status: str


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


def resolve_db_path(profile: str, explicit_db: Optional[str]) -> str:
    if explicit_db:
        return os.path.expanduser(explicit_db)
    profile_name = (profile or DEFAULT_PROFILE).strip().lower()
    if profile_name not in PROFILE_DB_PATHS:
        raise ValueError(f"Unknown profile '{profile_name}'. Expected one of: {', '.join(PROFILE_CHOICES)}")
    return os.path.expanduser(PROFILE_DB_PATHS[profile_name])


def get_session_file_path(profile: str) -> str:
    """Get the path to the session state file for a profile."""
    profile_name = (profile or DEFAULT_PROFILE).strip().lower()
    if profile_name == "codex":
        return os.path.expanduser("~/.codex_memory/current_session")
    elif profile_name == "claude":
        return os.path.expanduser("~/.claude_memory/current_session")
    else:
        return os.path.expanduser("~/.local/share/llm-memory/current_session")


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
            raw TEXT NOT NULL,
            session_id INTEGER REFERENCES sessions(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            project TEXT NOT NULL,
            working_dir TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            summary TEXT DEFAULT '',
            status TEXT DEFAULT 'active'
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
        version = 2
    if version < 3:
        # Version 3 adds sessions table and session_id to observations
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                project TEXT NOT NULL,
                working_dir TEXT NOT NULL,
                agent_type TEXT NOT NULL,
                summary TEXT DEFAULT '',
                status TEXT DEFAULT 'active'
            )
            """
        )
        try:
            conn.execute(
                "ALTER TABLE observations ADD COLUMN session_id INTEGER REFERENCES sessions(id)"
            )
        except sqlite3.OperationalError:
            pass
        set_schema_version(conn, 3)


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
    session_id: Optional[int] = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id),
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
                session_id=row["session_id"] if "session_id" in row.keys() else None,
            )
        )
    return results


# Session management functions
def start_session(
    conn: sqlite3.Connection,
    project: str,
    working_dir: str,
    agent_type: str,
    summary: str = "",
) -> int:
    """Start a new session and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO sessions (start_time, end_time, project, working_dir, agent_type, summary, status)
        VALUES (?, NULL, ?, ?, ?, ?, 'active')
        """,
        (utc_now(), project, working_dir, agent_type, summary),
    )
    conn.commit()
    return int(cursor.lastrowid)


def end_session(conn: sqlite3.Connection, session_id: int, summary: Optional[str] = None) -> None:
    """End a session by setting its end_time and optionally updating summary."""
    if summary:
        conn.execute(
            """
            UPDATE sessions SET end_time = ?, status = 'completed', summary = ?
            WHERE id = ?
            """,
            (utc_now(), summary, session_id),
        )
    else:
        conn.execute(
            """
            UPDATE sessions SET end_time = ?, status = 'completed'
            WHERE id = ?
            """,
            (utc_now(), session_id),
        )
    conn.commit()


def get_session(conn: sqlite3.Connection, session_id: int) -> Optional[Session]:
    """Get a session by ID."""
    row = conn.execute(
        "SELECT * FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return Session(
        id=row["id"],
        start_time=row["start_time"],
        end_time=row["end_time"],
        project=row["project"],
        working_dir=row["working_dir"],
        agent_type=row["agent_type"],
        summary=row["summary"],
        status=row["status"],
    )


def list_sessions(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Session]:
    """List sessions, optionally filtered by status."""
    query = "SELECT * FROM sessions"
    params: List[object] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY start_time DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()
    return [
        Session(
            id=row["id"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            project=row["project"],
            working_dir=row["working_dir"],
            agent_type=row["agent_type"],
            summary=row["summary"],
            status=row["status"],
        )
        for row in rows
    ]


def get_session_observations(
    conn: sqlite3.Connection,
    session_id: int,
    limit: int = 100,
    offset: int = 0,
) -> List[Observation]:
    """Get all observations for a session."""
    rows = conn.execute(
        """
        SELECT * FROM observations
        WHERE session_id = ?
        ORDER BY timestamp ASC
        LIMIT ? OFFSET ?
        """,
        (session_id, limit, offset),
    ).fetchall()
    return normalize_rows(rows)


def get_active_session(profile: str) -> Optional[dict]:
    """Get the currently active session from the session file."""
    session_file = get_session_file_path(profile)
    if not os.path.exists(session_file):
        return None
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def set_active_session(profile: str, session_id: int, db_path: str) -> None:
    """Set the active session in the session file."""
    session_file = get_session_file_path(profile)
    session_dir = os.path.dirname(session_file)
    if session_dir:
        os.makedirs(session_dir, exist_ok=True)
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump({"session_id": session_id, "db_path": db_path}, f)


def clear_active_session(profile: str) -> None:
    """Clear the active session."""
    session_file = get_session_file_path(profile)
    if os.path.exists(session_file):
        os.remove(session_file)


def generate_session_summary(conn: sqlite3.Connection, session_id: int) -> str:
    """Generate an automatic summary for a session based on its observations."""
    observations = get_session_observations(conn, session_id, limit=1000)
    if not observations:
        return "No observations in session"

    # Count kinds
    kind_counts: dict[str, int] = {}
    for obs in observations:
        kind_counts[obs.kind] = kind_counts.get(obs.kind, 0) + 1

    # Get all tags
    all_tags: set[str] = set()
    for obs in observations:
        all_tags.update(obs.tags)

    summary_parts = [f"{len(observations)} observation(s)"]

    if kind_counts:
        kind_str = ", ".join(f"{count} {kind}" for kind, count in sorted(kind_counts.items()))
        summary_parts.append(f"Types: {kind_str}")

    if all_tags:
        tag_str = ", ".join(sorted(all_tags)[:10])
        summary_parts.append(f"Tags: {tag_str}")

    return "; ".join(summary_parts)


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
                    "session_id": row["session_id"] if "session_id" in row.keys() else None,
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
            "session_id": row["session_id"] if "session_id" in row.keys() else None,
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
    results = normalize_rows(rows)
    return results


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


def run_share(
    conn: sqlite3.Connection,
    output_path: str,
    fmt: str,
    project: Optional[str],
    kind: Optional[str],
    tag: Optional[str],
    session_id: Optional[int],
    since: Optional[str],
    limit: int,
) -> dict:
    """Create a shareable bundle of observations."""
    # Build query based on filters
    query = "SELECT * FROM observations WHERE 1=1"
    params: List[object] = []

    if project:
        query += " AND project = ?"
        params.append(project)
    if kind:
        query += " AND kind = ?"
        params.append(kind)
    if session_id:
        query += " AND session_id = ?"
        params.append(session_id)
    if since:
        query += " AND timestamp >= ?"
        params.append(since)
    if tag:
        tag_values = normalize_tags_list(tag)
        if tag_values:
            tag_filters = []
            for _ in tag_values:
                tag_filters.append("tags_text LIKE ?")
                params.append(f"%{tag_values[0]}%")
            query += f" AND ({' OR '.join(tag_filters)})"

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    observations = normalize_rows(rows)

    # Get related sessions if any observations have session_ids
    session_ids = {obs.session_id for obs in observations if obs.session_id}
    sessions: List[Session] = []
    if session_ids:
        placeholders = ",".join("?" for _ in session_ids)
        session_rows = conn.execute(
            f"SELECT * FROM sessions WHERE id IN ({placeholders})",
            list(session_ids),
        ).fetchall()
        sessions = [
            Session(
                id=row["id"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                project=row["project"],
                working_dir=row["working_dir"],
                agent_type=row["agent_type"],
                summary=row["summary"],
                status=row["status"],
            )
            for row in session_rows
        ]

    # Generate bundle metadata
    bundle = {
        "version": "1.0",
        "exported_at": utc_now(),
        "format": fmt,
        "filters": {
            "project": project,
            "kind": kind,
            "tag": tag,
            "session_id": session_id,
            "since": since,
        },
        "stats": {
            "observation_count": len(observations),
            "session_count": len(sessions),
        },
        "sessions": [asdict(s) for s in sessions],
        "observations": [asdict(o) for o in observations],
    }

    # Write output based on format
    output_path = os.path.expanduser(output_path)
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    if fmt == "json":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2)
    elif fmt == "markdown":
        _write_markdown_bundle(output_path, bundle)
    elif fmt == "html":
        _write_html_bundle(output_path, bundle)

    return {
        "ok": True,
        "output_path": output_path,
        "format": fmt,
        "observations": len(observations),
        "sessions": len(sessions),
    }


def _write_markdown_bundle(output_path: str, bundle: dict) -> None:
    """Write bundle as a Markdown report."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# Memory Context Bundle\n\n")
        f.write(f"Exported: {bundle['exported_at']}\n\n")
        f.write(f"## Stats\n\n")
        f.write(f"- Observations: {bundle['stats']['observation_count']}\n")
        f.write(f"- Sessions: {bundle['stats']['session_count']}\n\n")

        if bundle["sessions"]:
            f.write(f"## Sessions\n\n")
            for session in bundle["sessions"]:
                f.write(f"### Session {session['id']}: {session['project']}\n\n")
                f.write(f"- Status: {session['status']}\n")
                f.write(f"- Started: {session['start_time']}\n")
                if session['end_time']:
                    f.write(f"- Ended: {session['end_time']}\n")
                f.write(f"- Agent: {session['agent_type']}\n")
                f.write(f"- Working dir: {session['working_dir']}\n")
                if session['summary']:
                    f.write(f"- Summary: {session['summary']}\n")
                f.write(f"\n")

        f.write(f"## Observations\n\n")
        for obs in bundle["observations"]:
            f.write(f"### {obs['title']}\n\n")
            f.write(f"- **ID**: {obs['id']}\n")
            f.write(f"- **Time**: {obs['timestamp']}\n")
            f.write(f"- **Project**: {obs['project']}\n")
            f.write(f"- **Kind**: {obs['kind']}\n")
            if obs['session_id']:
                f.write(f"- **Session**: {obs['session_id']}\n")
            if obs['tags']:
                f.write(f"- **Tags**: {', '.join(obs['tags'])}\n")
            f.write(f"\n{obs['summary']}\n\n")
            if obs['raw']:
                f.write(f"```\n{obs['raw']}\n```\n\n")


def _write_html_bundle(output_path: str, bundle: dict) -> None:
    """Write bundle as an HTML report."""
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Memory Context Bundle</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
        h1 {{ color: #333; border-bottom: 2px solid #4a9eff; padding-bottom: 10px; }}
        h2 {{ color: #555; margin-top: 30px; }}
        h3 {{ color: #666; margin-top: 25px; }}
        .stats {{ background: #f5f5f5; padding: 15px; border-radius: 8px; margin: 20px 0; }}
        .session {{ background: #f0f7ff; padding: 15px; border-radius: 8px; margin: 15px 0; border-left: 4px solid #4a9eff; }}
        .observation {{ background: #fafafa; padding: 15px; border-radius: 8px; margin: 15px 0; border-left: 4px solid #4caf50; }}
        .meta {{ color: #666; font-size: 0.9em; }}
        .tags {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }}
        .tag {{ background: #e3f2fd; color: #1976d2; padding: 2px 8px; border-radius: 12px; font-size: 0.85em; }}
        pre {{ background: #263238; color: #aed581; padding: 15px; border-radius: 8px; overflow-x: auto; }}
        .timestamp {{ color: #888; font-size: 0.85em; }}
    </style>
</head>
<body>
    <h1>Memory Context Bundle</h1>
    <p class="timestamp">Exported: {bundle['exported_at']}</p>

    <div class="stats">
        <h2>Stats</h2>
        <p>Observations: <strong>{bundle['stats']['observation_count']}</strong></p>
        <p>Sessions: <strong>{bundle['stats']['session_count']}</strong></p>
    </div>
"""

    if bundle["sessions"]:
        html += "    <h2>Sessions</h2>\n"
        for session in bundle["sessions"]:
            html += f"""
    <div class="session">
        <h3>Session {session['id']}: {session['project']}</h3>
        <div class="meta">
            <p>Status: <strong>{session['status']}</strong></p>
            <p>Started: {session['start_time']}</p>
            {f"<p>Ended: {session['end_time']}</p>" if session['end_time'] else ""}
            <p>Agent: {session['agent_type']}</p>
            <p>Working dir: {session['working_dir']}</p>
            {f"<p>Summary: {session['summary']}</p>" if session['summary'] else ""}
        </div>
    </div>
"""

    html += "    <h2>Observations</h2>\n"
    for obs in bundle["observations"]:
        tags_html = "".join(f'<span class="tag">{tag}</span>' for tag in obs['tags']) if obs['tags'] else ""
        raw_html = f"<pre>{obs['raw']}</pre>" if obs['raw'] else ""
        session_info = f"<p>Session: {obs['session_id']}</p>" if obs['session_id'] else ""
        html += f"""
    <div class="observation">
        <h3>{obs['title']}</h3>
        <div class="meta">
            <p>ID: {obs['id']} | Time: {obs['timestamp']} | Project: {obs['project']} | Kind: {obs['kind']}</p>
            {session_info}
        </div>
        <p>{obs['summary']}</p>
        <div class="tags">{tags_html}</div>
        {raw_html}
    </div>
"""

    html += """
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def run_import(
    conn: sqlite3.Connection,
    file_path: str,
    project_override: Optional[str],
    dry_run: bool,
) -> dict:
    """Import a shared context bundle."""
    file_path = os.path.expanduser(file_path)
    with open(file_path, "r", encoding="utf-8") as f:
        bundle = json.load(f)

    imported_observations = 0
    imported_sessions = 0
    session_id_map: dict[int, int] = {}

    # Import sessions first
    if "sessions" in bundle and bundle["sessions"]:
        for session_data in bundle["sessions"]:
            old_id = session_data["id"]
            if not dry_run:
                cursor = conn.execute(
                    """
                    INSERT INTO sessions (start_time, end_time, project, working_dir, agent_type, summary, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_data["start_time"],
                        session_data.get("end_time"),
                        session_data["project"],
                        session_data["working_dir"],
                        session_data["agent_type"],
                        session_data.get("summary", ""),
                        session_data.get("status", "completed"),
                    ),
                )
                new_id = int(cursor.lastrowid)
                session_id_map[old_id] = new_id
            imported_sessions += 1

    # Import observations
    if "observations" in bundle and bundle["observations"]:
        for obs_data in bundle["observations"]:
            project = project_override or obs_data["project"]
            old_session_id = obs_data.get("session_id")
            new_session_id = session_id_map.get(old_session_id) if old_session_id else None

            if not dry_run:
                tags_list = obs_data.get("tags", [])
                conn.execute(
                    """
                    INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        obs_data["timestamp"],
                        project,
                        obs_data["kind"],
                        obs_data["title"],
                        obs_data["summary"],
                        tags_to_json(tags_list),
                        tags_to_text(tags_list),
                        obs_data.get("raw", ""),
                        new_session_id,
                    ),
                )
            imported_observations += 1

    if not dry_run:
        conn.commit()

    return {
        "ok": True,
        "dry_run": dry_run,
        "imported_observations": imported_observations,
        "imported_sessions": imported_sessions,
        "source_file": file_path,
    }


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

    # Session commands
    session_parser = subparsers.add_parser("session", help="Session management")
    session_subparsers = session_parser.add_subparsers(dest="session_action", required=True)

    session_start_parser = session_subparsers.add_parser("start", help="Start a new session")
    session_start_parser.add_argument("--project", default="general", help="Project name")
    session_start_parser.add_argument("--working-dir", default=os.getcwd(), help="Working directory")
    session_start_parser.add_argument("--agent-type", default=DEFAULT_PROFILE, help="Agent type (codex/claude)")
    session_start_parser.add_argument("--summary", default="", help="Session summary/description")

    session_stop_parser = session_subparsers.add_parser("stop", help="Stop the current session")
    session_stop_parser.add_argument("--summary", default=None, help="Update session summary on stop")

    session_list_parser = session_subparsers.add_parser("list", help="List sessions")
    session_list_parser.add_argument("--status", choices=["active", "completed"], default=None)
    session_list_parser.add_argument("--limit", type=int, default=20)
    session_list_parser.add_argument("--offset", type=int, default=0)

    session_show_parser = session_subparsers.add_parser("show", help="Show session details")
    session_show_parser.add_argument("session_id", type=int, help="Session ID")
    session_show_parser.add_argument("--observations", action="store_true", help="Include observations")

    session_resume_parser = session_subparsers.add_parser("resume", help="Resume a session")
    session_resume_parser.add_argument("session_id", type=int, nargs="?", default=None, help="Session ID (or use active session)")

    # Share command
    share_parser = subparsers.add_parser("share", help="Create a shareable context bundle")
    share_parser.add_argument("--output", "-o", required=True, help="Output file path")
    share_parser.add_argument("--format", choices=["json", "markdown", "html"], default="json", help="Export format")
    share_parser.add_argument("--project", default=None, help="Filter by project")
    share_parser.add_argument("--kind", default=None, help="Filter by kind")
    share_parser.add_argument("--tag", default=None, help="Filter by tag")
    share_parser.add_argument("--session", type=int, default=None, help="Filter by session ID")
    share_parser.add_argument("--since", default=None, help="Include observations since ISO timestamp")
    share_parser.add_argument("--limit", type=int, default=1000, help="Maximum observations to include")

    # Import command
    import_parser = subparsers.add_parser("import", help="Import a shared context bundle")
    import_parser.add_argument("file", help="Bundle file to import")
    import_parser.add_argument("--project", default=None, help="Override project name")
    import_parser.add_argument("--dry-run", action="store_true", help="Preview without importing")

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
        # Check for active session
        active_session = get_active_session(args.profile)
        session_id = active_session["session_id"] if active_session else None
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
            session_id,
        )
        result = {"ok": True, "id": obs_id}
        if session_id:
            result["session_id"] = session_id
        print(json.dumps(result, indent=2))
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

    if args.command == "session":
        if args.session_action == "start":
            session_id = start_session(
                conn,
                project=args.project,
                working_dir=args.working_dir,
                agent_type=args.agent_type,
                summary=args.summary,
            )
            set_active_session(args.profile, session_id, db_path)
            print(json.dumps({
                "ok": True,
                "action": "start",
                "session_id": session_id,
                "project": args.project,
                "working_dir": args.working_dir,
            }, indent=2))
            return

        if args.session_action == "stop":
            active = get_active_session(args.profile)
            if not active:
                print(json.dumps({"ok": False, "error": "No active session"}, indent=2))
                sys.exit(1)
            session_id = active["session_id"]
            summary = args.summary
            if not summary:
                summary = generate_session_summary(conn, session_id)
            end_session(conn, session_id, summary)
            clear_active_session(args.profile)
            print(json.dumps({
                "ok": True,
                "action": "stop",
                "session_id": session_id,
                "summary": summary,
            }, indent=2))
            return

        if args.session_action == "list":
            sessions = list_sessions(conn, status=args.status, limit=args.limit, offset=args.offset)
            print(json.dumps({
                "ok": True,
                "action": "list",
                "sessions": [asdict(s) for s in sessions],
            }, indent=2))
            return

        if args.session_action == "show":
            session = get_session(conn, args.session_id)
            if not session:
                print(json.dumps({"ok": False, "error": f"Session {args.session_id} not found"}, indent=2))
                sys.exit(1)
            result = {
                "ok": True,
                "action": "show",
                "session": asdict(session),
            }
            if args.observations:
                observations = get_session_observations(conn, args.session_id)
                result["observations"] = [asdict(o) for o in observations]
            print(json.dumps(result, indent=2))
            return

        if args.session_action == "resume":
            if args.session_id:
                session = get_session(conn, args.session_id)
                if not session:
                    print(json.dumps({"ok": False, "error": f"Session {args.session_id} not found"}, indent=2))
                    sys.exit(1)
                set_active_session(args.profile, args.session_id, db_path)
                print(json.dumps({
                    "ok": True,
                    "action": "resume",
                    "session_id": args.session_id,
                }, indent=2))
            else:
                active = get_active_session(args.profile)
                if not active:
                    print(json.dumps({"ok": False, "error": "No active session to resume"}, indent=2))
                    sys.exit(1)
                print(json.dumps({
                    "ok": True,
                    "action": "resume",
                    "session_id": active["session_id"],
                }, indent=2))
            return

        return

    if args.command == "share":
        try:
            result = run_share(
                conn,
                output_path=args.output,
                fmt=args.format,
                project=args.project,
                kind=args.kind,
                tag=args.tag,
                session_id=args.session,
                since=args.since,
                limit=args.limit,
            )
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            sys.exit(1)
        result["db"] = db_path
        result["profile"] = args.profile
        print(json.dumps(result, indent=2))
        return

    if args.command == "import":
        try:
            result = run_import(
                conn,
                file_path=args.file,
                project_override=args.project,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            sys.exit(1)
        result["db"] = db_path
        result["profile"] = args.profile
        print(json.dumps(result, indent=2))
        return


if __name__ == "__main__":
    main()
