"""Integration tests for adapter layer.

Tests for full adapter workflows and phase transitions.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from memory_tool.migrate_out.approval.adapter import ApprovalMigrationAdapter
from memory_tool.migrate_out.approval.config import (
    DualWriteConfig,
    DualWriteMode,
    HMACConfig,
    MigrationConfig,
    MigrationPhase,
    SSEProxyConfig,
    VPSAgentWebConfig,
)
from memory_tool.migrate_out.approval.dual_write import DualWriteManager
from memory_tool.migrate_out.approval.hmac_bridge import HMACBridge


class TestApprovalMigrationAdapterPhases:
    """Tests for adapter behavior across migration phases."""

    @pytest.fixture
    def local_conn(self, tmp_path):
        """Fixture for local SQLite connection."""
        db_path = tmp_path / "test.db"
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
        try:
            yield conn
        finally:
            conn.close()

    def test_local_only_routes_to_local(self, local_conn):
        """Test LOCAL_ONLY phase routes to local storage."""
        config = MigrationConfig(
            phase=MigrationPhase.LOCAL_ONLY,
            vps_agent_web=VPSAgentWebConfig(),
            hmac=HMACConfig(),
        )
        adapter = ApprovalMigrationAdapter(config, local_conn)

        # Should not have dual-write manager in LOCAL_ONLY
        assert adapter._dual_write is None
        # Should have local API
        assert adapter._local_api is not None

    def test_dual_write_has_dual_write_manager(self, local_conn, tmp_path):
        """Test DUAL_WRITE phase has dual-write manager."""
        config = MigrationConfig(
            phase=MigrationPhase.DUAL_WRITE,
            vps_agent_web=VPSAgentWebConfig(url="https://test.example.com"),
            hmac=HMACConfig(
                legacy_active_secret="test-secret",
                vps_active_secret="vps-secret",
            ),
            dual_write=DualWriteConfig(mode=DualWriteMode.STRICT),
        )
        adapter = ApprovalMigrationAdapter(config, local_conn)

        assert adapter._dual_write is not None
        assert isinstance(adapter._dual_write, DualWriteManager)

    def test_remote_only_uses_remote_client(self, local_conn):
        """Test REMOTE_ONLY phase uses remote client."""
        config = MigrationConfig(
            phase=MigrationPhase.REMOTE_ONLY,
            vps_agent_web=VPSAgentWebConfig(url="https://test.example.com"),
            hmac=HMACConfig(vps_active_secret="vps-secret"),
        )
        adapter = ApprovalMigrationAdapter(config, local_conn)

        # Should have HMAC bridge configured
        assert adapter._hmac_bridge is not None
        assert isinstance(adapter._hmac_bridge, HMACBridge)
        # Should have remote client
        assert adapter._remote_client is not None

    def test_phase_transition_local_to_dual(self, local_conn):
        """Test transitioning from LOCAL_ONLY to DUAL_WRITE."""
        # Start with LOCAL_ONLY
        config1 = MigrationConfig(
            phase=MigrationPhase.LOCAL_ONLY,
            vps_agent_web=VPSAgentWebConfig(),
            hmac=HMACConfig(),
        )
        adapter1 = ApprovalMigrationAdapter(config1, local_conn)

        assert adapter1.config.is_bridge_enabled() is False

        # Transition to DUAL_WRITE
        config2 = MigrationConfig(
            phase=MigrationPhase.DUAL_WRITE,
            vps_agent_web=VPSAgentWebConfig(url="https://test.example.com"),
            hmac=HMACConfig(
                legacy_active_secret="test-secret",
                vps_active_secret="secret",
            ),
        )
        adapter2 = ApprovalMigrationAdapter(config2, local_conn)

        assert adapter2.config.is_bridge_enabled() is True
        assert adapter2._dual_write is not None

    def test_phase_transition_dual_to_remote(self, local_conn):
        """Test transitioning from DUAL_WRITE to REMOTE_ONLY."""
        # Start with DUAL_WRITE
        config1 = MigrationConfig(
            phase=MigrationPhase.DUAL_WRITE,
            vps_agent_web=VPSAgentWebConfig(url="https://test.example.com"),
            hmac=HMACConfig(
                legacy_active_secret="test-secret",
                vps_active_secret="secret",
            ),
        )
        adapter1 = ApprovalMigrationAdapter(config1, local_conn)

        assert adapter1._dual_write is not None

        # Transition to REMOTE_ONLY
        config2 = MigrationConfig(
            phase=MigrationPhase.REMOTE_ONLY,
            vps_agent_web=VPSAgentWebConfig(url="https://test.example.com"),
            hmac=HMACConfig(vps_active_secret="secret"),
        )
        adapter2 = ApprovalMigrationAdapter(config2, local_conn)

        assert adapter2.config.phase == MigrationPhase.REMOTE_ONLY

    def test_removed_phase_raises_error(self, local_conn):
        """Test REMOVED phase raises error on operations."""
        config = MigrationConfig(
            phase=MigrationPhase.REMOVED,
            vps_agent_web=VPSAgentWebConfig(),
            hmac=HMACConfig(),
        )
        adapter = ApprovalMigrationAdapter(config, local_conn)

        # Should raise RuntimeError when trying to create request
        with pytest.raises(RuntimeError, match="removed"):
            adapter.create_request(
                job_id="job-123",
                command="deploy",
                risk_level="high",
            )


class TestAdapterWorkflows:
    """Tests for full adapter workflows."""

    @pytest.fixture
    def adapter(self, tmp_path):
        """Fixture for configured adapter."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approval_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                command TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_approval',
                version INTEGER DEFAULT 0,
                expires_at TEXT NOT NULL,
                context TEXT DEFAULT '{}'
            )
        """)
        conn.commit()

        config = MigrationConfig(
            phase=MigrationPhase.REMOTE_ONLY,
            vps_agent_web=VPSAgentWebConfig(
                url="https://vps.example.com",
                timeout_seconds=30,
            ),
            hmac=HMACConfig(
                legacy_active_secret="legacy-secret",
                vps_active_secret="vps-secret",
            ),
        )
        try:
            yield ApprovalMigrationAdapter(config, conn)
        finally:
            conn.close()

    def test_create_request_workflow(self, adapter):
        """Test full create request workflow."""
        # Mock the remote client
        mock_response = {
            "id": "job-123",
            "status": "pending_approval",
            "created_at": "2024-01-15T10:00:00Z",
        }

        with patch.object(adapter._remote_client, "create_request") as mock_create:
            mock_create.return_value = mock_response

            result = adapter.create_request(
                job_id="job-123",
                command="deploy",
                risk_level="high",
            )

        assert result is not None
        mock_create.assert_called_once()

    def test_approve_request_workflow(self, adapter):
        """Test approve request workflow."""
        mock_response = {
            "id": "job-123",
            "status": "approved",
            "actor_id": "user-1",
        }

        with patch.object(adapter._remote_client, "approve_request") as mock_approve:
            mock_approve.return_value = mock_response

            result = adapter.approve_request(
                job_id="job-123",
                actor_id="user-1",
                version=1,
            )

        assert result is not None

    def test_reject_request_workflow(self, adapter):
        """Test reject request workflow."""
        mock_response = {
            "id": "job-123",
            "status": "rejected",
            "actor_id": "user-1",
            "reason": "Risk too high",
        }

        with patch.object(adapter._remote_client, "reject_request") as mock_reject:
            mock_reject.return_value = mock_response

            result = adapter.reject_request(
                job_id="job-123",
                actor_id="user-1",
                version=1,
                reason="Risk too high",
            )

        assert result is not None

    def test_get_request_status_workflow(self, adapter):
        """Test get request status workflow."""
        mock_response = {
            "id": "job-123",
            "status": "pending_approval",
            "command": "deploy",
        }

        with patch.object(adapter._remote_client, "get_request_status") as mock_get:
            mock_get.return_value = mock_response

            result = adapter.get_request_status("job-123")

        assert result is not None

    def test_list_requests_workflow(self, adapter):
        """Test list requests workflow."""
        mock_response = {
            "requests": [
                {"id": "job-1", "status": "pending_approval"},
                {"id": "job-2", "status": "approved"},
            ],
            "count": 2,
        }

        with patch.object(adapter._remote_client, "list_requests") as mock_list:
            mock_list.return_value = mock_response

            result = adapter.list_requests()

        assert result is not None


class TestAdapterErrorHandling:
    """Tests for adapter error handling."""

    @pytest.fixture
    def adapter(self, tmp_path):
        """Fixture for configured adapter."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        config = MigrationConfig(
            phase=MigrationPhase.REMOTE_ONLY,
            vps_agent_web=VPSAgentWebConfig(url="https://unreachable.example.com"),
            hmac=HMACConfig(vps_active_secret="secret"),
        )
        try:
            yield ApprovalMigrationAdapter(config, conn)
        finally:
            conn.close()

    def test_handles_remote_connection_error(self, adapter):
        """Test handling remote connection errors."""
        with patch.object(adapter._remote_client, "create_request") as mock_create:
            mock_create.side_effect = ConnectionError("Connection refused")

            with pytest.raises(ConnectionError):
                adapter.create_request(
                    job_id="job-123",
                    command="deploy",
                    risk_level="high",
                )

    def test_handles_authentication_error(self, adapter):
        """Test handling authentication errors."""
        from urllib.error import HTTPError

        error = HTTPError(
            url="https://test.example.com/jobs",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=None,
        )
        with patch.object(adapter._remote_client, "create_request") as mock_create:
            mock_create.side_effect = error

            with pytest.raises(HTTPError):
                adapter.create_request(
                    job_id="job-123",
                    command="deploy",
                    risk_level="high",
                )
        error.close()

    def test_handles_timeout_error(self, adapter):
        """Test handling timeout errors."""
        with patch.object(adapter._remote_client, "create_request") as mock_create:
            mock_create.side_effect = TimeoutError("Request timed out")

            with pytest.raises(TimeoutError):
                adapter.create_request(
                    job_id="job-123",
                    command="deploy",
                    risk_level="high",
                )


