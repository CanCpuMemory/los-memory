"""Recovery management extension for los-memory.

[EXT] This is an extension module (experimental).

Provides recovery action execution and policy management for the self-healing system.
"""
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    import sqlite3

# Extension metadata
EXTENSION_NAME = "recovery"
EXTENSION_VERSION = "1.0.0"
EXTENSION_STATUS = "experimental"

# Import from original location (forwarding)
# These will be physically migrated in a later phase
from memory_tool.cli_recovery import (
    add_recovery_subcommands,
    handle_recovery_command,
)

__all__ = [
    "add_recovery_subcommands",
    "handle_recovery_command",
    "EXTENSION_NAME",
    "EXTENSION_VERSION",
    "EXTENSION_STATUS",
]
