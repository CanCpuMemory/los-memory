#!/usr/bin/env python3
"""Command-line interface for the memory tool.

los-memory v2.0.0 - Architecture Convergence

Command Groups:
  Core Commands (stable, full compatibility承诺):
    memory, observation, session, checkpoint, project, tool, review, admin

  Extension Commands (experimental,尽力而为):
    incident [EXT], recovery [EXT], knowledge [EXT], attribution [EXT]

  Migrating Commands (deprecated, will be removed):
    approval [DEPRECATED] - Migrating to VPS Agent Web

Environment Variables:
  MEMORY_DISABLE_EXTENSIONS - Comma-separated list of extensions to disable
  Example: MEMORY_DISABLE_EXTENSIONS=incident,recovery,knowledge
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import warnings
from typing import Sequence
from dataclasses import asdict
from pathlib import Path

# Core imports - top-level modules are the active implementation path
from .database import connect_db, ensure_fts, ensure_schema, init_db
from .analytics import get_tool_stats, log_agent_transition, log_tool_call, suggest_tools_for_task
from .feedback import apply_feedback, get_feedback_history
from .review_feedback import apply_review_feedback
from .links import create_link, delete_link, find_similar_observations, get_related_observations
from .operations import (
    add_observation,
    generate_visual_timeline,
    run_bulk_add,
    run_clean,
    run_delete,
    run_edit,
    run_export,
    run_get,
    run_list,
    run_manage,
    run_search,
    run_timeline,
)
from .sessions import (
    clear_active_session,
    end_session,
    generate_session_summary,
    get_bound_active_session,
    get_session,
    get_session_observations,
    list_sessions,
    set_active_session,
    start_session,
)
from .checkpoints import (
    create_checkpoint,
    get_checkpoint,
    get_checkpoint_observations,
    list_checkpoints,
    resume_from_checkpoint,
)
from .projects import (
    get_active_project,
    get_project_stats,
    list_projects,
    set_active_project,
)
from .config import get_config
from .errors import (
    DB_CORRUPTED,
    DB_LOCKED,
    DB_NOT_FOUND,
    DB_READONLY,
    CFG_INVALID_PROFILE,
    NF_ACTIVE_SESSION,
    NF_CHECKPOINT,
    NF_COMMAND,
    NF_OBSERVATION,
    NF_PROJECT,
    NF_SESSION,
    SYS_INTERRUPTED,
    SYS_IO_ERROR,
    SYS_PYTHON_ERROR,
    SYS_SQLITE_ERROR,
    VAL_EMPTY_VALUE,
    VAL_INVALID_FORMAT,
    format_error_response,
)
from .exit_codes import get_exit_code_for_error
from .share import run_import, run_share
from .utils import (
    DEFAULT_LLM_HOOK,
    DEFAULT_PROFILE,
    OBSERVATION_METADATA_RESERVED_KEYS,
    PROFILE_CHOICES,
    auto_tags_from_text,
    load_json_input,
    load_json_object_input,
    metadata_to_json,
    normalize_tags_list,
    normalize_text,
    parse_ids,
    resolve_db_path,
    tags_to_json,
    tags_to_text,
    utc_now,
)

# Extension imports (static registration)
from .extensions import (
    dispatch_extension_command,
    get_disabled_extensions,
    list_extensions,
    register_extensions,
)

# Migration imports (lazy-loaded to avoid import-time deprecation warnings)
_approval_available = True

LEGACY_COMMAND_ALIASES = {
    "add": ("observation", "add"),
    "search": ("memory", "search"),
    "timeline": ("memory", "timeline"),
    "get": ("memory", "get"),
    "edit": ("observation", "edit"),
    "delete": ("observation", "delete"),
    "list": ("memory", "list"),
    "export": ("memory", "export"),
    "clean": ("memory", "clean"),
    "manage": ("admin", "manage"),
    "capture": ("observation", "capture"),
    "feedback": ("observation", "feedback"),
    "review-feedback": ("review", "apply"),
    "tool-log": ("tool", "log"),
    "transition-log": ("tool", "transition"),
    "tool-stats": ("tool", "stats"),
    "tool-suggest": ("tool", "suggest"),
    "link": ("observation", "link"),
    "unlink": ("observation", "unlink"),
    "related": ("observation", "related"),
    "share": ("admin", "share"),
    "import": ("admin", "import"),
}

GLOBAL_OPTIONS_WITH_VALUES = {
    "--profile",
    "--db",
    "--output-format",
    "--output",
    "-o",
    "--color",
}

GLOBAL_FLAG_OPTIONS = {
    "--human",
    "--verbose",
    "-v",
}

def _load_approval():
    """Lazy load approval module to avoid import-time warnings."""
    from .migrate_out.approval import (
        add_approval_subcommands,
        handle_approval_command,
    )
    return add_approval_subcommands, handle_approval_command


def _add_global_arguments(parser: argparse.ArgumentParser) -> None:
    """Register global CLI arguments shared by bootstrap and full parsers."""
    parser.add_argument(
        "--profile",
        choices=PROFILE_CHOICES,
        default=argparse.SUPPRESS,
        help="Memory profile to select default DB path",
    )
    parser.add_argument("--db", default=argparse.SUPPRESS, help="Path to SQLite database (overrides --profile)")
    parser.add_argument("--human", action="store_true", help="Output in human-readable format")
    parser.add_argument("--output-format", "--output", "-o", dest="output_format", choices=["json", "yaml", "table"],
                        default=argparse.SUPPRESS, help="Output format (default: json)")
    parser.add_argument("--color", choices=["auto", "always", "never"], default=argparse.SUPPRESS,
                        help="Color output mode")
    parser.add_argument("--verbose", "-v", action="store_true", default=argparse.SUPPRESS, help="Verbose output")


def _rewrite_legacy_command_argv(argv: Sequence[str]) -> tuple[list[str], str | None]:
    """Rewrite legacy flat commands to their nested equivalents before parsing."""
    command_index: int | None = None
    raw_argv = list(argv)
    idx = 0
    while idx < len(raw_argv):
        token = raw_argv[idx]
        if token == "--":
            if idx + 1 < len(raw_argv):
                command_index = idx + 1
            break
        if token in GLOBAL_FLAG_OPTIONS:
            idx += 1
            continue
        if token in GLOBAL_OPTIONS_WITH_VALUES:
            idx += 2
            continue
        if token.startswith("-o") and token != "-o":
            idx += 1
            continue
        if any(token.startswith(f"{option}=") for option in GLOBAL_OPTIONS_WITH_VALUES if option.startswith("--")):
            idx += 1
            continue
        if token.startswith("-"):
            return raw_argv, None
        command_index = idx
        break

    if command_index is None or command_index >= len(raw_argv):
        return list(argv), None

    legacy_command = raw_argv[command_index]
    target = LEGACY_COMMAND_ALIASES.get(legacy_command)
    if target is None:
        return list(argv), None

    rewritten = list(raw_argv)
    rewritten[command_index:command_index + 1] = list(target)
    return rewritten, legacy_command


def _register_init_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register `init` command."""
    subparsers.add_parser("init", help="Initialize the database")


