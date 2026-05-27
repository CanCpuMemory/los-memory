"""CRUD operations for observations."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, List, Optional

if TYPE_CHECKING:
    from .models import Observation


def normalize_rows(rows: Iterable[sqlite3.Row]) -> List["Observation"]:
    """Convert database rows to Observation objects."""
    from .models import Observation
    from .utils import parse_metadata_json, parse_tags_json
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
                metadata=parse_metadata_json(row["metadata"]) if "metadata" in row.keys() else {},
            )
        )
    return results


def _normalize_session_id(
    conn: sqlite3.Connection,
    session_id: Optional[int],
) -> Optional[int]:
    normalized_session_id: Optional[int]
    if session_id is None:
        normalized_session_id = None
    else:
        try:
            candidate_session_id = int(session_id)
        except (TypeError, ValueError):
            candidate_session_id = 0
        if candidate_session_id <= 0:
            normalized_session_id = None
        else:
            exists = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ? LIMIT 1",
                (candidate_session_id,),
            ).fetchone()
            normalized_session_id = candidate_session_id if exists else None
    return normalized_session_id


def _insert_observation(
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
    metadata: str = "{}",
) -> int:
    normalized_session_id = _normalize_session_id(conn, session_id)
    cursor = conn.execute(
        """
        INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            timestamp,
            project,
            kind,
            title,
            summary,
            tags,
            tags_text,
            raw,
            normalized_session_id,
            metadata,
        ),
    )
    return int(cursor.lastrowid)


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
    metadata: str = "{}",
) -> int:
    """Add a new observation and return its ID."""
    obs_id = _insert_observation(
        conn,
        timestamp,
        project,
        kind,
        title,
        summary,
        tags,
        tags_text,
        raw,
        session_id=session_id,
        metadata=metadata,
    )
    conn.commit()
    return obs_id


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


def _normalize_metadata_filters(metadata_filters: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not metadata_filters:
        return {}
    return {str(key): value for key, value in metadata_filters.items()}


def _matches_metadata_filters(item_metadata: dict[str, Any], metadata_filters: dict[str, Any]) -> bool:
    if not metadata_filters:
        return True
    metadata = item_metadata or {}
    return all(metadata.get(key) == value for key, value in metadata_filters.items())


def _filter_results(
    results: List[dict],
    required_tags: List[str],
    metadata_filters: dict[str, Any],
) -> List[dict]:
    return [
        item
        for item in results
        if _matches_required_tags(item.get("tags", []), required_tags)
        and _matches_metadata_filters(item.get("metadata", {}), metadata_filters)
    ]


def _slice_filtered_results(results: List[Any], limit: int, offset: int) -> List[Any]:
    if offset < 0:
        offset = 0
    if limit < 0:
        return results[offset:]
    return results[offset:offset + limit]


def run_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    offset: int = 0,
    mode: str = "auto",
    quote: bool = False,
    required_tags: Optional[List[str]] = None,
    metadata_filters: Optional[dict[str, Any]] = None,
) -> List[dict]:
    """Search observations using FTS or LIKE."""
    from .utils import parse_metadata_json, parse_tags_json, quote_fts_query
    query = query.strip()
    if not query:
        return []
    required = _normalize_required_tags(required_tags)
    metadata_filter_map = _normalize_metadata_filters(metadata_filters)
    use_post_filters = bool(required or metadata_filter_map)
    fts_query = quote_fts_query(query) if quote else query
    if mode != "like":
        try:
            fts_results = _run_search_fts(
                conn=conn,
                fts_query=fts_query,
                limit=None if use_post_filters else limit,
                offset=0 if use_post_filters else offset,
                parse_tags_json=parse_tags_json,
                parse_metadata_json=parse_metadata_json,
            )
            filtered_results = _filter_results(fts_results, required, metadata_filter_map)
            if use_post_filters:
                return _slice_filtered_results(filtered_results, limit, offset)
            return filtered_results
        except sqlite3.OperationalError:
            if mode == "fts":
                raise

    like_results = _run_search_like(
        conn=conn,
        query=query,
        limit=None if use_post_filters else limit,
        offset=0 if use_post_filters else offset,
        parse_tags_json=parse_tags_json,
        parse_metadata_json=parse_metadata_json,
    )
    filtered_results = _filter_results(like_results, required, metadata_filter_map)
    if use_post_filters:
        return _slice_filtered_results(filtered_results, limit, offset)
    return filtered_results


