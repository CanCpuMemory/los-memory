"""Compatibility shim for the historical ``memory_tool.core.cli`` module."""
from __future__ import annotations

from memory_tool.cli import main, parse_args

__all__ = ["main", "parse_args"]
