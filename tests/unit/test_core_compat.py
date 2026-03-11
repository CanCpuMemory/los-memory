"""Compatibility tests for the historical memory_tool.core package."""
from __future__ import annotations

from memory_tool import analytics, checkpoints, feedback, links, operations, sessions
from memory_tool.core import (
    analytics as core_analytics,
    checkpoints as core_checkpoints,
    feedback as core_feedback,
    links as core_links,
    operations as core_operations,
    sessions as core_sessions,
)


def test_core_operations_shim_exports_active_functions() -> None:
    assert core_operations.run_get is operations.run_get
    assert core_operations.run_list is operations.run_list
    assert core_operations.run_edit is operations.run_edit


def test_core_sessions_shim_exports_active_functions() -> None:
    assert core_sessions.get_active_session is sessions.get_active_session
    assert core_sessions.start_session is sessions.start_session
    assert core_sessions.generate_session_summary is sessions.generate_session_summary


def test_core_other_modules_forward_to_active_implementations() -> None:
    assert core_checkpoints.resume_from_checkpoint is checkpoints.resume_from_checkpoint
    assert core_feedback.apply_feedback is feedback.apply_feedback
    assert core_links.create_link is links.create_link
    assert core_analytics.get_tool_stats is analytics.get_tool_stats
