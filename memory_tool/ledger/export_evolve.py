"""Reverse export: ledger → evolve markdown (the rollback surface for the cutover).

Why this exists: `import-evolve` is one-way. After the atomic batch the markdown
corpus has no writer, so if the ledger write path turns out to be wrong there is no
way back to evolve — that is what makes the flip a one-way door. This module is the
other direction, deliberately narrow:

* **append-only and idempotent** — it adds entries whose canonical body is not
  already present in the target file, and never rewrites, reorders or removes an
  existing entry. Running it twice appends nothing the second time.
* **no ids are exported** — evolve assigns its own ``[id:8hex]``; handing it a
  ``unit_<12hex>`` token would make its parser treat the entry as legacy and lose
  the slot/supersede identity anyway.
* **default target is a staging directory** (dry-run unless ``--out`` or
  ``--in-place`` is given), because writing into the live corpus *before* the cutover
  would inject entries evolve never wrote — the very state this is meant to recover
  from.

What a rollback corpus does **not** reconstruct (documented, not hidden):
``origin``/revisions/events, evolve's own deprecated-vs-superseded bookkeeping, and
branch scoping beyond the ``[branch:a,b]`` token.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path
from typing import Any, Iterable

from .evolve_md import SEPARATOR_LINE, parse_entry, split_entries
from .store import canonical_content, content_digest

#: Tracks that live in one file at the corpus root.
ROOT_FILES = {
    "memory": ("MEMORY.md", "MEMORY-archive.md"),
    "user": ("USER.md", "USER-archive.md"),
}
#: Tracks that live under ``projects/<hash>/``.
PROJECT_FILES = {
    "key": ("KEY.md", "KEY-archive.md"),
    "project": ("MEMORY.md", "MEMORY-archive.md"),
}


def target_path(root: str | os.PathLike[str], track: str, project: str | None,
                day: str | None, *, archived: bool = False) -> Path | None:
    """Where one unit belongs in an evolve corpus. ``None`` when it cannot be placed."""
    base = Path(root).expanduser()
    if track in ROOT_FILES:
        return base / ROOT_FILES[track][1 if archived else 0]
    if track in PROJECT_FILES:
        if not project:
            return None
        return base / "projects" / str(project) / PROJECT_FILES[track][1 if archived else 0]
    if track == "daily":
        if not day:
            return None
        return base / "daily" / f"{day}.md"
    return None


def _date_token(day: str | None, created_at: str | None) -> str:
    """``[YYYY-MM-DD]`` — from the unit's day, else the write date (never invented)."""
    if day:
        return f"[{day}]"
    if created_at:
        try:
            ts = _dt.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            return f"[{ts.astimezone(_dt.timezone.utc).date().isoformat()}]"
        except ValueError:
            pass
    return f"[{_dt.datetime.now(_dt.timezone.utc).date().isoformat()}]"


def entry_text(unit: dict[str, Any], *, archived: bool = False) -> str:
    """One corpus entry: date token, then slot/branch/summary, then the body.

    The body is the ledger's canonical content, so a later ``import-evolve`` reads
    back the same digest (that is what makes the round trip provable).
    """
    tokens = [_date_token(unit.get("day"), unit.get("created_at"))]
    if unit.get("slot"):
        tokens.append(f"[slot:{unit['slot']}]")
    if unit.get("branch"):
        tokens.append(f"[branch:{unit['branch']}]")
    header = " ".join(tokens)
    body = canonical_content(str(unit.get("content") or ""))
    # evolve keeps the metadata tokens and the body on the **same line** (the text
    # wraps naturally). Mirroring that matters for one reason: the importer stores
    # ``entry.text`` as the unit content, so a same-line layout round-trips to the
    # same digest — a newline after the header would make every export look new.
    return f"{header} {body}".strip() if body else header


