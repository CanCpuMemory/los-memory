"""Session management functionality."""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Optional

from .database import ensure_schema
from .utils import utc_now

if TYPE_CHECKING:
    import sqlite3
    from .models import Observation, Session


def get_session_file_path(profile: str) -> str:
    """Get the path to the session state file for a profile."""
    from .utils import DEFAULT_PROFILE
    profile_name = (profile or DEFAULT_PROFILE).strip().lower()
    if profile_name == "codex":
        return os.path.expanduser("~/.codex_memory/current_session")
    elif profile_name == "claude":
        return os.path.expanduser("~/.claude_memory/current_session")
    else:
        return os.path.expanduser("~/.local/share/llm-memory/current_session")


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


def get_session(conn: sqlite3.Connection, session_id: int) -> Optional["Session"]:
    """Get a session by ID."""
    from .models import Session
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
) -> list["Session"]:
    """List sessions, optionally filtered by status."""
    from .models import Session
    query = "SELECT * FROM sessions"
    params: list[object] = []
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
) -> list["Observation"]:
    """Get all observations for a session."""
    from .models import Observation
    from .utils import parse_tags_json
    rows = conn.execute(
        """
        SELECT * FROM observations
        WHERE session_id = ?
        ORDER BY timestamp ASC
        LIMIT ? OFFSET ?
        """,
        (session_id, limit, offset),
    ).fetchall()
    return [
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
        for row in rows
    ]


def generate_session_summary(conn: sqlite3.Connection, session_id: int) -> str:
    """Generate an automatic summary for a session based on its observations."""
    observations = get_session_observations(conn, session_id, limit=1000)
    if not observations:
        return "No observations in session"

    kind_counts: dict[str, int] = {}
    for obs in observations:
        kind_counts[obs.kind] = kind_counts.get(obs.kind, 0) + 1

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
