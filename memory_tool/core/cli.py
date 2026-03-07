"""Core CLI commands for los-memory.

This module provides CLI commands for core memory capabilities only.
Extension commands are handled separately by the extension loading mechanism.
"""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from memory_tool import config
from memory_tool.config import get_database_path
from memory_tool.database import get_connection
from memory_tool.output import output_json, print_error, print_success
from memory_tool.projects import get_active_project, set_active_project

if TYPE_CHECKING:
    import sqlite3

# Import core operations
from . import (
    create_checkpoint,
    create_link,
    delete_link,
    end_session,
    get_checkpoint,
    get_checkpoint_observations,
    get_feedback_history,
    get_related_observations,
    get_session,
    get_session_observations,
    get_tool_stats,
    list_checkpoints,
    list_sessions,
    resume_from_checkpoint,
    run_add,
    run_delete,
    run_edit,
    run_get,
    run_list,
    run_search,
    start_session,
    suggest_tools_for_task,
    apply_feedback,
)


def add_memory_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add memory management subcommands."""
    memory_parser = subparsers.add_parser(
        "memory",
        help="Memory data access commands",
        description="Commands for managing memory observations",
    )
    memory_subparsers = memory_parser.add_subparsers(dest="memory_command")

    # memory add
    add_parser = memory_subparsers.add_parser("add", help="Add a new observation")
    add_parser.add_argument("--project", help="Project name")
    add_parser.add_argument("--kind", default="note", help="Observation kind")
    add_parser.add_argument("--title", required=True, help="Observation title")
    add_parser.add_argument("--summary", help="Observation summary")
    add_parser.add_argument("--tags", nargs="+", help="Tags")
    add_parser.add_argument("--raw", help="Raw data (JSON)")
    add_parser.add_argument("--session", type=int, help="Session ID")
    add_parser.add_argument("--no-auto-tags", action="store_true", help="Disable auto-tagging")

    # memory get
    get_parser = memory_subparsers.add_parser("get", help="Get observations by ID")
    get_parser.add_argument("ids", nargs="+", type=int, help="Observation IDs")

    # memory list
    list_parser = memory_subparsers.add_parser("list", help="List observations")
    list_parser.add_argument("--project", help="Filter by project")
    list_parser.add_argument("--kind", help="Filter by kind")
    list_parser.add_argument("--tag", help="Filter by tag")
    list_parser.add_argument("--session", type=int, help="Filter by session ID")
    list_parser.add_argument("--limit", type=int, default=20, help="Limit results")
    list_parser.add_argument("--offset", type=int, default=0, help="Offset results")

    # memory search
    search_parser = memory_subparsers.add_parser("search", help="Search observations")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--project", help="Filter by project")
    search_parser.add_argument("--kind", help="Filter by kind")
    search_parser.add_argument("--limit", type=int, default=20, help="Limit results")

    # memory delete
    delete_parser = memory_subparsers.add_parser("delete", help="Delete observations")
    delete_parser.add_argument("ids", nargs="+", type=int, help="Observation IDs to delete")
    delete_parser.add_argument("--execute", action="store_true", help="Actually delete (default is dry-run)")

    # memory edit
    edit_parser = memory_subparsers.add_parser("edit", help="Edit an observation")
    edit_parser.add_argument("id", type=int, help="Observation ID")
    edit_parser.add_argument("--title", help="New title")
    edit_parser.add_argument("--summary", help="New summary")
    edit_parser.add_argument("--project", help="New project")
    edit_parser.add_argument("--kind", help="New kind")
    edit_parser.add_argument("--tags", nargs="+", help="New tags")
    edit_parser.add_argument("--raw", help="New raw data")

    # memory link (core linking capability)
    link_parser = memory_subparsers.add_parser("link", help="Link observations")
    link_subparsers = link_parser.add_subparsers(dest="link_command")

    link_add = link_subparsers.add_parser("add", help="Create a link")
    link_add.add_argument("from_id", type=int, help="Source observation ID")
    link_add.add_argument("to_id", type=int, help="Target observation ID")
    link_add.add_argument("--type", default="related", choices=["related", "child", "parent", "refines"],
                         help="Link type")

    link_delete = link_subparsers.add_parser("delete", help="Delete a link")
    link_delete.add_argument("from_id", type=int, help="Source observation ID")
    link_delete.add_argument("to_id", type=int, help="Target observation ID")
    link_delete.add_argument("--type", help="Specific link type to delete")

    link_related = link_subparsers.add_parser("related", help="Get related observations")
    link_related.add_argument("id", type=int, help="Observation ID")
    link_related.add_argument("--type", choices=["related", "child", "parent", "refines"],
                              help="Filter by link type")
    link_related.add_argument("--limit", type=int, default=20, help="Limit results")


def add_observation_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add observation subcommands (shortcut for memory add)."""
    obs_parser = subparsers.add_parser(
        "observation",
        aliases=["obs"],
        help="Observation management commands",
        description="Shortcut commands for observation management",
    )
    obs_subparsers = obs_parser.add_subparsers(dest="obs_command")

    # obs add
    add_parser = obs_subparsers.add_parser("add", help="Add observation")
    add_parser.add_argument("--project", help="Project name")
    add_parser.add_argument("--kind", default="note", help="Observation kind")
    add_parser.add_argument("--title", required=True, help="Observation title")
    add_parser.add_argument("--summary", help="Observation summary")
    add_parser.add_argument("--tags", nargs="+", help="Tags")
    add_parser.add_argument("--raw", help="Raw data (JSON)")
    add_parser.add_argument("--session", type=int, help="Session ID")

    # obs list
    list_parser = obs_subparsers.add_parser("list", help="List observations")
    list_parser.add_argument("--project", help="Filter by project")
    list_parser.add_argument("--kind", help="Filter by kind")
    list_parser.add_argument("--limit", type=int, default=20, help="Limit results")


