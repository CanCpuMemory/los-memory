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
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import schema
from .import_evolve import import_evolve
from .schema import STATES, TRACKS, LedgerRefusedError, LedgerUnavailableError
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
    importer.add_argument("--samples", type=int, default=3)
    _add_common_arguments(importer)

    init = actions.add_parser("init", help="Create/verify the ledger database")
    _add_common_arguments(init)

    status = actions.add_parser("status", help="Ledger counts and index state")
    _add_common_arguments(status)


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
        )
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


def _cmd_list(args: argparse.Namespace) -> int:
    with _session(args, write=False) as (ledger, context):
        rows = ledger.list_units(
            track=args.track,
            project=args.project,
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
            project=args.project,
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
    project_map = _load_project_map(args.project_map)
    dry_run = bool(getattr(args, "dry_run", False))
    with _session(args, write=True, dry_run=dry_run) as (ledger, context):
        report = import_evolve(
            ledger, root, only=only, project_map=project_map, samples=max(0, args.samples)
        )
    report["dry_run"] = dry_run
    report["db"] = context["db"]
    report["simulated_fresh"] = context["simulated_fresh"]
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
    for track, bucket in report["tracks"].items():
        for sample in bucket["samples"]:
            lines.append(f"sample[{track}] {sample['source_file']}#{sample['source_id'] or '-'} "
                         f"{sample['excerpt'][:100]}")
    return _ok(args, "mem import-evolve", report, context, "\n".join(lines))


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
        data = ledger.status()
        data["db"] = context["db"]
        data["schema_version"] = schema.schema_version(ledger.conn)
    lines = [
        f"db: {data['db']} (schema v{data['schema_version']})",
        f"units={data['units']} revisions={data['revisions']} events={data['events']} "
        f"fts_rows={data['fts_rows']} bigram_rows={data['bigram_rows']} bytes={data['bytes']}",
        f"by_track={data['by_track']}",
        f"by_state={data['by_state']}",
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
    ledger = Ledger(conn)
    try:
        with ledger.transaction(dry_run=dry_run):
            yield ledger, context
    finally:
        conn.close()


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
        "error": {"code": error_code, "message": str(exc)},
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
