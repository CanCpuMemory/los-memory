"""Storage layer for the writable ledger (``service.sqlite3``).

This is the runtime that ``memory_tool/service_contract/`` deliberately left out:
the contract package declares a schema, receipt semantics and negative tests but
imports no ``sqlite3`` and creates no file. The runtime added here is a separate,
independent layer — the contract package is untouched, and this module does not
import it.

Three tables carry the state, one carries history and one is append-only:

* ``units``     — one row per memory entry, with track/scope/state/lifecycle.
* ``revisions`` — every content version; ``content`` is the full entry text.
* ``events``    — append-only ledger (nothing in this package updates or deletes
  an event row).
* ``units_fts`` / ``cjk_bigrams`` — derived search structures, rebuildable with
  ``mem search --rebuild-index``.

The path is deliberately *not* a profile database: the ``codex`` / ``claude`` /
``shared`` ledgers stay untouched (``MEMORY_SERVICE_DB`` overrides the default).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

#: Environment override, mirroring the ``MEMORY_DB_PATH`` discipline of the core
#: tool: a caller must be able to point the runtime at a scratch file.
DB_ENV = "MEMORY_SERVICE_DB"

#: Default location. Not a profile directory on purpose — see the module docstring.
DEFAULT_DB_PATH = "~/.local/share/los-memory/service.sqlite3"

TRACKS = ("memory", "user", "key", "project", "daily")
STATES = ("active", "archived", "deprecated")

#: Tracks whose entries are global; ``project``/``branch`` do not apply to them.
GLOBAL_TRACKS = ("memory", "user", "daily")

#: Tracks that require a project. Refused rather than defaulted: inventing a
#: project name is how cross-project isolation silently breaks.
PROJECT_TRACKS = ("key", "project")

#: Only the key track is branch scoped (``[branch:main]`` in evolve's vocabulary).
BRANCH_SCOPED_TRACKS = ("key",)

#: The separator used to build ``scope_key``. Values containing it are refused so
#: a scope key can never be ambiguous.
SCOPE_SEPARATOR = "|"


class LedgerError(Exception):
    """Base class for ledger failures, carrying a machine-readable code."""

    code = "ledger_error"

    def __init__(self, message: str, **details: object) -> None:
        """Store the message (a specific reason code) and optional structured details."""
        super().__init__(message)
        #: The specific reason, e.g. ``service_ledger_missing``. ``code`` stays the
        #: class-level exit-code class (unavailable / refused) so consumers can rely
        #: on a stable pair.
        self.detail_code = message
        self.details = details


class LedgerUnavailableError(LedgerError):
    """Cannot judge: the database is missing, uninitialised or unusable.

    Maps to exit 2. This is the class that keeps "no database" from reading as
    "empty ledger" — the distinction the core tool learned the hard way for
    ``admin doctor`` (a read-only diagnostic must not create a missing file).
    """

    code = "ledger_unavailable"


class LedgerRefusedError(LedgerError):
    """A real refusal: the target exists but the operation is not allowed."""

    code = "ledger_refused"


DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS units (
    id TEXT PRIMARY KEY,
    track TEXT NOT NULL CHECK (track IN ('memory','user','key','project','daily')),
    project TEXT,
    branch_scope TEXT,
    scope_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active','archived','deprecated')),
    slot TEXT,
    superseded_by TEXT REFERENCES units(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- Extensions beyond the minimum column set: the day a daily-track entry
    -- belongs to, and the provenance that makes imports idempotent.
    day TEXT,
    origin TEXT NOT NULL DEFAULT 'cli',
    origin_key TEXT
);

CREATE INDEX IF NOT EXISTS units_scope ON units(scope_key, state);
CREATE INDEX IF NOT EXISTS units_track ON units(track, project, state);
CREATE INDEX IF NOT EXISTS units_slot ON units(scope_key, slot, state);
CREATE UNIQUE INDEX IF NOT EXISTS units_origin_key
    ON units(origin_key) WHERE origin_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS revisions (
    revision_id TEXT PRIMARY KEY,
    unit_id TEXT NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    content TEXT NOT NULL,
    digest TEXT NOT NULL,
    claim_status TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (unit_id, revision)
);

CREATE INDEX IF NOT EXISTS revisions_unit ON revisions(unit_id, revision DESC);
CREATE INDEX IF NOT EXISTS revisions_digest ON revisions(digest);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    unit_id TEXT,
    revision_id TEXT,
    actor TEXT NOT NULL DEFAULT 'cli',
    at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS events_unit ON events(unit_id, id);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, id);

CREATE VIRTUAL TABLE IF NOT EXISTS units_fts
    USING fts5(unit_id UNINDEXED, text, tokenize='trigram');

CREATE TABLE IF NOT EXISTS cjk_bigrams (
    term TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    PRIMARY KEY (term, unit_id)
);

CREATE INDEX IF NOT EXISTS cjk_bigrams_unit ON cjk_bigrams(unit_id);
"""


