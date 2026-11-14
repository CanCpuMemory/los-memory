"""``los-memory mem ...`` — the writable ledger CLI.

Registered on the existing CLI as its own command group, and dispatched *before*
the profile/config path so a ledger command never opens, creates or touches a
``codex`` / ``claude`` / ``shared`` profile database.

Exit codes are the repository's three-state convention for instruments (the same
one ``scripts/hygiene_*.py`` uses), and they are deliberately not the legacy
``0``–``5`` error-code enum that profile commands emit:

===========  ==================================================================
``0``        judged: the operation ran, or the request was well formed and empty
``1``        a real negative: unknown id, or an operation the target's state forbids
``2``        cannot judge: missing/uninitialised ledger, unreadable input, bad request
===========  ==================================================================

Every failure path returns ``2`` at worst — never ``0`` — because "the tool broke"
must not read as "nothing to do" (the repo's ``verify-gate`` lesson, applied here).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import schema
from .import_evolve import import_evolve
from .registry import DEFAULT_REGISTRY, load_project_slugs, try_load_project_slugs
from .schema import STATES, TRACKS, LedgerRefusedError, LedgerUnavailableError
from .snapshot import DEFAULT_MAX_CHARS, DEFAULT_MAX_ENTRIES, SnapshotOptions, build_snapshot
from .store import Ledger

__all__ = ["EXIT_OK", "EXIT_REFUSED", "EXIT_UNJUDGEABLE", "register", "run"]

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_UNJUDGEABLE = 2

DEFAULT_CORPUS = "~/.dsh/memories"


def register(subparsers: Any) -> None:
    """Register the ``mem`` command group and its subcommands."""
    parser = subparsers.add_parser(
        "mem",
        help="Writable memory ledger (service.sqlite3): add/list/expand/search/import",
    )
    actions = parser.add_subparsers(dest="mem_action", required=True)
    _register_actions(actions)


def _register_actions(actions: Any) -> None:
    """Register every ``mem`` subcommand on a subparser collection."""
    add = actions.add_parser("add", help="Add one memory entry")
    add.add_argument("--track", required=True, choices=list(TRACKS))
    add.add_argument("--project", help="Project slug (required for key/project tracks)")
    add.add_argument("--branch", help="Branch scope (key track only)")
    add.add_argument("--slot", help="Slot slug; a new active slot deactivates the old one")
    add.add_argument("--day", help="Day for the daily track (YYYY-MM-DD, default today)")
    add.add_argument("--claim-status", help="Optional claim status for the revision")
    add.add_argument(
        "--actor",
        default="cli",
        help="Who asserts this (owner / agent:main / agent:subagent / job:x / import). "
             "Drives conflict adjudication; see memory_tool/ledger/admission.py",
    )
    add.add_argument(
        "--asserted-at",
        help="Event time of the assertion (ISO or YYYY-MM-DD). Defaults to write time",
    )
    add.add_argument(
        "--on-conflict",
        default="auto",
        choices=["auto", "escalate", "reject", "append"],
        help="auto=judge mechanically (default); escalate=always ask; "
             "reject=refuse any slot collision; append=bypass (historical imports)",
    )
    _add_content_arguments(add)
    _add_common_arguments(add)

    replace = actions.add_parser("replace", help="Append a revision to an active unit")
    replace.add_argument("--id", required=True, dest="unit_id")
    replace.add_argument("--claim-status")
    _add_content_arguments(replace)
    _add_common_arguments(replace)

    supersede = actions.add_parser(
        "supersede", help="Write a replacement and deprecate the old unit"
    )
    supersede.add_argument("--id", required=True, dest="unit_id")
    supersede.add_argument("--slot", help="Slot for the new unit (defaults to the old unit's slot)")
    supersede.add_argument("--claim-status")
    _add_content_arguments(supersede)
    _add_common_arguments(supersede)

    archive = actions.add_parser(
        "archive", help="Archive a unit (hidden from recall, readable by id)"
    )
    archive.add_argument("--id", required=True, dest="unit_id")
    archive.add_argument("--reason")
    _add_common_arguments(archive)

    listing = actions.add_parser("list", help="List units with their current revision")
    listing.add_argument("--track", choices=list(TRACKS))
    listing.add_argument("--project")
    listing.add_argument("--branch")
    listing.add_argument("--day")
    listing.add_argument("--state", default="active", choices=[*STATES, "all"])
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)
    _add_common_arguments(listing)

    expand = actions.add_parser("expand", help="Read one unit with its full history")
    expand.add_argument("--id", required=True, dest="unit_id")
    _add_common_arguments(expand)

    search = actions.add_parser("search", help="Search unit text (trigram + CJK bigrams)")
    search.add_argument("query")
    search.add_argument("--track", choices=list(TRACKS))
    search.add_argument("--project")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--include-archived", action="store_true")
    search.add_argument("--rebuild-index", action="store_true")
    _add_common_arguments(search)

    importer = actions.add_parser(
        "import-evolve", help="Import the dsh-memory-evolve markdown corpus (idempotent)"
    )
    importer.add_argument("--root", default=DEFAULT_CORPUS)
    importer.add_argument(
        "--only", help="Comma-separated track filter (memory,user,key,project,daily)"
    )
    importer.add_argument("--project-map", help="JSON file mapping evolve directory hash to a slug")
    importer.add_argument(
        "--project-map-from-registry",
        nargs="?",
        const=DEFAULT_REGISTRY,
        default=argparse.SUPPRESS,
        help=(
            "Map evolve directory hashes to project keys by hashing the workspace "
            f"registry's project roots (default {DEFAULT_REGISTRY}); an explicit path "
            "that is missing or unrecognised is exit 2"
        ),
    )
    importer.add_argument("--samples", type=int, default=3)
    _add_common_arguments(importer)

    init = actions.add_parser("init", help="Create/verify the ledger database")
    _add_common_arguments(init)

    snapshot = actions.add_parser(
        "snapshot", help="One-shot render payload for the injection surface (P2)"
    )
    snapshot.add_argument("--cwd", default=os.getcwd())
    snapshot.add_argument("--branch", help="Branch scope (default: git HEAD of --cwd)")
    snapshot.add_argument("--project", help="Project directory hash (default: sha1(cwd)[:12])")
    snapshot.add_argument("--date", help="Daily date to count (default: today)")
    snapshot.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    snapshot.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    snapshot.add_argument("--full-threshold-entries", type=int, default=200)
    snapshot.add_argument("--full-threshold-chars", type=int, default=60000)
    snapshot.add_argument("--summary-chars", type=int, default=160)
    snapshot.add_argument(
        "--project-map-from-registry",
        nargs="?",
        const=DEFAULT_REGISTRY,
        default=argparse.SUPPRESS,
        help="Registry used for project_slug (best effort: unavailability is reported, not fatal)",
    )
    snapshot.add_argument("--json", action="store_true", help="Emit JSON (the default)")
    _add_common_arguments(snapshot)

    status = actions.add_parser("status", help="Ledger counts and index state")
    status.add_argument(
        "--project-map-from-registry",
        nargs="?",
        const=DEFAULT_REGISTRY,
        default=argparse.SUPPRESS,
        help="Registry used for the project_slug_assigned/unassigned counters",
    )
    _add_common_arguments(status)

    conflicts = actions.add_parser(
        "conflicts", help="List escalated conflicts awaiting adjudication"
    )
    _add_common_arguments(conflicts)

    resolve = actions.add_parser(
        "resolve", help="Adjudicate one escalated conflict (replace / drop / keep_both)"
    )
    resolve.add_argument("conflict_id", help="conflict-<id> as listed by `mem conflicts`")
    resolve.add_argument("--action", required=True, choices=["replace", "drop", "keep_both"])
    resolve.add_argument("--note", help="Why (recorded on the conflict_resolved event)")
    resolve.add_argument("--actor", default="owner", help="Who adjudicated (default owner)")
    _add_common_arguments(resolve)


def _add_content_arguments(parser: argparse.ArgumentParser) -> None:
    """Add mutually exclusive content sources to a write subcommand."""
    parser.add_argument("--content-file", help="Read content from a file ('-' for stdin)")
    parser.add_argument("--content", help="Inline content")


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Add flags shared by every ``mem`` subcommand."""
    parser.add_argument(
        "--db", default=argparse.SUPPRESS, help="Ledger path (overrides MEMORY_SERVICE_DB)"
    )
    parser.add_argument("--format", dest="mem_format", default="json", choices=["json", "text"])
    parser.add_argument("--dry-run", action="store_true", help="Compute the result, write nothing")


