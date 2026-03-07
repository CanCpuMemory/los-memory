"""Extension management for los-memory.

This module provides static registration for extension capabilities.
Extensions are not loaded dynamically - they are registered at import time
for predictable runtime behavior.

Extension Commands:
  incident    [EXT] Incident management (experimental)
  recovery    [EXT] Recovery management (experimental)
  knowledge   [EXT] Knowledge base (experimental)
  attribution [EXT] Attribution analysis (experimental, internal)

Extensions can be disabled by setting environment variable:
  MEMORY_DISABLE_EXTENSIONS=incident,recovery,knowledge,attribution
"""
from __future__ import annotations

import os
import warnings
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    import sqlite3

# Extension registry - static definition
# Format: name -> (module_path, register_func_name, handler_func_name, status)
_EXTENSION_REGISTRY: dict[str, tuple[str, str, str, str]] = {
    "incident": ("memory_tool.cli_incidents", "add_incident_subcommands", "handle_incident_command", "experimental"),
    "recovery": ("memory_tool.cli_recovery", "add_recovery_subcommands", "handle_recovery_command", "experimental"),
    "knowledge": ("memory_tool.cli_knowledge", "add_knowledge_subcommands", "handle_knowledge_command", "experimental"),
    "attribution": ("memory_tool.cli_attribution", "add_attribution_subcommands", "handle_attribution_command", "experimental"),
}

# Cached extension handlers
_extension_handlers: dict[str, Callable] = {}
_extensions_loaded = False


def get_disabled_extensions() -> set[str]:
    """Get set of disabled extensions from environment."""
    disabled = os.environ.get("MEMORY_DISABLE_EXTENSIONS", "")
    return set(x.strip() for x in disabled.split(",") if x.strip())


def is_extension_enabled(name: str) -> bool:
    """Check if an extension is enabled."""
    disabled = get_disabled_extensions()
    return name not in disabled


def register_extensions(
    subparsers: argparse._SubParsersAction,
    show_warnings: bool = True,
) -> list[str]:
    """Register all enabled extensions.

    Args:
        subparsers: The argparse subparsers action
        show_warnings: Whether to show deprecation/experimental warnings

    Returns:
        List of registered extension names
    """
    global _extensions_loaded

    disabled = get_disabled_extensions()
    registered = []

    for name, (module_path, register_func, _, status) in _EXTENSION_REGISTRY.items():
        if name in disabled:
            continue

        try:
            # Static import - no dynamic importlib
            module = __import__(module_path, fromlist=[register_func])
            register_fn = getattr(module, register_func)
            register_fn(subparsers)
            registered.append(name)

            if show_warnings and status == "experimental":
                warnings.warn(
                    f"Extension '{name}' is experimental and may change or be removed. "
                    f"Set MEMORY_DISABLE_EXTENSIONS={name} to disable.",
                    UserWarning,
                    stacklevel=2,
                )

        except ImportError as e:
            warnings.warn(f"Failed to load extension '{name}': {e}", ImportWarning)
        except AttributeError as e:
            warnings.warn(f"Extension '{name}' registration failed: {e}", ImportWarning)

    _extensions_loaded = True
    return registered


def get_extension_handler(name: str) -> Callable | None:
    """Get the handler function for an extension.

    Args:
        name: Extension name

    Returns:
        Handler function or None if not found/disabled
    """
    if not is_extension_enabled(name):
        return None

    if name in _extension_handlers:
        return _extension_handlers[name]

    if name not in _EXTENSION_REGISTRY:
        return None

    module_path, _, handler_func, _ = _EXTENSION_REGISTRY[name]

    try:
        module = __import__(module_path, fromlist=[handler_func])
        handler = getattr(module, handler_func)
        _extension_handlers[name] = handler
        return handler
    except (ImportError, AttributeError):
        return None


def dispatch_extension_command(
    cmd: str,
    conn: sqlite3.Connection,
    args: argparse.Namespace,
) -> dict | None:
    """Dispatch a command to an extension handler.

    Args:
        cmd: Command name (e.g., 'incident', 'recovery')
        conn: Database connection
        args: Parsed arguments

    Returns:
        Result dict or None if command not handled
    """
    handler = get_extension_handler(cmd)
    if handler:
        return handler(conn, args)
    return None


def list_extensions() -> list[dict]:
    """List all extensions and their status.

    Returns:
        List of extension info dicts
    """
    disabled = get_disabled_extensions()

    return [
        {
            "name": name,
            "status": info[3],
            "enabled": name not in disabled,
            "module": info[0],
        }
        for name, info in _EXTENSION_REGISTRY.items()
    ]


def get_extension_help_text() -> str:
    """Get help text for extension commands.

    Returns:
        Formatted help text showing available extensions
    """
    extensions = list_extensions()

    if not extensions:
        return ""

    lines = ["\nExtension Commands (experimental):"]
    for ext in extensions:
        status_mark = "[EXT]" if ext["enabled"] else "[OFF]"
        lines.append(f"  {ext['name']:<12} {status_mark} ({ext['status']})")

    return "\n".join(lines)
