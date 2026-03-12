"""Import and export functionality for context sharing."""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    import sqlite3
    from .models import Observation, Session


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
    from .models import Session
    from .operations import normalize_rows
    from .utils import normalize_tags_list, utc_now

    query, params = _build_share_query(
        project=project,
        kind=kind,
        tag=tag,
        session_id=session_id,
        since=since,
        limit=limit,
        normalize_tags_list=normalize_tags_list,
    )
    rows = conn.execute(query, params).fetchall()
    observations = normalize_rows(rows)
    sessions = _load_related_sessions(conn, observations, Session)
    bundle = _build_share_bundle(
        fmt=fmt,
        project=project,
        kind=kind,
        tag=tag,
        session_id=session_id,
        since=since,
        observations=observations,
        sessions=sessions,
        utc_now=utc_now,
    )

    output_path = _prepare_share_output_path(output_path)
    _write_share_bundle(output_path=output_path, fmt=fmt, bundle=bundle)

    return {
        "ok": True,
        "output_path": output_path,
        "format": fmt,
        "observations": len(observations),
        "sessions": len(sessions),
    }


def _build_share_query(
    project: Optional[str],
    kind: Optional[str],
    tag: Optional[str],
    session_id: Optional[int],
    since: Optional[str],
    limit: int,
    normalize_tags_list,
) -> tuple[str, list[object]]:
    query = "SELECT * FROM observations WHERE 1=1"
    params: list[object] = []
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
            query += " AND tags_text LIKE ?"
            params.append(f"%{tag_values[0]}%")
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    return query, params


def _load_related_sessions(
    conn: sqlite3.Connection,
    observations,
    session_model,
):
    session_ids = {obs.session_id for obs in observations if obs.session_id}
    sessions = []
    if not session_ids:
        return sessions
    placeholders = ",".join("?" for _ in session_ids)
    session_rows = conn.execute(
        f"SELECT * FROM sessions WHERE id IN ({placeholders})",
        list(session_ids),
    ).fetchall()
    for row in session_rows:
        sessions.append(
            session_model(
                id=row["id"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                project=row["project"],
                working_dir=row["working_dir"],
                agent_type=row["agent_type"],
                summary=row["summary"],
                status=row["status"],
            )
        )
    return sessions


def _build_share_bundle(
    fmt: str,
    project: Optional[str],
    kind: Optional[str],
    tag: Optional[str],
    session_id: Optional[int],
    since: Optional[str],
    observations,
    sessions,
    utc_now,
) -> dict:
    return {
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
        "sessions": [session.__dict__ for session in sessions],
        "observations": [observation.__dict__ for observation in observations],
    }


def _prepare_share_output_path(output_path: str) -> str:
    output_path = os.path.expanduser(output_path)
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    return output_path


def _write_share_bundle(output_path: str, fmt: str, bundle: dict) -> None:
    if fmt == "json":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2)
    elif fmt == "csv":
        _write_csv_bundle(output_path, bundle)
    elif fmt == "markdown":
        _write_markdown_bundle(output_path, bundle)
    elif fmt == "html":
        _write_html_bundle(output_path, bundle)


def _write_csv_bundle(output_path: str, bundle: dict) -> None:
    """Write bundle as CSV."""
    import csv

    observations = bundle["observations"]
    if not observations:
        # Write empty file with headers
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            f.write("id,timestamp,project,kind,title,summary,tags,session_id,metadata\n")
        return

    # Get all fieldnames from first observation
    fieldnames = list(observations[0].keys())
    # Ensure consistent ordering
    preferred_order = ["id", "timestamp", "project", "kind", "title", "summary", "tags", "session_id", "metadata", "raw"]
    fieldnames = [f for f in preferred_order if f in fieldnames] + [f for f in fieldnames if f not in preferred_order]

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for obs in observations:
            if isinstance(obs.get("metadata"), dict):
                obs = {**obs, "metadata": json.dumps(obs["metadata"], ensure_ascii=False, sort_keys=True)}
            writer.writerow(obs)


def _write_markdown_bundle(output_path: str, bundle: dict) -> None:
    """Write bundle as Markdown."""
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
                if session.get('end_time'):
                    f.write(f"- Ended: {session['end_time']}\n")
                f.write(f"- Agent: {session['agent_type']}\n")
                f.write(f"- Working dir: {session['working_dir']}\n")
                if session.get('summary'):
                    f.write(f"- Summary: {session['summary']}\n")
                f.write(f"\n")

        f.write(f"## Observations\n\n")
        for obs in bundle["observations"]:
            f.write(f"### {obs['title']}\n\n")
            f.write(f"- **ID**: {obs['id']}\n")
            f.write(f"- **Time**: {obs['timestamp']}\n")
            f.write(f"- **Project**: {obs['project']}\n")
            f.write(f"- **Kind**: {obs['kind']}\n")
            if obs.get('session_id'):
                f.write(f"- **Session**: {obs['session_id']}\n")
            if obs.get('metadata'):
                f.write(f"- **Metadata**: `{json.dumps(obs['metadata'], ensure_ascii=False, sort_keys=True)}`\n")
            if obs['tags']:
                f.write(f"- **Tags**: {', '.join(obs['tags'])}\n")
            f.write(f"\n{obs['summary']}\n\n")
            if obs.get('raw'):
                f.write(f"```\n{obs['raw']}\n```\n\n")


