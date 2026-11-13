"""Writable memory ledger runtime (``service.sqlite3``).

Scope of this package, in one sentence: it is the **writer** and the **searchable
source of truth** for the DSH memory corpus (five evolve tracks), and it is a new
layer — ``memory_tool/service_contract/`` stays exactly as it was (a contract with
no runtime), and ``memory_tool/shadow*.py`` keeps its read-only semantics.

Public surface:

* :class:`memory_tool.ledger.store.Ledger` — units, revisions, lifecycle, search.
* :func:`memory_tool.ledger.import_evolve.import_evolve` — one-shot (idempotent)
  import of ``~/.dsh/memories``.
* :mod:`memory_tool.ledger.cli` — the ``los-memory mem ...`` command group.

Design: ``docs/design/m1-writable-ledger.md``.
"""
from __future__ import annotations

from .evolve_md import Entry, TrackFile, discover, parse_entry, split_entries
from .import_evolve import import_evolve
from .schema import (
    DB_ENV,
    DEFAULT_DB_PATH,
    SCHEMA_VERSION,
    STATES,
    TRACKS,
    LedgerError,
    LedgerRefusedError,
    LedgerUnavailableError,
    connect,
    init_schema,
    resolve_service_db,
)
from .store import Ledger

__all__ = [
    "DEFAULT_DB_PATH",
    "DB_ENV",
    "Entry",
    "Ledger",
    "LedgerError",
    "LedgerRefusedError",
    "LedgerUnavailableError",
    "SCHEMA_VERSION",
    "STATES",
    "TRACKS",
    "TrackFile",
    "connect",
    "discover",
    "import_evolve",
    "init_schema",
    "parse_entry",
    "resolve_service_db",
    "split_entries",
]