def run(args: argparse.Namespace) -> int:
    """Dispatch one ``mem`` subcommand and return its process exit code."""
    action = getattr(args, "mem_action", None)
    try:
        if action == "add":
            return _cmd_add(args)
        if action == "replace":
            return _cmd_replace(args)
        if action == "supersede":
            return _cmd_supersede(args)
        if action == "archive":
            return _cmd_archive(args)
        if action == "list":
            return _cmd_list(args)
        if action == "expand":
            return _cmd_expand(args)
        if action == "search":
            return _cmd_search(args)
        if action == "import-evolve":
            return _cmd_import(args)
        if action == "init":
            return _cmd_init(args)
        if action == "status":
            return _cmd_status(args)
        if action == "snapshot":
            return _cmd_snapshot(args)
        if action == "conflicts":
            return _cmd_conflicts(args)
        if action == "resolve":
            return _cmd_resolve(args)
        raise ValueError("unknown_mem_action")
    except LedgerRefusedError as exc:
        return _fail(args, exc, EXIT_REFUSED)
    except LedgerUnavailableError as exc:
        return _fail(args, exc, EXIT_UNJUDGEABLE)
    except ValueError as exc:
        return _fail(args, exc, EXIT_UNJUDGEABLE)
    except (sqlite3.Error, OSError) as exc:
        return _fail(args, exc, EXIT_UNJUDGEABLE)
    except Exception as exc:  # noqa: BLE001 - fail closed, never report success on a crash
        return _fail(args, exc, EXIT_UNJUDGEABLE, code="unexpected_error")


