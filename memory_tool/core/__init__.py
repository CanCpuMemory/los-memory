"""Compatibility exports for the historical ``memory_tool.core`` package.

The active implementation now lives in the top-level ``memory_tool.*`` modules.
This package remains as a thin forwarding layer so older imports keep resolving
to the current, schema-compatible code paths.
"""
from __future__ import annotations

from memory_tool.analytics import (
    get_tool_stats,
    log_agent_transition,
    log_tool_call,
    suggest_tools_for_task,
)
from memory_tool.checkpoints import (
    create_checkpoint,
    get_checkpoint,
    get_checkpoint_observations,
    list_checkpoints,
    resume_from_checkpoint,
)
from memory_tool.feedback import (
    FeedbackIntent,
    apply_feedback,
    get_feedback_history,
    parse_feedback_intent,
    record_feedback,
)
from memory_tool.links import (
    create_link,
    delete_link,
    find_similar_observations,
    get_links_for_observations,
    get_related_observations,
)
from memory_tool.operations import (
    add_observation,
    generate_visual_timeline,
    normalize_rows,
    run_clean,
    run_delete,
    run_edit,
    run_export,
    run_get,
    run_list,
    run_manage,
    run_search,
    run_timeline,
)
from memory_tool.sessions import (
    clear_active_session,
    end_session,
    generate_session_summary,
    get_active_session,
    get_session,
    get_session_file_path,
    get_session_observations,
    list_sessions,
    set_active_session,
    start_session,
)

__all__ = [
    "FeedbackIntent",
    "add_observation",
    "apply_feedback",
    "clear_active_session",
    "create_checkpoint",
    "create_link",
    "delete_link",
    "end_session",
    "find_similar_observations",
    "generate_session_summary",
    "generate_visual_timeline",
    "get_active_session",
    "get_checkpoint",
    "get_checkpoint_observations",
    "get_feedback_history",
    "get_links_for_observations",
    "get_related_observations",
    "get_session",
    "get_session_file_path",
    "get_session_observations",
    "get_tool_stats",
    "list_checkpoints",
    "list_sessions",
    "log_agent_transition",
    "log_tool_call",
    "normalize_rows",
    "parse_feedback_intent",
    "record_feedback",
    "resume_from_checkpoint",
    "run_clean",
    "run_delete",
    "run_edit",
    "run_export",
    "run_get",
    "run_list",
    "run_manage",
    "run_search",
    "run_timeline",
    "set_active_session",
    "start_session",
    "suggest_tools_for_task",
]
