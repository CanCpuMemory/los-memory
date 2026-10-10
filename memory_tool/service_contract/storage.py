"""The declared - and deliberately **unexecuted** - storage schema for ``service.sqlite3``.

P2-01 owns the versioned schema declaration; P2-02 owns running it.  Nothing in this module
opens a connection, creates a file or migrates anything: it is a single source of truth so that
the design document and the future migration cannot drift apart silently, and so the declared
DDL can be checked for *SQL validity* in a throwaway in-memory database during tests.

Two defects were found in the §3 sketch while making the declaration executable, and both are
recorded in :data:`STORAGE_SCHEMA_ADDITIONS` rather than edited into the design document:
``source_events``'s ``UNIQUE(owner_id, client_event_id)`` named a column the table never declared
(and was coarser than §2.2's ``(principal, client_event_id)`` key), and ``project_aliases`` had no
place for the ``environment`` the architecture keeps distinct.  The drift gate lives in
``tests/unit/test_service_contract_contract.py``: every column of the §3 sketch must exist here,
and every column added here must be registered with a reason.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

#: Version of the isolated service database schema.  Bumped by an explicit migration only.
STORAGE_SCHEMA_VERSION = 1

#: The file P2-02 will create.  Never created by this layer.
SERVICE_DB_FILENAME = "service.sqlite3"

#: Connection disciplines carried over from the existing profile databases.
STORAGE_PRAGMAS: Tuple[str, ...] = (
    "PRAGMA foreign_keys=ON",
    "PRAGMA journal_mode=WAL",
)

#: Columns this declaration adds to the §3 sketch, and why.  A new column without an entry here
#: fails the drift gate in ``tests/unit/test_service_contract_contract.py``.
STORAGE_SCHEMA_ADDITIONS: Dict[str, Dict[str, str]] = {
    "source_events": {
        "principal_key_id": (
            "design §2.2 pins idempotency to (principal, client_event_id); the §3 sketch's "
            "UNIQUE(owner_id, client_event_id) referenced an undeclared column and keyed on the "
            "owner rather than the principal."
        ),
    },
    "project_aliases": {
        "environment": (
            "architecture §4 keeps a project's production, test and development configurations "
            "distinct; without the column two environments cannot both be registered."
        ),
    },
}

#: Declared DDL for the isolated service database (design §3 + the recorded additions).
STORAGE_DDL: Tuple[str, ...] = (
    """
    CREATE TABLE principals (
        key_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        hashed_secret TEXT NOT NULL,
        device_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        revoked_at TEXT
    )
    """,
    """
    CREATE TABLE grants (
        owner_id TEXT NOT NULL,
        space_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        action TEXT NOT NULL,
        expires_at TEXT,
        PRIMARY KEY (owner_id, space_id, project_id, action)
    )
    """,
    """
    CREATE TABLE projects (
        project_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        space_id TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE project_aliases (
        project_id TEXT NOT NULL,
        alias_kind TEXT NOT NULL,
        alias_value TEXT NOT NULL,
        environment TEXT,
        PRIMARY KEY (alias_kind, alias_value, environment)
    )
    """,
    """
    CREATE TABLE source_events (
        server_seq INTEGER PRIMARY KEY AUTOINCREMENT,
        principal_key_id TEXT NOT NULL,
        client_event_id TEXT NOT NULL,
        source_instance TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        source_seq INTEGER NOT NULL,
        role TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        received_at TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        UNIQUE (principal_key_id, client_event_id)
    )
    """,
    """
    CREATE TABLE sources (
        source_id TEXT PRIMARY KEY,
        revision TEXT NOT NULL,
        uri TEXT NOT NULL,
        permission TEXT NOT NULL,
        chunker_version TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE memory_units (
        memory_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        space_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        visibility TEXT NOT NULL,
        current_revision TEXT,
        lifecycle TEXT NOT NULL,
        imported_read_only INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE memory_revisions (
        revision_id TEXT PRIMARY KEY,
        memory_id TEXT NOT NULL,
        parent_revision TEXT,
        content TEXT NOT NULL,
        claim_status TEXT NOT NULL DEFAULT 'undeclared',
        valid_from TEXT,
        valid_to TEXT,
        recorded_at TEXT NOT NULL,
        supersedes TEXT,
        contradicts TEXT,
        extractor_version TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE evidence_links (
        revision_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_revision TEXT NOT NULL,
        span TEXT NOT NULL,
        relation TEXT NOT NULL,
        source_family TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE jobs (
        job_id TEXT PRIMARY KEY,
        revision_id TEXT,
        operation TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        lease_until TEXT,
        attempt INTEGER NOT NULL DEFAULT 0,
        next_attempt TEXT,
        error_class TEXT,
        budget INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE index_manifests (
        projection TEXT NOT NULL,
        generation INTEGER NOT NULL,
        model_fingerprint TEXT NOT NULL,
        cursor TEXT,
        failures INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE retractions (
        memory_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        scope TEXT NOT NULL,
        created_at TEXT NOT NULL,
        receipt TEXT NOT NULL
    )
    """,
)

_CONSTRAINT_KEYWORDS = frozenset(
    {"CHECK", "CONSTRAINT", "FOREIGN", "KEY", "PK", "PRIMARY", "UNIQUE"}
)


def declared_columns(sql: str) -> Dict[str, Tuple[str, ...]]:
    """Parse ``table(column, ...)`` declarations out of a SQL fragment.

    Handles the design document's compact ``table(col, col, PK(...))`` form and ordinary
    ``CREATE TABLE`` statements.  Constraint entries (``PRIMARY KEY``, ``UNIQUE``, ``CHECK``,
    ``FOREIGN``, ``CONSTRAINT`` and the document's ``PK(...)`` shorthand) are skipped.

    Args:
        sql: One or more table declarations.

    Returns:
        Mapping of table name to column names, in declaration order.
    """
    tables: Dict[str, Tuple[str, ...]] = {}
    for statement in _statements(sql):
        name, body = statement
        columns: List[str] = []
        for item in _split_top_level(body):
            token = item.strip().split("(", 1)[0].split(" ", 1)[0].strip()
            if not token or token.upper() in _CONSTRAINT_KEYWORDS:
                continue
            columns.append(token)
        tables[name] = tuple(columns)
    return tables


def _statements(sql: str) -> List[Tuple[str, str]]:
    """Return ``(table_name, body)`` pairs found in a SQL fragment."""
    cleaned = re.sub(r"--[^\n]*", " ", sql)
    found: List[Tuple[str, str]] = []
    index = 0
    while True:
        start = cleaned.find("(", index)
        if start == -1:
            break
        head = cleaned[max(0, start - 60) : start]
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", head)
        if not tokens:
            index = start + 1
            continue
        name = tokens[-1]
        if name.upper() in {"TABLE", "IF"}:
            index = start + 1
            continue
        depth = 0
        end = -1
        for position in range(start, len(cleaned)):
            if cleaned[position] == "(":
                depth += 1
            elif cleaned[position] == ")":
                depth -= 1
                if depth == 0:
                    end = position
                    break
        if end == -1:
            break
        found.append((name.lower(), cleaned[start + 1 : end]))
        index = end + 1
    return found


def _split_top_level(body: str) -> List[str]:
    """Split a declaration body on top-level commas only."""
    items: List[str] = []
    depth = 0
    current = ""
    for character in body:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character == "," and depth == 0:
            items.append(current)
            current = ""
        else:
            current += character
    if current.strip():
        items.append(current)
    return items


def declared_tables() -> Dict[str, Tuple[str, ...]]:
    """Return the declared tables and their columns."""
    tables: Dict[str, Tuple[str, ...]] = {}
    for statement in STORAGE_DDL:
        tables.update(declared_columns(statement))
    return tables


__all__ = [
    "SERVICE_DB_FILENAME",
    "STORAGE_DDL",
    "STORAGE_SCHEMA_ADDITIONS",
    "STORAGE_PRAGMAS",
    "STORAGE_SCHEMA_VERSION",
    "declared_columns",
    "declared_tables",
]