# --------------------------------------------------------------------- handlers


def _cmd_add(args: argparse.Namespace) -> int:
    content = _read_content(args)
    dry_run = bool(getattr(args, "dry_run", False))
    with _session(args, write=True, dry_run=dry_run) as (ledger, context):
        result = ledger.add(
            args.track,
            content,
            project=args.project,
            branch=args.branch,
            slot=args.slot,
            day=args.day,
            claim_status=args.claim_status,
            actor=getattr(args, "actor", "cli"),
            asserted_at=getattr(args, "asserted_at", None),
            on_conflict=getattr(args, "on_conflict", "auto"),
        )
    # 「写入自由，冲突才判」：被拦/被压过/待裁决都不是"成功"，但**审计事件已落账**
    # （所以这里在 savepoint 之外退出，exit 1 表示"这条没写成"，不是"命令坏了"）。
    if result.get("admitted") is False:
        return _not_admitted(args, result, context)
    return _ok(args, "mem add", result, context, _describe_add(result))


def _cmd_replace(args: argparse.Namespace) -> int:
    content = _read_content(args)
    dry_run = bool(getattr(args, "dry_run", False))
    with _session(args, write=True, dry_run=dry_run) as (ledger, context):
        result = ledger.replace(args.unit_id, content, claim_status=args.claim_status)
    return _ok(args, "mem replace", result, context, _describe_change("replaced", result))


def _cmd_supersede(args: argparse.Namespace) -> int:
    content = _read_content(args)
    dry_run = bool(getattr(args, "dry_run", False))
    with _session(args, write=True, dry_run=dry_run) as (ledger, context):
        result = ledger.supersede(
            args.unit_id, content, slot=args.slot, claim_status=args.claim_status
        )
    line = f"superseded {result['superseded']} -> {result['id']} (revision {result['revision']})"
    return _ok(args, "mem supersede", result, context, line)


def _cmd_archive(args: argparse.Namespace) -> int:
    dry_run = bool(getattr(args, "dry_run", False))
    with _session(args, write=True, dry_run=dry_run) as (ledger, context):
        result = ledger.archive(args.unit_id, reason=args.reason)
    line = f"archived {result['id']}" if result["changed"] else f"{result['id']} already archived"
    return _ok(args, "mem archive", result, context, line)


def _cmd_conflicts(args: argparse.Namespace) -> int:
    with _session(args, write=False) as (ledger, context):
        result = ledger.list_conflicts()
    open_count = len(result["open"])
    line = f"{open_count} open conflict(s)"
    if open_count:
        line += " — adjudicate with `mem resolve <conflict-id> --action replace|drop|keep_both`"
    return _ok(args, "mem conflicts", result, context, line)


