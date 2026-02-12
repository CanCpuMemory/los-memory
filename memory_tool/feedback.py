"""Natural language feedback processor for memory observations."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

from .utils import utc_now

if TYPE_CHECKING:
    import sqlite3


@dataclass
class FeedbackIntent:
    """Parsed feedback intent."""
    action: Literal["correct", "supplement", "delete", "unknown"]
    new_title: Optional[str] = None
    new_summary: Optional[str] = None
    supplement_text: Optional[str] = None


def parse_feedback_intent(feedback_text: str) -> FeedbackIntent:
    """Parse natural language feedback to identify intent.

    Supports patterns like:
    - "修正: xxx是yyy而非zzz" / "correct: xxx is yyy not zzz"
    - "补充: 还需要考虑xxx" / "supplement: also need to consider xxx"
    - "删除" / "delete this"
    """
    text = feedback_text.strip()
    lower_text = text.lower()

    # Check for delete intent
    delete_patterns = [
        r"^删除", r"^delete", r"^remove", r"^drop",
        r"^标记删除", r"^mark.*delete",
    ]
    for pattern in delete_patterns:
        if re.search(pattern, lower_text, re.IGNORECASE):
            return FeedbackIntent(action="delete")

    # Check for correct intent
    correct_patterns = [
        r"^修正[:：]",
        r"^correct[:：]",
        r"^修改[:：]",
        r"^update[:：]",
        r"^改为[:：]",
        r"^should be[:：]",
        r"^应该是[:：]",
        r"^实际是[:：]",
        r"^actually[:：]",
    ]
    for pattern in correct_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            content = text[match.end():].strip()
            return _parse_correction_content(content)

    # Check for supplement intent
    supplement_patterns = [
        r"^补充[:：]",
        r"^supplement[:：]",
        r"^add[:：]",
        r"^添加[:：]",
        r"^补充说明[:：]",
        r"^note[:：]",
        r"^还需要[:：]",
        r"^also[:：]",
        r"^additionally[:：]",
    ]
    for pattern in supplement_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            content = text[match.end():].strip()
            return FeedbackIntent(action="supplement", supplement_text=content)

    # Default: try to infer from content
    # If it contains "而非" / "instead of" / "not", treat as correction
    if re.search(r"而非|instead of|not\s+\w+|is\s+\w+\s+not", text):
        return _parse_correction_content(text)

    # Default to supplement for any other text
    return FeedbackIntent(action="supplement", supplement_text=text)


def _parse_correction_content(content: str) -> FeedbackIntent:
    """Parse correction content to extract title/summary changes."""
    # Pattern: "X是Y" or "X is Y" (title correction)
    # Pattern: "summary: xxx" or "内容: xxx"

    new_title = None
    new_summary = None

    # Check for title/summary separation patterns
    title_summary_patterns = [
        r"title[:：]\s*(.+?)(?:\s+summary[:：]|\s+内容[:：]|$)",
        r"标题[:：]\s*(.+?)(?:\s+summary[:：]|\s+内容[:：]|\s+正文[:：]|$)",
    ]

    for pattern in title_summary_patterns:
        match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            new_title = match.group(1).strip()
            # Try to extract summary if present
            remaining = content[match.end():].strip()
            if remaining:
                new_summary = remaining.lstrip("summary:").lstrip("内容:").lstrip("正文:").strip()
            break
    else:
        # No explicit separation, treat as summary correction
        new_summary = content

    return FeedbackIntent(action="correct", new_title=new_title, new_summary=new_summary)


def apply_feedback(
    conn: sqlite3.Connection,
    observation_id: int,
    feedback_text: str,
    auto_apply: bool = True,
) -> dict:
    """Apply feedback to an observation.

    Args:
        conn: Database connection
        observation_id: Target observation ID
        feedback_text: Natural language feedback
        auto_apply: Whether to auto-apply changes or just record

    Returns:
        Result dictionary with action taken
    """
    from .operations import run_delete, run_edit, run_get

    # First check if observation exists
    results = run_get(conn, [observation_id])
    if not results:
        raise ValueError(f"Observation {observation_id} not found")

    observation = results[0]
    intent = parse_feedback_intent(feedback_text)

    result = {
        "ok": True,
        "observation_id": observation_id,
        "action": intent.action,
        "feedback_text": feedback_text,
    }

    if intent.action == "delete":
        if auto_apply:
            run_delete(conn, [observation_id], dry_run=False)
            result["deleted"] = True
        record_feedback(conn, observation_id, "delete", feedback_text)

    elif intent.action == "correct":
        if auto_apply:
            # Prepare updates
            title = intent.new_title
            summary = intent.new_summary
            # If only summary provided, keep current title
            if title is None and summary is not None:
                title = observation.title
            run_edit(
                conn,
                observation_id,
                project=None,
                kind=None,
                title=title,
                summary=summary,
                tags=None,
                raw=None,
                timestamp=None,
                auto_tags=False,
            )
            result["updated"] = True
        record_feedback(conn, observation_id, "correct", feedback_text)

    elif intent.action == "supplement":
        if auto_apply and intent.supplement_text:
            # Append supplement to summary
            new_summary = observation.summary
            if new_summary:
                new_summary += "\n\n[补充] " + intent.supplement_text
            else:
                new_summary = "[补充] " + intent.supplement_text

            run_edit(
                conn,
                observation_id,
                project=None,
                kind=None,
                title=None,
                summary=new_summary,
                tags=None,
                raw=None,
                timestamp=None,
                auto_tags=False,
            )
            result["updated"] = True
        record_feedback(conn, observation_id, "supplement", feedback_text)

    else:
        result["action"] = "unknown"
        result["message"] = "Could not determine feedback intent"

    return result


def record_feedback(
    conn: sqlite3.Connection,
    observation_id: int,
    action_type: str,
    feedback_text: str,
) -> int:
    """Record feedback to the feedback_log table.

    Returns:
        The ID of the newly created feedback record
    """
    cursor = conn.execute(
        """
        INSERT INTO feedback_log (target_observation_id, action_type, feedback_text, timestamp)
        VALUES (?, ?, ?, ?)
        """,
        (observation_id, action_type, feedback_text, utc_now()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_feedback_history(
    conn: sqlite3.Connection,
    observation_id: int,
) -> list[dict]:
    """Get feedback history for an observation."""
    rows = conn.execute(
        """
        SELECT id, action_type, feedback_text, timestamp
        FROM feedback_log
        WHERE target_observation_id = ?
        ORDER BY timestamp DESC, id DESC
        """,
        (observation_id,),
    ).fetchall()

    return [
        {
            "id": row["id"],
            "action_type": row["action_type"],
            "feedback_text": row["feedback_text"],
            "timestamp": row["timestamp"],
        }
        for row in rows
    ]
