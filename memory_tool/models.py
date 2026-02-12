"""Data models for the memory tool."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Observation:
    id: int
    timestamp: str
    project: str
    kind: str
    title: str
    summary: str
    tags: List[str]
    raw: str
    session_id: Optional[int] = None


@dataclass
class Session:
    id: int
    start_time: str
    end_time: Optional[str]
    project: str
    working_dir: str
    agent_type: str
    summary: str
    status: str


@dataclass
class Checkpoint:
    id: int
    timestamp: str
    name: str
    description: str
    tag: str
    session_id: Optional[int]
    observation_count: int
    project: str


@dataclass
class Feedback:
    """Feedback record for observation corrections/supplements."""

    id: int
    target_observation_id: int
    action_type: str  # "correct", "supplement", "delete"
    feedback_text: str
    timestamp: str


@dataclass
class ObservationLink:
    """Link between two observations representing a relationship."""

    id: int
    from_id: int
    to_id: int
    link_type: str  # "related", "child", "parent", "refines"
    created_at: str


@dataclass
class ToolCall:
    """Tool call observation for tracking tool usage."""

    id: int
    timestamp: str
    project: str
    tool_name: str
    tool_input: dict
    tool_output: Optional[dict]
    status: str  # "success", "error"
    duration_ms: Optional[int]
    session_id: Optional[int] = None
