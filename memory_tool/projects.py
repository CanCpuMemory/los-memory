"""Project management functionality."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3


def get_project_file_path(profile: str) -> str:
    """Get the path to store active project for a profile."""
    from .utils import DEFAULT_PROFILE
    profile_name = (profile or DEFAULT_PROFILE).strip().lower()
    if profile_name == "codex":
        return os.path.expanduser("~/.codex_memory/active_project")
    elif profile_name == "claude":
        return os.path.expanduser("~/.claude_memory/active_project")
    else:
        return os.path.expanduser("~/.local/share/llm-memory/active_project")


def get_active_project(profile: str) -> str | None:
    """Get the currently active project."""
    project_file = get_project_file_path(profile)
    if not os.path.exists(project_file):
        return None
    try:
        with open(project_file, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except IOError:
        return None


def set_active_project(profile: str, project: str) -> None:
    """Set the active project."""
    project_file = get_project_file_path(profile)
    project_dir = os.path.dirname(project_file)
    if project_dir:
        os.makedirs(project_dir, exist_ok=True)
    with open(project_file, "w", encoding="utf-8") as f:
        f.write(project)


def list_projects(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """List all projects with observation counts."""
    rows = conn.execute(
        """
        SELECT
            o.project,
            COUNT(*) as observation_count,
            COUNT(DISTINCT o.kind) as kind_count,
            MIN(o.timestamp) as earliest,
            MAX(o.timestamp) as latest
        FROM observations o
        GROUP BY o.project
        ORDER BY latest DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "project": row["project"],
            "observation_count": row["observation_count"],
            "kind_count": row["kind_count"],
            "earliest": row["earliest"],
            "latest": row["latest"],
        }
        for row in rows
    ]


def get_project_stats(conn: sqlite3.Connection, project: str) -> dict:
    """Get detailed statistics for a project."""
    row = conn.execute(
        """
        SELECT
            COUNT(*) as observation_count,
            COUNT(DISTINCT kind) as kind_count,
            MIN(timestamp) as earliest,
            MAX(timestamp) as latest
        FROM observations
        WHERE project = ?
        """,
        (project,),
    ).fetchone()

    kinds = [
        {"kind": r["kind"], "count": r["count"]}
        for r in conn.execute(
            "SELECT kind, COUNT(*) as count FROM observations WHERE project = ? GROUP BY kind",
            (project,),
        ).fetchall()
    ]

    from .utils import parse_tags_json
    tag_counts: dict[str, int] = {}
    for r in conn.execute("SELECT tags FROM observations WHERE project = ?", (project,)).fetchall():
        for tag in parse_tags_json(r["tags"]):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    top_tags = sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))[:10]

    sessions = [
        {"id": r["id"], "start_time": r["start_time"], "status": r["status"]}
        for r in conn.execute(
            "SELECT id, start_time, status FROM sessions WHERE project = ? ORDER BY start_time DESC LIMIT 5",
            (project,),
        ).fetchall()
    ]

    return {
        "project": project,
        "observation_count": row["observation_count"],
        "kind_count": row["kind_count"],
        "earliest": row["earliest"],
        "latest": row["latest"],
        "kinds": kinds,
        "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
        "recent_sessions": sessions,
    }