def _register_memory_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register `memory` command group."""
    memory_parser = subparsers.add_parser("memory", help="Memory data access commands")
    memory_subparsers = memory_parser.add_subparsers(dest="memory_action", required=True)
    _add_memory_search_subcommand(memory_subparsers)
    _add_memory_list_subcommand(memory_subparsers)
    _add_memory_get_subcommand(memory_subparsers)
    _add_memory_timeline_subcommand(memory_subparsers)
    _add_memory_export_subcommand(memory_subparsers)
    _add_memory_clean_subcommand(memory_subparsers)


def _add_memory_search_subcommand(memory_subparsers: argparse._SubParsersAction) -> None:
    memory_search = memory_subparsers.add_parser("search", help="Search observations")
    memory_search.add_argument("query")
    memory_search.add_argument("--limit", type=int, default=10)
    memory_search.add_argument("--offset", type=int, default=0)
    memory_search.add_argument("--mode", choices=["auto", "fts", "like"], default="auto")
    memory_search.add_argument("--fts-quote", action="store_true")
    memory_search.add_argument(
        "--require-tags",
        default="",
        help="Comma-separated tags that every result must contain",
    )
    memory_search.add_argument(
        "--metadata-filter",
        default=None,
        help="Metadata equality filter as a JSON object. Use '@/path/file.json' or '@-' to read from stdin.",
    )


def _add_memory_list_subcommand(memory_subparsers: argparse._SubParsersAction) -> None:
    memory_list = memory_subparsers.add_parser("list", help="List observations")
    memory_list.add_argument("--limit", type=int, default=20)
    memory_list.add_argument("--offset", type=int, default=0)
    memory_list.add_argument(
        "--require-tags",
        default="",
        help="Comma-separated tags that every result must contain",
    )
    memory_list.add_argument(
        "--metadata-filter",
        default=None,
        help="Metadata equality filter as a JSON object. Use '@/path/file.json' or '@-' to read from stdin.",
    )


def _add_memory_get_subcommand(memory_subparsers: argparse._SubParsersAction) -> None:
    memory_get = memory_subparsers.add_parser("get", help="Fetch observations by id")
    memory_get.add_argument("ids", help="Comma-separated observation ids")


def _add_memory_timeline_subcommand(memory_subparsers: argparse._SubParsersAction) -> None:
    memory_timeline = memory_subparsers.add_parser("timeline", help="Timeline query")
    memory_timeline.add_argument("--start")
    memory_timeline.add_argument("--end")
    memory_timeline.add_argument("--around-id", type=int)
    memory_timeline.add_argument("--window-minutes", type=int, default=120)
    memory_timeline.add_argument("--limit", type=int, default=20)
    memory_timeline.add_argument("--offset", type=int, default=0)
    memory_timeline.add_argument("--visual", action="store_true")
    memory_timeline.add_argument("--group-by", choices=["hour", "day", "session"], default=None)


def _add_memory_export_subcommand(memory_subparsers: argparse._SubParsersAction) -> None:
    memory_export = memory_subparsers.add_parser("export", help="Export observations")
    memory_export.add_argument("--format", choices=["json", "csv"], default="json")
    memory_export.add_argument("--output", default=None)
    memory_export.add_argument("--limit", type=int, default=1000)
    memory_export.add_argument("--offset", type=int, default=0)


def _add_memory_clean_subcommand(memory_subparsers: argparse._SubParsersAction) -> None:
    memory_clean = memory_subparsers.add_parser("clean", help="Delete old observations")
    memory_clean.add_argument("--before")
    memory_clean.add_argument("--older-than-days", type=int)
    memory_clean.add_argument("--project")
    memory_clean.add_argument("--kind")
    memory_clean.add_argument("--tag")
    memory_clean.add_argument("--all", action="store_true")
    memory_clean.add_argument("--dry-run", action="store_true")
    memory_clean.add_argument("--vacuum", action="store_true")


def _register_observation_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register `observation` command group."""
    obs_parser = subparsers.add_parser("observation", help="Observation management commands")
    obs_subparsers = obs_parser.add_subparsers(dest="obs_action", required=True)
    _add_observation_add_subcommand(obs_subparsers)
    _add_observation_edit_subcommand(obs_subparsers)
    _add_observation_delete_subcommand(obs_subparsers)
    _add_observation_capture_subcommand(obs_subparsers)
    _add_observation_feedback_subcommand(obs_subparsers)
    _add_observation_bulk_subcommand(obs_subparsers)
    _add_observation_link_subcommand(obs_subparsers)
    _add_observation_unlink_subcommand(obs_subparsers)
    _add_observation_related_subcommand(obs_subparsers)


def _add_observation_add_subcommand(obs_subparsers: argparse._SubParsersAction) -> None:
    obs_add = obs_subparsers.add_parser("add", help="Add an observation")
    obs_add.add_argument("--timestamp", default=utc_now())
    obs_add.add_argument("--project", default="general")
    obs_add.add_argument("--kind", default="note")
    obs_add.add_argument("--title", required=True)
    obs_add.add_argument("--summary", required=True)
    obs_add.add_argument("--tags", default="")
    obs_add.add_argument("--raw", default="")
    obs_add.add_argument(
        "--metadata",
        default=None,
        help="Observation metadata JSON object. Use '@/path/file.json' or '@-' to read from stdin.",
    )
    obs_add.add_argument("--auto-tags", action="store_true")
    obs_add.add_argument("--auto-importance", action="store_true")
    obs_add.add_argument(
        "--dedup-mode", choices=["allow", "skip"], default="allow",
        help="Dedup strategy: 'allow' always inserts, 'skip' skips duplicates"
    )
    obs_add.add_argument("--llm-hook", default=DEFAULT_LLM_HOOK)


def _add_observation_edit_subcommand(obs_subparsers: argparse._SubParsersAction) -> None:
    obs_edit = obs_subparsers.add_parser("edit", help="Edit an observation")
    obs_edit.add_argument("--id", type=int, required=True)
    obs_edit.add_argument("--timestamp", default=None)
    obs_edit.add_argument("--project", default=None)
    obs_edit.add_argument("--kind", default=None)
    obs_edit.add_argument("--title", default=None)
    obs_edit.add_argument("--summary", default=None)
    obs_edit.add_argument("--tags", default=None)
    obs_edit.add_argument("--raw", default=None)
    obs_edit.add_argument(
        "--metadata",
        default=None,
        help="Replace observation metadata with a JSON object. Use '@/path/file.json' or '@-' to read from stdin.",
    )
    obs_edit.add_argument("--auto-tags", action="store_true")


def _add_observation_delete_subcommand(obs_subparsers: argparse._SubParsersAction) -> None:
    obs_delete = obs_subparsers.add_parser("delete", help="Delete observations")
    obs_delete.add_argument("ids")
    obs_delete.add_argument("--dry-run", action="store_true")


def _add_observation_capture_subcommand(obs_subparsers: argparse._SubParsersAction) -> None:
    obs_capture = obs_subparsers.add_parser("capture", help="Quick capture")
    obs_capture.add_argument("text", nargs="+")
    obs_capture.add_argument("--project")
    obs_capture.add_argument("--kind", default="note")
    obs_capture.add_argument("--tags", default="")
    obs_capture.add_argument("--auto-tags", action="store_true")


def _add_observation_feedback_subcommand(obs_subparsers: argparse._SubParsersAction) -> None:
    obs_feedback = obs_subparsers.add_parser("feedback", help="Provide feedback on observations")
    obs_feedback.add_argument("text", nargs="+", help="Feedback text")
    obs_feedback.add_argument("--id", type=int, required=True, dest="observation_id")
    obs_feedback.add_argument(
        "--metadata",
        default=None,
        help="Structured feedback metadata JSON object. Use '@/path/file.json' or '@-' to read from stdin.",
    )
    obs_feedback.add_argument("--dry-run", action="store_true")
    obs_feedback.add_argument("--history", action="store_true")


