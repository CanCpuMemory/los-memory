"""Unit tests for dual-write manager.

Tests for DualWriteManager with thread-safe operations and mode handling.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from memory_tool.migrate_out.approval.config import DualWriteConfig, DualWriteMode
from memory_tool.migrate_out.approval.dual_write import (
    DualWriteError,
    DualWriteManager,
    DualWriteResult,
)


class TestDualWriteResult:
    """Tests for DualWriteResult dataclass."""

    def test_success_result(self):
        """Test creating a successful result."""
        result = DualWriteResult(
            success=True,
            local_success=True,
            remote_success=True,
            local_result={"id": "123", "status": "ok"},
            remote_result={"id": "123", "status": "ok"},
        )

        assert result.success is True
        assert result.local_success is True
        assert result.remote_success is True
        assert result.local_result == {"id": "123", "status": "ok"}
        assert result.error_message is None

    def test_failure_result(self):
        """Test creating a failed result."""
        result = DualWriteResult(
            success=False,
            local_success=True,
            remote_success=False,
            local_result={"id": "123"},
            remote_result={"error": "Connection failed"},
            error_message="Remote: Connection failed",
        )

        assert result.success is False
        assert result.local_success is True
        assert result.remote_success is False
        assert result.error_message == "Remote: Connection failed"

    def test_to_dict(self):
        """Test converting result to dictionary."""
        result = DualWriteResult(
            success=True,
            local_success=True,
            remote_success=True,
            local_result={"id": "123"},
            remote_result={"id": "123"},
        )

        data = result.to_dict()

        assert data["success"] is True
        assert data["local_success"] is True
        assert data["remote_success"] is True
        assert data["local_result"] == {"id": "123"}


class TestDualWriteError:
    """Tests for DualWriteError exception."""

    def test_error_creation(self):
        """Test creating a dual-write error."""
        error = DualWriteError(
            message="Operation failed",
            local_result={"id": "123"},
            remote_result={"error": "timeout"},
        )

        assert str(error) == "Operation failed"
        assert error.local_result == {"id": "123"}
        assert error.remote_result == {"error": "timeout"}


class TestDualWriteManagerInitialization:
    """Tests for DualWriteManager initialization."""

    def test_initialization(self, tmp_path):
        """Test initialization with parameters."""
        db_path = tmp_path / "test.db"
        config = DualWriteConfig(mode=DualWriteMode.STRICT)

        # Create mock remote client
        mock_remote = MagicMock()

        # Create connection factory
        def conn_factory():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            return conn

        manager = DualWriteManager(
            config=config,
            local_conn=conn_factory,
            remote_client=mock_remote,
        )

        assert manager.config == config
        assert manager.remote_client == mock_remote
        assert manager._conn_factory is not None


class TestDualWriteManagerModes:
    """Tests for dual-write mode behaviors."""

    @pytest.fixture
    def setup_manager(self, tmp_path):
        """Fixture to create manager with test database."""
        db_path = tmp_path / "test.db"

        def conn_factory():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            # Create approval_requests table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approval_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL UNIQUE,
                    command TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending_approval',
                    version INTEGER DEFAULT 0,
                    requested_by TEXT,
                    approved_by TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    context TEXT DEFAULT '{}'
                )
            """)
            conn.commit()
            return conn

        mock_remote = MagicMock()
        return conn_factory, mock_remote, db_path

    def test_strict_mode_both_succeed(self, setup_manager):
        """Test STRICT mode when both writes succeed."""
        conn_factory, mock_remote, _ = setup_manager
        config = DualWriteConfig(mode=DualWriteMode.STRICT)

        mock_remote.create_request.return_value = {
            "success": True,
            "job_id": "job-123",
        }

        manager = DualWriteManager(
            config=config,
            local_conn=conn_factory,
            remote_client=mock_remote,
        )

        result = manager.create_request(
            job_id="job-123",
            command="deploy",
            risk_level="high",
        )

        assert result.success is True
        assert result.local_success is True
        assert result.remote_success is True
        mock_remote.create_request.assert_called_once()

    def test_strict_mode_local_fails(self, setup_manager):
        """Test STRICT mode when local write fails."""
        conn_factory, mock_remote, _ = setup_manager
        config = DualWriteConfig(mode=DualWriteMode.STRICT)

        # Create broken connection factory that always fails
        def broken_conn_factory():
            raise sqlite3.Error("Database locked")

        manager = DualWriteManager(
            config=config,
            local_conn=broken_conn_factory,
            remote_client=mock_remote,
        )

        result = manager.create_request(
            job_id="job-123",
            command="deploy",
            risk_level="high",
        )

        assert result.success is False
        assert result.local_success is False
        # Remote may be called (parallel execution), but overall result is failure
        # in STRICT mode when local fails

    def test_local_preferred_mode_remote_fails(self, setup_manager):
        """Test LOCAL_PREFERRED mode when remote fails."""
        conn_factory, mock_remote, _ = setup_manager
        config = DualWriteConfig(mode=DualWriteMode.LOCAL_PREFERRED)

        mock_remote.create_request.side_effect = Exception("Remote unavailable")

        manager = DualWriteManager(
            config=config,
            local_conn=conn_factory,
            remote_client=mock_remote,
        )

        result = manager.create_request(
            job_id="job-123",
            command="deploy",
            risk_level="high",
        )

        # Should succeed because local succeeded
        assert result.success is True
        assert result.local_success is True
        assert result.remote_success is False

    def test_remote_preferred_mode_prioritizes_remote(self, setup_manager):
        """Test REMOTE_PREFERRED mode returns remote data."""
        conn_factory, mock_remote, _ = setup_manager
        config = DualWriteConfig(mode=DualWriteMode.REMOTE_PREFERRED)

        mock_remote.create_request.return_value = {
            "success": True,
            "job_id": "job-123",
            "remote_data": "from_remote",
        }

        manager = DualWriteManager(
            config=config,
            local_conn=conn_factory,
            remote_client=mock_remote,
        )

        result = manager.create_request(
            job_id="job-123",
            command="deploy",
            risk_level="high",
        )

        assert result.success is True
        assert result.remote_result is not None

    def test_read_only_mode(self, setup_manager):
        """Test READ_ONLY mode doesn't call remote."""
        conn_factory, mock_remote, _ = setup_manager
        config = DualWriteConfig(mode=DualWriteMode.READ_ONLY)

        manager = DualWriteManager(
            config=config,
            local_conn=conn_factory,
            remote_client=mock_remote,
        )

        result = manager.create_request(
            job_id="job-123",
            command="deploy",
            risk_level="high",
        )

        assert result.success is False
        assert "Read-only mode" in result.error_message
        mock_remote.create_request.assert_not_called()


class TestDualWriteManagerThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_local_reads(self, tmp_path):
        """Test concurrent reads don't cause errors."""
        db_path = tmp_path / "test.db"

        def conn_factory():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("""
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
            """)
            conn.commit()
            return conn

        mock_remote = MagicMock()
        config = DualWriteConfig(mode=DualWriteMode.LOCAL_PREFERRED)
        manager = DualWriteManager(
            config=config,
            local_conn=conn_factory,
            remote_client=mock_remote,
        )

        # Write initial data
        for i in range(10):
            manager.create_request(
                job_id=f"job-{i}",
                command=f"cmd-{i}",
                risk_level="medium",
            )

        results = []
        errors = []

        def get_status(job_id: str):
            try:
                result = manager.get_request_status(job_id)
                results.append(result)
            except Exception as e:
                errors.append(e)

        # Concurrent reads
        with ThreadPoolExecutor(max_workers=5) as executor:
            for i in range(10):
                executor.submit(get_status, f"job-{i}")

        assert len(errors) == 0
        assert len(results) == 10

    def test_thread_local_connections(self, tmp_path):
        """Test each thread gets its own connection."""
        db_path = tmp_path / "test.db"

        def conn_factory():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approval_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL
                )
            """)
            conn.commit()
            return conn

        mock_remote = MagicMock()
        config = DualWriteConfig(mode=DualWriteMode.LOCAL_PREFERRED)
        manager = DualWriteManager(
            config=config,
            local_conn=conn_factory,
            remote_client=mock_remote,
        )

        connections = {}

        def get_connection(thread_name: str):
            conn = manager._get_connection()
            connections[thread_name] = id(conn)

        # Get connections from multiple threads
        threads = [
            threading.Thread(target=get_connection, args=(f"thread-{i}",))
            for i in range(5)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each thread should have a different connection
        assert len(set(connections.values())) == 5


class TestDualWriteManagerStatusQueries:
    """Tests for status query methods."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Fixture for configured manager."""
        db_path = tmp_path / "test.db"

        def conn_factory():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("""
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
            """)
            conn.commit()
            return conn

        mock_remote = MagicMock()
        config = DualWriteConfig(mode=DualWriteMode.LOCAL_PREFERRED)
        return DualWriteManager(
            config=config,
            local_conn=conn_factory,
            remote_client=mock_remote,
        )

    def test_get_request_status_prefers_local(self, manager):
        """Test get_request_status prefers local by default."""
        # Create a request first
        manager.create_request(
            job_id="job-123",
            command="deploy",
            risk_level="high",
        )

        result = manager.get_request_status("job-123")

        assert result is not None
        assert result["request"]["job_id"] == "job-123"

    def test_list_requests(self, manager):
        """Test list_requests returns results."""
        # Create some requests
        for i in range(5):
            manager.create_request(
                job_id=f"job-{i}",
                command=f"cmd-{i}",
                risk_level="medium",
            )

        result = manager.list_requests(limit=10)

        assert "requests" in result or "items" in result or "count" in result

    def test_get_migration_statistics(self, manager):
        """Test get_migration_statistics returns stats."""
        # Create a request
        manager.create_request(
            job_id="job-123",
            command="deploy",
            risk_level="high",
        )

        stats = manager.get_migration_statistics()

        assert "mode" in stats
        assert "local_requests_count" in stats
        assert "remote_requests_count" in stats
        assert "sync_needed" in stats
