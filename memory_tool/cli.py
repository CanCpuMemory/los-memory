#!/usr/bin/env python3
"""Command-line interface for the memory tool."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from typing import Optional

from .database import connect_db, ensure_fts, ensure_schema, init_db
from .models import Observation
from .operations import (
    add_observation,
    generate_visual_timeline,
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
    get_active_session,
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
from .share import run_import, run_share
from .utils import (
    DEFAULT_LLM_HOOK,
    DEFAULT_PROFILE,
    PROFILE_CHOICES,
    auto_tags_from_text,
    normalize_tags_list,
    normalize_text,
    parse_ids,
    resolve_db_path,
    tags_to_json,
    tags_to_text,
    utc_now,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Standalone memory tool")
    parser.add_argument(
        "--profile",
        choices=PROFILE_CHOICES,
        default=DEFAULT_PROFILE,
        help="Memory profile to select default DB path",
    )
    parser.add_argument("--db", default=None, help="Path to SQLite database (overrides --profile)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    init_parser = subparsers.add_parser("init", help="Initialize the database")

    # add
    add_parser = subparsers.add_parser("add", help="Add an observation")
    add_parser.add_argument("--timestamp", default=utc_now())
    add_parser.add_argument("--project", default="general")
    add_parser.add_argument("--kind", default="note")
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--summary", required=True)
    add_parser.add_argument("--tags", default="")
    add_parser.add_argument("--raw", default="")
    add_parser.add_argument("--auto-tags", action="store_true")
    add_parser.add_argument("--llm-hook", default=DEFAULT_LLM_HOOK)

    # search
    search_parser = subparsers.add_parser("search", help="Search observations")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--offset", type=int, default=0)
    search_parser.add_argument("--mode", choices=["auto", "fts", "like"], default="auto")
    search_parser.add_argument("--fts-quote", action="store_true")

    # timeline
    timeline_parser = subparsers.add_parser("timeline", help="Timeline query")
    timeline_parser.add_argument("--start")
    timeline_parser.add_argument("--end")
    timeline_parser.add_argument("--around-id", type=int)
    timeline_parser.add_argument("--window-minutes", type=int, default=120)
    timeline_parser.add_argument("--limit", type=int, default=20)
    timeline_parser.add_argument("--offset", type=int, default=0)
    timeline_parser.add_argument("--visual", "-v", action="store_true")
    timeline_parser.add_argument("--group-by", choices=["hour", "day", "session"], default=None)

    # get
    get_parser = subparsers.add_parser("get", help="Fetch observations by id")
    get_parser.add_argument("ids", help="Comma-separated observation ids")

    # edit
    edit_parser = subparsers.add_parser("edit", help="Edit an observation")
    edit_parser.add_argument("--id", type=int, required=True)
    edit_parser.add_argument("--timestamp", default=None)
    edit_parser.add_argument("--project", default=None)
    edit_parser.add_argument("--kind", default=None)
    edit_parser.add_argument("--title", default=None)
    edit_parser.add_argument("--summary", default=None)
    edit_parser.add_argument("--tags", default=None)
    edit_parser.add_argument("--raw", default=None)
    edit_parser.add_argument("--auto-tags", action="store_true")

    # delete
    delete_parser = subparsers.add_parser("delete", help="Delete observations")
    delete_parser.add_argument("ids")
    delete_parser.add_argument("--dry-run", action="store_true")

    # list
    list_parser = subparsers.add_parser("list", help="List observations")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--offset", type=int, default=0)

    # export
    export_parser = subparsers.add_parser("export", help="Export observations")
    export_parser.add_argument("--format", choices=["json", "csv"], default="json")
    export_parser.add_argument("--output", default=None)
    export_parser.add_argument("--limit", type=int, default=1000)
    export_parser.add_argument("--offset", type=int, default=0)

    # clean
    clean_parser = subparsers.add_parser("clean", help="Delete old observations")
    clean_parser.add_argument("--before")
    clean_parser.add_argument("--older-than-days", type=int)
    clean_parser.add_argument("--project")
    clean_parser.add_argument("--kind")
    clean_parser.add_argument("--tag")
    clean_parser.add_argument("--all", action="store_true")
    clean_parser.add_argument("--dry-run", action="store_true")
    clean_parser.add_argument("--vacuum", action="store_true")

    # manage
    manage_parser = subparsers.add_parser("manage", help="Manage database")
    manage_parser.add_argument("action", choices=["stats", "projects", "tags", "vacuum"])
    manage_parser.add_argument("--limit", type=int, default=20)

    # session
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
    session_list.add_argument("--status", choices=["active", "completed"])
    session_list.add_argument("--limit", type=int, default=20)

    session_show = session_subparsers.add_parser("show", help="Show session details")
    session_show.add_argument("session_id", type=int)
    session_show.add_argument("--observations", action="store_true")

    session_resume = session_subparsers.add_parser("resume", help="Resume a session")
    session_resume.add_argument("session_id", type=int, nargs="?")

    # project
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

    # checkpoint
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

    # share
    share_parser = subparsers.add_parser("share", help="Create shareable bundle")
    share_parser.add_argument("--output", "-o", required=True)
    share_parser.add_argument("--format", choices=["json", "markdown", "html"], default="json")
    share_parser.add_argument("--project")
    share_parser.add_argument("--kind")
    share_parser.add_argument("--tag")
    share_parser.add_argument("--session", type=int)
    share_parser.add_argument("--since")
    share_parser.add_argument("--limit", type=int, default=1000)

    # import
    import_parser = subparsers.add_parser("import", help="Import bundle")
    import_parser.add_argument("file")
    import_parser.add_argument("--project")
    import_parser.add_argument("--dry-run", action="store_true")

    # capture
    capture_parser = subparsers.add_parser("capture", help="Quick capture")
    capture_parser.add_argument("text", nargs="+")
    capture_parser.add_argument("--project")
    capture_parser.add_argument("--kind", default="note")
    capture_parser.add_argument("--tags", default="")
    capture_parser.add_argument("--auto-tags", action="store_true")

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()
    db_path = resolve_db_path(args.profile, args.db)

    if args.command == "init":
        init_db(db_path)
        print(json.dumps({"ok": True, "db": db_path, "profile": args.profile}, indent=2))
        return

    conn = connect_db(db_path)
    ensure_schema(conn)
    ensure_fts(conn)

    try:
        if args.command == "add":
            _handle_add(conn, args)
        elif args.command == "search":
            _handle_search(conn, args)
        elif args.command == "timeline":
            _handle_timeline(conn, args)
        elif args.command == "get":
            _handle_get(conn, args)
        elif args.command == "edit":
            _handle_edit(conn, args)
        elif args.command == "delete":
            _handle_delete(conn, args)
        elif args.command == "list":
            _handle_list(conn, args)
        elif args.command == "export":
            _handle_export(conn, args)
        elif args.command == "clean":
            _handle_clean(conn, args)
        elif args.command == "manage":
            _handle_manage(conn, args)
        elif args.command == "session":
            _handle_session(conn, args)
        elif args.command == "project":
            _handle_project(conn, args)
        elif args.command == "checkpoint":
            _handle_checkpoint(conn, args)
        elif args.command == "share":
            _handle_share(conn, args)
        elif args.command == "import":
            _handle_import(conn, args)
        elif args.command == "capture":
            _handle_capture(conn, args)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        sys.exit(1)
    finally:
        conn.close()


def _handle_add(conn, args):
    title = normalize_text(args.title)
    summary = normalize_text(args.summary)
    tags_list = normalize_tags_list(args.tags)
    raw = args.raw

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
        tags_list = auto_tags_from_text(title, summary)

    active_session = get_active_session(args.profile)
    session_id = active_session["session_id"] if active_session else None

    obs_id = add_observation(
        conn, args.timestamp, project, args.kind, title, summary,
        tags_to_json(tags_list), tags_to_text(tags_list), raw, session_id,
    )
    result = {"ok": True, "id": obs_id}
    if session_id:
        result["session_id"] = session_id
    print(json.dumps(result, indent=2))


def _handle_search(conn, args):
    results = run_search(conn, args.query, args.limit, offset=args.offset, mode=args.mode, quote=args.fts_quote)
    print(json.dumps({"ok": True, "results": results}, indent=2))


def _handle_timeline(conn, args):
    results = run_timeline(conn, args.start, args.end, args.around_id, args.window_minutes, args.limit, offset=args.offset)
    output = {"ok": True, "results": [asdict(r) for r in results]}
    if args.visual:
        visual = generate_visual_timeline(results, group_by=args.group_by)
        output["visual"] = visual
        print(visual, file=sys.stderr)
    print(json.dumps(output, indent=2))


def _handle_get(conn, args):
    ids = parse_ids(args.ids)
    results = run_get(conn, ids)
    print(json.dumps({"ok": True, "results": [asdict(r) for r in results]}, indent=2))


def _handle_edit(conn, args):
    result = run_edit(conn, args.id, args.project, args.kind, args.title, args.summary, args.tags, args.raw, args.timestamp, args.auto_tags)
    result["db"] = args.db
    result["profile"] = args.profile
    print(json.dumps(result, indent=2))


def _handle_delete(conn, args):
    result = run_delete(conn, parse_ids(args.ids), args.dry_run)
    result["db"] = args.db
    result["profile"] = args.profile
    print(json.dumps(result, indent=2))


def _handle_list(conn, args):
    results = run_list(conn, args.limit, offset=args.offset)
    print(json.dumps({"ok": True, "results": [asdict(r) for r in results]}, indent=2))


def _handle_export(conn, args):
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
        else:
            writer = csv.DictWriter(output, fieldnames=["id", "timestamp", "project", "kind", "title", "summary", "tags", "raw"])
            writer.writeheader()
            for item in results:
                row = asdict(item)
                row["tags"] = tags_to_json(item.tags)
                writer.writerow(row)
    finally:
        if output is not sys.stdout:
            output.close()


def _handle_clean(conn, args):
    result = run_clean(conn, args.before, args.older_than_days, args.project, args.kind, args.tag, args.all, args.dry_run, args.vacuum)
    result["db"] = args.db
    result["profile"] = args.profile
    print(json.dumps(result, indent=2))


def _handle_manage(conn, args):
    result = run_manage(conn, args.action, args.limit)
    result["db"] = args.db
    result["profile"] = args.profile
    print(json.dumps(result, indent=2))


def _handle_session(conn, args):
    if args.session_action == "start":
        session_id = start_session(conn, args.project, args.working_dir, args.agent_type, args.summary)
        set_active_session(args.profile, session_id, "")
        print(json.dumps({"ok": True, "action": "start", "session_id": session_id}, indent=2))
    elif args.session_action == "stop":
        active = get_active_session(args.profile)
        if not active:
            raise ValueError("No active session")
        summary = args.summary or generate_session_summary(conn, active["session_id"])
        end_session(conn, active["session_id"], summary)
        clear_active_session(args.profile)
        print(json.dumps({"ok": True, "action": "stop", "session_id": active["session_id"], "summary": summary}, indent=2))
    elif args.session_action == "list":
        sessions = list_sessions(conn, status=args.status, limit=args.limit)
        print(json.dumps({"ok": True, "action": "list", "sessions": [asdict(s) for s in sessions]}, indent=2))
    elif args.session_action == "show":
        session = get_session(conn, args.session_id)
        if not session:
            raise ValueError(f"Session {args.session_id} not found")
        result = {"ok": True, "action": "show", "session": asdict(session)}
        if args.observations:
            observations = get_session_observations(conn, args.session_id)
            result["observations"] = [asdict(o) for o in observations]
        print(json.dumps(result, indent=2))
    elif args.session_action == "resume":
        if args.session_id:
            set_active_session(args.profile, args.session_id, "")
            print(json.dumps({"ok": True, "action": "resume", "session_id": args.session_id}, indent=2))
        else:
            active = get_active_session(args.profile)
            if not active:
                raise ValueError("No active session")
            print(json.dumps({"ok": True, "action": "resume", "session_id": active["session_id"]}, indent=2))


def _handle_project(conn, args):
    if args.project_action == "list":
        projects = list_projects(conn, args.limit)
        active = get_active_project(args.profile)
        print(json.dumps({"ok": True, "action": "list", "active_project": active, "projects": projects}, indent=2))
    elif args.project_action == "switch":
        set_active_project(args.profile, args.project_name)
        print(json.dumps({"ok": True, "action": "switch", "project": args.project_name}, indent=2))
    elif args.project_action == "active":
        if args.project_name:
            set_active_project(args.profile, args.project_name)
            print(json.dumps({"ok": True, "action": "active", "project": args.project_name}, indent=2))
        else:
            active = get_active_project(args.profile)
            print(json.dumps({"ok": True, "action": "active", "project": active}, indent=2))
    elif args.project_action == "stats":
        project_name = args.project_name or get_active_project(args.profile) or "general"
        stats = get_project_stats(conn, project_name)
        print(json.dumps({"ok": True, "action": "stats", **stats}, indent=2))
    elif args.project_action == "archive":
        new_name = f"archived/{args.project_name}"
        conn.execute("UPDATE observations SET project = ? WHERE project = ?", (new_name, args.project_name))
        conn.execute("UPDATE sessions SET project = ? WHERE project = ?", (new_name, args.project_name))
        conn.commit()
        print(json.dumps({"ok": True, "action": "archive", "old_name": args.project_name, "new_name": new_name}, indent=2))


def _handle_checkpoint(conn, args):
    if args.checkpoint_action == "create":
        active_session = get_active_session(args.profile)
        session_id = active_session["session_id"] if active_session else None
        project = get_active_project(args.profile) or "general"
        checkpoint_id = create_checkpoint(conn, args.name, args.description, args.tag, session_id, project)
        print(json.dumps({"ok": True, "action": "create", "checkpoint_id": checkpoint_id}, indent=2))
    elif args.checkpoint_action == "list":
        checkpoints = list_checkpoints(conn, limit=args.limit)
        print(json.dumps({"ok": True, "action": "list", "checkpoints": [asdict(c) for c in checkpoints]}, indent=2))
    elif args.checkpoint_action == "show":
        checkpoint = get_checkpoint(conn, args.checkpoint_id)
        if not checkpoint:
            raise ValueError(f"Checkpoint {args.checkpoint_id} not found")
        observations = get_checkpoint_observations(conn, args.checkpoint_id)
        print(json.dumps({"ok": True, "checkpoint": asdict(checkpoint), "observations": [asdict(o) for o in observations]}, indent=2))
    elif args.checkpoint_action == "resume":
        result = resume_from_checkpoint(conn, args.checkpoint_id, args.profile)
        print(json.dumps({"ok": True, "action": "resume", **result}, indent=2))


def _handle_share(conn, args):
    result = run_share(conn, args.output, args.format, args.project, args.kind, args.tag, args.session, args.since, args.limit)
    result["db"] = args.db
    result["profile"] = args.profile
    print(json.dumps(result, indent=2))


def _handle_import(conn, args):
    result = run_import(conn, args.file, args.project, args.dry_run)
    result["db"] = args.db
    result["profile"] = args.profile
    print(json.dumps(result, indent=2))


def _handle_capture(conn, args):
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

    active_session = get_active_session(args.profile)
    session_id = active_session["session_id"] if active_session else None

    obs_id = add_observation(conn, utc_now(), project, args.kind, title, summary,
                             tags_to_json(tags_list), tags_to_text(tags_list), full_text, session_id)
    result = {"ok": True, "id": obs_id, "title": title, "project": project}
    if session_id:
        result["session_id"] = session_id
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
