"""Incident extension model shim.

Keeps extension import path stable while reusing the canonical
top-level incident implementation.
"""
from __future__ import annotations

from memory_tool.incidents import Incident, IncidentManager

__all__ = [
    "Incident",
    "IncidentManager",
]
