"""Compatibility shim for historical ``memory_tool.core.analytics`` imports."""
from __future__ import annotations

from memory_tool.analytics import (
    get_tool_stats,
    log_agent_transition,
    log_tool_call,
    suggest_tools_for_task,
)

__all__ = [
    "get_tool_stats",
    "log_agent_transition",
    "log_tool_call",
    "suggest_tools_for_task",
]
