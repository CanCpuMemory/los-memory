"""The writable ledger: units, revisions, lifecycle and search.

One write path, one read path. Every mutation appends to ``events`` (append-only)
and every content change appends a ``revisions`` row, so nothing rewrites
history. The derived search structures (``units_fts``, ``cjk_bigrams``) are
maintained here and can be rebuilt from ``revisions`` at any time.

Exit-code mapping is the caller's job (see ``memory_tool/ledger/cli.py``), but the
exception split *is* the contract:

* :class:`~memory_tool.ledger.schema.LedgerUnavailableError` — cannot judge (exit 2).
* :class:`~memory_tool.ledger.schema.LedgerRefusedError` — a real refusal (exit 1).
* ``ValueError`` — the request itself is not well formed (exit 2).
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .evolve_md import parse_entry
from .schema import (
    BRANCH_SCOPED_TRACKS,
    PROJECT_TRACKS,
    SCOPE_SEPARATOR,
    STATES,
    TRACKS,
    LedgerRefusedError,
)

__all__ = ["Ledger", "utc_now"]

#: trigram tokenizer cannot match patterns shorter than 3 characters.
FTS_MIN_TERM = 3
#: 2-character CJK terms (trigram cannot serve them) route to the bigram index.
CJK_CHAR = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
#: Snippet window around the first hit, in characters each side.
SNIPPET_RADIUS = 80
#: Search result cap, so a broad query cannot turn into a corpus dump.
MAX_RESULTS = 50


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string (``...Z``)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_local() -> str:
    """Return today's local date (``YYYY-MM-DD``) for the daily track."""
    return datetime.now().strftime("%Y-%m-%d")


def canonical_content(raw: str) -> str:
    """Normalise entry text for storage, refusing an empty entry.

    Raises:
        ValueError: ``empty_content`` — an empty entry is not a writable fact.
    """
    if not isinstance(raw, str):
        raise ValueError("content_must_be_text")
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("empty_content")
    return text


def content_digest(content: str) -> str:
    """Return the sha256 of the canonical content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def fts_rowid(unit_id: str) -> int:
    """Stable FTS rowid so reindexing a unit is a primary-key delete, not a scan.

    Same reason ``memory_tool/shadow.py`` derives its rowid: ``units_fts`` keeps a
    copy of the text, and replacing an entry must remove the previous row in O(1).
    """
    digest = hashlib.sha256(unit_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def searchable_text(content: str) -> str:
    """Derive the indexed text from stored content.

    Program tokens (``[id:…] [slot:…] [supersedes:…] [2026-01-01]``) are metadata
    and stay out of the index; human-facing tokens such as ``[summary:…]`` and
    ``[<project>]`` are kept, because they are how the corpus titles an entry.
    """
    entry = parse_entry(content)
    return entry.searchable_text or entry.body


def build_scope_key(track: str, project: str | None, branch: str | None, day: str | None) -> str:
    """Build the unambiguous scope key of a unit."""
    return SCOPE_SEPARATOR.join([track, project or "", branch or "", day or ""])


def validate_scope(
    track: str,
    project: str | None,
    branch: str | None,
    day: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Validate a scope request and return its normalised parts.

    Validation is fail-closed on purpose: a missing project for a project track is
    refused rather than defaulted, because guessing a project is how cross-project
    isolation breaks silently (the same rule the contract layer states).

    Raises:
        ValueError: The scope is not well formed. The message is the machine code.
    """
    if track not in TRACKS:
        raise ValueError("unknown_track")
    project = _clean(project)
    branch = _clean(branch)
    day = _clean(day)
    for name, value in (("project", project), ("branch", branch), ("day", day)):
        if value is not None and SCOPE_SEPARATOR in value:
            raise ValueError(f"reserved_separator_in_{name}")
    if track in PROJECT_TRACKS:
        if project is None:
            raise ValueError("project_required")
    elif project is not None:
        raise ValueError("project_not_applicable")
    if branch is not None and track not in BRANCH_SCOPED_TRACKS:
        raise ValueError("branch_not_applicable")
    if track == "daily":
        if day is None:
            day = today_local()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("invalid_day")
    elif day is not None:
        raise ValueError("day_not_applicable")
    return project, branch, day