def _cmd_resolve(args: argparse.Namespace) -> int:
    dry_run = bool(getattr(args, "dry_run", False))
    with _session(args, write=True, dry_run=dry_run) as (ledger, context):
        result = ledger.resolve_conflict(
            args.conflict_id,
            args.action,
            actor=getattr(args, "actor", "owner"),
            note=getattr(args, "note", None),
        )
    if not result.get("changed"):
        line = f"{args.conflict_id} already resolved"
    elif args.action == "drop":
        line = f"{args.conflict_id} dropped (kept existing {result.get('existing_id')})"
    else:
        line = f"{args.conflict_id} resolved by {args.action} -> {result.get('written')}"
    return _ok(args, "mem resolve", result, context, line)


def _cmd_list(args: argparse.Namespace) -> int:
    with _session(args, write=False) as (ledger, context):
        rows = ledger.list_units(
            track=args.track,
            project=ledger.project_aliases(args.project) or None,
            state=args.state,
            branch=args.branch,
            day=args.day,
            limit=args.limit,
            offset=args.offset,
        )
        data = {"results": [row.brief() for row in rows], "count": len(rows)}
    lines = [
        f"{row['id']}  {row['track']}/{row['project'] or '-'}  {row['state']}  {row['excerpt']}"
        for row in data["results"]
    ]
    return _ok(args, "mem list", data, context, "\n".join(lines) or "(no units)")


def _cmd_expand(args: argparse.Namespace) -> int:
    with _session(args, write=False) as (ledger, context):
        data = ledger.expand(args.unit_id)
    unit = data["unit"]
    lines = [
        f"{unit['id']}  {unit['track']}/{unit['project'] or '-'}  state={unit['state']}"
        f"  revision={data['current_revision']}",
        data["content"],
        f"revisions={len(data['revisions'])} events={len(data['events'])}",
    ]
    return _ok(args, "mem expand", data, context, "\n".join(lines))


def _cmd_search(args: argparse.Namespace) -> int:
    with _session(args, write=False) as (ledger, context):
        rebuild: dict[str, Any] | None = None
        if args.rebuild_index:
            rebuild = ledger.rebuild_index()
        data = ledger.search(
            args.query,
            track=args.track,
            project=ledger.project_aliases(args.project) or None,
            limit=args.limit,
            include_archived=args.include_archived,
        )
        if rebuild is not None:
            data["meta"]["rebuilt"] = rebuild
    lines = [
        f"{hit['score']:>5}  {hit['track']}/{hit['project'] or '-'}  {hit['id']}  {hit['snippet']}"
        for hit in data["results"]
    ]
    if not lines:
        lines = [f"no matches ({data['meta']['mode']}; paths={data['meta']['paths']})"]
    return _ok(args, "mem search", data, context, "\n".join(lines))


