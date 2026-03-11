"""Compatibility shim for historical ``memory_tool.core.feedback`` imports."""
from __future__ import annotations

from memory_tool.feedback import (
    FeedbackIntent,
    apply_feedback,
    get_feedback_history,
    parse_feedback_intent,
    record_feedback,
)

__all__ = [
    "FeedbackIntent",
    "apply_feedback",
    "get_feedback_history",
    "parse_feedback_intent",
    "record_feedback",
]
