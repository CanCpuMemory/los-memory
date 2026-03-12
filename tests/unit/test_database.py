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

    def test_connect_enables_foreign_keys(self, temp_db_path):
        """Test that foreign key enforcement is enabled on connection."""
        conn = connect_db(str(temp_db_path))
        result = conn.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 1
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

    def test_foreign_key_cascade_on_observation_links(self, db_connection):
        """Verify cascading deletes on observation links with FK enforcement."""
        from memory_tool.operations import add_observation
        from memory_tool.utils import tags_to_json, tags_to_text

        first_id = add_observation(
            db_connection,
            "2024-01-01T00:00:00Z",
            "test",
            "note",
            "first",
            "first",
            tags_to_json([]),
            tags_to_text([]),
            "",
        )
        second_id = add_observation(
            db_connection,
            "2024-01-01T00:01:00Z",
            "test",
            "note",
            "second",
            "second",
            tags_to_json([]),
            tags_to_text([]),
            "",
        )

        db_connection.execute(
            """
            INSERT INTO observation_links (from_id, to_id, link_type, created_at)
            VALUES (?, ?, 'related', '2024-01-01T00:02:00Z')
            """,
            (first_id, second_id),
        )
        db_connection.commit()

        db_connection.execute("DELETE FROM observations WHERE id = ?", (first_id,))
        db_connection.commit()

        remaining = db_connection.execute(
            "SELECT COUNT(*) FROM observation_links WHERE from_id = ? OR to_id = ?",
            (first_id, first_id),
        ).fetchone()[0]
        assert remaining == 0

    def test_sessions_status_constraint_rejects_invalid_value(self, db_connection):
        """Verify sessions.status enforces expected enum values."""
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO sessions
                (start_time, end_time, project, working_dir, agent_type, summary, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "2024-01-01T00:00:00Z",
                    None,
                    "test",
                    "/tmp",
                    "codex",
                    "",
                    "invalid_status",
                ),
            )

    def test_observation_links_constraint_rejects_invalid_link_type(self, db_connection):
        """Verify observation_links.link_type enforces expected enum values."""
        from memory_tool.operations import add_observation
        from memory_tool.utils import tags_to_json, tags_to_text

        first_id = add_observation(
            db_connection,
            "2024-01-01T00:00:00Z",
            "test",
            "note",
            "first",
            "first",
            tags_to_json([]),
            tags_to_text([]),
            "",
        )
        second_id = add_observation(
            db_connection,
            "2024-01-01T00:01:00Z",
            "test",
            "note",
            "second",
            "second",
            tags_to_json([]),
            tags_to_text([]),
            "",
        )

        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO observation_links (from_id, to_id, link_type, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (first_id, second_id, "invalid_type", "2024-01-01T00:02:00Z"),
            )

    def test_feedback_action_constraint_rejects_invalid_action(self, db_connection):
        """Verify feedback_log.action_type enforces expected enum values."""
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO feedback_log
                (target_observation_id, action_type, feedback_text, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (1, "invalid_action", "test", "2024-01-01T00:00:00Z", "{}"),
            )

    def test_incident_observations_constraint_rejects_invalid_link_type(self, db_connection):
        """Verify incident_observations.link_type enforces expected enum values."""
        from memory_tool.operations import add_observation
        from memory_tool.utils import tags_to_json, tags_to_text

        observation_id = add_observation(
            db_connection,
            "2024-01-01T00:00:00Z",
            "test",
            "note",
            "first",
            "first",
            tags_to_json([]),
            tags_to_text([]),
            "",
        )
        incident_id = db_connection.execute(
            """
            INSERT INTO incidents
            (incident_type, severity, status, title, description, context_snapshot, detected_at, project)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("error", "p1", "detected", "Incident", "desc", "{}", "2024-01-01T00:00:00Z", "test"),
        ).lastrowid
        db_connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO incident_observations
                (incident_id, observation_id, link_type, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (incident_id, observation_id, "invalid_link_type", "2024-01-01T00:01:00Z"),
            )

    def test_recovery_actions_constraint_rejects_invalid_action_type(self, db_connection):
        """Verify recovery_actions.action_type enforces expected enum values."""
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO recovery_actions
                (name, action_type, config, description, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "invalid-action-type-test",
                    "invalid_action_type",
                    "{}",
                    "test",
                    1,
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z",
                ),
            )

    def test_incidents_status_constraint_rejects_invalid_value(self, db_connection):
        """Verify incidents.status enforces expected enum values."""
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO incidents
                (incident_type, severity, status, title, description, context_snapshot, detected_at, project)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "error",
                    "p1",
                    "invalid_status",
                    "Bad Incident",
                    "invalid status test",
                    "{}",
                    "2024-01-01T00:00:00Z",
                    "test",
                ),
            )

    def test_incidents_type_constraint_rejects_invalid_value(self, db_connection):
        """Verify incidents.incident_type enforces expected enum values."""
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO incidents
                (incident_type, severity, status, title, description, context_snapshot, detected_at, project)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "invalid_type",
                    "p1",
                    "detected",
                    "Bad Incident",
                    "invalid type test",
                    "{}",
                    "2024-01-01T00:00:00Z",
                    "test",
                ),
            )

    def test_incidents_severity_constraint_rejects_invalid_value(self, db_connection):
        """Verify incidents.severity enforces expected enum values."""
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO incidents
                (incident_type, severity, status, title, description, context_snapshot, detected_at, project)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "error",
                    "invalid_severity",
                    "detected",
                    "Bad Incident",
                    "invalid severity test",
                    "{}",
                    "2024-01-01T00:00:00Z",
                    "test",
                ),
            )

    def test_recovery_executions_status_constraint_rejects_invalid_value(self, db_connection):
        """Verify recovery_executions.status enforces expected enum values."""
        incident_id = db_connection.execute(
            """
            INSERT INTO incidents
            (incident_type, severity, status, title, description, context_snapshot, detected_at, project)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("error", "p1", "detected", "Incident", "desc", "{}", "2024-01-01T00:00:00Z", "test"),
        ).lastrowid
        db_connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO recovery_executions
                (incident_id, action_id, status, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (incident_id, None, "invalid_status", "2024-01-01T00:01:00Z"),
            )

    def test_recovery_policies_strategy_constraint_rejects_invalid_value(self, db_connection):
        """Verify recovery_policies.execution_strategy enforces expected enum values."""
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO recovery_policies
                (trigger_id, trigger_type, action_ids, execution_strategy,
                 timeout_seconds, enabled, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "trigger-bad-strategy",
                    "threshold",
                    "[]",
                    "invalid_strategy",
                    300,
                    1,
                    "bad strategy",
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z",
                ),
            )

    def test_recovery_policies_trigger_type_constraint_rejects_invalid_value(self, db_connection):
        """Verify recovery_policies.trigger_type enforces expected enum values."""
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO recovery_policies
                (trigger_id, trigger_type, action_ids, execution_strategy,
                 timeout_seconds, enabled, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "trigger-bad-type",
                    "invalid_trigger_type",
                    "[]",
                    "sequential",
                    300,
                    1,
                    "bad trigger type",
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z",
                ),
            )

    def test_approval_requests_status_constraint_rejects_invalid_value(self, db_connection):
        """Verify approval_requests.status enforces expected enum values."""
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO approval_requests
                (job_id, command, risk_level, status, version, created_at, updated_at, expires_at, context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "job-invalid-status",
                    "echo test",
                    "medium",
                    "invalid_status",
                    1,
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z",
                    "2024-01-03T00:00:00Z",
                    "{}",
                ),
            )

    def test_approval_requests_risk_level_constraint_rejects_invalid_value(self, db_connection):
        """Verify approval_requests.risk_level enforces expected enum values."""
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO approval_requests
                (job_id, command, risk_level, status, version, created_at, updated_at, expires_at, context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "job-invalid-risk",
                    "echo test",
                    "invalid_risk",
                    "pending",
                    1,
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z",
                    "2024-01-03T00:00:00Z",
                    "{}",
                ),
            )

    def test_approval_audit_status_constraints_reject_invalid_values(self, db_connection):
        """Verify approval_audit_log status columns enforce expected enum values."""
        request_id = db_connection.execute(
            """
            INSERT INTO approval_requests
            (job_id, command, risk_level, status, version, created_at, updated_at, expires_at, context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-audit-status",
                "echo ok",
                "medium",
                "pending",
                1,
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
                "2024-01-03T00:00:00Z",
                "{}",
            ),
        ).lastrowid
        db_connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO approval_audit_log
                (request_id, action, actor_id, previous_status, new_status, version, reason, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    "approved",
                    "user-1",
                    "pending",
                    "invalid_status",
                    2,
                    "test",
                    "2024-01-01T00:00:01Z",
                ),
            )

    def test_approval_audit_action_constraint_rejects_invalid_value(self, db_connection):
        """Verify approval_audit_log.action enforces expected enum values."""
        request_id = db_connection.execute(
            """
            INSERT INTO approval_requests
            (job_id, command, risk_level, status, version, created_at, updated_at, expires_at, context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-audit-action",
                "echo ok",
                "medium",
                "pending",
                1,
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
                "2024-01-03T00:00:00Z",
                "{}",
            ),
        ).lastrowid
        db_connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO approval_audit_log
                (request_id, action, actor_id, previous_status, new_status, version, reason, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    "invalid_action",
                    "user-1",
                    None,
                    "pending",
                    1,
                    "test",
                    "2024-01-01T00:00:01Z",
                ),
            )

    def test_migrate_v15_normalizes_invalid_enum_values(self, temp_db_path):
        """Verify v15 migration normalizes legacy invalid enum values."""
        conn = sqlite3.connect(str(temp_db_path))
        conn.row_factory = sqlite3.Row

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO meta (key, value) VALUES ('schema_version', '14')
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
            CREATE TABLE IF NOT EXISTS observation_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id INTEGER NOT NULL,
                to_id INTEGER NOT NULL,
                link_type TEXT NOT NULL DEFAULT 'related',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_observation_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                feedback_text TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )

        conn.execute(
            """
            INSERT INTO sessions
            (start_time, end_time, project, working_dir, agent_type, summary, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("2024-01-01T00:00:00Z", None, "test", "/tmp", "codex", "", "legacy_bad_status"),
        )
        conn.execute(
            """
            INSERT INTO observation_links (from_id, to_id, link_type, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (1, 2, "legacy_bad_link_type", "2024-01-01T00:00:00Z"),
        )
        conn.execute(
            """
            INSERT INTO feedback_log
            (target_observation_id, action_type, feedback_text, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (1, "legacy_bad_action", "old feedback", "2024-01-01T00:00:00Z", "{}"),
        )
        conn.commit()

        migrate_schema(conn)
        conn.commit()

        session_status = conn.execute("SELECT status FROM sessions LIMIT 1").fetchone()["status"]
        assert session_status == "active"
        link_type = conn.execute("SELECT link_type FROM observation_links LIMIT 1").fetchone()["link_type"]
        assert link_type == "related"
        action_type = conn.execute("SELECT action_type FROM feedback_log LIMIT 1").fetchone()["action_type"]
        assert action_type == "supplement"

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO feedback_log
                (target_observation_id, action_type, feedback_text, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (1, "still_bad", "new feedback", "2024-01-01T00:00:01Z", "{}"),
            )

        conn.close()

    def test_migrate_v16_normalizes_invalid_status_values(self, temp_db_path):
        """Verify v16 migration normalizes legacy invalid status values."""
        conn = sqlite3.connect(str(temp_db_path))
        conn.row_factory = sqlite3.Row

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', '15')"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'detected',
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                source_observation_id INTEGER,
                context_snapshot TEXT NOT NULL DEFAULT '{}',
                detected_at TEXT NOT NULL,
                resolved_at TEXT,
                project TEXT NOT NULL DEFAULT 'general'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL,
                action_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                started_at TEXT,
                completed_at TEXT,
                output_text TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                retry_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                command TEXT NOT NULL,
                risk_level TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'pending',
                version INTEGER NOT NULL DEFAULT 1,
                requested_by TEXT,
                approved_by TEXT,
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                context TEXT DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                actor_id TEXT,
                previous_status TEXT,
                new_status TEXT NOT NULL,
                version INTEGER NOT NULL,
                reason TEXT,
                timestamp TEXT NOT NULL
            )
            """
        )

        incident_id = conn.execute(
            """
            INSERT INTO incidents
            (incident_type, severity, status, title, description, context_snapshot, detected_at, project)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("error", "p1", "legacy_bad_incident_status", "Incident", "desc", "{}", "2024-01-01T00:00:00Z", "test"),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO recovery_executions
            (incident_id, action_id, status, started_at, completed_at, output_text, error_message, retry_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                None,
                "legacy_bad_exec_status",
                "2024-01-01T00:01:00Z",
                "2024-01-01T00:02:00Z",
                "",
                "boom",
                0,
                "2024-01-01T00:01:00Z",
            ),
        )
        request_id = conn.execute(
            """
            INSERT INTO approval_requests
            (job_id, command, risk_level, status, version, created_at, updated_at, expires_at, context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-job",
                "echo test",
                "medium",
                "legacy_bad_approval_status",
                1,
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
                "2024-01-03T00:00:00Z",
                "{}",
            ),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO approval_audit_log
            (request_id, action, actor_id, previous_status, new_status, version, reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                "approved",
                "user-1",
                "legacy_bad_prev_status",
                "legacy_bad_new_status",
                2,
                "ok",
                "2024-01-01T00:00:01Z",
            ),
        )
        conn.commit()

        migrate_schema(conn)
        conn.commit()

        assert get_schema_version(conn) == SCHEMA_VERSION
        assert conn.execute("SELECT status FROM incidents").fetchone()["status"] == "detected"
        assert conn.execute("SELECT status FROM recovery_executions").fetchone()["status"] == "failed"
        assert conn.execute("SELECT status FROM approval_requests").fetchone()["status"] == "pending"
        audit_row = conn.execute(
            "SELECT previous_status, new_status FROM approval_audit_log LIMIT 1"
        ).fetchone()
        assert audit_row["previous_status"] is None
        assert audit_row["new_status"] == "approved"

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO incidents
                (incident_type, severity, status, title, description, context_snapshot, detected_at, project)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("error", "p1", "still_bad", "Another Incident", "desc", "{}", "2024-01-01T00:00:00Z", "test"),
            )

        conn.close()

    def test_migrate_v17_normalizes_invalid_non_status_enums(self, temp_db_path):
        """Verify v17 migration normalizes legacy invalid non-status enum values."""
        conn = sqlite3.connect(str(temp_db_path))
        conn.row_factory = sqlite3.Row

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', '16')"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'detected',
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                source_observation_id INTEGER,
                context_snapshot TEXT NOT NULL DEFAULT '{}',
                detected_at TEXT NOT NULL,
                resolved_at TEXT,
                project TEXT NOT NULL DEFAULT 'general'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_policies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_id TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                action_ids TEXT NOT NULL,
                execution_strategy TEXT DEFAULT 'sequential',
                timeout_seconds INTEGER DEFAULT 300,
                enabled BOOLEAN DEFAULT 1,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                command TEXT NOT NULL,
                risk_level TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'pending',
                version INTEGER NOT NULL DEFAULT 1,
                requested_by TEXT,
                approved_by TEXT,
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                context TEXT DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                actor_id TEXT,
                previous_status TEXT,
                new_status TEXT NOT NULL,
                version INTEGER NOT NULL,
                reason TEXT,
                timestamp TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            INSERT INTO incidents
            (incident_type, severity, status, title, description, context_snapshot, detected_at, project)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy_bad_type",
                "legacy_bad_severity",
                "detected",
                "Incident",
                "desc",
                "{}",
                "2024-01-01T00:00:00Z",
                "test",
            ),
        )
        conn.execute(
            """
            INSERT INTO recovery_policies
            (trigger_id, trigger_type, action_ids, execution_strategy, timeout_seconds, enabled, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "trigger-legacy",
                "threshold",
                "[]",
                "legacy_bad_strategy",
                300,
                1,
                "",
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
            ),
        )
        request_id = conn.execute(
            """
            INSERT INTO approval_requests
            (job_id, command, risk_level, status, version, created_at, updated_at, expires_at, context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-risk-job",
                "echo test",
                "legacy_bad_risk",
                "pending",
                1,
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
                "2024-01-03T00:00:00Z",
                "{}",
            ),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO approval_audit_log
            (request_id, action, actor_id, previous_status, new_status, version, reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                "legacy_bad_action",
                "user-1",
                None,
                "approved",
                2,
                "ok",
                "2024-01-01T00:00:01Z",
            ),
        )
        conn.commit()

        migrate_schema(conn)
        conn.commit()

        assert get_schema_version(conn) == SCHEMA_VERSION
        incident_row = conn.execute(
            "SELECT incident_type, severity FROM incidents LIMIT 1"
        ).fetchone()
        assert incident_row["incident_type"] == "error"
        assert incident_row["severity"] == "p2"
        assert conn.execute(
            "SELECT execution_strategy FROM recovery_policies LIMIT 1"
        ).fetchone()["execution_strategy"] == "sequential"
        assert conn.execute(
            "SELECT risk_level FROM approval_requests LIMIT 1"
        ).fetchone()["risk_level"] == "medium"
        assert conn.execute(
            "SELECT action FROM approval_audit_log LIMIT 1"
        ).fetchone()["action"] == "approved"

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO approval_requests
                (job_id, command, risk_level, status, version, created_at, updated_at, expires_at, context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "still-bad-risk-job",
                    "echo test",
                    "still_bad_risk",
                    "pending",
                    1,
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z",
                    "2024-01-03T00:00:00Z",
                    "{}",
                ),
            )

        conn.close()

    def test_migrate_v18_normalizes_invalid_followup_enums(self, temp_db_path):
        """Verify v18 migration normalizes follow-up enum-like fields."""
        conn = sqlite3.connect(str(temp_db_path))
        conn.row_factory = sqlite3.Row

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', '17')"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incident_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL,
                observation_id INTEGER NOT NULL,
                link_type TEXT NOT NULL DEFAULT 'related',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                action_type TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                description TEXT DEFAULT '',
                enabled BOOLEAN DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_policies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_id TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                action_ids TEXT NOT NULL,
                execution_strategy TEXT DEFAULT 'sequential',
                timeout_seconds INTEGER DEFAULT 300,
                enabled BOOLEAN DEFAULT 1,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            INSERT INTO incident_observations
            (incident_id, observation_id, link_type, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (1, 1, "legacy_bad_link_type", "2024-01-01T00:00:00Z"),
        )
        conn.execute(
            """
            INSERT INTO recovery_actions
            (name, action_type, config, description, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "switch_database",
                "database",
                "{}",
                "legacy database alias",
                1,
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO recovery_actions
            (name, action_type, config, description, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy_custom_action",
                "legacy_bad_action_type",
                "{}",
                "legacy custom action",
                1,
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO recovery_policies
            (trigger_id, trigger_type, action_ids, execution_strategy, timeout_seconds, enabled, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "trigger-legacy",
                "legacy_bad_trigger",
                "[]",
                "sequential",
                300,
                1,
                "",
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
            ),
        )
        conn.commit()

        migrate_schema(conn)
        conn.commit()

        assert get_schema_version(conn) == SCHEMA_VERSION
        assert conn.execute(
            "SELECT link_type FROM incident_observations LIMIT 1"
        ).fetchone()["link_type"] == "related"

        action_rows = conn.execute(
            "SELECT name, action_type FROM recovery_actions ORDER BY name"
        ).fetchall()
        actions_by_name = {row["name"]: row["action_type"] for row in action_rows}
        assert actions_by_name["switch_database"] == "switch_database"
        assert actions_by_name["legacy_custom_action"] == "shell"

        assert conn.execute(
            "SELECT trigger_type FROM recovery_policies LIMIT 1"
        ).fetchone()["trigger_type"] == "threshold"

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO recovery_actions
                (name, action_type, config, description, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "still-invalid-action",
                    "still_bad",
                    "{}",
                    "",
                    1,
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z",
                ),
            )

        conn.close()


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