def _add_observation_bulk_subcommand(obs_subparsers: argparse._SubParsersAction) -> None:
    obs_bulk = obs_subparsers.add_parser("bulk", help="Create observations from a JSON payload")
    obs_bulk.add_argument(
        "--input",
        required=True,
        help="JSON array or object-with-items payload. Use '@/path/file.json' or '@-' to read from stdin.",
    )
    obs_bulk.add_argument("--dry-run", action="store_true")
    obs_bulk.add_argument("--auto-tags", action="store_true")
    obs_bulk.add_argument("--auto-importance", action="store_true")
    obs_bulk.add_argument(
        "--dedup-mode", choices=["allow", "skip"], default="allow",
        help="Dedup strategy: 'allow' always inserts, 'skip' skips duplicates"
    )


def _add_observation_link_subcommand(obs_subparsers: argparse._SubParsersAction) -> None:
    obs_link = obs_subparsers.add_parser("link", help="Create link between observations")
    obs_link.add_argument("--from", type=int, required=True, dest="from_id")
    obs_link.add_argument("--to", type=int, required=True, dest="to_id")
    obs_link.add_argument("--type", choices=["related", "child", "parent", "refines"], default="related")


def _add_observation_unlink_subcommand(obs_subparsers: argparse._SubParsersAction) -> None:
    obs_unlink = obs_subparsers.add_parser("unlink", help="Remove link between observations")
    obs_unlink.add_argument("--from", type=int, required=True, dest="from_id")
    obs_unlink.add_argument("--to", type=int, required=True, dest="to_id")
    obs_unlink.add_argument("--type", choices=["related", "child", "parent", "refines"], default=None)


def _add_observation_related_subcommand(obs_subparsers: argparse._SubParsersAction) -> None:
    obs_related = obs_subparsers.add_parser("related", help="Find related observations")
    obs_related.add_argument("id", type=int, help="Observation ID")
    obs_related.add_argument("--type", choices=["related", "child", "parent", "refines"], default=None)
    obs_related.add_argument("--limit", type=int, default=20)
    obs_related.add_argument("--suggest", action="store_true")


def _register_session_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register `session` command group."""
    session_parser = subparsers.add_parser("session", help="Session management")
    session_subparsers = session_parser.add_subparsers(dest="session_action", required=True)

    session_start = session_subparsers.add_parser("start", help="Start a new session")
    session_start.add_argument("--project", default="general")
    session_start.add_argument("--working-dir", default=os.getcwd())
    session_start.add_argument("--agent-type", default=DEFAULT_PROFILE)
    session_start.add_argument("--summary", default="")

    session_stop = session_subparsers.add_parser("stop", help="Stop current session")
    session_stop.add_argument("--summary")

    session_list = session_subparsers.add_parser("list", help="List sessions")
    session_list.add_argument("--status", choices=["active", "completed", "ended"])
    session_list.add_argument("--limit", type=int, default=20)

    session_show = session_subparsers.add_parser("show", help="Show session details")
    session_show.add_argument("session_id", type=int)
    session_show.add_argument("--observations", action="store_true")

    session_resume = session_subparsers.add_parser("resume", help="Resume a session")
    session_resume.add_argument("session_id", type=int, nargs="?")


def _register_checkpoint_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register `checkpoint` command group."""
    checkpoint_parser = subparsers.add_parser("checkpoint", help="Checkpoint management")
    checkpoint_subparsers = checkpoint_parser.add_subparsers(dest="checkpoint_action", required=True)

    checkpoint_create = checkpoint_subparsers.add_parser("create", help="Create checkpoint")
    checkpoint_create.add_argument("--name", required=True)
    checkpoint_create.add_argument("--description", default="")
    checkpoint_create.add_argument("--tag", default="")

    checkpoint_list = checkpoint_subparsers.add_parser("list", help="List checkpoints")
    checkpoint_list.add_argument("--limit", type=int, default=20)

    checkpoint_resume = checkpoint_subparsers.add_parser("resume", help="Resume from checkpoint")
    checkpoint_resume.add_argument("checkpoint_id", type=int)

    checkpoint_show = checkpoint_subparsers.add_parser("show", help="Show checkpoint")
    checkpoint_show.add_argument("checkpoint_id", type=int)


def _register_project_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register `project` command group."""
    project_parser = subparsers.add_parser("project", help="Project management")
    project_subparsers = project_parser.add_subparsers(dest="project_action", required=True)

    project_list = project_subparsers.add_parser("list", help="List projects")
    project_list.add_argument("--limit", type=int, default=50)

    project_switch = project_subparsers.add_parser("switch", help="Set active project")
    project_switch.add_argument("project_name")

    project_archive = project_subparsers.add_parser("archive", help="Archive project")
    project_archive.add_argument("project_name")

    project_stats = project_subparsers.add_parser("stats", help="Show project stats")
    project_stats.add_argument("project_name", nargs="?")

    project_active = project_subparsers.add_parser("active", help="Show/set active project")
    project_active.add_argument("project_name", nargs="?")


def _register_tool_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register `tool` command group."""
    tool_parser = subparsers.add_parser("tool", help="Tool tracking commands")
    tool_subparsers = tool_parser.add_subparsers(dest="tool_action", required=True)

    tool_log = tool_subparsers.add_parser("log", help="Log a tool call")
    tool_log.add_argument("--tool", required=True)
    tool_log.add_argument("--input", required=True)
    tool_log.add_argument("--output", default="{}")
    tool_log.add_argument("--status", choices=["success", "error"], default="success")
    tool_log.add_argument("--duration", type=int, default=None)
    tool_log.add_argument("--project", default=None)

    tool_stats = tool_subparsers.add_parser("stats", help="Show tool usage statistics")
    tool_stats.add_argument("--project", default=None)
    tool_stats.add_argument("--limit", type=int, default=20)

    tool_suggest = tool_subparsers.add_parser("suggest", help="Suggest tools for a task")
    tool_suggest.add_argument("task", nargs="+")
    tool_suggest.add_argument("--limit", type=int, default=5)

    tool_transition = tool_subparsers.add_parser("transition", help="Log an agent transition")
    tool_transition.add_argument("--phase", required=True)
    tool_transition.add_argument("--action", required=True)
    tool_transition.add_argument("--input", required=True)
    tool_transition.add_argument("--output", default="{}")
    tool_transition.add_argument("--status", choices=["success", "error"], default="success")
    tool_transition.add_argument("--reward", type=float, default=None)
    tool_transition.add_argument("--project", default=None)


def _register_admin_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register `admin` command group."""
    admin_parser = subparsers.add_parser("admin", help="Administrative commands")
    admin_subparsers = admin_parser.add_subparsers(dest="admin_action", required=True)

    admin_doctor = admin_subparsers.add_parser("doctor", help="Run health checks")
    admin_doctor.add_argument("--fix", action="store_true", help="Attempt auto-fixes")

    admin_manage = admin_subparsers.add_parser("manage", help="Manage database")
    admin_manage.add_argument("action", choices=["stats", "projects", "tags", "vacuum"])
    admin_manage.add_argument("--limit", type=int, default=20)

    admin_share = admin_subparsers.add_parser("share", help="Create shareable bundle")
    admin_share.add_argument("--output", "-o", required=True)
    admin_share.add_argument("--format", choices=["json", "markdown", "html"], default="json")
    admin_share.add_argument("--project")
    admin_share.add_argument("--kind")
    admin_share.add_argument("--tag")
    admin_share.add_argument("--session", type=int)
    admin_share.add_argument("--since")
    admin_share.add_argument("--limit", type=int, default=1000)

    admin_import = admin_subparsers.add_parser("import", help="Import bundle")
    admin_import.add_argument("file")
    admin_import.add_argument("--project")
    admin_import.add_argument("--dry-run", action="store_true")

    # admin extensions - Extension management
    admin_extensions = admin_subparsers.add_parser("extensions", help="Manage extensions [EXT]")
    admin_extensions.add_argument("action", choices=["list", "status"], default="list", nargs="?")


def _register_review_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register `review` command group."""
    review_parser = subparsers.add_parser("review", help="Review feedback commands")
    review_subparsers = review_parser.add_subparsers(dest="review_action", required=True)

    review_apply = review_subparsers.add_parser("apply", help="Apply review feedback")
    review_apply.add_argument("--file", required=True)
    review_apply.add_argument("--dry-run", action="store_true")


