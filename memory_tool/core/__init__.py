"""Core memory capabilities for los-memory.

This module contains the core capabilities that are part of the project's
primary定位 as a CLI-first personal/agent memory tool:

- Observation management (operations)
- Session management (sessions)
- Checkpoint management (checkpoints)
- Feedback processing (feedback)
- Observation linking (links)
- Tool call analytics (analytics)

These capabilities are always available and have full backward compatibility承诺.
"""

from __future__ import annotations

# Core operations
from .operations import (
    add_observation,
    run_add,
    run_get,
    run_list,
    run_delete,
    run_edit,
    run_search,
    normalize_rows,
)

# Session management
from .sessions import (
    get_active_session,
    set_active_session,
    start_session,
    end_session,
    get_session,
    list_sessions,
    get_session_observations,
)

# Checkpoint management
from .checkpoints import (
    create_checkpoint,
    list_checkpoints,
    get_checkpoint,
    get_checkpoint_observations,
    resume_from_checkpoint,
)

# Feedback processing
from .feedback import (
    FeedbackIntent,
    parse_feedback_intent,
    apply_feedback,
    record_feedback,
    get_feedback_history,
)

# Observation linking
from .links import (
    create_link,
    delete_link,
    get_related_observations,
    find_similar_observations,
    get_links_for_observations,
)

# Analytics
from .analytics import (
    get_tool_stats,
    suggest_tools_for_task,
    log_tool_call,
    log_agent_transition,
)

__all__ = [
    # Operations
    "add_observation",
    "run_add",
    "run_get",
    "run_list",
    "run_delete",
    "run_edit",
    "run_search",
    "normalize_rows",
    # Sessions
    "get_active_session",
    "set_active_session",
    "start_session",
    "end_session",
    "get_session",
    "list_sessions",
    "get_session_observations",
    # Checkpoints
    "create_checkpoint",
    "list_checkpoints",
    "get_checkpoint",
    "get_checkpoint_observations",
    "resume_from_checkpoint",
    # Feedback
    "FeedbackIntent",
    "parse_feedback_intent",
    "apply_feedback",
    "record_feedback",
    "get_feedback_history",
    # Links
    "create_link",
    "delete_link",
    "get_related_observations",
    "find_similar_observations",
    "get_links_for_observations",
    # Analytics
    "get_tool_stats",
    "suggest_tools_for_task",
    "log_tool_call",
    "log_agent_transition",
]
