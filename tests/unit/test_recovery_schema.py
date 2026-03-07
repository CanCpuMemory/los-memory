"""Tests for Phase 2 L1自动恢复系统数据库Schema v9."""
import pytest


class TestRecoverySchemaV9:
    """Test v9 schema migration for auto-recovery system."""

    def test_recovery_actions_table_exists(self, db_connection):
        """Verify recovery_actions table is created."""
        tables = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "recovery_actions" in table_names

    def test_recovery_executions_table_exists(self, db_connection):
        """Verify recovery_executions table is created."""
        tables = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "recovery_executions" in table_names

    def test_recovery_policies_table_exists(self, db_connection):
        """Verify recovery_policies table is created."""
        tables = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "recovery_policies" in table_names

    def test_recovery_actions_columns(self, db_connection):
        """Verify recovery_actions has required columns."""
        columns = db_connection.execute(
            "PRAGMA table_info(recovery_actions)"
        ).fetchall()
        column_names = {c[1] for c in columns}
        required = {'id', 'name', 'action_type', 'config', 'description', 'enabled', 'created_at', 'updated_at'}
        assert required.issubset(column_names)

    def test_recovery_executions_columns(self, db_connection):
        """Verify recovery_executions has required columns."""
        columns = db_connection.execute(
            "PRAGMA table_info(recovery_executions)"
        ).fetchall()
        column_names = {c[1] for c in columns}
        required = {'id', 'incident_id', 'action_id', 'status', 'started_at', 'completed_at', 'output_text', 'error_message', 'retry_count'}
        assert required.issubset(column_names)

    def test_recovery_policies_columns(self, db_connection):
        """Verify recovery_policies has required columns."""
        columns = db_connection.execute(
            "PRAGMA table_info(recovery_policies)"
        ).fetchall()
        column_names = {c[1] for c in columns}
        required = {'id', 'trigger_id', 'trigger_type', 'action_ids', 'execution_strategy', 'timeout_seconds', 'enabled'}
        assert required.issubset(column_names)

    def test_recovery_executions_indexes(self, db_connection):
        """Verify recovery execution indexes exist."""
        indexes = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = {i[0] for i in indexes}
        assert "idx_recovery_executions_incident" in index_names
        assert "idx_recovery_executions_status" in index_names

    def test_recovery_policies_index(self, db_connection):
        """Verify recovery policy index exists."""
        indexes = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = {i[0] for i in indexes}
        assert "idx_recovery_policies_trigger" in index_names

    def test_default_recovery_actions_inserted(self, db_connection):
        """Verify default recovery actions are pre-inserted."""
        actions = db_connection.execute(
            "SELECT name FROM recovery_actions"
        ).fetchall()
        action_names = {a[0] for a in actions}
        expected = {'restart_service', 'clear_cache', 'send_alert', 'switch_database'}
        assert expected.issubset(action_names)

    def test_recovery_actions_unique_name(self, db_connection):
        """Verify recovery action name is unique."""
        # Try to insert duplicate
        from memory_tool.utils import utc_now
        now = utc_now()
        with pytest.raises(Exception):
            db_connection.execute(
                """
                INSERT INTO recovery_actions (name, action_type, config, description, created_at, updated_at)
                VALUES ('restart_service', 'test', '{}', 'test', ?, ?)
                """,
                (now, now)
            )

    def test_schema_version_updated(self, db_connection):
        """Verify schema version is set to 9."""
        from memory_tool.database import get_schema_version
        version = get_schema_version(db_connection)
        assert version >= 9