def add_session_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add session management subcommands."""
    session_parser = subparsers.add_parser(
        "session",
        help="Session management commands",
        description="Commands for managing work sessions",
    )
    session_subparsers = session_parser.add_subparsers(dest="session_command")

    # session start
    start_parser = session_subparsers.add_parser("start", help="Start a new session")
    start_parser.add_argument("--description", default="", help="Session description")

    # session end
    session_subparsers.add_parser("end", help="End current session")

    # session list
    list_parser = session_subparsers.add_parser("list", help="List sessions")
    list_parser.add_argument("--limit", type=int, default=20, help="Limit results")

    # session get
    get_parser = session_subparsers.add_parser("get", help="Get session details")
    get_parser.add_argument("id", type=int, help="Session ID")

    # session observations
    obs_parser = session_subparsers.add_parser("observations", help="Get session observations")
    obs_parser.add_argument("id", type=int, help="Session ID")
    obs_parser.add_argument("--limit", type=int, default=100, help="Limit results")


def add_checkpoint_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add checkpoint management subcommands."""
    checkpoint_parser = subparsers.add_parser(
        "checkpoint",
        help="Checkpoint management commands",
        description="Commands for managing work checkpoints",
    )
    checkpoint_subparsers = checkpoint_parser.add_subparsers(dest="checkpoint_command")

    # checkpoint create
    create_parser = checkpoint_subparsers.add_parser("create", help="Create a checkpoint")
    create_parser.add_argument("--name", required=True, help="Checkpoint name")
    create_parser.add_argument("--description", default="", help="Checkpoint description")
    create_parser.add_argument("--tag", default="", help="Checkpoint tag")
    create_parser.add_argument("--session", type=int, help="Session ID to checkpoint")

    # checkpoint list
    list_parser = checkpoint_subparsers.add_parser("list", help="List checkpoints")
    list_parser.add_argument("--tag", help="Filter by tag")
    list_parser.add_argument("--limit", type=int, default=20, help="Limit results")

    # checkpoint get
    get_parser = checkpoint_subparsers.add_parser("get", help="Get checkpoint details")
    get_parser.add_argument("id", type=int, help="Checkpoint ID")

    # checkpoint observations
    obs_parser = checkpoint_subparsers.add_parser("observations", help="Get checkpoint observations")
    obs_parser.add_argument("id", type=int, help="Checkpoint ID")
    obs_parser.add_argument("--limit", type=int, default=100, help="Limit results")

    # checkpoint resume
    resume_parser = checkpoint_subparsers.add_parser("resume", help="Resume from checkpoint")
    resume_parser.add_argument("id", type=int, help="Checkpoint ID")


def add_project_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add project management subcommands."""
    project_parser = subparsers.add_parser(
        "project",
        help="Project management commands",
        description="Commands for managing projects",
    )
    project_subparsers = project_parser.add_subparsers(dest="project_command")

    # project set
    set_parser = project_subparsers.add_parser("set", help="Set active project")
    set_parser.add_argument("name", help="Project name")

    # project get
    project_subparsers.add_parser("get", help="Get active project")


def add_tool_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add tool analytics subcommands."""
    tool_parser = subparsers.add_parser(
        "tool",
        help="Tool analytics commands",
        description="Commands for tool usage analytics",
    )
    tool_subparsers = tool_parser.add_subparsers(dest="tool_command")

    # tool stats
    stats_parser = tool_subparsers.add_parser("stats", help="Get tool usage statistics")
    stats_parser.add_argument("--project", help="Filter by project")
    stats_parser.add_argument("--limit", type=int, default=20, help="Limit results")

    # tool suggest
    suggest_parser = tool_subparsers.add_parser("suggest", help="Suggest tools for task")
    suggest_parser.add_argument("task", help="Task description")
    suggest_parser.add_argument("--limit", type=int, default=5, help="Limit suggestions")