def _cmd_import(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    if not root.is_dir():
        raise LedgerUnavailableError("corpus_root_missing", root=str(root))
    only = tuple(part.strip() for part in (args.only or "").split(",") if part.strip()) or None
    explicit_map = _load_project_map(args.project_map)
    project_map, map_info = _resolve_project_slugs(args, explicit_map=explicit_map)
    dry_run = bool(getattr(args, "dry_run", False))
    with _session(args, write=True, dry_run=dry_run) as (ledger, context):
        report = import_evolve(
            ledger, root, only=only, project_map=project_map, samples=max(0, args.samples)
        )
    report["dry_run"] = dry_run
    report["db"] = context["db"]
    report["simulated_fresh"] = context["simulated_fresh"]
    report["project_map"] = {**map_info, **report.pop("project_coverage")}
    lines = [f"root: {report['root']}"]
    for track, bucket in report["tracks"].items():
        lines.append(
            f"{track:<8} files={bucket['files']:<4} entries={bucket['entries']:<6} "
            f"created={bucket['created']:<6} updated={bucket['updated']:<4} "
            f"unchanged={bucket['unchanged']:<6} deprecated={bucket['deprecated_imported']:<4} "
            f"slot_superseded={bucket['slot_superseded']:<4} "
            f"supersede_edges={bucket['supersede_edges']}"
        )
    totals = report["totals"]
    lines.append(
        f"total    files={totals['files']} entries={totals['entries']} created={totals['created']} "
        f"updated={totals['updated']} unchanged={totals['unchanged']}"
    )
    lines.append(
        f"skipped: TODOS files={report['skipped']['todos_files']} "
        f"({report['skipped']['todos_bytes']} bytes) — {report['skipped']['reason']}"
    )
    mapping = report["project_map"]
    lines.append(
        f"project_map: available={mapping['available']} source={mapping['source']} "
        f"corpus_projects={mapping['corpus_projects']} mapped={mapping['mapped']} "
        f"unmapped={mapping['unmapped']}"
    )
    for track, bucket in report["tracks"].items():
        for sample in bucket["samples"]:
            lines.append(f"sample[{track}] {sample['source_file']}#{sample['source_id'] or '-'} "
                         f"{sample['excerpt'][:100]}")
    return _ok(args, "mem import-evolve", report, context, "\n".join(lines))


def _cmd_snapshot(args: argparse.Namespace) -> int:
    """Emit the one-shot render payload (a read: it never creates the ledger)."""
    if getattr(args, "json", False):
        args.mem_format = "json"
    for name in ("max_entries", "max_chars", "full_threshold_entries", "full_threshold_chars"):
        value = getattr(args, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"invalid_{name}")
    if not isinstance(args.summary_chars, int) or args.summary_chars < 1:
        raise ValueError("invalid_summary_chars")

    slugs = _cached_project_slugs(args)
    map_info = getattr(
        args, "_project_map_info_cache", {"source": None, "available": bool(slugs)}
    )
    options = SnapshotOptions(
        cwd=os.path.abspath(os.path.expanduser(args.cwd)),
        branch=args.branch,
        project=args.project,
        max_entries=args.max_entries,
        max_chars=args.max_chars,
        full_threshold_entries=args.full_threshold_entries,
        full_threshold_chars=args.full_threshold_chars,
        summary_chars=args.summary_chars,
        date=args.date,
        project_slugs=slugs,
        project_map_info=map_info,
    )
    with _session(args, write=False) as (ledger, _context):
        payload = build_snapshot(ledger, options)

    if args.mem_format == "text":
        lines = [
            f"cwd={payload['cwd']} branch={payload['branch']} "
            f"project={payload['project']} slug={payload['project_slug']}",
        ]
        for track, bucket in payload["tracks"].items():
            lines.append(
                f"{track:<7} {bucket['mode']:<7} count={bucket['count']:<5} "
                f"shown={len(bucket['entries']):<4} truncated={bucket['truncated']}"
            )
        lines.append(f"daily {payload['daily']['date']} count={payload['daily']['count']}")
        lines.append(f"project_log count={payload['project_log']['count']}")
        slots = payload["slots"]
        lines.append(
            f"slots active={slots['active']} deprecated={slots['deprecated']} "
            f"same_slot_multi_active={slots['same_slot_multi_active']}"
        )
        lines.append(f"degraded={payload['degraded']}")
        print("\n".join(lines))
        return EXIT_OK
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return EXIT_OK


def _resolve_project_slugs(
    args: argparse.Namespace, explicit_map: dict[str, str] | None
) -> tuple[dict[str, str], dict[str, Any]]:
    """Build the hash→slug map: registry lookup, an explicit map won over it.

    An **explicitly given** ``--project-map-from-registry`` is required to work
    (exit 2 when it does not). The default registry is best effort: its absence is
    reported in ``project_map``/``degraded`` instead of failing the command, because
    a read (and a CI run) must not depend on this machine's workspace layout.
    """
    explicit_registry = hasattr(args, "project_map_from_registry")
    registry_path = getattr(args, "project_map_from_registry", None)
    if explicit_registry:
        registry_slugs = load_project_slugs(registry_path)
        info: dict[str, Any] = {"source": registry_path, "available": True}
    else:
        registry_slugs, info = try_load_project_slugs()
    merged = dict(registry_slugs)
    if explicit_map:
        merged.update(explicit_map)
        info = {**info, "explicit_entries": len(explicit_map)}
    return merged, {**info, "entries": len(merged)}


def _cmd_init(args: argparse.Namespace) -> int:
    path = schema.resolve_service_db(getattr(args, "db", None))
    if getattr(args, "dry_run", False):
        exists = Path(path).exists()
        data = {"db": path, "would_create": not exists, "schema_version": None}
        line = f"would {'create' if not exists else 'verify'}: {path}"
        return _ok(args, "mem init", data, {"db": path, "simulated_fresh": True}, line)
    conn = schema.connect(path, create=True)
    try:
        conn.commit()
        data = {"db": path, "schema_version": schema.schema_version(conn)}
    finally:
        conn.close()
    return _ok(args, "mem init", data, {"db": path, "simulated_fresh": False},
               f"ledger ready: {path} (schema v{data['schema_version']})")


def _cmd_status(args: argparse.Namespace) -> int:
    with _session(args, write=False) as (ledger, context):
        slugs = ledger.project_slugs
        map_info = getattr(
            args, "_project_map_info_cache", {"source": None, "available": bool(slugs)}
        )
        data = ledger.status()
        data["db"] = context["db"]
        data["schema_version"] = schema.schema_version(ledger.conn)
        projects = [
            str(row[0])
            for row in ledger.conn.execute(
                "SELECT DISTINCT project FROM units "
                "WHERE project IS NOT NULL AND track IN ('key','project')"
            )
        ]
        # A stored value is a hash (imported without a map) or already a registry key
        # (imported with one), so both shapes must count as assigned.
        known = set(slugs) | set(slugs.values())
        assigned = sum(1 for project in projects if project in known)
        data["project_slug_assigned"] = assigned
        data["project_slug_unassigned"] = len(projects) - assigned
        data["project_slug_map"] = {**map_info, "mapped_projects": len(slugs)}
    lines = [
        f"db: {data['db']} (schema v{data['schema_version']})",
        f"units={data['units']} revisions={data['revisions']} events={data['events']} "
        f"fts_rows={data['fts_rows']} bigram_rows={data['bigram_rows']} bytes={data['bytes']}",
        f"by_track={data['by_track']}",
        f"by_state={data['by_state']}",
        f"project_slug: assigned={data['project_slug_assigned']} "
        f"unassigned={data['project_slug_unassigned']} "
        f"(registry available={data['project_slug_map']['available']}, "
        f"mapped_projects={data['project_slug_map']['mapped_projects']})",
    ]
    return _ok(args, "mem status", data, context, "\n".join(lines))


# ---------------------------------------------------------------------- helpers


@contextmanager
def _session(
    args: argparse.Namespace, *, write: bool, dry_run: bool = False
) -> Iterator[tuple[Ledger, dict[str, Any]]]:
    """Open the ledger for one command, honouring dry-run without writing.

    A dry run uses the real ledger (inside a rolled-back savepoint) when it is
    already initialised, so ``--dry-run`` answers "what would happen to *this*
    ledger". When the ledger is absent or an empty file it simulates on a scratch
    in-memory database instead — that is what keeps ``--dry-run`` from creating the
    file it is supposed to inspect.
    """
    path = schema.resolve_service_db(getattr(args, "db", None))
    scratch = dry_run and _treat_as_absent(path)
    if scratch:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        schema.init_schema(conn)
        context = {"db": ":memory: (scratch)", "simulated_fresh": True}
    else:
        conn = schema.connect(path, create=write)
        context = {"db": path, "simulated_fresh": False}
    ledger = Ledger(conn, project_slugs=_cached_project_slugs(args))
    try:
        with ledger.transaction(dry_run=dry_run):
            yield ledger, context
    finally:
        conn.close()


def _cached_project_slugs(args: argparse.Namespace) -> dict[str, str]:
    """Resolve the hash→slug map once per command (best effort unless made explicit)."""
    cached = getattr(args, "_project_slugs_cache", None)
    if cached is not None:
        return cached
    try:
        slugs, info = _resolve_project_slugs(args, explicit_map=None)
    except LedgerUnavailableError:
        slugs, info = {}, {
            "source": None,
            "available": False,
            "reason": "project_registry_unavailable",
        }
    args._project_slugs_cache = slugs
    args._project_map_info_cache = info
    return slugs


def _treat_as_absent(path: str) -> bool:
    """Whether a dry run must simulate on a scratch database instead of opening it."""
    candidate = Path(path)
    try:
        return not candidate.exists() or candidate.stat().st_size == 0
    except OSError:  # pragma: no cover - unreadable path
        return True


def _read_content(args: argparse.Namespace) -> str:
    """Read the entry text from exactly one of ``--content-file`` / ``--content``."""
    inline = getattr(args, "content", None)
    path = getattr(args, "content_file", None)
    if inline is not None and path is not None:
        raise ValueError("content_source_ambiguous")
    if inline is not None:
        return inline
    if path is None:
        raise ValueError("content_required")
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerUnavailableError("content_file_unreadable", path=path, detail=str(exc)) from exc


def _load_project_map(path: str | None) -> dict[str, str] | None:
    """Load an optional ``{evolve_directory: slug}`` mapping."""
    if not path:
        return None
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerUnavailableError("project_map_unreadable", path=path, detail=str(exc)) from exc
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
    ):
        raise ValueError("project_map_must_be_string_map")
    return payload