def _clean(value: str | None) -> str | None:
    """Strip a scope value, mapping an empty string to ``None``."""
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _new_id(prefix: str) -> str:
    """Return a short opaque identifier (random, so two devices cannot collide)."""
    return f"{prefix}_{secrets.token_hex(6)}"


@dataclass(frozen=True)
class LedgerRow:
    """One unit joined with its current revision, as read by ``list``/``search``."""

    id: str
    track: str
    project: str | None
    branch: str | None
    scope_key: str
    state: str
    slot: str | None
    superseded_by: str | None
    created_at: str
    updated_at: str
    day: str | None
    origin: str
    origin_key: str | None
    revision: int
    digest: str
    content: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> LedgerRow:
        """Build a row from a ``units JOIN revisions`` result."""
        return cls(
            id=row["id"],
            track=row["track"],
            project=row["project"],
            branch=row["branch_scope"],
            scope_key=row["scope_key"],
            state=row["state"],
            slot=row["slot"],
            superseded_by=row["superseded_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            day=row["day"],
            origin=row["origin"],
            origin_key=row["origin_key"],
            revision=row["revision"],
            digest=row["digest"],
            content=row["content"],
        )

    def brief(self, excerpt: int = 160) -> dict[str, Any]:
        """Return the list-shaped projection (no full body)."""
        return {
            "id": self.id,
            "track": self.track,
            "project": self.project,
            "branch": self.branch,
            "state": self.state,
            "slot": self.slot,
            "superseded_by": self.superseded_by,
            "day": self.day,
            "revision": self.revision,
            "origin": self.origin,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "excerpt": _excerpt(self.content, excerpt),
        }


def _excerpt(content: str, limit: int) -> str:
    """Return a single-line excerpt of an entry."""
    flat = re.sub(r"\s+", " ", content).strip()
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


_LATEST_REVISION_SQL = (
    "SELECT u.*, r.revision AS revision, r.content AS content, r.digest AS digest "
    "FROM units u JOIN revisions r ON r.unit_id = u.id "
    "AND r.revision = (SELECT MAX(r2.revision) FROM revisions r2 WHERE r2.unit_id = u.id) "
)


class Ledger:
    """Writable memory ledger bound to one open SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the ledger to an open, schema-initialised connection."""
        self.conn = conn

    # ------------------------------------------------------------------ writes

    @contextmanager
    def transaction(self, dry_run: bool = False) -> Iterator[None]:
        """Run a unit of work atomically; a dry run rolls everything back.

        ``SAVEPOINT`` is used rather than ``BEGIN`` because the driver may already
        be inside an implicit transaction; rolling back to the savepoint is what
        makes ``--dry-run`` a real "wrote nothing" guarantee instead of a claim.
        """
        if dry_run:
            self.conn.execute("SAVEPOINT ledger_dry_run")
            try:
                yield
            finally:
                self.conn.execute("ROLLBACK TO ledger_dry_run")
                self.conn.execute("RELEASE ledger_dry_run")
            return
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def add(
        self,
        track: str,
        content: str,
        *,
        project: str | None = None,
        branch: str | None = None,
        slot: str | None = None,
        day: str | None = None,
        claim_status: str | None = None,
        origin: str = "cli",
        origin_key: str | None = None,
        actor: str = "cli",
        payload: dict[str, Any] | None = None,
        dedupe: bool = True,
    ) -> dict[str, Any]:
        """Create a unit (or update the unit identified by ``origin_key``).

        Two idempotency rules, both required by the write contract:

        * same content + same scope (track/project/branch/day) + still active ⇒
          return the existing unit and append nothing (``created: false``);
        * a given ``origin_key`` identifies one unit forever; a changed digest
          appends a new revision instead of creating a second unit.

        A slot behaves like evolve's ``[slot:]``: activating a slot deactivates
        whatever else was active in that slot *in the same scope*.

        Returns:
            A JSON-ready dict describing what happened.
        """
        project, branch, day = validate_scope(track, project, branch, day)
        text = canonical_content(content)
        digest = content_digest(text)
        slot = _clean(slot)
        scope_key = build_scope_key(track, project, branch, day)
        now = utc_now()

        if origin_key is not None:
            existing = self._by_origin_key(origin_key)
            if existing is not None:
                return self._update_existing(
                    existing, text, digest, claim_status, actor, now, payload
                )

        if dedupe:
            duplicate = self._find_active_duplicate(scope_key, digest)
            if duplicate is not None:
                self._event(
                    "add_deduped",
                    duplicate.id,
                    None,
                    actor,
                    now,
                    {"digest": digest, "scope_key": scope_key, **(payload or {})},
                )
                return {
                    "id": duplicate.id,
                    "created": False,
                    "deduped": True,
                    "changed": False,
                    "track": track,
                    "project": project,
                    "branch": branch,
                    "day": day,
                    "slot": duplicate.slot,
                    "state": duplicate.state,
                    "revision": duplicate.revision,
                }

        unit_id = _new_id("unit")
        # The new row goes in first: the slot hand-off points the old row's
        # ``superseded_by`` at it, and the foreign key must already resolve.
        self.conn.execute(
            "INSERT INTO units (id, track, project, branch_scope, scope_key, state, slot, "
            "superseded_by, created_at, updated_at, day, origin, origin_key) "
            "VALUES (?,?,?,?,?,'active',?,NULL,?,?,?,?,?)",
            (unit_id, track, project, branch, scope_key, slot, now, now, day, origin, origin_key),
        )
        deprecated: list[str] = []
        if slot is not None:
            deprecated = self._deprecate_slot(scope_key, slot, unit_id, actor, now)
        revision_id = self._insert_revision(unit_id, 1, text, digest, claim_status, now)
        self._index_unit(unit_id, text)
        self._event(
            "add",
            unit_id,
            revision_id,
            actor,
            now,
            {
                "track": track,
                "project": project,
                "branch": branch,
                "slot": slot,
                "day": day,
                "digest": digest,
                "origin": origin,
                "origin_key": origin_key,
                **(payload or {}),
            },
        )
        return {
            "id": unit_id,
            "created": True,
            "deduped": False,
            "changed": True,
            "track": track,
            "project": project,
            "branch": branch,
            "day": day,
            "slot": slot,
            "state": "active",
            "revision": 1,
            "digest": digest,
            "deprecated": deprecated,
        }

    def replace(
        self,
        unit_id: str,
        content: str,
        *,
        claim_status: str | None = None,
        actor: str = "cli",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a new revision to an active unit.

        Replacing archived or deprecated content is refused: those states exist so
        a retired fact cannot be edited back into the default recall set through a
        side door.
        """
        row = self._require_unit(unit_id)
        if row.state != "active":
            raise LedgerRefusedError("unit_not_active", id=unit_id, state=row.state)
        text = canonical_content(content)
        digest = content_digest(text)
        if digest == row.digest:
            return {
                "id": unit_id,
                "created": False,
                "changed": False,
                "revision": row.revision,
                "digest": digest,
            }
        now = utc_now()
        revision_id = self._insert_revision(
            unit_id, row.revision + 1, text, digest, claim_status, now
        )
        self.conn.execute("UPDATE units SET updated_at=? WHERE id=?", (now, unit_id))
        self._index_unit(unit_id, text)
        self._event(
            "replace",
            unit_id,
            revision_id,
            actor,
            now,
            {"from_revision": row.revision, "to_revision": row.revision + 1, "digest": digest,
             **(payload or {})},
        )
        return {
            "id": unit_id,
            "created": False,
            "changed": True,
            "revision": row.revision + 1,
            "digest": digest,
        }

    def supersede(
        self,
        unit_id: str,
        content: str,
        *,
        slot: str | None = None,
        claim_status: str | None = None,
        actor: str = "cli",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write a replacement unit and mark the old one ``deprecated``.

        The old unit keeps its revisions and gains ``superseded_by``; history is
        never rewritten, and ``expand`` still reads the retired content.
        """
        old = self._require_unit(unit_id)
        if old.state != "active":
            raise LedgerRefusedError("unit_not_active", id=unit_id, state=old.state)
        text = canonical_content(content)
        digest = content_digest(text)
        now = utc_now()
        new_id = _new_id("unit")
        inherited_slot = _clean(slot) if slot is not None else old.slot
        self.conn.execute(
            "INSERT INTO units (id, track, project, branch_scope, scope_key, state, slot, "
            "superseded_by, created_at, updated_at, day, origin, origin_key) "
            "VALUES (?,?,?,?,?,'active',?,NULL,?,?,?,?,NULL)",
            (new_id, old.track, old.project, old.branch, old.scope_key, inherited_slot, now, now,
             old.day, old.origin),
        )
        revision_id = self._insert_revision(new_id, 1, text, digest, claim_status, now)
        self._index_unit(new_id, text)
        self.conn.execute(
            "UPDATE units SET state='deprecated', superseded_by=?, updated_at=? WHERE id=?",
            (new_id, now, unit_id),
        )
        self._event(
            "supersede",
            new_id,
            revision_id,
            actor,
            now,
            {
                "superseded_unit": unit_id,
                "slot": inherited_slot,
                "digest": digest,
                **(payload or {}),
            },
        )
        return {
            "id": new_id,
            "superseded": unit_id,
            "created": True,
            "revision": 1,
            "digest": digest,
            "slot": inherited_slot,
        }

    def archive(
        self,
        unit_id: str,
        *,
        actor: str = "cli",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Move a unit to ``archived``: hidden from default recall, readable by id."""
        row = self._require_unit(unit_id)
        if row.state == "archived":
            return {"id": unit_id, "state": "archived", "changed": False}
        now = utc_now()
        self.conn.execute(
            "UPDATE units SET state='archived', updated_at=? WHERE id=?", (now, unit_id)
        )
        self._event(
            "archive", unit_id, None, actor, now, {"from_state": row.state, "reason": reason}
        )
        return {"id": unit_id, "state": "archived", "changed": True, "from_state": row.state}

    # ------------------------------------------------------------------- reads

    def list_units(
        self,
        *,
        track: str | None = None,
        project: str | None = None,
        state: str = "active",
        branch: str | None = None,
        day: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LedgerRow]:
        """List units with their current revision, newest last.

        ``state='all'`` is the only wildcard; a missing ledger is not a wildcard,
        and callers get :class:`LedgerUnavailableError` before reaching this method.
        """
        if state not in (*STATES, "all"):
            raise ValueError("unknown_state")
        if track is not None and track not in TRACKS:
            raise ValueError("unknown_track")
        sql = _LATEST_REVISION_SQL + "WHERE 1=1"
        params: list[Any] = []
        if state != "all":
            sql += " AND u.state=?"
            params.append(state)
        if track is not None:
            sql += " AND u.track=?"
            params.append(track)
        if project is not None:
            sql += " AND u.project=?"
            params.append(project)
        if branch is not None:
            sql += " AND u.branch_scope=?"
            params.append(branch)
        if day is not None:
            sql += " AND u.day=?"
            params.append(day)
        sql += " ORDER BY u.created_at ASC, u.id ASC LIMIT ? OFFSET ?"
        params.extend([max(0, limit), max(0, offset)])
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [LedgerRow.from_row(row) for row in rows]

    def expand(self, unit_id: str) -> dict[str, Any]:
        """Return one unit with its full history: revisions, lifecycle and events."""
        row = self._require_unit(unit_id)
        revisions = self.conn.execute(
            "SELECT revision_id, revision, digest, claim_status, created_at, content "
            "FROM revisions WHERE unit_id=? ORDER BY revision ASC",
            (unit_id,),
        ).fetchall()
        events = self.conn.execute(
            "SELECT id, kind, unit_id, revision_id, actor, at, payload_json "
            "FROM events WHERE unit_id=? ORDER BY id ASC",
            (unit_id,),
        ).fetchall()
        return {
            "unit": {
                key: getattr(row, key)
                for key in (
                    "id",
                    "track",
                    "project",
                    "branch",
                    "scope_key",
                    "state",
                    "slot",
                    "superseded_by",
                    "created_at",
                    "updated_at",
                    "day",
                    "origin",
                    "origin_key",
                )
            },
            "content": row.content,
            "current_revision": row.revision,
            "revisions": [
                {
                    "revision_id": item["revision_id"],
                    "revision": item["revision"],
                    "digest": item["digest"],
                    "claim_status": item["claim_status"],
                    "created_at": item["created_at"],
                    "content": item["content"],
                }
                for item in revisions
            ],
            "events": [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "revision_id": item["revision_id"],
                    "actor": item["actor"],
                    "at": item["at"],
                    "payload": json.loads(item["payload_json"] or "{}"),
                }
                for item in events
            ],
        }

    def status(self) -> dict[str, Any]:
        """Return counts per track/state plus index and ledger size."""
        by_track = {
            str(row["track"]): int(row["count"])
            for row in self.conn.execute(
                "SELECT track, COUNT(*) AS count FROM units GROUP BY track"
            )
        }
        by_state = {
            str(row["state"]): int(row["count"])
            for row in self.conn.execute(
                "SELECT state, COUNT(*) AS count FROM units GROUP BY state"
            )
        }
        page_count = self.conn.execute("PRAGMA page_count").fetchone()[0]
        page_size = self.conn.execute("PRAGMA page_size").fetchone()[0]
        return {
            "units": sum(by_track.values()),
            "by_track": by_track,
            "by_state": by_state,
            "revisions": int(self.conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]),
            "events": int(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
            "events_last_id": self._last_event_id(),
            "fts_rows": self._count("SELECT COUNT(*) FROM units_fts"),
            "bigram_rows": self._count("SELECT COUNT(*) FROM cjk_bigrams"),
            "bytes": int(page_count) * int(page_size),
        }

    def find_by_origin_key(self, origin_key: str) -> LedgerRow | None:
        """Return the unit registered under an import key, or ``None``."""
        return self._by_origin_key(origin_key)

    def get_unit(self, unit_id: str) -> LedgerRow | None:
        """Return one unit row, or ``None`` when it does not exist."""
        row = self.conn.execute(_LATEST_REVISION_SQL + "WHERE u.id=?", (unit_id,)).fetchone()
        return None if row is None else LedgerRow.from_row(row)

    # ------------------------------------------------------------------ search

    def search(
        self,
        query: str,
        *,
        track: str | None = None,
        project: str | None = None,
        limit: int = 10,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """Substring search over unit text, accelerated by the derived indexes.

        Indexes only narrow *which* units are loaded; the substring check in Python
        stays authoritative, so recall can never be worse than a full scan. Each
        term picks a path: trigram at >= 3 characters, CJK bigrams at exactly 2,
        and a scan for anything the indexes cannot represent. ``meta`` names the
        path per term, so "no matches" is distinguishable from "index not built".
        """
        if not isinstance(query, str):
            raise ValueError("query_must_be_text")
        query = query.strip()
        if not query:
            raise ValueError("empty_query")
        if len(query) > 500:
            raise ValueError("query_too_long")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
            raise ValueError("invalid_limit")
        if track is not None and track not in TRACKS:
            raise ValueError("unknown_track")

        terms = [term for term in _casefold(query).split() if term]
        paths = {term: _term_path(term) for term in terms}
        usable = {"trigram": self._fts_has_rows(), "bigram": self._count(
            "SELECT COUNT(*) FROM cjk_bigrams"
        ) > 0}
        meta: dict[str, Any] = {
            "query": query,
            "terms": terms,
            "paths": paths,
            "usable": usable,
            "mode": "scan",
            "candidates": None,
        }

        indexed_terms = [term for term in terms if paths[term] == "trigram" and usable["trigram"]]
        bigram_terms = [term for term in terms if paths[term] == "bigram" and usable["bigram"]]
        uncovered = [
            term
            for term in terms
            if paths[term] == "scan"
            or (paths[term] == "trigram" and not usable["trigram"])
            or (paths[term] == "bigram" and not usable["bigram"])
        ]
        candidates: set[str] | None = None
        if indexed_terms or bigram_terms:
            try:
                if indexed_terms:
                    candidates = self._fts_candidates(indexed_terms)
                if bigram_terms:
                    found = self._bigram_candidates(bigram_terms)
                    candidates = found if candidates is None else candidates & found
                meta["mode"] = "index+filter"
                meta["candidates"] = None if candidates is None else len(candidates)
            except sqlite3.Error as exc:  # unusable index -> scan, never lose recall
                meta["index_error"] = str(exc)
                candidates = None
        meta["scan_terms"] = uncovered or None
        if candidates is not None and not candidates:
            return {"results": [], "meta": {**meta, "hits": 0}}

        sql = _LATEST_REVISION_SQL + "WHERE 1=1"
        params: list[Any] = []
        if not include_archived:
            sql += " AND u.state != 'archived'"
        if track is not None:
            sql += " AND u.track=?"
            params.append(track)
        if project is not None:
            sql += " AND u.project=?"
            params.append(project)
        if candidates is not None:
            marks = ",".join("?" for _ in candidates)
            sql += f" AND u.id IN ({marks})"
            params.extend(sorted(candidates))
        rows = self.conn.execute(sql, tuple(params)).fetchall()

        scored: list[tuple[float, LedgerRow, str]] = []
        for row in rows:
            unit = LedgerRow.from_row(row)
            text = _casefold(searchable_text(unit.content))
            occurrences = 0
            matched = 0
            for term in terms:
                hits = text.count(term)
                if hits:
                    matched += 1
                    occurrences += hits
            if matched != len(terms):
                continue
            score = round(matched + min(0.999, occurrences / 1000.0), 3)
            scored.append((score, unit, text))
        scored.sort(key=lambda item: (-item[0], item[1].created_at, item[1].id))
        results = []
        for score, unit, text in scored[:limit]:
            results.append(
                {
                    "id": unit.id,
                    "track": unit.track,
                    "project": unit.project,
                    "state": unit.state,
                    "revision": unit.revision,
                    "score": score,
                    "snippet": _snippet(text, terms[0]),
                }
            )
        return {"results": results, "meta": {**meta, "hits": len(scored)}}

    def rebuild_index(self) -> dict[str, Any]:
        """Rebuild the derived search structures from ``revisions``."""
        rows = self.conn.execute(_LATEST_REVISION_SQL).fetchall()
        self.conn.execute("DELETE FROM units_fts")
        self.conn.execute("DELETE FROM cjk_bigrams")
        for row in rows:
            self._index_unit(str(row["id"]), str(row["content"]))
        self.conn.execute("INSERT INTO units_fts(units_fts) VALUES('optimize')")
        return {
            "units": len(rows),
            "fts_rows": self._count("SELECT COUNT(*) FROM units_fts"),
            "bigram_rows": self._count("SELECT COUNT(*) FROM cjk_bigrams"),
        }

    # ---------------------------------------------------------------- internals

    def _require_unit(self, unit_id: str) -> LedgerRow:
        """Return the unit or refuse: an unknown id is a real negative, not "empty"."""
        if not isinstance(unit_id, str) or not unit_id.strip():
            raise ValueError("id_required")
        row = self.get_unit(unit_id.strip())
        if row is None:
            raise LedgerRefusedError("unit_not_found", id=unit_id)
        return row

    def _by_origin_key(self, origin_key: str) -> LedgerRow | None:
        """Return the unit registered under an import key."""
        row = self.conn.execute(
            _LATEST_REVISION_SQL + "WHERE u.origin_key=?", (origin_key,)
        ).fetchone()
        return None if row is None else LedgerRow.from_row(row)

    def _find_active_duplicate(self, scope_key: str, digest: str) -> LedgerRow | None:
        """Return an active unit with this exact content in this scope."""
        row = self.conn.execute(
            _LATEST_REVISION_SQL
            + "WHERE u.scope_key=? AND u.state='active' AND r.digest=? "
            "ORDER BY u.created_at ASC, u.id ASC LIMIT 1",
            (scope_key, digest),
        ).fetchone()
        return None if row is None else LedgerRow.from_row(row)

    def _update_existing(
        self,
        existing: LedgerRow,
        text: str,
        digest: str,
        claim_status: str | None,
        actor: str,
        now: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Append a revision to an origin-keyed unit when its source text changed."""
        if digest == existing.digest:
            return {
                "id": existing.id,
                "created": False,
                "deduped": True,
                "changed": False,
                "track": existing.track,
                "project": existing.project,
                "branch": existing.branch,
                "day": existing.day,
                "slot": existing.slot,
                "state": existing.state,
                "revision": existing.revision,
            }
        revision_id = self._insert_revision(
            existing.id, existing.revision + 1, text, digest, claim_status, now
        )
        self.conn.execute("UPDATE units SET updated_at=? WHERE id=?", (now, existing.id))
        self._index_unit(existing.id, text)
        self._event(
            "import_update",
            existing.id,
            revision_id,
            actor,
            now,
            {"from_revision": existing.revision, "to_revision": existing.revision + 1,
             "digest": digest, **(payload or {})},
        )
        return {
            "id": existing.id,
            "created": False,
            "deduped": False,
            "changed": True,
            "track": existing.track,
            "project": existing.project,
            "branch": existing.branch,
            "day": existing.day,
            "slot": existing.slot,
            "state": existing.state,
            "revision": existing.revision + 1,
            "digest": digest,
        }

    def _deprecate_slot(
        self, scope_key: str, slot: str, keep_id: str, actor: str, now: str
    ) -> list[str]:
        """Deprecate every other active unit in this slot; return their ids."""
        rows = self.conn.execute(
            "SELECT id FROM units WHERE scope_key=? AND slot=? AND state='active' AND id != ?",
            (scope_key, slot, keep_id),
        ).fetchall()
        deprecated = [str(row["id"]) for row in rows]
        for unit_id in deprecated:
            self.conn.execute(
                "UPDATE units SET state='deprecated', superseded_by=?, updated_at=? WHERE id=?",
                (keep_id, now, unit_id),
            )
            self._event(
                "slot_supersede",
                unit_id,
                None,
                actor,
                now,
                {"slot": slot, "replaced_by": keep_id},
            )
        return deprecated

    def deprecate_for_import(
        self, unit_id: str, superseded_by: str, actor: str, payload: dict[str, Any] | None = None
    ) -> bool:
        """Mark an imported unit deprecated (used for ``[supersedes:]``/``[deprecated]``).

        Returns:
            ``True`` when the row changed, ``False`` when it was already retired.
        """
        row = self.get_unit(unit_id)
        if row is None:
            return False
        if row.state == "deprecated" and row.superseded_by is not None:
            return False
        now = utc_now()
        self.conn.execute(
            "UPDATE units SET state='deprecated', superseded_by=?, updated_at=? WHERE id=?",
            (superseded_by, now, unit_id),
        )
        self._event("import_deprecate", unit_id, None, actor, now,
                    {"superseded_by": superseded_by, **(payload or {})})
        return True

    def mark_import_state(
        self, unit_id: str, *, state: str, superseded_by: str | None, actor: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Set an imported unit's lifecycle state without inventing a revision."""
        if state not in STATES:
            raise ValueError("unknown_state")
        now = utc_now()
        self.conn.execute(
            "UPDATE units SET state=?, superseded_by=?, updated_at=? WHERE id=?",
            (state, superseded_by, now, unit_id),
        )
        self._event("import_state", unit_id, None, actor, now,
                    {"state": state, "superseded_by": superseded_by, **(payload or {})})

    def _insert_revision(
        self,
        unit_id: str,
        revision: int,
        content: str,
        digest: str,
        claim_status: str | None,
        now: str,
    ) -> str:
        """Append one revision row and return its id."""
        revision_id = _new_id("rev")
        self.conn.execute(
            "INSERT INTO revisions (revision_id, unit_id, revision, content, digest, "
            "claim_status, created_at) VALUES (?,?,?,?,?,?,?)",
            (revision_id, unit_id, revision, content, digest, claim_status, now),
        )
        return revision_id

    def _event(
        self,
        kind: str,
        unit_id: str | None,
        revision_id: str | None,
        actor: str,
        at: str,
        payload: dict[str, Any] | None,
    ) -> None:
        """Append one event. Nothing in this package updates or deletes events."""
        self.conn.execute(
            "INSERT INTO events (kind, unit_id, revision_id, actor, at, payload_json) "
            "VALUES (?,?,?,?,?,?)",
            (kind, unit_id, revision_id, actor, at,
             json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, default=str)),
        )

    def _index_unit(self, unit_id: str, content: str) -> None:
        """Insert (or refresh) the search rows for one unit."""
        text = searchable_text(content)
        rowid = fts_rowid(unit_id)
        self.conn.execute("DELETE FROM units_fts WHERE rowid=?", (rowid,))
        self.conn.execute(
            "INSERT INTO units_fts (rowid, unit_id, text) VALUES (?,?,?)", (rowid, unit_id, text)
        )
        self.conn.execute("DELETE FROM cjk_bigrams WHERE unit_id=?", (unit_id,))
        terms = _cjk_bigrams(text)
        if terms:
            self.conn.executemany(
                "INSERT OR IGNORE INTO cjk_bigrams (term, unit_id) VALUES (?,?)",
                [(term, unit_id) for term in sorted(terms)],
            )

    def _fts_has_rows(self) -> bool:
        """Whether the trigram index holds anything (existence, not count)."""
        return _has_any(self.conn, "SELECT 1 FROM units_fts LIMIT 1")

    def _fts_candidates(self, terms: Iterable[str]) -> set[str]:
        """Units matching every quoted term via the trigram index."""
        query = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
        rows = self.conn.execute(
            "SELECT unit_id FROM units_fts WHERE units_fts MATCH ?", (query,)
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _bigram_candidates(self, terms: Iterable[str]) -> set[str]:
        """Units matching every 2-character CJK term via the bigram index."""
        candidates: set[str] | None = None
        for term in terms:
            rows = {
                str(row[0])
                for row in self.conn.execute(
                    "SELECT unit_id FROM cjk_bigrams WHERE term=?", (term,)
                )
            }
            candidates = rows if candidates is None else candidates & rows
        return candidates or set()

    def _last_event_id(self) -> int:
        """Return the newest event id (0 when the ledger has no events)."""
        row = self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
        return int(row[0])

    def _count(self, sql: str) -> int:
        """Run a scalar count query."""
        return int(self.conn.execute(sql).fetchone()[0])


def _has_any(conn: sqlite3.Connection, sql: str) -> bool:
    """Whether a query returns at least one row."""
    try:
        return conn.execute(sql).fetchone() is not None
    except sqlite3.Error:  # pragma: no cover - index missing
        return False


def _casefold(text: str) -> str:
    """Case-fold text for matching (the index is case-insensitive for ASCII)."""
    return text.casefold()


def _term_path(term: str) -> str:
    """Which index path can serve this term, or ``scan`` when none can."""
    if CJK_CHAR.search(term):
        if len(term) >= FTS_MIN_TERM:
            return "trigram"
        return "bigram" if len(term) == 2 else "scan"
    return "trigram" if len(term) >= FTS_MIN_TERM else "scan"


def _cjk_bigrams(text: str) -> set[str]:
    """Every adjacent 2-character CJK pair in ``text``.

    Mirrors ``memory_tool/shadow.py``'s helper (same measured reason: trigram
    cannot serve a 2-character term). Duplicated locally so the shadow module's
    behaviour is not a dependency of this runtime.
    """
    found: set[str] = set()
    run: list[str] = []
    for character in text:
        if CJK_CHAR.match(character):
            run.append(character)
            continue
        if len(run) >= 2:
            found.update("".join(run[index:index + 2]) for index in range(len(run) - 1))
        run = []
    if len(run) >= 2:
        found.update("".join(run[index:index + 2]) for index in range(len(run) - 1))
    return found


def _snippet(text: str, term: str) -> str:
    """Return a window of ``text`` around the first occurrence of ``term``."""
    position = text.find(term)
    if position < 0:
        return _excerpt(text, SNIPPET_RADIUS * 2)
    start = max(0, position - SNIPPET_RADIUS)
    end = min(len(text), position + len(term) + SNIPPET_RADIUS)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return prefix + re.sub(r"\s+", " ", text[start:end]).strip() + suffix