def add_review_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add feedback/review subcommands."""
    review_parser = subparsers.add_parser(
        "review",
        help="Feedback and review commands",
        description="Commands for providing feedback on observations",
    )
    review_subparsers = review_parser.add_subparsers(dest="review_command")

    # review feedback
    feedback_parser = review_subparsers.add_parser("feedback", help="Provide feedback")
    feedback_parser.add_argument("observation_id", type=int, help="Observation ID")
    feedback_parser.add_argument("--text", required=True, help="Feedback text")
    feedback_parser.add_argument("--no-apply", action="store_true", help="Don't auto-apply changes")

    # review history
    history_parser = review_subparsers.add_parser("history", help="Get feedback history")
    history_parser.add_argument("observation_id", type=int, help="Observation ID")


def register_core_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register all core CLI commands.

    This function registers only the core commands. Extension commands
    are registered separately by the extension loading mechanism.
    """
    add_memory_subparser(subparsers)
    add_observation_subparser(subparsers)
    add_session_subparser(subparsers)
    add_checkpoint_subparser(subparsers)
    add_project_subparser(subparsers)
    add_tool_subparser(subparsers)
    add_review_subparser(subparsers)


def handle_memory_command(args: argparse.Namespace, conn: "sqlite3.Connection") -> int:
    """Handle memory subcommands."""
    profile = config.get_current_profile()
    project = get_active_project(profile) or "default"

    if args.memory_command == "add":
        result = run_add(
            conn,
            project=project,
            kind=args.kind,
            title=args.title,
            summary=args.summary or "",
            tags=args.tags,
            raw=args.raw,
            session_id=args.session,
            auto_tags=not args.no_auto_tags,
        )
        output_json(result)
        return 0 if result.get("ok") else 1

    elif args.memory_command == "get":
        results = run_get(conn, args.ids)
        output_json({"ok": True, "count": len(results), "observations": results})
        return 0

    elif args.memory_command == "list":
        results = run_list(
            conn,
            project=args.project or project,
            kind=args.kind,
            tag=args.tag,
            session_id=args.session,
            limit=args.limit,
            offset=args.offset,
        )
        output_json({"ok": True, "count": len(results), "observations": results})
        return 0

    elif args.memory_command == "search":
        results = run_search(
            conn,
            query=args.query,
            project=args.project or project,
            kind=args.kind,
            limit=args.limit,
        )
        output_json({"ok": True, "count": len(results), "observations": results})
        return 0

    elif args.memory_command == "delete":
        result = run_delete(conn, args.ids, dry_run=not args.execute)
        output_json(result)
        return 0 if result.get("ok") else 1

    elif args.memory_command == "edit":
        result = run_edit(
            conn,
            args.id,
            project=args.project,
            kind=args.kind,
            title=args.title,
            summary=args.summary,
            tags=args.tags,
            raw=args.raw,
        )
        output_json(result)
        return 0 if result.get("ok") else 1

    elif args.memory_command == "link":
        if args.link_command == "add":
            try:
                link_id = create_link(conn, args.from_id, args.to_id, args.type)
                print_success(f"Created link {link_id}")
                return 0
            except ValueError as e:
                print_error(str(e))
                return 1

        elif args.link_command == "delete":
            deleted = delete_link(conn, args.from_id, args.to_id, args.type)
            if deleted:
                print_success("Link deleted")
                return 0
            else:
                print_error("Link not found")
                return 1

        elif args.link_command == "related":
            results = get_related_observations(
                conn,
                args.id,
                link_type=args.type,
                limit=args.limit,
            )
            output_json({"ok": True, "count": len(results), "related": results})
            return 0

    return 1


def handle_observation_command(args: argparse.Namespace, conn: "sqlite3.Connection") -> int:
    """Handle observation subcommands."""
    profile = config.get_current_profile()
    project = get_active_project(profile) or "default"

    if args.obs_command == "add":
        result = run_add(
            conn,
            project=args.project or project,
            kind=args.kind,
            title=args.title,
            summary=args.summary or "",
            tags=args.tags,
            raw=args.raw,
            session_id=args.session,
        )
        output_json(result)
        return 0 if result.get("ok") else 1

    elif args.obs_command == "list":
        results = run_list(
            conn,
            project=args.project or project,
            kind=args.kind,
            limit=args.limit,
        )
        output_json({"ok": True, "count": len(results), "observations": results})
        return 0

    return 1


