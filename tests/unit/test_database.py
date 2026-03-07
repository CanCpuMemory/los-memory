"""Tests for database operations and schema management."""
import sqlite3

import pytest

from memory_tool.database import (
    SCHEMA_VERSION,
    connect_db,
    ensure_schema,
    ensure_fts,
    get_schema_version,
    set_schema_version,
    migrate_schema,
    rebuild_fts,
    optimize_connection,
)


class TestDatabaseConnection:
    """Test database connection functions."""

    def test_connect_creates_file(self, temp_db_path):
        """Test that connect_db creates the database file."""
        conn = connect_db(str(temp_db_path))
        assert temp_db_path.exists()
        conn.close()

    def test_connect_applies_optimizations(self, temp_db_path):
        """Test that connection optimizations are applied."""
        conn = connect_db(str(temp_db_path))
        cursor = conn.execute("PRAGMA journal_mode")
        result = cursor.fetchone()
        assert result[0] == "wal"  # WAL mode enabled
        conn.close()

    def test_connect_sets_row_factory(self, temp_db_path):
        """Test that row factory is set to sqlite3.Row."""
        conn = connect_db(str(temp_db_path))
        assert conn.row_factory == sqlite3.Row
        conn.close()

    def test_connect_creates_directory(self, tmp_path):
        """Test that connect_db creates parent directories."""
        db_path = tmp_path / "subdir" / "nested" / "memory.db"
        conn = connect_db(str(db_path))
        assert db_path.parent.exists()
        conn.close()


class TestSchemaManagement:
    """Test schema creation and migration."""

    def test_ensure_schema_creates_observations_table(self, db_connection):
        """Verify observations table is created."""
        tables = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "observations" in table_names

    def test_ensure_schema_creates_sessions_table(self, db_connection):
        """Verify sessions table is created."""
        tables = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "sessions" in table_names

    def test_ensure_schema_creates_checkpoints_table(self, db_connection):
        """Verify checkpoints table is created."""
        tables = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "checkpoints" in table_names

    def test_ensure_schema_creates_meta_table(self, db_connection):
        """Verify meta table is created."""
        tables = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "meta" in table_names

    def test_ensure_fts_creates_virtual_table(self, db_connection):
        """Verify FTS5 virtual table is created."""
        ensure_fts(db_connection)

        tables = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "observations_fts" in table_names

    def test_ensure_fts_creates_triggers(self, db_connection):
        """Verify FTS triggers are created."""
        ensure_fts(db_connection)

        triggers = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
        trigger_names = {t[0] for t in triggers}
        assert "observations_ai" in trigger_names
        assert "observations_ad" in trigger_names
        assert "observations_au" in trigger_names

    def test_schema_version_tracking(self, db_connection):
        """Test schema version get/set."""
        set_schema_version(db_connection, 5)
        version = get_schema_version(db_connection)
        assert version == 5

    def test_migrate_schema_updates_version(self, empty_db):
        """Test migration updates schema version."""
        conn = empty_db
        ensure_schema(conn)  # This calls migrate_schema

        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION


class TestFTSTriggers:
    """Test FTS trigger functionality."""

    def test_insert_trigger_updates_fts(self, db_connection):
        """Verify insert trigger updates FTS index."""
        ensure_fts(db_connection)

        # Insert observation
        db_connection.execute(
            """INSERT INTO observations
               (timestamp, project, kind, title, summary, tags, tags_text, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("2024-01-15T10:00:00Z", "test", "note", "Test Title",
             "Test Summary", "[]", "", "raw")
        )
        db_connection.commit()

        # Search via FTS
        result = db_connection.execute(
            "SELECT * FROM observations_fts WHERE observations_fts MATCH 'Test'"
        ).fetchall()

        assert len(result) > 0

    def test_delete_trigger_updates_fts(self, db_connection):
        """Verify delete trigger updates FTS index."""
        ensure_fts(db_connection)

        # Insert and then delete observation
        cursor = db_connection.execute(
            """INSERT INTO observations
               (timestamp, project, kind, title, summary, tags, tags_text, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("2024-01-15T10:00:00Z", "test", "note", "Test Title",
             "Test Summary", "[]", "", "raw")
        )
        obs_id = cursor.lastrowid
        db_connection.commit()

        # Delete
        db_connection.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
        db_connection.commit()

        # Search should find nothing
        result = db_connection.execute(
            "SELECT * FROM observations_fts WHERE observations_fts MATCH 'Test'"
        ).fetchall()

        assert len(result) == 0


class TestIndexes:
    """Test database indexes."""

    def test_performance_indexes_exist(self, db_connection):
        """Verify performance indexes are created."""
        indexes = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = {i[0] for i in indexes}

        # v7 migration indexes
        assert "idx_observations_project_timestamp" in index_names
        assert "idx_observations_project_kind_timestamp" in index_names
        assert "idx_observations_tags_text" in index_names


class TestRebuildFTS:
    """Test FTS rebuild functionality."""

    def test_rebuild_fts_recreates_table(self, db_connection):
        """Test rebuild_fts recreates FTS table."""
        ensure_fts(db_connection)

        # Insert test data
        db_connection.execute(
            """INSERT INTO observations
               (timestamp, project, kind, title, summary, tags, tags_text, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("2024-01-15T10:00:00Z", "test", "note", "Test Title",
             "Test Summary", "[]", "", "raw")
        )
        db_connection.commit()

        # Rebuild FTS
        rebuild_fts(db_connection)

        # Verify FTS still works
        result = db_connection.execute(
            "SELECT * FROM observations_fts WHERE observations_fts MATCH 'Test'"
        ).fetchall()
        assert len(result) > 0
