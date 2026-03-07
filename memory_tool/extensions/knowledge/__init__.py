"""Knowledge base extension for los-memory.

[EXT] This is an extension module (experimental).

Provides knowledge management capabilities for incident resolution patterns.
"""
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    import sqlite3

# Extension metadata
EXTENSION_NAME = "knowledge"
EXTENSION_VERSION = "1.0.0"
EXTENSION_STATUS = "experimental"

# Import from original location (forwarding)
from memory_tool.cli_knowledge import (
    add_knowledge_subcommands,
    handle_knowledge_command,
)

__all__ = [
    "add_knowledge_subcommands",
    "handle_knowledge_command",
    "EXTENSION_NAME",
    "EXTENSION_VERSION",
    "EXTENSION_STATUS",
]
