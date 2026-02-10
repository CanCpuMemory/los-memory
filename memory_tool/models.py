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