def _run_search_fts(
    conn: sqlite3.Connection,
    fts_query: str,
    limit: int | None,
    offset: int,
    parse_tags_json,
    parse_metadata_json,
) -> List[dict]:
    query = """
        SELECT observations.id, observations.timestamp, observations.project,
               observations.kind, observations.title, observations.summary,
               observations.tags, observations.raw, observations.session_id, observations.metadata,
               bm25(observations_fts) AS score
        FROM observations_fts
        JOIN observations ON observations_fts.rowid = observations.id
        WHERE observations_fts MATCH ?
        ORDER BY score
    """
    params: list[Any] = [fts_query]
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    return [
        _row_to_search_result(
            row=row,
            score=row["score"],
            parse_tags_json=parse_tags_json,
            parse_metadata_json=parse_metadata_json,
        )
        for row in rows
    ]


def _run_search_like(
    conn: sqlite3.Connection,
    query: str,
    limit: int | None,
    offset: int,
    parse_tags_json,
    parse_metadata_json,
) -> List[dict]:
    sql = """
        SELECT id, timestamp, project, kind, title, summary, tags, raw, session_id, metadata
        FROM observations
        WHERE title LIKE ? OR summary LIKE ? OR tags_text LIKE ? OR raw LIKE ?
        ORDER BY id DESC
    """
    params: list[Any] = [f"%{query}%"] * 4
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return [
        _row_to_search_result(
            row=row,
            score=None,
            parse_tags_json=parse_tags_json,
            parse_metadata_json=parse_metadata_json,
        )
        for row in rows
    ]


