"""Database operations and schema management."""
from __future__ import annotations

import os
import sqlite3
from typing import TYPE_CHECKING

from .utils import ISO_FORMAT, utc_now

if TYPE_CHECKING:
    pass

SCHEMA_VERSION = 4


def connect_db(path: str) -> sqlite3.Connection:
    """Connect to SQLite database."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_meta_table(conn: sqlite3.Connection) -> None:
    """Ensure meta table exists."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version."""
    ensure_meta_table(conn)
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'",
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Set schema version."""
    ensure_meta_table(conn)
    conn.execute(
        """
        INSERT INTO meta (key, value)
        VALUES ('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(version),),
    )


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure database schema is up to date."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            project TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            tags TEXT NOT NULL,
            tags_text TEXT NOT NULL,
            raw TEXT NOT NULL,
            session_id INTEGER REFERENCES sessions(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            project TEXT NOT NULL,
            working_dir TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            summary TEXT DEFAULT '',
            status TEXT DEFAULT 'active'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            tag TEXT DEFAULT '',
            session_id INTEGER REFERENCES sessions(id),
            observation_count INTEGER DEFAULT 0,
            project TEXT DEFAULT ''
        )
        """
    )
    migrate_schema(conn)
    conn.commit()


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Migrate database to current schema version."""
    from .utils import normalize_tags_list, tags_to_json, tags_to_text

    version = get_schema_version(conn)
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {version} is newer than this tool supports "
            f"(max {SCHEMA_VERSION})."
        )

    if version < 1:
        set_schema_version(conn, 1)
        version = 1

    if version < 2:
        try:
            conn.execute(
                "ALTER TABLE observations ADD COLUMN tags_text TEXT NOT NULL DEFAULT ''",
            )
        except sqlite3.OperationalError:
            pass
        rows = conn.execute("SELECT id, tags FROM observations").fetchall()
        for row in rows:
            tags_list = normalize_tags_list(row["tags"])
            conn.execute(
                "UPDATE observations SET tags = ?, tags_text = ? WHERE id = ?",
                (tags_to_json(tags_list), tags_to_text(tags_list), row["id"]),
            )
        rebuild_fts(conn)
        set_schema_version(conn, 2)
        version = 2

    if version < 3:
        try:
            conn.execute(
                "ALTER TABLE observations ADD COLUMN session_id INTEGER REFERENCES sessions(id)"
            )
        except sqlite3.OperationalError:
            pass
        set_schema_version(conn, 3)
        version = 3

    if version < 4:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                tag TEXT DEFAULT '',
                session_id INTEGER REFERENCES sessions(id),
                observation_count INTEGER DEFAULT 0,
                project TEXT DEFAULT ''
            )
            """
        )
        set_schema_version(conn, 4)


def ensure_fts(conn: sqlite3.Connection) -> bool:
    """Ensure FTS5 virtual table and triggers exist."""
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts
            USING fts5(title, summary, tags_text, raw, content='observations', content_rowid='id')
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_ai
            AFTER INSERT ON observations BEGIN
                INSERT INTO observations_fts(rowid, title, summary, tags_text, raw)
                VALUES (new.id, new.title, new.summary, new.tags_text, new.raw);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_ad
            AFTER DELETE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, title, summary, tags_text, raw)
                VALUES ('delete', old.id, old.title, old.summary, old.tags_text, old.raw);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_au
            AFTER UPDATE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, title, summary, tags_text, raw)
                VALUES ('delete', old.id, old.title, old.summary, old.tags_text, old.raw);
                INSERT INTO observations_fts(rowid, title, summary, tags_text, raw)
                VALUES (new.id, new.title, new.summary, new.tags_text, new.raw);
            END;
            """
        )
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild FTS index."""
    conn.execute("DROP TRIGGER IF EXISTS observations_ai")
    conn.execute("DROP TRIGGER IF EXISTS observations_ad")
    conn.execute("DROP TRIGGER IF EXISTS observations_au")
    conn.execute("DROP TABLE IF EXISTS observations_fts")
    ensure_fts(conn)


def init_db(path: str) -> None:
    """Initialize database."""
    conn = connect_db(path)
    ensure_schema(conn)
    ensure_fts(conn)
    conn.close()