def handle_session_command(args: argparse.Namespace, conn: "sqlite3.Connection") -> int:
    """Handle session subcommands."""
    profile = config.get_current_profile()

    if args.session_command == "start":
        result = start_session(conn, profile, args.description)
        output_json(result)
        return 0 if result.get("ok") else 1

    elif args.session_command == "end":
        result = end_session(conn, profile)
        output_json(result)
        return 0 if result.get("ok") else 1

    elif args.session_command == "list":
        results = list_sessions(conn, profile, args.limit)
        output_json({"ok": True, "count": len(results), "sessions": results})
        return 0

    elif args.session_command == "get":
        session = get_session(conn, args.id)
        if session:
            output_json({"ok": True, "session": session})
            return 0
        else:
            print_error(f"Session {args.id} not found")
            return 1

    elif args.session_command == "observations":
        observations = get_session_observations(conn, args.id, args.limit)
        output_json({"ok": True, "count": len(observations), "observations": observations})
        return 0

    return 1


def handle_checkpoint_command(args: argparse.Namespace, conn: "sqlite3.Connection") -> int:
    """Handle checkpoint subcommands."""
    profile = config.get_current_profile()

    if args.checkpoint_command == "create":
        checkpoint_id = create_checkpoint(
            conn,
            name=args.name,
            description=args.description,
            tag=args.tag,
            session_id=args.session,
            project=get_active_project(profile) or "default",
        )
        print_success(f"Created checkpoint {checkpoint_id}")
        return 0

    elif args.checkpoint_command == "list":
        results = list_checkpoints(conn, args.limit, args.tag)
        output_json({"ok": True, "count": len(results), "checkpoints": results})
        return 0

    elif args.checkpoint_command == "get":
        checkpoint = get_checkpoint(conn, args.id)
        if checkpoint:
            output_json({"ok": True, "checkpoint": checkpoint})
            return 0
        else:
            print_error(f"Checkpoint {args.id} not found")
            return 1

    elif args.checkpoint_command == "observations":
        observations = get_checkpoint_observations(conn, args.id, args.limit)
        output_json({"ok": True, "count": len(observations), "observations": observations})
        return 0

    elif args.checkpoint_command == "resume":
        try:
            result = resume_from_checkpoint(conn, args.id, profile)
            output_json({"ok": True, "result": result})
            return 0
        except ValueError as e:
            print_error(str(e))
            return 1

    return 1


def handle_project_command(args: argparse.Namespace, conn: "sqlite3.Connection") -> int:
    """Handle project subcommands."""
    profile = config.get_current_profile()

    if args.project_command == "set":
        set_active_project(profile, args.name)
        print_success(f"Set active project to '{args.name}'")
        return 0

    elif args.project_command == "get":
        project = get_active_project(profile)
        output_json({"ok": True, "project": project or "default"})
        return 0

    return 1


def handle_tool_command(args: argparse.Namespace, conn: "sqlite3.Connection") -> int:
    """Handle tool subcommands."""
    profile = config.get_current_profile()

    if args.tool_command == "stats":
        result = get_tool_stats(conn, args.project, args.limit)
        output_json(result)
        return 0 if result.get("ok") else 1

    elif args.tool_command == "suggest":
        result = suggest_tools_for_task(conn, args.task, args.limit)
        output_json(result)
        return 0 if result.get("ok") else 1

    return 1


def handle_review_command(args: argparse.Namespace, conn: "sqlite3.Connection") -> int:
    """Handle review subcommands."""
    if args.review_command == "feedback":
        try:
            result = apply_feedback(
                conn,
                args.observation_id,
                args.text,
                auto_apply=not args.no_apply,
            )
            output_json(result)
            return 0 if result.get("ok") else 1
        except ValueError as e:
            print_error(str(e))
            return 1

    elif args.review_command == "history":
        history = get_feedback_history(conn, args.observation_id)
        output_json({"ok": True, "count": len(history), "history": history})
        return 0

    return 1


def handle_core_command(args: argparse.Namespace) -> int:
    """Handle core CLI commands.

    This is the main entry point for core command handling.
    It dispatches to the appropriate handler based on the command type.
    """
    # Commands that don't need a database connection
    if hasattr(args, "command") and args.command == "project":
        if args.project_command == "get":
            profile = config.get_current_profile()
            project = get_active_project(profile)
            output_json({"ok": True, "project": project or "default"})
            return 0

    # Commands that need a database connection
    db_path = get_database_path()
    conn = get_connection(db_path)

    try:
        if hasattr(args, "command"):
            if args.command == "memory":
                return handle_memory_command(args, conn)
            elif args.command == "observation":
                return handle_observation_command(args, conn)
            elif args.command == "session":
                return handle_session_command(args, conn)
            elif args.command == "checkpoint":
                return handle_checkpoint_command(args, conn)
            elif args.command == "project":
                return handle_project_command(args, conn)
            elif args.command == "tool":
                return handle_tool_command(args, conn)
            elif args.command == "review":
                return handle_review_command(args, conn)

        return 1
    finally:
        conn.close()
