"""CRUD operations for observations."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterable, List, Optional

if TYPE_CHECKING:
    import sqlite3
    from .models import Observation


def normalize_rows(rows: Iterable[sqlite3.Row]) -> List["Observation"]:
    """Convert database rows to Observation objects."""
    from .models import Observation
    from .utils import parse_tags_json
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
    """Add a new observation and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _normalize_required_tags(required_tags: Optional[List[str]]) -> List[str]:
    from .utils import normalize_tags_list
    if not required_tags:
        return []
    return normalize_tags_list(required_tags)


def _matches_required_tags(item_tags: List[str], required_tags: List[str]) -> bool:
    if not required_tags:
        return True
    tag_set = set(item_tags or [])
    return all(tag in tag_set for tag in required_tags)


def run_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    offset: int = 0,
    mode: str = "auto",
    quote: bool = False,
    required_tags: Optional[List[str]] = None,
) -> List[dict]:
    """Search observations using FTS or LIKE."""
    from .utils import parse_tags_json, quote_fts_query
    query = query.strip()
    if not query:
        return []
    required = _normalize_required_tags(required_tags)
    fts_query = quote_fts_query(query) if quote else query
    if mode != "like":
        try:
            rows = conn.execute(
                """
                SELECT observations.id, observations.timestamp, observations.project,
                       observations.kind, observations.title, observations.summary,
                       observations.tags, observations.raw, observations.session_id,
                       bm25(observations_fts) AS score
                FROM observations_fts
                JOIN observations ON observations_fts.rowid = observations.id
                WHERE observations_fts MATCH ?
                ORDER BY score
                LIMIT ? OFFSET ?
                """,
                (fts_query, limit, offset),
            ).fetchall()
            fts_results = [
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
            if not required:
                return fts_results
            return [item for item in fts_results if _matches_required_tags(item.get("tags", []), required)]
        except sqlite3.OperationalError:
            if mode == "fts":
                raise

    rows = conn.execute(
        """
        SELECT id, timestamp, project, kind, title, summary, tags, raw, session_id
        FROM observations
        WHERE title LIKE ? OR summary LIKE ? OR tags_text LIKE ? OR raw LIKE ?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        tuple([f"%{query}%"] * 4 + [limit, offset]),
    ).fetchall()
    like_results = [
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
    if not required:
        return like_results
    return [item for item in like_results if _matches_required_tags(item.get("tags", []), required)]


def run_timeline(
    conn: sqlite3.Connection,
    start: Optional[str],
    end: Optional[str],
    around_id: Optional[int],
    window_minutes: int,
    limit: int,
    offset: int = 0,
) -> List["Observation"]:
    """Query observations by time range."""
    from .utils import ISO_FORMAT
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


def generate_visual_timeline(observations: List["Observation"], group_by: Optional[str] = None) -> str:
    """Generate a visual ASCII timeline of observations."""
    from collections import defaultdict
    from .utils import ISO_FORMAT
    if not observations:
        return "No observations to display."

    lines = ["\n📅 Visual Timeline", "=" * 60]
    sorted_obs = sorted(observations, key=lambda x: x.timestamp)

    if group_by == "day":
        by_day: dict[str, List["Observation"]] = defaultdict(list)
        for obs in sorted_obs:
            day = obs.timestamp[:10]
            by_day[day].append(obs)

        for day, obs_list in sorted(by_day.items()):
            lines.append(f"\n📆 {day}")
            lines.append("-" * 40)
            for obs in obs_list:
                time = obs.timestamp[11:16]
                icon = {"decision": "🎯", "fix": "🔧", "note": "📝", "incident": "🚨"}.get(obs.kind, "•")
                lines.append(f"  {time} {icon} [{obs.kind}] {obs.title}")

    elif group_by == "session":
        by_session: dict[Optional[int], List["Observation"]] = defaultdict(list)
        for obs in sorted_obs:
            by_session[obs.session_id].append(obs)

        for session_id, obs_list in sorted(by_session.items(), key=lambda x: x[0] or 0):
            if session_id:
                lines.append(f"\n🔷 Session {session_id}")
            else:
                lines.append(f"\n🔸 No Session")
            lines.append("-" * 40)
            for obs in obs_list:
                time = obs.timestamp[11:16]
                icon = {"decision": "🎯", "fix": "🔧", "note": "📝", "incident": "🚨"}.get(obs.kind, "•")
                lines.append(f"  {time} {icon} [{obs.kind}] {obs.title}")

    else:
        prev_time: Optional[datetime] = None
        for obs in sorted_obs:
            obs_time = datetime.strptime(obs.timestamp, ISO_FORMAT).replace(tzinfo=timezone.utc)
            time_str = obs.timestamp[11:16]

            if prev_time:
                gap = obs_time - prev_time
                if gap > timedelta(hours=1):
                    gap_hours = gap.total_seconds() / 3600
                    lines.append(f"\n  ... {gap_hours:.1f} hours gap ...")
                elif gap > timedelta(minutes=10):
                    gap_mins = gap.total_seconds() / 60
                    lines.append(f"\n  ... {gap_mins:.0f} min gap ...")

            icon = {"decision": "🎯", "fix": "🔧", "note": "📝", "incident": "🚨"}.get(obs.kind, "•")
            lines.append(f"{time_str} {icon} [{obs.kind}] {obs.title}")
            prev_time = obs_time

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def run_get(conn: sqlite3.Connection, ids: List[int]) -> List["Observation"]:
    """Get observations by IDs."""
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM observations WHERE id IN ({placeholders}) ORDER BY timestamp DESC",
        ids,
    ).fetchall()
    return normalize_rows(rows)


def run_list(
    conn: sqlite3.Connection,
    limit: int,
    offset: int = 0,
    required_tags: Optional[List[str]] = None,
) -> List["Observation"]:
    """List latest observations."""
    required = _normalize_required_tags(required_tags)
    rows = conn.execute(
        "SELECT * FROM observations ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    results = normalize_rows(rows)
    if not required:
        return results
    return [item for item in results if _matches_required_tags(item.tags, required)]


def run_export(conn: sqlite3.Connection, limit: int, offset: int = 0) -> List["Observation"]:
    """Export observations."""
    return run_list(conn, limit, offset=offset)


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
    """Edit an observation."""
    from .utils import auto_tags_from_text, normalize_tags_list, normalize_text, tags_to_json, tags_to_text
    row = conn.execute("SELECT * FROM observations WHERE id = ?", (obs_id,)).fetchone()
    if row is None:
        raise ValueError(f"Observation {obs_id} not found")

    current_title = row["title"]
    current_summary = row["summary"]
    current_tags = normalize_tags_list(row["tags"])

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
    """Delete observations by IDs."""
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
    """Delete old or filtered observations."""
    from .utils import normalize_tags_list, utc_now
    if before and older_than_days is not None:
        raise ValueError("Use either --before or --older-than-days, not both")
    filters: List[str] = []
    params: List[object] = []

    cutoff = before
    if older_than_days is not None:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        cutoff = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
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
    """Manage and inspect database."""
    from .utils import parse_tags_json
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
