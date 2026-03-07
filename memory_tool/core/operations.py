"""Core CRUD operations for observations."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from memory_tool.utils import normalize_tags_list, tags_to_json, utc_now

if TYPE_CHECKING:
    import sqlite3


def add_observation(
    conn: "sqlite3.Connection",
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
    """Add a new observation to the database.

    Returns:
        The ID of the newly created observation
    """
    cursor = conn.execute(
        """
        INSERT INTO observations
        (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id),
    )
    conn.commit()
    return int(cursor.lastrowid)


def run_add(
    conn: "sqlite3.Connection",
    project: str,
    kind: str,
    title: str,
    summary: str,
    tags: Optional[list[str]],
    raw: Optional[str],
    session_id: Optional[int],
    timestamp: Optional[str] = None,
    auto_tags: bool = True,
) -> dict:
    """Add a new observation with proper tag processing."""
    # Normalize and deduplicate tags
    if auto_tags:
        normalized = normalize_tags_list(tags or [])
    else:
        normalized = tags or []

    # Ensure title normalization
    normalized_title = title.strip()
    if not normalized_title.endswith(".") and not normalized_title.endswith("？"):
        normalized_title += "."

    # Build tag strings
    tags_json = tags_to_json(normalized)
    tags_text = " ".join(normalized).lower()

    obs_id = add_observation(
        conn,
        timestamp or utc_now(),
        project,
        kind,
        normalized_title,
        summary.strip(),
        tags_json,
        tags_text,
        raw or "",
        session_id,
    )

    return {
        "ok": True,
        "id": obs_id,
        "project": project,
        "kind": kind,
        "title": normalized_title,
        "tags": normalized,
    }


def run_get(conn: "sqlite3.Connection", ids: list[int]) -> list[dict]:
    """Get observations by IDs."""
    from memory_tool.utils import parse_tags_json

    if not ids:
        return []

    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM observations WHERE id IN ({placeholders})",
        ids,
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
            "session_id": row["session_id"] if "session_id" in row.keys() else None,
        }
        for row in rows
    ]


def run_list(
    conn: "sqlite3.Connection",
    project: Optional[str] = None,
    kind: Optional[str] = None,
    tag: Optional[str] = None,
    session_id: Optional[int] = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """List observations with filtering."""
    from memory_tool.utils import parse_tags_json

    conditions = []
    params: list = []

    if project:
        conditions.append("project = ?")
        params.append(project)
    if kind:
        conditions.append("kind = ?")
        params.append(kind)
    if tag:
        conditions.append("tags_text LIKE ?")
        params.append(f"%{tag.lower()}%")
    if session_id is not None:
        conditions.append("session_id = ?")
        params.append(session_id)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    rows = conn.execute(
        f"""
        SELECT * FROM observations
        {where_clause}
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()

    return normalize_rows(rows)


def run_delete(
    conn: "sqlite3.Connection",
    ids: list[int],
    dry_run: bool = True,
) -> dict:
    """Delete observations by IDs."""
    if not ids:
        return {"ok": False, "error": "No IDs provided"}

    # Check what will be deleted
    placeholders = ",".join("?" for _ in ids)
    to_delete = conn.execute(
        f"SELECT id, title FROM observations WHERE id IN ({placeholders})",
        ids,
    ).fetchall()

    if not to_delete:
        return {"ok": False, "error": "No matching observations found"}

    result = {
        "ok": True,
        "dry_run": dry_run,
        "count": len(to_delete),
        "items": [{"id": r["id"], "title": r["title"]} for r in to_delete],
    }

    if not dry_run:
        conn.execute(
            f"DELETE FROM observations WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        result["deleted"] = True

    return result


def run_edit(
    conn: "sqlite3.Connection",
    observation_id: int,
    project: Optional[str] = None,
    kind: Optional[str] = None,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    tags: Optional[list[str]] = None,
    raw: Optional[str] = None,
    timestamp: Optional[str] = None,
    auto_tags: bool = False,
) -> dict:
    """Edit an observation."""
    # Check if observation exists
    row = conn.execute(
        "SELECT * FROM observations WHERE id = ?",
        (observation_id,),
    ).fetchone()

    if not row:
        return {"ok": False, "error": f"Observation {observation_id} not found"}

    # Build update fields
    updates = []
    params: list = []

    if project is not None:
        updates.append("project = ?")
        params.append(project)
    if kind is not None:
        updates.append("kind = ?")
        params.append(kind)
    if title is not None:
        updates.append("title = ?")
        params.append(title)
    if summary is not None:
        updates.append("summary = ?")
        params.append(summary)
    if raw is not None:
        updates.append("raw = ?")
        params.append(raw)
    if timestamp is not None:
        updates.append("timestamp = ?")
        params.append(timestamp)

    if tags is not None:
        if auto_tags:
            normalized = normalize_tags(
                title or row["title"],
                summary or row["summary"],
                tags,
            )
        else:
            normalized = tags
        updates.append("tags = ?")
        params.append(tags_to_json(normalized))
        updates.append("tags_text = ?")
        params.append(" ".join(normalized).lower())

    if not updates:
        return {"ok": False, "error": "No fields to update"}

    params.append(observation_id)
    conn.execute(
        f"UPDATE observations SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()

    return {
        "ok": True,
        "id": observation_id,
        "updated": True,
    }


def run_search(
    conn: "sqlite3.Connection",
    query: str,
    project: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """Search observations by text."""
    from memory_tool.utils import parse_tags_json

    search_pattern = f"%{query.lower()}%"
    conditions = ["(LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR tags_text LIKE ?)"]
    params: list = [search_pattern, search_pattern, search_pattern]

    if project:
        conditions.append("project = ?")
        params.append(project)
    if kind:
        conditions.append("kind = ?")
        params.append(kind)

    rows = conn.execute(
        f"""
        SELECT * FROM observations
        WHERE {' AND '.join(conditions)}
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        params + [limit],
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
            "session_id": row["session_id"] if "session_id" in row.keys() else None,
        }
        for row in rows
    ]


def normalize_rows(rows: list) -> list[dict]:
    """Normalize database rows to observation dicts."""
    from memory_tool.utils import parse_tags_json

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
            "session_id": row["session_id"] if "session_id" in row.keys() else None,
        }
        for row in rows
    ]
