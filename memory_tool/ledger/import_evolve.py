"""Import the ``dsh-memory-evolve`` markdown corpus into the ledger.

The corpus is the only copy of its own content, so this importer is additive and
idempotent: it creates units, appends revisions when a source entry's text changed,
and never deletes. Re-running it must report ``created: 0``.

Two separate identities are used, because the corpus has two shapes:

* ``[id:8hex]`` entries (the key track) keep a stable identity — an upstream edit
  becomes a new revision of the same unit.
* Entries without an id are keyed by content: ``(file, digest, occurrence)``, so a
  file that grows only imports its new entries, and a true duplicate inside one
  file keeps both rows (reported as ``duplicates_kept``).

``TODOS*.md`` is counted and skipped: todos belong to the host plugin, not here.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .evolve_md import Entry, TrackFile, discover, parse_entry, split_entries
from .schema import TRACKS
from .store import Ledger, content_digest

__all__ = ["import_evolve"]

def import_evolve(
    ledger: Ledger,
    root: str | Path,
    *,
    only: Iterable[str] | None = None,
    project_map: dict[str, str] | None = None,
    samples: int = 3,
) -> dict[str, Any]:
    """Import every mapped track under ``root``.

    The caller owns the transaction (see ``memory_tool/ledger/cli.py``): a dry run
    runs this inside a savepoint that is rolled back, which is what makes
    ``created: N`` here mean exactly "N rows would be written".

    Args:
        ledger: Open ledger bound to an initialised connection.
        root: Corpus root (``~/.dsh/memories``).
        only: Optional track filter (``("memory","user",...)``).
        project_map: Optional ``{directory_hash: slug}`` mapping, used **only** to
            report slug coverage. Stored identity is always the evolve hash, so a unit
            cannot become addressable under two names depending on import flags.
        samples: How many example entries to carry per track.

    Returns:
        A JSON-ready report. Every number is computed from the actual run.
    """
    only_tuple = tuple(only) if only else None
    if only_tuple is not None:
        unknown = [track for track in only_tuple if track not in TRACKS]
        if unknown:
            raise ValueError(f"unknown_track:{unknown[0]}")

    discovery = discover(root, only=only_tuple)
    report: dict[str, Any] = {
        "root": str(Path(root).expanduser()),
        "only": list(only_tuple) if only_tuple else None,
        "files": len(discovery.files),
        "tracks": {
            track: {
                "files": 0,
                "entries": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "deprecated_imported": 0,
                "slot_superseded": 0,
                "supersede_edges": 0,
                "supersede_unresolved": 0,
                "samples": [],
            }
            for track in TRACKS
        },
        "skipped": {
            "todos_files": discovery.skipped_todos_files,
            "todos_bytes": discovery.skipped_todos_bytes,
            "daily_other": discovery.skipped_daily_other,
            "reason": (
                "TODOS*.md belongs to the host plugin (different schema), "
                "not to memory tracks"
            ),
        },
        "aliases": discovery.aliases,
        "duplicates_kept": 0,
    }

    for track_file in discovery.files:
        _import_file(ledger, track_file, report, samples)

    project_dirs = {track_file.project for track_file in discovery.files if track_file.project}
    mapped = project_dirs & set(project_map or {})
    report["project_coverage"] = {
        "corpus_projects": len(project_dirs),
        "mapped": len(mapped),
        "unmapped": len(project_dirs - mapped),
    }
    report["project_slugs"] = {
        directory: (project_map or {})[directory]
        for directory in sorted(mapped)
    }
    report["totals"] = {
        key: sum(bucket[key] for bucket in report["tracks"].values())
        for key in ("files", "entries", "created", "updated", "unchanged", "deprecated_imported",
                    "slot_superseded", "supersede_edges", "supersede_unresolved")
    }
    return report


def _import_file(
    ledger: Ledger, track_file: TrackFile, report: dict[str, Any], samples: int
) -> None:
    """Import one corpus file into its track bucket of the report."""
    bucket = report["tracks"][track_file.track]
    bucket["files"] += 1
    try:
        text = track_file.path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # pragma: no cover - unreadable file
        bucket.setdefault("errors", []).append({"file": track_file.rel_path, "error": str(exc)})
        return

    seen: dict[str, int] = {}
    for index, raw in enumerate(split_entries(text)):
        entry = parse_entry(raw)
        bucket["entries"] += 1
        origin_key, is_duplicate = _origin_key(track_file, entry, seen)
        if is_duplicate:
            report["duplicates_kept"] += 1
        payload = {
            "source_file": track_file.rel_path,
            "source_index": index,
            "source_id": entry.source_id,
            "source_day": entry.day,
            "source_time": entry.time,
            "source_slot": entry.slot,
            "source_branches": entry.branches,
            "source_summary": entry.summary,
            "source_supersedes": entry.supersedes,
            "source_deprecated": entry.deprecated,
        }
        result = ledger.add(
            track_file.track,
            entry.text,
            project=track_file.project,
            branch=",".join(entry.branches) if entry.branches else None,
            slot=entry.slot,
            day=track_file.day,
            origin="import:evolve",
            origin_key=origin_key,
            actor="import:evolve",
            payload=payload,
            # Import identity is the origin key (source id / file+digest+ordinal), not
            # the content: a genuine duplicate line in the source must survive rather
            # than be collapsed, otherwise the imported count cannot be reconciled.
            dedupe=False,
        )
        if result.get("admitted") is False:
            # 镜像不裁决权威：导入轨 rank=0，若同槽位已被更高权威（agent/owner）的
            # 条目占着，这条会被判 outranked / escalate。**跳过它并计数**，不让整份
            # 导入失败，也不让旧镜像反过来压掉活的事实。
            bucket["refused_admission"] = bucket.get("refused_admission", 0) + 1
            report.setdefault("refused_admission", 0)
            report["refused_admission"] += 1
            continue
        if result["created"]:
            bucket["created"] += 1
            if len(bucket["samples"]) < samples:
                bucket["samples"].append(
                    {
                        "id": result["id"],
                        "source_file": track_file.rel_path,
                        "source_id": entry.source_id,
                        "excerpt": entry.body[:160],
                    }
                )
        elif result.get("changed"):
            bucket["updated"] += 1
        else:
            bucket["unchanged"] += 1

        bucket["slot_superseded"] += len(result.get("deprecated") or [])

        if entry.deprecated or entry.deprecated_by:
            target = _resolve_source_id(ledger, track_file, entry.deprecated_by)
            current = ledger.get_unit(result["id"])
            already = current is not None and current.state == "deprecated"
            if not already or (target and current is not None and current.superseded_by != target):
                ledger.mark_import_state(
                    result["id"],
                    state="deprecated",
                    superseded_by=target,
                    actor="import:evolve",
                    payload=payload,
                )
            bucket["deprecated_imported"] += 1

        if entry.supersedes:
            target = _resolve_source_id(ledger, track_file, entry.supersedes)
            if target is None or target == result["id"]:
                bucket["supersede_unresolved"] += 1
            else:
                ledger.deprecate_for_import(
                    target,
                    result["id"],
                    actor="import:evolve",
                    payload={"source_file": track_file.rel_path, "source_id": entry.source_id},
                )
                bucket["supersede_edges"] += 1


def _origin_key(track_file: TrackFile, entry: Entry, seen: dict[str, int]) -> tuple[str, bool]:
    """Build the stable import identity for one entry.

    A source id wins when present (upstream edits then become revisions); otherwise
    the digest is used, with an occurrence ordinal so a genuine duplicate inside one
    file is preserved instead of being silently collapsed.
    """
    scope = f"{track_file.track}:{track_file.project or ''}"
    if entry.source_id:
        return f"evolve:id:{scope}:{entry.source_id}", False
    digest = content_digest(entry.text)
    base = f"evolve:hash:{scope}:{track_file.rel_path}:{digest}"
    ordinal = seen.get(base, 0)
    seen[base] = ordinal + 1
    if ordinal:
        return f"{base}#{ordinal}", True
    return base, False


def _resolve_source_id(ledger: Ledger, track_file: TrackFile, source_id: str | None) -> str | None:
    """Resolve an evolve ``[id:8hex]`` token to a ledger unit id."""
    if not source_id:
        return None
    scope = f"{track_file.track}:{track_file.project or ''}"
    row = ledger.find_by_origin_key(f"evolve:id:{scope}:{source_id}")
    return None if row is None else row.id


def dumps(report: dict[str, Any]) -> str:
    """Render a report as stable JSON (used by tests and the CLI)."""
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False)
