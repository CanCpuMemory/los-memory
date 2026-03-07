"""Session management functionality."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from memory_tool.utils import utc_now

if TYPE_CHECKING:
    import sqlite3


def get_active_session(conn: "sqlite3.Connection", profile: str) -> Optional[int]:
    """Get the currently active session ID for a profile."""
    row = conn.execute(
        "SELECT id FROM sessions WHERE profile = ? AND active = 1 ORDER BY started_at DESC LIMIT 1",
        (profile,),
    ).fetchone()
    return row["id"] if row else None


def set_active_session(
    conn: "sqlite3.Connection",
    profile: str,
    session_id: int,
    description: str = "",
) -> dict:
    """Set the active session for a profile."""
    # Deactivate all other sessions for this profile
    conn.execute(
        "UPDATE sessions SET active = 0 WHERE profile = ?",
        (profile,),
    )

    # Activate the specified session
    conn.execute(
        "UPDATE sessions SET active = 1, description = ? WHERE id = ? AND profile = ?",
        (description, session_id, profile),
    )
    conn.commit()

    return {"ok": True, "session_id": session_id, "profile": profile}


def start_session(
    conn: "sqlite3.Connection",
    profile: str,
    description: str = "",
) -> dict:
    """Start a new session."""
    # Deactivate current session
    conn.execute(
        "UPDATE sessions SET active = 0 WHERE profile = ?",
        (profile,),
    )

    # Create new session
    cursor = conn.execute(
        """
        INSERT INTO sessions (profile, started_at, description, active)
        VALUES (?, ?, ?, 1)
        """,
        (profile, utc_now(), description),
    )
    conn.commit()

    return {
        "ok": True,
        "session_id": int(cursor.lastrowid),
        "profile": profile,
        "description": description,
    }


def end_session(conn: "sqlite3.Connection", profile: str) -> dict:
    """End the current session."""
    session_id = get_active_session(conn, profile)

    if session_id:
        conn.execute(
            "UPDATE sessions SET active = 0, ended_at = ? WHERE id = ?",
            (utc_now(), session_id),
        )
        conn.commit()
        return {"ok": True, "session_id": session_id, "ended": True}

    return {"ok": False, "error": "No active session to end"}


def get_session(conn: "sqlite3.Connection", session_id: int) -> Optional[dict]:
    """Get session details."""
    row = conn.execute(
        "SELECT * FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()

    if not row:
        return None

    return {
        "id": row["id"],
        "profile": row["profile"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "description": row["description"],
        "active": bool(row["active"]),
    }


def list_sessions(conn: "sqlite3.Connection", profile: str, limit: int = 20) -> list[dict]:
    """List sessions for a profile."""
    rows = conn.execute(
        """
        SELECT * FROM sessions
        WHERE profile = ?
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (profile, limit),
    ).fetchall()

    return [
        {
            "id": row["id"],
            "profile": row["profile"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "description": row["description"],
            "active": bool(row["active"]),
        }
        for row in rows
    ]


def get_session_observations(
    conn: "sqlite3.Connection",
    session_id: int,
    limit: int = 100,
) -> list[dict]:
    """Get observations for a session."""
    from memory_tool.utils import parse_tags_json

    rows = conn.execute(
        """
        SELECT * FROM observations
        WHERE session_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (session_id, limit),
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
            "raw": row["raw"],
        }
        for row in rows
    ]