def _register_extension_subcommands(
    parser: argparse.ArgumentParser,
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register extension and migrating command groups."""
    # Register extensions via static registration system
    registered = register_extensions(subparsers, show_warnings=False)

    # Register migrating approval with deprecation warning
    # Use lazy import to avoid import-time deprecation warnings
    if _approval_available and "approval" not in get_disabled_extensions():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # Suppress immediate warning
            add_approval_subcommands, _ = _load_approval()
            add_approval_subcommands(subparsers)
            registered.append("approval")

    # Store registered extensions for help text
    parser.registered_extensions = registered  # type: ignore


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with all command groups."""
    parser = argparse.ArgumentParser(
        description="los-memory: Memory ledger for AI agent observations",
        prog="los-memory",
    )
    _add_global_arguments(parser)

    subparsers = parser.add_subparsers(dest="command", required=True)
    _register_init_subcommands(subparsers)
    _register_memory_subcommands(subparsers)
    _register_observation_subcommands(subparsers)
    _register_session_subcommands(subparsers)
    _register_checkpoint_subcommands(subparsers)
    _register_project_subcommands(subparsers)
    _register_tool_subcommands(subparsers)
    _register_admin_subcommands(subparsers)
    _register_review_subcommands(subparsers)
    _register_extension_subcommands(parser, subparsers)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    rewritten_argv, legacy_command = _rewrite_legacy_command_argv(raw_argv)
    parser = _build_parser()
    args = parser.parse_args(rewritten_argv)
    if legacy_command is not None:
        args.legacy_command = legacy_command
    return args


def _ensure_human_flag(args: argparse.Namespace) -> None:
    if not hasattr(args, "human"):
        args.human = False


def _build_cli_config_overrides(args: argparse.Namespace) -> dict:
    cli_config_overrides = {}
    if hasattr(args, "profile"):
        cli_config_overrides["profile"] = args.profile
    if hasattr(args, "db"):
        cli_config_overrides["db_path"] = args.db
    if hasattr(args, "output_format"):
        cli_config_overrides["output"] = args.output_format
    if hasattr(args, "color"):
        cli_config_overrides["color"] = args.color
    if hasattr(args, "verbose"):
        cli_config_overrides["verbose"] = args.verbose
    return cli_config_overrides


def _apply_config_to_args(args: argparse.Namespace) -> str:
    config = get_config(
        cli_args=_build_cli_config_overrides(args),
        force_reload=True,
    )
    args.profile = config.profile
    args.db = config.db_path
    args.output_format = config.output
    args.color = config.color
    args.verbose = config.verbose
    return resolve_db_path(args.profile, args.db)


def _run_init_command(args: argparse.Namespace, db_path: str) -> int:
    init_db(db_path)
    _print_output(args, {"ok": True, "db": db_path, "profile": args.profile}, "init")
    return 0


def _is_doctor_command(args: argparse.Namespace) -> bool:
    return args.command == "admin" and getattr(args, "admin_action", None) == "doctor"


def _run_doctor_command(args: argparse.Namespace, db_path: str) -> int:
    from .doctor import doctor_command, format_human_output, open_doctor_connection

    conn = None
    try:
        conn = open_doctor_connection(db_path)
    except sqlite3.Error:
        pass  # Will be reported in doctor report

    response = doctor_command(
        db_path=db_path,
        profile=args.profile,
        conn=conn,
        fix=getattr(args, "fix", False),
    )
    if conn:
        conn.close()

    use_human = args.human or args.output_format == "table"
    if use_human:
        print(format_human_output(response.data))
    else:
        response.print(format=args.output_format, human=False)
    return 0 if response.ok else 1


def _run_standard_command(args: argparse.Namespace, db_path: str) -> int:
    # Regular command path - connect and initialize schema within the unified
    # error-handling block so DB/open failures get standardized error codes.
    conn = None
    try:
        conn = connect_db(db_path)
        ensure_schema(conn)
        ensure_fts(conn)
        result = _dispatch_command(conn, args)
        if result is not None:
            _print_output(args, result, args.command)
        return 0
    except ValueError as exc:
        error_response, exit_code = _build_cli_error(exc)
        _print_output(args, error_response, "error")
        return exit_code
    except KeyboardInterrupt:
        error_response, exit_code = _build_structured_error(SYS_INTERRUPTED)
        _print_output(args, error_response, "error")
        return exit_code
    except OSError as exc:
        error_response, exit_code = _build_structured_error(SYS_IO_ERROR, details=str(exc))
        _print_output(args, error_response, "error")
        return exit_code
    except Exception as exc:
        error_response, exit_code = _build_runtime_error(exc, db_path)
        _print_output(args, error_response, "error")
        return exit_code
    finally:
        if conn is not None:
            conn.close()


def main() -> int:
    """Main entry point."""
    args = parse_args()
    _ensure_human_flag(args)

    try:
        db_path = _apply_config_to_args(args)
    except ValueError as exc:
        error_response, exit_code = _build_cli_error(exc)
        _print_output(args, error_response, "error")
        return exit_code

    if args.command == "init":
        return _run_init_command(args, db_path)

    if _is_doctor_command(args):
        return _run_doctor_command(args, db_path)

    return _run_standard_command(args, db_path)


def _dispatch_memory_command(conn, args) -> dict | None:
    """Dispatch `memory` subcommands."""
    action = args.memory_action
    if action == "search":
        return _handle_memory_search(conn, args)
    if action == "list":
        return _handle_memory_list(conn, args)
    if action == "get":
        return _handle_memory_get(conn, args)
    if action == "timeline":
        return _handle_memory_timeline(conn, args)
    if action == "export":
        return _handle_memory_export(conn, args)
    if action == "clean":
        return _handle_memory_clean(conn, args)
    return None


def _dispatch_observation_command(conn, args) -> dict | None:
    """Dispatch `observation` subcommands."""
    action = args.obs_action
    if action == "add":
        return _handle_obs_add(conn, args)
    if action == "edit":
        return _handle_obs_edit(conn, args)
    if action == "delete":
        return _handle_obs_delete(conn, args)
    if action == "capture":
        return _handle_obs_capture(conn, args)
    if action == "feedback":
        return _handle_obs_feedback(conn, args)
    if action == "bulk":
        return _handle_obs_bulk(conn, args)
    if action == "link":
        return _handle_obs_link(conn, args)
    if action == "unlink":
        return _handle_obs_unlink(conn, args)
    if action == "related":
        return _handle_obs_related(conn, args)
    return None


def _dispatch_tool_command(conn, args) -> dict | None:
    """Dispatch `tool` subcommands."""
    action = args.tool_action
    if action == "log":
        return _handle_tool_log(conn, args)
    if action == "stats":
        return _handle_tool_stats(conn, args)
    if action == "suggest":
        return _handle_tool_suggest(conn, args)
    if action == "transition":
        return _handle_tool_transition(conn, args)
    return None


def _dispatch_admin_command(conn, args) -> dict | None:
    """Dispatch `admin` subcommands."""
    action = args.admin_action
    if action == "manage":
        return _handle_admin_manage(conn, args)
    if action == "share":
        return _handle_admin_share(conn, args)
    if action == "import":
        return _handle_admin_import(conn, args)
    if action == "extensions":
        return _handle_admin_extensions(conn, args)
    return None


def _dispatch_review_command(conn, args) -> dict | None:
    """Dispatch `review` subcommands."""
    action = args.review_action
    if action == "apply":
        return _handle_review_apply(conn, args)
    return None


def _dispatch_command(conn, args) -> dict | None:
    """Dispatch to appropriate handler based on command structure."""
    cmd = args.command

    # New nested command structure
    if cmd == "memory":
        return _dispatch_memory_command(conn, args)
    if cmd == "observation":
        return _dispatch_observation_command(conn, args)
    if cmd == "session":
        return _handle_session(conn, args)
    if cmd == "checkpoint":
        return _handle_checkpoint(conn, args)
    if cmd == "project":
        return _handle_project(conn, args)
    if cmd == "tool":
        return _dispatch_tool_command(conn, args)
    if cmd == "admin":
        return _dispatch_admin_command(conn, args)
    if cmd == "review":
        return _dispatch_review_command(conn, args)
    # Extension commands (incident, recovery, knowledge, attribution)
    # Dispatch via extension system for consistent handling
    if cmd in ("incident", "recovery", "knowledge", "attribution"):
        result = dispatch_extension_command(cmd, conn, args)
        if result is not None:
            return result
        raise ValueError(f"Extension command '{cmd}' failed to dispatch")

    # Approval command (deprecated, migrating to VPS Agent Web)
    if cmd == "approval":
        if not _approval_available:
            raise ValueError(
                "Approval command is deprecated and has been disabled. "
                "Please use VPS Agent Web for approval workflows."
            )
        warnings.warn(
            "Approval command is deprecated and will be removed. "
            "Migrate to VPS Agent Web's approval workflow.",
            DeprecationWarning,
            stacklevel=2,
        )
        _, handle_approval_command = _load_approval()
        return handle_approval_command(conn, args)

    # Backward compatibility: handle old flat commands
    return _handle_backward_compat(conn, args)


def _handle_backward_compat(conn, args) -> dict | None:
    """Handle old flat command names for backward compatibility."""
    cmd = args.command

    # These are the old command names that should still work
    legacy_map = {
        "add": lambda: _handle_obs_add(conn, args),
        "search": lambda: _handle_memory_search(conn, args),
        "timeline": lambda: _handle_memory_timeline(conn, args),
        "get": lambda: _handle_memory_get(conn, args),
        "edit": lambda: _handle_obs_edit(conn, args),
        "delete": lambda: _handle_obs_delete(conn, args),
        "list": lambda: _handle_memory_list(conn, args),
        "export": lambda: _handle_memory_export(conn, args),
        "clean": lambda: _handle_memory_clean(conn, args),
        "manage": lambda: _handle_admin_manage(conn, args),
        "capture": lambda: _handle_obs_capture(conn, args),
        "feedback": lambda: _handle_obs_feedback(conn, args),
        "review-feedback": lambda: _handle_review_apply_legacy(conn, args),
        "tool-log": lambda: _handle_tool_log_legacy(conn, args),
        "transition-log": lambda: _handle_tool_transition_legacy(conn, args),
        "tool-stats": lambda: _handle_tool_stats(conn, args),
        "tool-suggest": lambda: _handle_tool_suggest(conn, args),
        "link": lambda: _handle_obs_link_legacy(conn, args),
        "unlink": lambda: _handle_obs_unlink_legacy(conn, args),
        "related": lambda: _handle_obs_related(conn, args),
        "share": lambda: _handle_admin_share(conn, args),
        "import": lambda: _handle_admin_import_legacy(conn, args),
    }

    if cmd in legacy_map:
        import warnings
        new_cmd = _get_new_command_name(cmd)
        warnings.warn(
            f"Command '{cmd}' is deprecated. Use '{new_cmd}' instead.",
            DeprecationWarning,
            stacklevel=2
        )
        return legacy_map[cmd]()

    raise ValueError(f"Unknown command: {cmd}")


def _get_new_command_name(old_cmd: str) -> str:
    """Get the new command name for a deprecated command."""
    mapping = {
        "add": "observation add",
        "search": "memory search",
        "timeline": "memory timeline",
        "get": "memory get",
        "edit": "observation edit",
        "delete": "observation delete",
        "list": "memory list",
        "export": "memory export",
        "clean": "memory clean",
        "manage": "admin manage",
        "capture": "observation capture",
        "feedback": "observation feedback",
        "review-feedback": "review apply",
        "tool-log": "tool log",
        "transition-log": "tool transition",
        "tool-stats": "tool stats",
        "tool-suggest": "tool suggest",
        "link": "observation link",
        "unlink": "observation unlink",
        "related": "observation related",
        "share": "admin share",
        "import": "admin import",
    }
    return mapping.get(old_cmd, old_cmd)


def _build_structured_error(error_code, **kwargs) -> tuple[dict, int]:
    """Build a flat, backward-compatible error payload with standardized fields."""
    payload = format_error_response(error_code, **kwargs)
    result = {
        "ok": False,
        "error": payload["error"]["message"],
        "error_code": payload["error"]["code"],
        "suggestion": payload["error"]["suggestion"],
    }
    if "help_command" in payload["error"]:
        result["help_command"] = payload["error"]["help_command"]
    return result, get_exit_code_for_error(error_code)


def _match_cli_not_found_error(message: str) -> tuple[str, dict] | None:
    observation_match = re.match(r"Observation (\d+) not found$", message)
    if observation_match:
        return NF_OBSERVATION, {"id": observation_match.group(1)}

    session_match = re.match(r"Session (\d+) not found$", message)
    if session_match:
        return NF_SESSION, {"id": session_match.group(1)}

    if message == "No active session":
        return NF_ACTIVE_SESSION, {}

    checkpoint_match = re.match(r"Checkpoint (\d+) not found$", message)
    if checkpoint_match:
        return NF_CHECKPOINT, {"id": checkpoint_match.group(1)}

    project_match = re.match(r"Project '(.+)' not found$", message)
    if project_match:
        return NF_PROJECT, {"project": project_match.group(1)}

    command_match = re.match(r"Unknown command: (.+)$", message)
    if command_match:
        return NF_COMMAND, {"command": command_match.group(1)}

    return None


def _match_cli_validation_error(message: str) -> tuple[str, dict] | None:
    profile_match = re.match(r"Unknown profile '(.+)'\. Expected one of: .+$", message)
    if profile_match:
        return CFG_INVALID_PROFILE, {"profile": profile_match.group(1)}

    invalid_json_match = re.match(r"Invalid JSON for (.+?): (.+)$", message)
    if invalid_json_match:
        return VAL_INVALID_FORMAT, {
            "param": invalid_json_match.group(1),
            "value": invalid_json_match.group(2),
        }

    invalid_ids_match = re.match(r"Invalid IDs for (.+?): (.+)$", message)
    if invalid_ids_match:
        return VAL_INVALID_FORMAT, {
            "param": invalid_ids_match.group(1),
            "value": invalid_ids_match.group(2),
        }

    empty_ids_match = re.match(r"No IDs provided for (.+)$", message)
    if empty_ids_match:
        return VAL_EMPTY_VALUE, {"param": empty_ids_match.group(1)}

    if message == "No changes requested. Provide at least one editable field.":
        return VAL_EMPTY_VALUE, {"param": "editable fields"}

    if message == "Use either --before or --older-than-days, not both":
        return VAL_INVALID_FORMAT, {
            "param": "cleanup filters",
            "value": "--before and --older-than-days",
        }

    if message == "Refusing to clean without filters. Use --all to delete everything.":
        return VAL_EMPTY_VALUE, {"param": "cleanup filters"}

    return None


def _match_cli_review_error(message: str) -> tuple[str, dict] | None:
    if message == "review_feedback_file_must_contain_array_or_object":
        return VAL_INVALID_FORMAT, {
            "param": "--file",
            "value": "expected top-level JSON array or object",
        }

    if message == "review_feedback_items_must_be_array":
        return VAL_INVALID_FORMAT, {"param": "items", "value": "expected array"}

    return None


def _match_cli_bulk_error(message: str) -> tuple[str, dict] | None:
    bulk_input_errors = {
        "bulk_observation_input_must_contain_array_or_object": {
            "param": "--input",
            "value": "expected top-level JSON array or object",
        },
        "bulk_observation_items_must_be_array": {
            "param": "items",
            "value": "expected array",
        },
        "bulk_observation_item_must_be_object": {
            "param": "items[]",
            "value": "expected object",
        },
        "bulk_observation_title_required": {
            "param": "items[].title",
            "value": "required",
        },
        "bulk_observation_summary_required": {
            "param": "items[].summary",
            "value": "required",
        },
    }
    payload = bulk_input_errors.get(message)
    if payload is None:
        return None
    return VAL_INVALID_FORMAT, payload


def _build_cli_error(exc: ValueError) -> tuple[dict, int]:
    """Map common CLI ValueErrors to standardized error codes."""
    message = str(exc)
    for matcher in (
        _match_cli_not_found_error,
        _match_cli_validation_error,
        _match_cli_review_error,
        _match_cli_bulk_error,
    ):
        matched = matcher(message)
        if matched:
            error_code, kwargs = matched
            return _build_structured_error(error_code, **kwargs)

    return {"ok": False, "error": message}, 1


def _build_runtime_error(exc: Exception, db_path: str | None = None) -> tuple[dict, int]:
    """Map runtime exceptions to structured CLI/database errors."""
    if isinstance(exc, sqlite3.OperationalError):
        details = str(exc).lower()
        if "unable to open database file" in details:
            return _build_structured_error(DB_NOT_FOUND, path=db_path or "<unknown>")
        if "readonly" in details:
            return _build_structured_error(DB_READONLY, path=db_path or "<unknown>")
        if "locked" in details:
            return _build_structured_error(DB_LOCKED)
        if "malformed" in details or "not a database" in details:
            return _build_structured_error(DB_CORRUPTED)
        return _build_structured_error(SYS_SQLITE_ERROR, details=str(exc))

    if isinstance(exc, sqlite3.Error):
        return _build_structured_error(SYS_SQLITE_ERROR, details=str(exc))

    return _build_structured_error(SYS_PYTHON_ERROR, details=str(exc))


def _print_output(args, data: dict, command_type: str) -> None:
    """Print output in appropriate format."""
    # Determine output format
    fmt = getattr(args, "output_format", "json")
    human = args.human

    # Auto-enable human format for TTY if output is auto
    if fmt == "auto" and not human:
        import sys
        human = sys.stdout.isatty()

    # Use table formatters for human-readable output
    if human and "ok" in data and data["ok"]:
        from .output import (
            format_observations_table,
            format_search_results,
            format_sessions_table,
            format_stats_table,
            format_timeline_visual,
        )

        formatted = None
        if command_type in ("search", "memory search") and "results" in data:
            query = getattr(args, "query", "")
            formatted = format_search_results(data["results"], query)
        elif command_type in ("list", "memory list", "get", "memory get") and "results" in data:
            formatted = format_observations_table(data["results"], verbose=args.verbose)
        elif command_type in ("timeline", "memory timeline") and "results" in data:
            group_by = getattr(args, "group_by", None)
            formatted = format_timeline_visual(data["results"], group_by)
        elif command_type == "session" and "sessions" in data:
            formatted = format_sessions_table(data["sessions"])
        elif command_type in ("manage", "admin manage", "stats"):
            formatted = format_stats_table(data)

        if formatted:
            print(formatted)
            return

    # Default JSON output
    import json
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _handle_obs_add(conn, args):
    title = normalize_text(args.title)
    summary = normalize_text(args.summary)
    tags_list = normalize_tags_list(args.tags)
    raw = args.raw
    metadata = _load_observation_metadata(args.metadata)

    project = args.project
    if project == "general":
        active_project = get_active_project(args.profile)
        if active_project:
            project = active_project

    if args.llm_hook:
        from .utils import run_llm_hook
        hook_result = run_llm_hook({
            "title": title, "summary": summary, "raw": raw,
            "project": project, "kind": args.kind, "tags": tags_list,
        }, args.llm_hook)
        title = normalize_text(hook_result.get("title", title))
        summary = normalize_text(hook_result.get("summary", summary))
        if "tags" in hook_result:
            tags_list = normalize_tags_list(hook_result.get("tags"))

    if args.auto_tags and not tags_list:
        tags_list = auto_tags_from_text(title, summary, kind=args.kind)

    # Auto-importance
    if args.auto_importance:
        from .utils import calculate_importance
        metadata["importance"] = calculate_importance(args.kind, title, summary)

    # Dedup check
    if args.dedup_mode == "skip":
        from .utils import compute_content_hash
        content_hash = compute_content_hash(title, summary)
        metadata["contentHash"] = content_hash
        existing = conn.execute(
            "SELECT id FROM observations "
            "WHERE json_extract(metadata, '$.contentHash') = ?",
            (content_hash,),
        ).fetchone()
        if existing:
            return {
                "ok": True,
                "skipped": True,
                "existingId": existing[0],
                "title": title,
                "profile": args.profile,
            }

    active_session = get_bound_active_session(conn, args.profile, args.db)
    session_id = active_session["session_id"] if active_session else None

    obs_id = add_observation(
        conn, args.timestamp, project, args.kind, title, summary,
        tags_to_json(tags_list), tags_to_text(tags_list), raw, session_id,
        metadata=metadata_to_json(metadata),
    )
    result = {"ok": True, "id": obs_id, "metadata": metadata, "profile": args.profile}
    if session_id:
        result["session_id"] = session_id
    return result


def _handle_memory_search(conn, args):
    required_tags = normalize_tags_list(args.require_tags)
    metadata_filters = _load_metadata_filters(args.metadata_filter)
    results = run_search(
        conn,
        args.query,
        args.limit,
        offset=args.offset,
        mode=args.mode,
        quote=args.fts_quote,
        required_tags=required_tags,
        metadata_filters=metadata_filters,
    )
    return {"ok": True, "results": results}


def _handle_memory_timeline(conn, args):
    results = run_timeline(conn, args.start, args.end, args.around_id, args.window_minutes, args.limit, offset=args.offset)
    output = {"ok": True, "results": [asdict(r) for r in results]}
    if args.visual:
        visual = generate_visual_timeline(results, group_by=args.group_by)
        output["visual"] = visual
        print(visual, file=sys.stderr)
    return output


def _handle_memory_get(conn, args):
    ids = _parse_ids_argument(args.ids, "ids")
    results = run_get(conn, ids)
    return {"ok": True, "results": [asdict(r) for r in results]}


def _handle_obs_edit(conn, args):
    metadata = None
    if args.metadata is not None:
        metadata = metadata_to_json(_load_observation_metadata(args.metadata))
    result = run_edit(
        conn,
        args.id,
        args.project,
        args.kind,
        args.title,
        args.summary,
        args.tags,
        args.raw,
        args.timestamp,
        args.auto_tags,
        metadata=metadata,
    )
    result["db"] = args.db
    result["profile"] = args.profile
    return result


def _handle_obs_delete(conn, args):
    result = run_delete(conn, _parse_ids_argument(args.ids, "ids"), args.dry_run)
    result["db"] = args.db
    result["profile"] = args.profile
    return result


def _handle_memory_list(conn, args):
    required_tags = normalize_tags_list(args.require_tags)
    metadata_filters = _load_metadata_filters(args.metadata_filter)
    results = run_list(
        conn,
        args.limit,
        offset=args.offset,
        required_tags=required_tags,
        metadata_filters=metadata_filters,
    )
    return {"ok": True, "results": [asdict(r) for r in results]}


def _handle_memory_export(conn, args):
    import csv
    results = run_export(conn, args.limit, offset=args.offset)
    output = sys.stdout
    if args.output:
        output = open(args.output, "w", encoding="utf-8", newline="")
    try:
        if args.format == "json":
            json.dump([asdict(r) for r in results], output, indent=2)
            if output is sys.stdout:
                output.write("\n")
                return None
            else:
                return {"ok": True, "output": args.output, "count": len(results)}
        else:
            writer = csv.DictWriter(
                output,
                fieldnames=["id", "timestamp", "project", "kind", "title", "summary", "tags", "raw", "session_id", "metadata"],
            )
            writer.writeheader()
            for item in results:
                row = asdict(item)
                row["tags"] = tags_to_json(item.tags)
                row["metadata"] = metadata_to_json(item.metadata)
                writer.writerow(row)
            if output is sys.stdout:
                return None
            return {"ok": True, "output": args.output, "count": len(results)}
    finally:
        if output is not sys.stdout:
            output.close()
    return None


def _handle_memory_clean(conn, args):
    result = run_clean(conn, args.before, args.older_than_days, args.project, args.kind, args.tag, args.all, args.dry_run, args.vacuum)
    result["db"] = args.db
    result["profile"] = args.profile
    return result


def _handle_admin_manage(conn, args):
    result = run_manage(conn, args.action, args.limit)
    result["db"] = args.db
    result["profile"] = args.profile
    return result


def _handle_session(conn, args):
    if args.session_action == "start":
        session_id = start_session(conn, args.project, args.working_dir, args.agent_type, args.summary)
        set_active_session(args.profile, session_id, args.db)
        return {"ok": True, "action": "start", "session_id": session_id}
    elif args.session_action == "stop":
        active = get_bound_active_session(conn, args.profile, args.db)
        if not active:
            raise ValueError("No active session")
        summary = args.summary or generate_session_summary(conn, active["session_id"])
        end_session(conn, active["session_id"], summary)
        clear_active_session(args.profile)
        return {"ok": True, "action": "stop", "session_id": active["session_id"], "summary": summary}
    elif args.session_action == "list":
        sessions = list_sessions(conn, status=args.status, limit=args.limit)
        return {"ok": True, "action": "list", "sessions": [asdict(s) for s in sessions]}
    elif args.session_action == "show":
        session = get_session(conn, args.session_id)
        if not session:
            raise ValueError(f"Session {args.session_id} not found")
        result = {"ok": True, "action": "show", "session": asdict(session)}
        if args.observations:
            observations = get_session_observations(conn, args.session_id)
            result["observations"] = [asdict(o) for o in observations]
        return result
    elif args.session_action == "resume":
        if args.session_id:
            set_active_session(args.profile, args.session_id, args.db)
            return {"ok": True, "action": "resume", "session_id": args.session_id}
        else:
            active = get_bound_active_session(conn, args.profile, args.db)
            if not active:
                raise ValueError("No active session")
            return {"ok": True, "action": "resume", "session_id": active["session_id"]}


def _handle_project(conn, args):
    if args.project_action == "list":
        projects = list_projects(conn, args.limit)
        active = get_active_project(args.profile)
        return {"ok": True, "action": "list", "active_project": active, "projects": projects}
    elif args.project_action == "switch":
        set_active_project(args.profile, args.project_name)
        return {"ok": True, "action": "switch", "project": args.project_name}
    elif args.project_action == "active":
        if args.project_name:
            set_active_project(args.profile, args.project_name)
            return {"ok": True, "action": "active", "project": args.project_name}
        else:
            active = get_active_project(args.profile)
            return {"ok": True, "action": "active", "project": active}
    elif args.project_action == "stats":
        project_name = args.project_name or get_active_project(args.profile) or "general"
        stats = get_project_stats(conn, project_name)
        return {"ok": True, "action": "stats", **stats}
    elif args.project_action == "archive":
        new_name = f"archived/{args.project_name}"
        conn.execute("UPDATE observations SET project = ? WHERE project = ?", (new_name, args.project_name))
        conn.execute("UPDATE sessions SET project = ? WHERE project = ?", (new_name, args.project_name))
        conn.execute("UPDATE checkpoints SET project = ? WHERE project = ?", (new_name, args.project_name))
        conn.commit()
        return {"ok": True, "action": "archive", "old_name": args.project_name, "new_name": new_name}


def _handle_checkpoint(conn, args):
    if args.checkpoint_action == "create":
        active_session = get_bound_active_session(conn, args.profile, args.db)
        session_id = active_session["session_id"] if active_session else None
        project = get_active_project(args.profile) or "general"
        checkpoint_id = create_checkpoint(conn, args.name, args.description, args.tag, session_id, project)
        return {"ok": True, "action": "create", "checkpoint_id": checkpoint_id}
    elif args.checkpoint_action == "list":
        checkpoints = list_checkpoints(conn, limit=args.limit)
        return {"ok": True, "action": "list", "checkpoints": [asdict(c) for c in checkpoints]}
    elif args.checkpoint_action == "show":
        checkpoint = get_checkpoint(conn, args.checkpoint_id)
        if not checkpoint:
            raise ValueError(f"Checkpoint {args.checkpoint_id} not found")
        observations = get_checkpoint_observations(conn, args.checkpoint_id)
        return {"ok": True, "checkpoint": asdict(checkpoint), "observations": [asdict(o) for o in observations]}
    elif args.checkpoint_action == "resume":
        result = resume_from_checkpoint(conn, args.checkpoint_id, args.profile, args.db)
        return {"ok": True, "action": "resume", **result}


def _handle_admin_share(conn, args):
    result = run_share(conn, args.output, args.format, args.project, args.kind, args.tag, args.session, args.since, args.limit)
    result["db"] = args.db
    result["profile"] = args.profile
    return result


def _handle_admin_import(conn, args):
    result = run_import(conn, args.file, args.project, args.dry_run)
    result["db"] = args.db
    result["profile"] = args.profile
    return result


def _handle_admin_extensions(conn, args):
    """Handle admin extensions command."""
    action = args.action or "list"

    if action == "list":
        extensions = list_extensions()
        return {
            "ok": True,
            "action": "list",
            "extensions": extensions,
        }
    elif action == "status":
        disabled = get_disabled_extensions()
        return {
            "ok": True,
            "action": "status",
            "disabled_extensions": list(disabled) if disabled else [],
            "all_extensions": list_extensions(),
        }

    raise ValueError(f"Unknown command: admin extensions {action}")


def _handle_obs_capture(conn, args):
    full_text = " ".join(args.text)
    sentences = full_text.replace("! ", "!|").replace("? ", "?|").replace(". ", ".|").split("|")
    if len(sentences) > 1 and len(sentences[0]) < 100:
        title = sentences[0].strip()
        summary = " ".join(s.strip() for s in sentences[1:]).strip()
        if not summary:
            summary = title
    else:
        if len(full_text) <= 80:
            title = full_text
            summary = full_text
        else:
            break_point = full_text.rfind(" ", 0, 80)
            if break_point == -1:
                break_point = 80
            title = full_text[:break_point].strip()
            summary = full_text.strip()

    project = args.project or get_active_project(args.profile) or "general"
    tags_list = normalize_tags_list(args.tags)
    if args.auto_tags and not tags_list:
        tags_list = auto_tags_from_text(title, summary)

    active_session = get_bound_active_session(conn, args.profile, args.db)
    session_id = active_session["session_id"] if active_session else None

    obs_id = add_observation(conn, utc_now(), project, args.kind, title, summary,
                             tags_to_json(tags_list), tags_to_text(tags_list), full_text, session_id)
    result = {"ok": True, "id": obs_id, "title": title, "project": project}
    if session_id:
        result["session_id"] = session_id
    return result


def _handle_obs_feedback(conn, args):
    full_text = " ".join(args.text)
    metadata = _load_observation_metadata(args.metadata)

    if args.history:
        history = get_feedback_history(conn, args.observation_id)
        return {"ok": True, "observation_id": args.observation_id, "history": history}

    result = apply_feedback(
        conn,
        args.observation_id,
        full_text,
        auto_apply=not args.dry_run,
        metadata=metadata,
    )
    result["db"] = args.db
    result["profile"] = args.profile
    result["dry_run"] = args.dry_run
    return result


def _handle_review_apply(conn, args):
    try:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for {args.file}: {exc.msg}") from exc
    if isinstance(payload, dict):
        items = payload.get("items")
        if items is None:
            # Accept common review report shapes
            items = payload.get("findings", payload.get("reviews", []))
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("review_feedback_file_must_contain_array_or_object")
    if not isinstance(items, list):
        raise ValueError("review_feedback_items_must_be_array")

    result = apply_review_feedback(conn, items, auto_apply=not args.dry_run)
    result["db"] = args.db
    result["profile"] = args.profile
    return result


def _handle_tool_log(conn, args):
    tool_input = _load_json_argument("--input", args.input)
    tool_output = _load_json_argument("--output", args.output)
    project = args.project or get_active_project(args.profile) or "general"
    active_session = get_bound_active_session(conn, args.profile, args.db)
    session_id = active_session["session_id"] if active_session else None

    obs_id = log_tool_call(
        conn, args.tool, tool_input, tool_output,
        args.status, args.duration, project, session_id
    )
    return {"ok": True, "id": obs_id, "tool": args.tool, "status": args.status}


def _handle_tool_transition(conn, args):
    transition_input = _load_json_argument("--input", args.input)
    transition_output = _load_json_argument("--output", args.output)
    project = args.project or get_active_project(args.profile) or "general"
    active_session = get_bound_active_session(conn, args.profile, args.db)
    session_id = active_session["session_id"] if active_session else None

    obs_id = log_agent_transition(
        conn,
        phase=args.phase,
        action=args.action,
        transition_input=transition_input,
        transition_output=transition_output,
        status=args.status,
        reward=args.reward,
        project=project,
        session_id=session_id,
    )
    return {"ok": True, "id": obs_id, "phase": args.phase, "action": args.action, "status": args.status}


def _handle_tool_stats(conn, args):
    result = get_tool_stats(conn, args.project, args.limit)
    result["db"] = args.db
    result["profile"] = args.profile
    return result


def _handle_tool_suggest(conn, args):
    task = " ".join(args.task)
    result = suggest_tools_for_task(conn, task, args.limit)
    result["db"] = args.db
    result["profile"] = args.profile
    return result


def _handle_obs_link(conn, args):
    link_id = create_link(conn, args.from_id, args.to_id, args.type)
    return {
        "ok": True,
        "link_id": link_id,
        "from_id": args.from_id,
        "to_id": args.to_id,
        "type": args.type,
    }


def _handle_obs_unlink(conn, args):
    deleted = delete_link(conn, args.from_id, args.to_id, args.type)
    return {
        "ok": True,
        "deleted": deleted,
        "from_id": args.from_id,
        "to_id": args.to_id,
        "type": args.type,
    }


def _handle_obs_related(conn, args):
    if args.suggest:
        suggestions = find_similar_observations(conn, args.id, args.limit)
        return {
            "ok": True,
            "observation_id": args.id,
            "mode": "suggested",
            "suggestions": suggestions,
        }
    else:
        related = get_related_observations(conn, args.id, args.type, args.limit)
        return {
            "ok": True,
            "observation_id": args.id,
            "mode": "linked",
            "related": related,
        }


def _handle_obs_bulk(conn, args):
    payload = _load_bulk_observation_input(args.input)
    result = run_bulk_add(
        conn, payload,
        dry_run=args.dry_run,
        dedup_mode=getattr(args, "dedup_mode", "allow"),
        auto_importance=getattr(args, "auto_importance", False),
    )
    result["profile"] = args.profile
    result["db"] = args.db
    return result


# Legacy wrapper functions for backward compatibility
def _handle_review_apply_legacy(conn, args):
    """Legacy wrapper for review-feedback command."""
    return _handle_review_apply(conn, args)


def _handle_tool_log_legacy(conn, args):
    """Legacy wrapper for tool-log command."""
    return _handle_tool_log(conn, args)


def _handle_tool_transition_legacy(conn, args):
    """Legacy wrapper for transition-log command."""
    return _handle_tool_transition(conn, args)


def _handle_obs_link_legacy(conn, args):
    """Legacy wrapper for link command."""
    return _handle_obs_link(conn, args)


def _handle_obs_unlink_legacy(conn, args):
    """Legacy wrapper for unlink command."""
    return _handle_obs_unlink(conn, args)


def _handle_admin_import_legacy(conn, args):
    """Legacy wrapper for import command."""
    return _handle_admin_import(conn, args)


def _load_json_argument(arg_name: str, raw_value: str):
    """Parse a JSON CLI argument and raise a validation-friendly error."""
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for {arg_name}: {exc.msg}") from exc


def _load_observation_metadata(raw_value: str | None) -> dict:
    """Load structured metadata for observation commands."""
    if raw_value is None:
        return {}
    metadata = load_json_object_input(raw_value, "--metadata")
    normalized = {str(key): value for key, value in metadata.items()}
    if "profile" in normalized:
        raise ValueError("Invalid JSON for --metadata: profile must be provided via --profile")

    reserved_keys = set(OBSERVATION_METADATA_RESERVED_KEYS)
    ordered: dict[str, object] = {}
    for key in OBSERVATION_METADATA_RESERVED_KEYS:
        if key in normalized:
            ordered[key] = normalized[key]
    for key, value in normalized.items():
        if key not in reserved_keys:
            ordered[key] = value
    return ordered


def _load_metadata_filters(raw_value: str | None) -> dict[str, object]:
    if raw_value is None:
        return {}
    metadata = load_json_object_input(raw_value, "--metadata-filter")
    normalized = {str(key): value for key, value in metadata.items()}
    if "profile" in normalized:
        raise ValueError("Invalid JSON for --metadata-filter: profile must be provided via --profile")
    return normalized


def _load_bulk_observation_input(raw_value: str) -> list[dict[str, object]]:
    payload = load_json_input(raw_value, "--input")
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if "items" not in payload:
            raise ValueError("bulk_observation_items_must_be_array")
        items = payload["items"]
    else:
        raise ValueError("bulk_observation_input_must_contain_array_or_object")

    if not isinstance(items, list):
        raise ValueError("bulk_observation_items_must_be_array")
    return items


def _parse_ids_argument(raw_value: str, param_name: str):
    """Parse comma-separated IDs and raise validation-friendly errors."""
    try:
        ids = parse_ids(raw_value)
    except ValueError as exc:
        raise ValueError(f"Invalid IDs for {param_name}: {raw_value}") from exc
    if not ids:
        raise ValueError(f"No IDs provided for {param_name}")
    return ids


if __name__ == "__main__":
    sys.exit(main())
