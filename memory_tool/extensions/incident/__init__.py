"""Incident management extension for los-memory.

[EXT] This is an extension module (experimental).

Provides incident tracking and management capabilities for the self-healing system.
"""
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    import sqlite3

# Extension metadata
EXTENSION_NAME = "incident"
EXTENSION_VERSION = "1.0.0"
EXTENSION_STATUS = "experimental"


def _show_experimental_warning() -> None:
    """Show experimental extension warning."""
    warnings.warn(
        "Extension 'incident' is experimental and may change or be removed. "
        "Set MEMORY_DISABLE_EXTENSIONS=incident to disable.",
        UserWarning,
        stacklevel=3,
    )


# Import from models
from .models import (
    Incident,
    IncidentManager,
)

# Import from CLI
from .cli import (
    add_incident_subcommands,
    handle_incident_command,
)

__all__ = [
    "Incident",
    "IncidentManager",
    "add_incident_subcommands",
    "handle_incident_command",
    "EXTENSION_NAME",
    "EXTENSION_VERSION",
    "EXTENSION_STATUS",
]
