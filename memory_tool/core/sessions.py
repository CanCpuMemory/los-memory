"""Compatibility shim for historical ``memory_tool.core.sessions`` imports."""
from __future__ import annotations

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
    "clear_active_session",
    "end_session",
    "generate_session_summary",
    "get_active_session",
    "get_session",
    "get_session_file_path",
    "get_session_observations",
    "list_sessions",
    "set_active_session",
    "start_session",
]
