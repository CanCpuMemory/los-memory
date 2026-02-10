"""Checkpoint management functionality."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .utils import utc_now

if TYPE_CHECKING:
    import sqlite3
    from .models import Checkpoint, Observation


def create_checkpoint(
    conn: sqlite3.Connection,
    name: str,
    description: str,
    tag: str,
    session_id: Optional[int],
    project: str,
) -> int:
    """Create a new checkpoint."""
    if session_id:
        count = conn.execute(
            "SELECT COUNT(*) FROM observations WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
    else:
        count = conn.execute(
            "SELECT COUNT(*) FROM observations WHERE project = ?",
            (project,),
        ).fetchone()[0]

    cursor = conn.execute(
        """
        INSERT INTO checkpoints (timestamp, name, description, tag, session_id, observation_count, project)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (utc_now(), name, description, tag, session_id, count, project),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_checkpoints(
    conn: sqlite3.Connection,
    limit: int = 20,
    tag: Optional[str] = None,
) -> list["Checkpoint"]:
    """List checkpoints."""
    from .models import Checkpoint
    query = "SELECT * FROM checkpoints"
    params: list[object] = []
    if tag:
        query += " WHERE tag = ?"
        params.append(tag)
    query += " ORDER BY timestamp DESC LIMIT ? OFFSET 0"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [
        Checkpoint(
            id=row["id"],
            timestamp=row["timestamp"],
            name=row["name"],
            description=row["description"],
            tag=row["tag"],
            session_id=row["session_id"],
            observation_count=row["observation_count"],
            project=row["project"],
        )
        for row in rows
    ]


def get_checkpoint(conn: sqlite3.Connection, checkpoint_id: int) -> Optional["Checkpoint"]:
    """Get a checkpoint by ID."""
    from .models import Checkpoint
    row = conn.execute(
        "SELECT * FROM checkpoints WHERE id = ?",
        (checkpoint_id,),
    ).fetchone()
    if row is None:
        return None
    return Checkpoint(
        id=row["id"],
        timestamp=row["timestamp"],
        name=row["name"],
        description=row["description"],
        tag=row["tag"],
        session_id=row["session_id"],
        observation_count=row["observation_count"],
        project=row["project"],
    )


def get_checkpoint_observations(
    conn: sqlite3.Connection,
    checkpoint_id: int,
    limit: int = 100,
) -> list["Observation"]:
    """Get observations relevant to a checkpoint."""
    from .models import Observation
    from .utils import parse_tags_json
    checkpoint = get_checkpoint(conn, checkpoint_id)
    if not checkpoint:
        return []

    if checkpoint.session_id:
        rows = conn.execute(
            """
            SELECT * FROM observations
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (checkpoint.session_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM observations
            WHERE project = ? AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (checkpoint.project, checkpoint.timestamp, limit),
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


def resume_from_checkpoint(
    conn: sqlite3.Connection,
    checkpoint_id: int,
    profile: str,
) -> dict:
    """Resume work from a checkpoint."""
    from dataclasses import asdict
    from .projects import set_active_project
    from .sessions import set_active_session
    checkpoint = get_checkpoint(conn, checkpoint_id)
    if not checkpoint:
        raise ValueError(f"Checkpoint {checkpoint_id} not found")

    set_active_project(profile, checkpoint.project)

    if checkpoint.session_id:
        set_active_session(profile, checkpoint.session_id, "")
        session_info = {"session_id": checkpoint.session_id, "resumed": True}
    else:
        session_info = {"session_id": None}

    observations = get_checkpoint_observations(conn, checkpoint_id, limit=20)

    return {
        "checkpoint_id": checkpoint_id,
        "checkpoint_name": checkpoint.name,
        "project": checkpoint.project,
        **session_info,
        "observation_count": len(observations),
        "recent_observations": [asdict(o) for o in observations[:5]],
    }