def _row_to_search_result(
    row: sqlite3.Row,
    score: float | None,
    parse_tags_json,
    parse_metadata_json,
) -> dict:
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "project": row["project"],
        "kind": row["kind"],
        "title": row["title"],
        "summary": row["summary"],
        "tags": parse_tags_json(row["tags"]),
        "score": score,
        "session_id": row["session_id"] if "session_id" in row.keys() else None,
        "metadata": parse_metadata_json(row["metadata"]) if "metadata" in row.keys() else {},
    }


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
                lines.append("\n🔸 No Session")
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
    metadata_filters: Optional[dict[str, Any]] = None,
) -> List["Observation"]:
    """List latest observations."""
    required = _normalize_required_tags(required_tags)
    metadata_filter_map = _normalize_metadata_filters(metadata_filters)
    use_post_filters = bool(required or metadata_filter_map)
    if use_post_filters:
        rows = conn.execute(
            "SELECT * FROM observations ORDER BY timestamp DESC",
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM observations ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    results = normalize_rows(rows)
    if not use_post_filters:
        return results
    filtered_results = [
        item
        for item in results
        if _matches_required_tags(item.tags, required)
        and _matches_metadata_filters(item.metadata, metadata_filter_map)
    ]
    return _slice_filtered_results(filtered_results, limit, offset)


def _normalize_bulk_observation_item(raw_item: object) -> dict[str, Any]:
    from .utils import (
        auto_tags_from_text,
        metadata_to_json,
        normalize_metadata_dict,
        normalize_tags_list,
        tags_to_json,
        tags_to_text,
    )

    if not isinstance(raw_item, dict):
        raise ValueError("bulk_observation_item_must_be_object")

    raw_title = raw_item.get("title")
    raw_summary = raw_item.get("summary")
    title = "" if raw_title is None else str(raw_title).strip()
    summary = "" if raw_summary is None else str(raw_summary).strip()
    if not title:
        raise ValueError("bulk_observation_title_required")
    if not summary:
        raise ValueError("bulk_observation_summary_required")

    project = str(raw_item.get("project") or "general")
    kind = str(raw_item.get("kind") or "note")
    timestamp = str(raw_item.get("timestamp") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    raw = str(raw_item.get("raw") or "")
    auto_tags = bool(raw_item.get("auto_tags", False))
    tags_list = normalize_tags_list(raw_item.get("tags", []))
    if auto_tags and not tags_list:
        tags_list = auto_tags_from_text(title, summary, kind=kind)

    metadata = normalize_metadata_dict(raw_item.get("metadata", {}))

    return {
        "timestamp": timestamp,
        "project": project,
        "kind": kind,
        "title": title,
        "summary": summary,
        "tags": tags_to_json(tags_list),
        "tags_text": tags_to_text(tags_list),
        "raw": raw,
        "session_id": raw_item.get("session_id"),
        "metadata": metadata_to_json(metadata),
    }


def run_bulk_add(
    conn: sqlite3.Connection,
    items: list[dict[str, Any]],
    dry_run: bool = False,
    dedup_mode: str = "allow",
    auto_importance: bool = False,
) -> dict[str, Any]:
    """Create multiple observations from JSON-style payload items.

    Args:
        conn: Database connection.
        items: List of observation dicts.
        dry_run: If True, roll back after insertion.
        dedup_mode: "allow" (default) to always insert, "skip" to skip items
            whose content hash already exists in the database.
        auto_importance: If True, auto-calculate importance score.
    """
    from .utils import (
        calculate_importance,
        compute_content_hash,
        metadata_to_json,
        parse_metadata_json,
        parse_tags_json,
    )

    normalized_items = [_normalize_bulk_observation_item(item) for item in items]
    created_ids: list[int] = []
    skipped: list[dict[str, Any]] = []

    try:
        for item in normalized_items:
            metadata = parse_metadata_json(item["metadata"])

            # Auto-importance
            if auto_importance:
                metadata["importance"] = calculate_importance(
                    item["kind"], item["title"], item["summary"]
                )

            # Dedup check
            if dedup_mode == "skip":
                content_hash = compute_content_hash(item["title"], item["summary"])
                metadata["contentHash"] = content_hash
                existing = conn.execute(
                    "SELECT id FROM observations "
                    "WHERE json_extract(metadata, '$.contentHash') = ?",
                    (content_hash,),
                ).fetchone()
                if existing:
                    skipped.append({
                        "item": {"title": item["title"], "summary": item["summary"]},
                        "existingId": existing[0],
                    })
                    continue

            item["metadata"] = metadata_to_json(metadata)
            created_ids.append(
                _insert_observation(
                    conn,
                    item["timestamp"],
                    item["project"],
                    item["kind"],
                    item["title"],
                    item["summary"],
                    item["tags"],
                    item["tags_text"],
                    item["raw"],
                    session_id=item["session_id"],
                    metadata=item["metadata"],
                )
            )
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise

    result_ids = [] if dry_run else created_ids
    results = [
        {
            "id": None if dry_run else created_id,
            "timestamp": item["timestamp"],
            "project": item["project"],
            "kind": item["kind"],
            "title": item["title"],
            "summary": item["summary"],
            "tags": parse_tags_json(item["tags"]),
            "raw": item["raw"],
            "session_id": _normalize_session_id(conn, item["session_id"]),
            "metadata": parse_metadata_json(item["metadata"]),
        }
        for created_id, item in zip(created_ids, normalized_items)
    ]
    return {
        "ok": True,
        "total": len(normalized_items),
        "created": 0 if dry_run else len(created_ids),
        "skipped": len(skipped),
        "skippedDetails": skipped,
        "ids": result_ids,
        "results": results,
        "dry_run": dry_run,
    }


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
    metadata: Optional[str] = None,
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
    if metadata is not None:
        updates["metadata"] = metadata

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
    _validate_clean_cutoff_inputs(before, older_than_days)
    cutoff = _resolve_clean_cutoff(before, older_than_days)
    where_clause, params = _build_clean_where_clause(cutoff, project, kind, tag, delete_all)
    matched = _count_clean_matches(conn, where_clause, params)
    deleted = _execute_clean_delete(conn, where_clause, params, dry_run, matched)
    vacuumed = _maybe_vacuum_after_clean(conn, vacuum, dry_run)

    return {
        "ok": True,
        "matched": matched,
        "deleted": deleted,
        "dry_run": dry_run,
        "before": cutoff,
        "vacuum": vacuumed,
    }


def run_manage(conn: sqlite3.Connection, action: str, limit: int) -> dict:
    """Manage and inspect database."""
    from .utils import parse_tags_json
    handlers = {
        "stats": lambda: _run_manage_stats(conn, action, limit),
        "projects": lambda: _run_manage_projects(conn, action, limit),
        "tags": lambda: _run_manage_tags(conn, action, limit, parse_tags_json),
        "vacuum": lambda: _run_manage_vacuum(conn, action),
    }
    handler = handlers.get(action)
    if handler is None:
        raise ValueError(f"Unsupported manage action: {action}")
    return handler()


def _validate_clean_cutoff_inputs(
    before: Optional[str],
    older_than_days: Optional[int],
) -> None:
    if before and older_than_days is not None:
        raise ValueError("Use either --before or --older-than-days, not both")


def _resolve_clean_cutoff(
    before: Optional[str],
    older_than_days: Optional[int],
) -> Optional[str]:
    if older_than_days is None:
        return before
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    return cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_clean_where_clause(
    cutoff: Optional[str],
    project: Optional[str],
    kind: Optional[str],
    tag: Optional[str],
    delete_all: bool,
) -> tuple[str, List[object]]:
    filters: List[str] = []
    params: List[object] = []
    if cutoff:
        filters.append("timestamp < ?")
        params.append(cutoff)
    if project:
        filters.append("project = ?")
        params.append(project)
    if kind:
        filters.append("kind = ?")
        params.append(kind)

    _append_clean_tag_filters(filters, params, tag)

    if not filters and not delete_all:
        raise ValueError("Refusing to clean without filters. Use --all to delete everything.")

    return (" AND ".join(filters) if filters else "1=1"), params


def _append_clean_tag_filters(
    filters: List[str],
    params: List[object],
    tag: Optional[str],
) -> None:
    from .utils import normalize_tags_list

    tag_values = normalize_tags_list(tag) if tag else []
    if not tag_values:
        return

    tag_filters: List[str] = []
    for item in tag_values:
        tag_filters.append("tags_text LIKE ?")
        params.append(f"%{item}%")
    filters.append(f"({' OR '.join(tag_filters)})")


def _count_clean_matches(
    conn: sqlite3.Connection,
    where_clause: str,
    params: List[object],
) -> int:
    return int(
        conn.execute(
            f"SELECT COUNT(*) AS c FROM observations WHERE {where_clause}",
            params,
        ).fetchone()["c"]
    )


def _execute_clean_delete(
    conn: sqlite3.Connection,
    where_clause: str,
    params: List[object],
    dry_run: bool,
    matched: int,
) -> int:
    if dry_run:
        conn.rollback()
        return 0
    if not matched:
        return 0
    cursor = conn.execute(f"DELETE FROM observations WHERE {where_clause}", params)
    deleted = int(cursor.rowcount)
    conn.commit()
    return deleted


def _maybe_vacuum_after_clean(
    conn: sqlite3.Connection,
    vacuum: bool,
    dry_run: bool,
) -> bool:
    if not vacuum or dry_run:
        return False
    conn.execute("VACUUM")
    return True


def _run_manage_stats(conn: sqlite3.Connection, action: str, limit: int) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total, MIN(timestamp) AS earliest, MAX(timestamp) AS latest
        FROM observations
        """
    ).fetchone()
    projects = _query_project_counts(conn, limit)
    kinds = _query_kind_counts(conn, limit)
    return {
        "ok": True,
        "action": action,
        "total": int(row["total"]),
        "earliest": row["earliest"],
        "latest": row["latest"],
        "projects": projects,
        "kinds": kinds,
    }


def _run_manage_projects(conn: sqlite3.Connection, action: str, limit: int) -> dict:
    return {
        "ok": True,
        "action": action,
        "projects": _query_project_counts(conn, limit),
    }


def _query_project_counts(conn: sqlite3.Connection, limit: int) -> List[dict]:
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
    return [{"project": row["project"], "count": row["count"]} for row in rows]


def _query_kind_counts(conn: sqlite3.Connection, limit: int) -> List[dict]:
    rows = conn.execute(
        """
        SELECT kind, COUNT(*) AS count
        FROM observations
        GROUP BY kind
        ORDER BY count DESC, kind ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [{"kind": row["kind"], "count": row["count"]} for row in rows]


def _run_manage_tags(
    conn: sqlite3.Connection,
    action: str,
    limit: int,
    parse_tags_json,
) -> dict:
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


def _run_manage_vacuum(conn: sqlite3.Connection, action: str) -> dict:
    conn.execute("VACUUM")
    return {"ok": True, "action": action, "vacuumed": True}