def existing_digests(path: Path) -> set[str]:
    """Canonical digests of the entries already in one corpus file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    out: set[str] = set()
    for chunk in split_entries(text):
        # Compare the **body** (metadata tokens stripped), because the two sides do
        # not carry the same tokens: the corpus has evolve's ``[id:8hex]``, the ledger
        # keeps it in the stored text for imported rows, and an export emits no id at
        # all. Whole-text digests would therefore never match on either side.
        body = parse_entry(chunk).body
        if body.strip():
            out.add(content_digest(canonical_content(body)))
    return out


def corpus_digests(root: str | os.PathLike[str]) -> set[str]:
    """Body digests of **every** entry already in the corpus, across all files.

    Deduplication has to be corpus-wide, not per-file: a unit that evolve archived by
    writing ``[deprecated:…]`` into the *main* file would otherwise be re-appended to
    the archive file, leaving the same text in two files — and evolve reads both, so
    a rollback would report the memory twice.
    """
    base = Path(root).expanduser()
    if not base.is_dir():
        return set()
    patterns = ["MEMORY.md", "USER.md", "MEMORY-archive.md", "USER-archive.md",
                "projects/*/KEY.md", "projects/*/MEMORY.md",
                "projects/*/KEY-archive.md", "projects/*/MEMORY-archive.md",
                "daily/*.md"]
    out: set[str] = set()
    for pattern in patterns:
        for path in base.glob(pattern):
            out |= existing_digests(path)
    return out


def append_entries(path: Path, entries: Iterable[str], *, dry_run: bool) -> int:
    """Append entries separated by the corpus separator. Returns how many were added."""
    pending = [e for e in entries if e and e.strip()]
    if not pending:
        return 0
    if dry_run:
        return len(pending)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    prefix = "" if not existing.strip() else ("" if existing.endswith("\n") else "\n")
    blocks = [f"{SEPARATOR_LINE}\n{e.strip()}\n" for e in pending]
    with path.open("a", encoding="utf-8") as fh:
        if not existing.strip():
            # First entry in a new file: no leading separator (evolve tolerates one,
            # but a stray leading § would count as an empty block for some readers).
            fh.write(blocks[0].lstrip())
            blocks = blocks[1:]
        else:
            fh.write(prefix)
        for block in blocks:
            fh.write(block)
    return len(pending)


def export_units(units: list[dict[str, Any]], *, root: str, dry_run: bool) -> dict[str, Any]:
    """Group units by target file, skip what is already there, append the rest."""
    per_file: dict[str, dict[str, Any]] = {}
    skipped_unplaceable: list[str] = []
    for unit in units:
        archived = str(unit.get("state")) in ("archived", "deprecated")
        day = unit.get("day")
        if unit.get("track") == "daily" and not day:
            created = str(unit.get("created_at") or "")
            day = created[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", created) else None
            unit = {**unit, "day": day}
        path = target_path(root, str(unit.get("track")), unit.get("project"), day, archived=archived)
        if path is None:
            skipped_unplaceable.append(str(unit.get("id")))
            continue
        bucket = per_file.setdefault(str(path), {"path": str(path), "appended": 0, "skipped": 0,
                                                 "entries": []})
        digest = content_digest(canonical_content(parse_entry(str(unit.get("content") or "")).body))
        bucket.setdefault("digests", set())
        bucket["entries"].append((digest, entry_text(unit, archived=archived)))

    seen_corpus = corpus_digests(root)
    total_appended = 0
    for path_str, bucket in per_file.items():
        path = Path(path_str)
        seen = set(seen_corpus)
        fresh: list[str] = []
        for digest, text in bucket["entries"]:
            if digest in seen:
                bucket["skipped"] += 1
                continue
            seen.add(digest)
            fresh.append(text)
        added = append_entries(path, fresh, dry_run=dry_run)
        bucket["appended"] = added
        total_appended += added
        bucket.pop("entries", None)
        bucket.pop("digests", None)

    return {
        "root": str(Path(root).expanduser()),
        "dry_run": dry_run,
        "files": sorted(per_file.values(), key=lambda b: b["path"]),
        "appended": total_appended,
        "skipped_existing": sum(b["skipped"] for b in per_file.values()),
        "skipped_unplaceable": skipped_unplaceable,
    }
