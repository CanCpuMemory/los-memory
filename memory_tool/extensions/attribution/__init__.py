"""Attribution analysis extension for los-memory.

[EXT] This is an extension module (experimental).

Provides root cause attribution analysis for incidents.
Note: This is an internal support extension tied to incident management.
"""
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    import sqlite3

# Extension metadata
EXTENSION_NAME = "attribution"
EXTENSION_VERSION = "1.0.0"
EXTENSION_STATUS = "experimental"

# Import from original location (forwarding)
from memory_tool.cli_attribution import (
    add_attribution_subcommands,
    handle_attribution_command,
)

__all__ = [
    "add_attribution_subcommands",
    "handle_attribution_command",
    "EXTENSION_NAME",
    "EXTENSION_VERSION",
    "EXTENSION_STATUS",
]