def _not_admitted(
    args: argparse.Namespace, result: dict[str, Any], context: dict[str, Any]
) -> int:
    """Emit a refused-but-audited payload and return exit code ``1``."""
    reason = result.get("reason") or result.get("action") or "not_admitted"
    detail = result.get("hint") or ""
    if getattr(args, "mem_format", "json") == "text":
        print(f"refused[{reason}]: {detail}")
        return EXIT_REFUSED
    payload = {
        "ok": False,
        "command": "mem add",
        "dry_run": bool(getattr(args, "dry_run", False)),
        "db": context.get("db"),
        "error": {"code": reason, **{k: v for k, v in result.items() if k != "hint"},
                  "hint": detail or None},
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return EXIT_REFUSED


def _describe_add(result: dict[str, Any]) -> str:
    """Human line for ``mem add``."""
    if result.get("deduped"):
        return f"unchanged: identical content already active as {result['id']}"
    line = (
        f"created {result['id']} (track={result['track']} project={result['project'] or '-'} "
        f"slot={result.get('slot') or '-'} revision={result['revision']})"
    )
    deprecated = result.get("deprecated") or []
    if deprecated:
        line += f"; deprecated same-slot: {', '.join(deprecated)}"
    return line


def _describe_change(verb: str, result: dict[str, Any]) -> str:
    """Human line for a revision-appending command."""
    if not result.get("changed"):
        return f"unchanged: {result['id']} already at this content (revision {result['revision']})"
    return f"{verb} {result['id']} (revision {result['revision']})"


def _ok(
    args: argparse.Namespace,
    command: str,
    data: dict[str, Any],
    context: dict[str, Any],
    text: str,
) -> int:
    """Emit a success payload and return exit code ``0``."""
    if getattr(args, "mem_format", "json") == "text":
        print(text)
        return EXIT_OK
    payload = {
        "ok": True,
        "command": command,
        "dry_run": bool(getattr(args, "dry_run", False)),
        "db": context.get("db"),
        "simulated_fresh": context.get("simulated_fresh", False),
        "data": data,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return EXIT_OK


def _fail(
    args: argparse.Namespace,
    exc: BaseException,
    exit_code: int,
    *,
    code: str | None = None,
) -> int:
    """Emit a failure payload and return the three-state exit code."""
    error_code = code or getattr(exc, "code", None) or _code_from_value_error(exc)
    details = dict(getattr(exc, "details", {}) or {})
    payload = {
        "ok": False,
        "command": f"mem {getattr(args, 'mem_action', '')}",
        "dry_run": bool(getattr(args, "dry_run", False)),
        "error": {
            "code": error_code,
            "detail": getattr(exc, "detail_code", None),
            "message": str(exc),
        },
        "details": details,
    }
    if getattr(args, "mem_format", "json") == "text":
        print(f"error[{error_code}]: {exc}", file=sys.stderr)
        return exit_code
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return exit_code


def _code_from_value_error(exc: BaseException) -> str:
    """Turn a validation ``ValueError`` message into an error code."""
    if isinstance(exc, ValueError):
        message = str(exc)
        if message:
            return message.split(":", 1)[0]
    return "invalid_request"


def main(argv: list[str] | None = None) -> int:
    """Run ``mem`` standalone (``python3 -m memory_tool.ledger.cli add ...``)."""
    parser = argparse.ArgumentParser(prog="los-memory mem")
    actions = parser.add_subparsers(dest="mem_action", required=True)
    _register_actions(actions)
    return run(parser.parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
