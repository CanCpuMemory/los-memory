"""Analytics for tool usage and suggestions."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import sqlite3


def get_tool_stats(conn: sqlite3.Connection, project: Optional[str] = None, limit: int = 20) -> dict:
    """Get tool usage statistics.

    Returns:
        Dict with total_calls, success_rate, tools_breakdown, recent_errors
    """
    # Base query for tool_call observations
    base_where = "kind = 'tool_call'"
    params: list = []

    if project:
        base_where += " AND project = ?"
        params.append(project)

    # Total calls
    row = conn.execute(
        f"SELECT COUNT(*) as total FROM observations WHERE {base_where}",
        params,
    ).fetchone()
    total_calls = int(row["total"]) if row else 0

    # Success rate - parse raw field as JSON and look for status
    success_count = 0
    error_count = 0
    tool_counts: dict[str, int] = {}
    recent_errors: list[dict] = []

    rows = conn.execute(
        f"SELECT title, summary, raw, timestamp FROM observations WHERE {base_where} ORDER BY timestamp DESC",
        params,
    ).fetchall()

    for row in rows:
        # Extract tool name from title (format: "Tool: {tool_name}")
        tool_name = row["title"].replace("Tool: ", "").strip() if row["title"].startswith("Tool:") else row["title"]
        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

        # Parse raw field for status
        try:
            raw_data = json.loads(row["raw"]) if row["raw"] else {}
            status = raw_data.get("status", "unknown")
            if status == "success":
                success_count += 1
            elif status == "error":
                error_count += 1
                if len(recent_errors) < 5:
                    recent_errors.append({
                        "tool": tool_name,
                        "error": raw_data.get("error", "Unknown error"),
                        "timestamp": row["timestamp"],
                    })
        except json.JSONDecodeError:
            pass

    # Sort tools by usage
    sorted_tools = sorted(tool_counts.items(), key=lambda x: (-x[1], x[0]))[:limit]

    return {
        "ok": True,
        "total_calls": total_calls,
        "success_count": success_count,
        "error_count": error_count,
        "success_rate": round(success_count / total_calls * 100, 1) if total_calls > 0 else 0,
        "tools": [{"name": name, "calls": count} for name, count in sorted_tools],
        "recent_errors": recent_errors,
    }


def suggest_tools_for_task(conn: sqlite3.Connection, task_description: str, limit: int = 5) -> dict:
    """Suggest tools based on task description and historical usage.

    Uses simple keyword matching against tool names and past tool usages.
    """
    task_lower = task_description.lower()
    task_keywords = set(task_lower.split())

    # Get all tool calls
    rows = conn.execute(
        "SELECT title, summary, raw, COUNT(*) as count FROM observations WHERE kind = 'tool_call' GROUP BY title ORDER BY count DESC"
    ).fetchall()

    scored_tools: list[tuple[float, str, dict]] = []

    for row in rows:
        tool_name = row["title"].replace("Tool: ", "").strip() if row["title"].startswith("Tool:") else row["title"]

        # Calculate score based on keyword matches
        score = 0.0
        tool_text = f"{tool_name} {row['summary']}".lower()

        # Direct keyword matches
        for keyword in task_keywords:
            if len(keyword) > 2 and keyword in tool_text:
                score += 1.0

        # Frequency bonus
        score += row["count"] * 0.1

        if score > 0:
            try:
                raw_data = json.loads(row["raw"]) if row["raw"] else {}
            except json.JSONDecodeError:
                raw_data = {}

            scored_tools.append((
                score,
                tool_name,
                {
                    "name": tool_name,
                    "description": row["summary"],
                    "usage_count": row["count"],
                    "score": round(score, 2),
                    "example_input": raw_data.get("input_preview", ""),
                }
            ))

    # Sort by score descending
    scored_tools.sort(key=lambda x: -x[0])

    return {
        "ok": True,
        "task": task_description,
        "suggestions": [t[2] for t in scored_tools[:limit]],
    }


def log_tool_call(
    conn: sqlite3.Connection,
    tool_name: str,
    tool_input: dict,
    tool_output: Optional[dict],
    status: str,
    duration_ms: Optional[int],
    project: str,
    session_id: Optional[int] = None,
) -> int:
    """Log a tool call as an observation.

    Returns:
        The ID of the created observation
    """
    from .operations import add_observation
    from .utils import tags_to_json, utc_now

    # Build summary and raw data
    input_preview = json.dumps(tool_input)[:200] if tool_input else ""
    error_msg = ""
    if tool_output and "error" in tool_output:
        error_msg = str(tool_output["error"])

    summary = f"Status: {status}"
    if duration_ms:
        summary += f" | Duration: {duration_ms}ms"
    if error_msg:
        summary += f" | Error: {error_msg[:100]}"

    raw_data = {
        "tool": tool_name,
        "input": tool_input,
        "output": tool_output,
        "status": status,
        "duration_ms": duration_ms,
        "input_preview": input_preview,
    }

    tags = ["tool", tool_name.lower().replace(" ", "_")]
    if status == "error":
        tags.append("error")

    obs_id = add_observation(
        conn,
        utc_now(),
        project,
        "tool_call",
        f"Tool: {tool_name}",
        summary,
        tags_to_json(tags),
        " ".join(tags),
        json.dumps(raw_data, ensure_ascii=False),
        session_id,
    )

    return obs_id


def log_agent_transition(
    conn: sqlite3.Connection,
    phase: str,
    action: str,
    transition_input: dict,
    transition_output: Optional[dict],
    status: str,
    reward: Optional[float],
    project: str,
    session_id: Optional[int] = None,
) -> int:
    """Log an agent transition as a structured memory record."""
    from .operations import add_observation
    from .utils import tags_to_json, utc_now

    safe_phase = (phase or "unknown").strip() or "unknown"
    safe_action = (action or "unknown").strip() or "unknown"

    summary = f"Status: {status}"
    if reward is not None:
        summary += f" | Reward: {reward}"

    raw_data = {
        "phase": safe_phase,
        "action": safe_action,
        "input": transition_input,
        "output": transition_output,
        "status": status,
        "reward": reward,
    }

    tags = ["transition", safe_phase.lower().replace(" ", "_"), safe_action.lower().replace(" ", "_")]
    if status == "error":
        tags.append("error")

    obs_id = add_observation(
        conn,
        utc_now(),
        project,
        "agent_transition",
        f"Transition: {safe_phase}/{safe_action}",
        summary,
        tags_to_json(tags),
        " ".join(tags),
        json.dumps(raw_data, ensure_ascii=False),
        session_id,
    )
    return obs_id