def resolve_service_db(explicit: str | None = None) -> str:
    """Resolve the ledger path: explicit argument, then env override, then default."""
    if explicit:
        return os.path.expanduser(explicit)
    env_value = os.environ.get(DB_ENV)
    if env_value:
        return os.path.expanduser(env_value)
    return os.path.expanduser(DEFAULT_DB_PATH)


def connect(db_path: str, create: bool = False) -> sqlite3.Connection:
    """Open the ledger.

    Args:
        db_path: Resolved filesystem path.
        create: Allow creating the file and installing the schema. Write commands
            pass ``True``; read commands pass ``False`` so a missing ledger is
            reported instead of fabricated.

    Returns:
        An open connection with ``foreign_keys`` on and WAL enabled.

    Raises:
        LedgerUnavailableError: The database does not exist (and ``create`` is false),
            or the schema is missing/newer than this code.
    """
    path = Path(db_path)
    exists = path.exists()
    if not exists and not create:
        raise LedgerUnavailableError(
            "service_ledger_missing",
            db=db_path,
            hint=f"create it with `los-memory mem add ...` or `los-memory mem init --db {db_path}`",
        )
    if exists:
        try:
            conn = sqlite3.connect(str(path), timeout=30.0)
        except sqlite3.Error as exc:  # pragma: no cover - filesystem level failure
            raise LedgerUnavailableError(
                "service_ledger_unopenable", db=db_path, detail=str(exc)
            ) from exc
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        init_schema(conn)
    except LedgerError:
        conn.close()
        raise
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Install or verify the schema.

    Raises:
        LedgerUnavailableError: FTS5 with the ``trigram`` tokenizer is unavailable (the
            search contract cannot be met), or the stored schema is newer than
            this code (refuse to downgrade silently).
    """
    try:
        conn.executescript(DDL)
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "trigram" in message or "no such module" in message or "fts5" in message:
            raise LedgerUnavailableError(
                "fts5_trigram_unavailable",
                detail=str(exc),
                hint="the ledger needs SQLite built with FTS5 and the trigram tokenizer",
            ) from exc
        raise LedgerUnavailableError("service_ledger_schema_failed", detail=str(exc)) from exc
    current = schema_version(conn)
    if current is None:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    elif current > SCHEMA_VERSION:
        raise LedgerUnavailableError(
            "service_ledger_schema_newer",
            stored=current,
            code_version=SCHEMA_VERSION,
        )
    elif current < SCHEMA_VERSION:
        raise LedgerUnavailableError(
            "service_ledger_schema_older",
            stored=current,
            code_version=SCHEMA_VERSION,
            hint="this runtime has no migration from that version",
        )


def schema_version(conn: sqlite3.Connection) -> int | None:
    """Return the stored schema version, or ``None`` when the ledger is uninitialised."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):  # pragma: no cover - corrupt meta row
        return None