def _write_html_bundle(output_path: str, bundle: dict) -> None:
    """Write bundle as HTML."""
    html_parts = [_build_html_bundle_header(bundle)]
    html_parts.append(_build_html_sessions_section(bundle["sessions"]))
    html_parts.append(_build_html_observations_section(bundle["observations"]))
    html_parts.append("\n</body>\n</html>\n")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("".join(html_parts))


def _build_html_bundle_header(bundle: dict) -> str:
    return f"""<!DOCTYPE html>
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


def _build_html_sessions_section(sessions: list[dict]) -> str:
    if not sessions:
        return ""
    section = ["    <h2>Sessions</h2>\n"]
    for session in sessions:
        section.append(_build_html_session_card(session))
    return "".join(section)


def _build_html_session_card(session: dict) -> str:
    ended = f"<p>Ended: {session['end_time']}</p>" if session.get("end_time") else ""
    summary = f"<p>Summary: {session['summary']}</p>" if session.get("summary") else ""
    return f"""
    <div class="session">
        <h3>Session {session['id']}: {session['project']}</h3>
        <div class="meta">
            <p>Status: <strong>{session['status']}</strong></p>
            <p>Started: {session['start_time']}</p>
            {ended}
            <p>Agent: {session['agent_type']}</p>
            <p>Working dir: {session['working_dir']}</p>
            {summary}
        </div>
    </div>
"""


def _build_html_observations_section(observations: list[dict]) -> str:
    section = ["    <h2>Observations</h2>\n"]
    for observation in observations:
        section.append(_build_html_observation_card(observation))
    return "".join(section)


def _build_html_observation_card(obs: dict) -> str:
    tags_html = (
        "".join(f'<span class="tag">{tag}</span>' for tag in obs["tags"])
        if obs["tags"]
        else ""
    )
    raw_html = f"<pre>{obs['raw']}</pre>" if obs.get("raw") else ""
    session_info = f"<p>Session: {obs['session_id']}</p>" if obs.get("session_id") else ""
    metadata_info = (
        f"<p>Metadata: <code>{json.dumps(obs['metadata'], ensure_ascii=False, sort_keys=True)}</code></p>"
        if obs.get("metadata")
        else ""
    )
    return f"""
    <div class="observation">
        <h3>{obs['title']}</h3>
        <div class="meta">
            <p>ID: {obs['id']} | Time: {obs['timestamp']} | Project: {obs['project']} | Kind: {obs['kind']}</p>
            {session_info}
            {metadata_info}
        </div>
        <p>{obs['summary']}</p>
        <div class="tags">{tags_html}</div>
        {raw_html}
    </div>
"""


def run_import(
    conn: sqlite3.Connection,
    file_path: str,
    project_override: Optional[str],
    dry_run: bool,
) -> dict:
    """Import a shared context bundle."""
    from .utils import metadata_to_json, tags_to_json, tags_to_text

    file_path = os.path.expanduser(file_path)
    bundle = _load_import_bundle(file_path)
    imported_sessions, session_id_map = _import_sessions(
        conn=conn,
        sessions=bundle.get("sessions", []),
        dry_run=dry_run,
    )
    imported_observations = _import_observations(
        conn=conn,
        observations=bundle.get("observations", []),
        project_override=project_override,
        dry_run=dry_run,
        session_id_map=session_id_map,
        tags_to_json=tags_to_json,
        tags_to_text=tags_to_text,
        metadata_to_json=metadata_to_json,
    )

    if not dry_run:
        conn.commit()

    return {
        "ok": True,
        "dry_run": dry_run,
        "imported_observations": imported_observations,
        "imported_sessions": imported_sessions,
        "source_file": file_path,
    }


def _load_import_bundle(file_path: str) -> dict:
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _import_sessions(
    conn: sqlite3.Connection,
    sessions: list[dict],
    dry_run: bool,
) -> tuple[int, dict[int, int]]:
    imported_sessions = 0
    session_id_map: dict[int, int] = {}
    for session_data in sessions:
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
            session_id_map[old_id] = int(cursor.lastrowid)
        imported_sessions += 1
    return imported_sessions, session_id_map


def _import_observations(
    conn: sqlite3.Connection,
    observations: list[dict],
    project_override: Optional[str],
    dry_run: bool,
    session_id_map: dict[int, int],
    tags_to_json,
    tags_to_text,
    metadata_to_json,
) -> int:
    imported_observations = 0
    for obs_data in observations:
        project = project_override or obs_data["project"]
        old_session_id = obs_data.get("session_id")
        new_session_id = session_id_map.get(old_session_id) if old_session_id else None
        if not dry_run:
            tags_list = obs_data.get("tags", [])
            conn.execute(
                """
                INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    metadata_to_json(obs_data.get("metadata", {})),
                ),
            )
        imported_observations += 1
    return imported_observations
