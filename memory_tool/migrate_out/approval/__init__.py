"""Approval system - MIGRATING OUT to VPS Agent Web.

⚠️  DEPRECATION WARNING ⚠️

The approval system is being migrated out of los-memory to VPS Agent Web.
This module will be removed in a future version (target: 12 months).

Migration Timeline:
  Phase 1 (Now):     Freeze new features, add deprecation warnings
  Phase 2 (4-8m):    VPS Agent Web parallel build
  Phase 3 (9-12m):   Data migration, dual-system run, complete removal

Recommended Action:
  Use VPS Agent Web's approval workflow instead of los-memory approval.
  See: https://docs.vps-agent-web.example.com/approval

Environment:
  Set MEMORY_DISABLE_EXTENSIONS=approval to disable now.
"""
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    import sqlite3

# Emit deprecation warning on import
warnings.warn(
    "The approval module is deprecated and will be removed in a future version. "
    "Please migrate to VPS Agent Web's approval workflow. "
    "See: https://docs.vps-agent-web.example.com/approval",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from original location for now
# These will be removed after migration
from memory_tool.cli_approval import (
    add_approval_subcommands,
    handle_approval_command,
)
from memory_tool.approval_api import ApprovalAPI
from memory_tool.approval_store import ApprovalStore

__all__ = [
    "add_approval_subcommands",
    "handle_approval_command",
    "ApprovalAPI",
    "ApprovalStore",
]


def register_migrating_approval(
    subparsers: argparse._SubParsersAction,
    show_warning: bool = True,
) -> None:
    """Register approval commands with deprecation warning.

    This function wraps the original registration to add
    deprecation messaging.
    """
    if show_warning:
        warnings.warn(
            "Approval commands are deprecated and will be removed. "
            "Use VPS Agent Web instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    # Call original registration
    add_approval_subcommands(subparsers)


def handle_migrating_approval_command(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
) -> dict:
    """Handle approval command with deprecation warning."""
    warnings.warn(
        "Approval command is deprecated. Migrate to VPS Agent Web.",
        DeprecationWarning,
        stacklevel=2,
    )
    return handle_approval_command(conn, args)
