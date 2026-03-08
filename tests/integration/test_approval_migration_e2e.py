from __future__ import annotations

import sqlite3

import pytest

from memory_tool.migrate_out.approval.adapter import ApprovalMigrationAdapter
from memory_tool.migrate_out.approval.config import (
    HMACConfig,
    MigrationConfig,
    MigrationPhase,
    VPSAgentWebConfig,
)


@pytest.fixture
def local_conn(tmp_path):
    db_path = tmp_path / "approval-e2e.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
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
        """
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.mark.e2e
def test_approval_adapter_dual_write_bootstrap(local_conn):
    config = MigrationConfig(
        phase=MigrationPhase.DUAL_WRITE,
        vps_agent_web=VPSAgentWebConfig(url="https://vps.example.com"),
        hmac=HMACConfig(
            legacy_active_secret="legacy-secret",
            vps_active_secret="vps-secret",
        ),
    )
    adapter = ApprovalMigrationAdapter(config, local_conn)

    assert adapter.config.get_effective_mode() == "dual-write (strict)"
    assert adapter._local_api is not None
    assert adapter._remote_client is not None
    assert adapter._dual_write is not None


@pytest.mark.e2e
def test_approval_adapter_remote_config_validation(local_conn):
    config = MigrationConfig(
        phase=MigrationPhase.REMOTE_ONLY,
        vps_agent_web=VPSAgentWebConfig(url="https://vps.example.com"),
        hmac=HMACConfig(vps_active_secret="vps-secret"),
    )
    adapter = ApprovalMigrationAdapter(config, local_conn)

    assert adapter.config.get_effective_mode() == "remote-only"
    assert adapter._remote_client is not None
