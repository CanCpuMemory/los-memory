"""Incident extension CLI shim.

Keeps extension import path stable while reusing the canonical
top-level incident CLI implementation.
"""
from __future__ import annotations

from memory_tool.cli_incidents import add_incident_subcommands, handle_incident_command

__all__ = [
    "add_incident_subcommands",
    "handle_incident_command",
]