class TestAdapterConfiguration:
    """Tests for adapter configuration handling."""

    @pytest.fixture
    def local_conn(self, tmp_path):
        """Fixture for local SQLite connection."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def test_custom_config_initialization(self, local_conn):
        """Test adapter with custom configuration."""
        config = MigrationConfig(
            phase=MigrationPhase.DUAL_WRITE,
            vps_agent_web=VPSAgentWebConfig(
                url="https://custom.example.com",
                timeout_seconds=60,
                retry_count=5,
            ),
            hmac=HMACConfig(
                legacy_active_secret="custom-legacy",
                vps_active_secret="custom-vps",
                vps_key_id="custom-key",
            ),
            dual_write=DualWriteConfig(mode=DualWriteMode.REMOTE_PREFERRED),
        )

        adapter = ApprovalMigrationAdapter(config, local_conn)

        assert adapter.config.vps_agent_web.url == "https://custom.example.com"
        assert adapter.config.vps_agent_web.timeout_seconds == 60
        assert adapter.config.vps_agent_web.retry_count == 5
        assert adapter.config.hmac.vps_key_id == "custom-key"

    def test_config_validation_remote_requires_url(self, local_conn):
        """Test validation fails when remote URL missing."""
        with patch.dict("os.environ", {"VPS_AGENT_WEB_URL": ""}):
            config = MigrationConfig(
                phase=MigrationPhase.REMOTE_ONLY,
                vps_agent_web=VPSAgentWebConfig(url=""),
                hmac=HMACConfig(vps_active_secret="secret"),
            )

            errors = config.validate()

            assert any("VPS_AGENT_WEB_URL" in e for e in errors)

    def test_config_validation_remote_requires_secret(self, local_conn):
        """Test validation fails when VPS secret missing."""
        config = MigrationConfig(
            phase=MigrationPhase.REMOTE_ONLY,
            vps_agent_web=VPSAgentWebConfig(url="https://test.com"),
            hmac=HMACConfig(vps_active_secret=None),
        )

        errors = config.validate()

        assert any("VPS_AGENT_HMAC_SECRET" in e for e in errors)

    def test_get_effective_mode(self, local_conn):
        """Test effective mode description."""
        config = MigrationConfig(
            phase=MigrationPhase.DUAL_WRITE,
            vps_agent_web=VPSAgentWebConfig(url="https://test.example.com"),
            hmac=HMACConfig(
                legacy_active_secret="legacy-secret",
                vps_active_secret="vps-secret",
            ),
        )
        adapter = ApprovalMigrationAdapter(config, local_conn)

        mode = adapter.config.get_effective_mode()

        assert "dual-write" in mode
