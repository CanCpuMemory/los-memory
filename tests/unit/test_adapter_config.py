"""Unit tests for adapter configuration.

Tests for MigrationConfig, DualWriteConfig, HMACConfig, and related enums.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from memory_tool.migrate_out.approval.config import (
    DualWriteConfig,
    DualWriteMode,
    HMACConfig,
    MigrationConfig,
    MigrationPhase,
    SSEProxyConfig,
    VPSAgentWebConfig,
)


class TestMigrationPhase:
    """Tests for MigrationPhase enum."""

    def test_phase_values(self):
        """Test phase enum values."""
        assert MigrationPhase.LOCAL_ONLY.value == "local-only"
        assert MigrationPhase.DUAL_WRITE.value == "dual-write"
        assert MigrationPhase.REMOTE_ONLY.value == "remote-only"
        assert MigrationPhase.REMOVED.value == "removed"


class TestDualWriteMode:
    """Tests for DualWriteMode enum."""

    def test_mode_values(self):
        """Test mode enum values."""
        assert DualWriteMode.STRICT.value == "strict"
        assert DualWriteMode.LOCAL_PREFERRED.value == "local_preferred"
        assert DualWriteMode.REMOTE_PREFERRED.value == "remote_preferred"
        assert DualWriteMode.READ_ONLY.value == "read_only"


class TestVPSAgentWebConfig:
    """Tests for VPSAgentWebConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = VPSAgentWebConfig()
        assert config.url == "https://vps-agent-web.example.com"
        assert config.timeout_seconds == 30
        assert config.retry_count == 3

    def test_custom_values(self):
        """Test custom configuration values."""
        config = VPSAgentWebConfig(
            url="https://custom.example.com",
            timeout_seconds=60,
            retry_count=5,
        )
        assert config.url == "https://custom.example.com"
        assert config.timeout_seconds == 60
        assert config.retry_count == 5

    def test_env_override(self):
        """Test environment variable override."""
        with patch.dict(os.environ, {
            "VPS_AGENT_WEB_URL": "https://env.example.com",
            "APPROVAL_MIGRATION_TIMEOUT": "45",
        }):
            config = VPSAgentWebConfig()
            assert config.url == "https://env.example.com"
            assert config.timeout_seconds == 45


class TestSSEProxyConfig:
    """Tests for SSEProxyConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = SSEProxyConfig()
        assert config.enabled is True
        assert config.buffer_size == 1000
        assert config.reconnect_timeout_seconds == 30
        assert config.history_minutes == 5


class TestHMACConfig:
    """Tests for HMACConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = HMACConfig()
        assert config.legacy_active_secret is None
        assert config.legacy_previous_secret is None
        assert config.vps_active_secret is None
        assert config.vps_key_id == "v1"
        assert config.rotation_window_seconds == 86400  # 24 hours

    def test_custom_values(self):
        """Test custom configuration values."""
        config = HMACConfig(
            legacy_active_secret="secret1",
            legacy_previous_secret="secret2",
            vps_active_secret="secret3",
            vps_key_id="v2",
        )
        assert config.legacy_active_secret == "secret1"
        assert config.legacy_previous_secret == "secret2"
        assert config.vps_active_secret == "secret3"
        assert config.vps_key_id == "v2"

    def test_env_override(self):
        """Test environment variable override."""
        with patch.dict(os.environ, {
            "APPROVAL_HMAC_SECRET": "env-legacy",
            "APPROVAL_HMAC_PREVIOUS_SECRET": "env-previous",
            "VPS_AGENT_HMAC_SECRET": "env-vps",
            "VPS_AGENT_KEY_ID": "env-key",
        }):
            config = HMACConfig()
            assert config.legacy_active_secret == "env-legacy"
            assert config.legacy_previous_secret == "env-previous"
            assert config.vps_active_secret == "env-vps"
            assert config.vps_key_id == "env-key"


class TestDualWriteConfig:
    """Tests for DualWriteConfig."""

    def test_default_mode(self):
        """Test default mode is STRICT."""
        config = DualWriteConfig()
        assert config.mode == DualWriteMode.STRICT

    def test_custom_mode(self):
        """Test custom mode setting."""
        config = DualWriteConfig(mode=DualWriteMode.LOCAL_PREFERRED)
        assert config.mode == DualWriteMode.LOCAL_PREFERRED

    def test_env_override(self):
        """Test environment variable override."""
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_MODE": "remote_preferred"}):
            config = DualWriteConfig()
            assert config.mode == DualWriteMode.REMOTE_PREFERRED

    def test_invalid_env_fallback(self):
        """Test fallback to STRICT on invalid env value."""
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_MODE": "invalid_mode"}):
            config = DualWriteConfig()
            assert config.mode == DualWriteMode.STRICT


class TestMigrationConfig:
    """Tests for MigrationConfig."""

    def test_default_phase(self):
        """Test default phase is LOCAL_ONLY."""
        config = MigrationConfig()
        assert config.phase == MigrationPhase.LOCAL_ONLY

    def test_phase_feature_flags(self):
        """Test feature flags are set correctly per phase."""
        # LOCAL_ONLY - need to clear env to test explicit phase
        with patch.dict(os.environ, {}, clear=False):
            config = MigrationConfig(phase=MigrationPhase.LOCAL_ONLY)
            assert config.phase == MigrationPhase.LOCAL_ONLY
            assert config.enable_local is True
            assert config.enable_remote is False

        # DUAL_WRITE
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_PHASE": "dual-write"}, clear=False):
            config = MigrationConfig()
            assert config.phase == MigrationPhase.DUAL_WRITE
            assert config.enable_local is True
            assert config.enable_remote is True

        # REMOTE_ONLY
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_PHASE": "remote-only"}, clear=False):
            config = MigrationConfig()
            assert config.phase == MigrationPhase.REMOTE_ONLY
            assert config.enable_local is False
            assert config.enable_remote is True

        # REMOVED
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_PHASE": "removed"}, clear=False):
            config = MigrationConfig()
            assert config.phase == MigrationPhase.REMOVED
            assert config.enable_local is False
            assert config.enable_remote is False

    def test_env_phase_override(self):
        """Test phase from environment variable."""
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_PHASE": "dual-write"}):
            config = MigrationConfig()
            assert config.phase == MigrationPhase.DUAL_WRITE

    def test_env_feature_override(self):
        """Test explicit feature flag override."""
        with patch.dict(os.environ, {
            "APPROVAL_MIGRATION_PHASE": "local-only",
            "APPROVAL_ENABLE_REMOTE": "true",
        }):
            config = MigrationConfig()
            assert config.phase == MigrationPhase.LOCAL_ONLY
            assert config.enable_remote is True  # Override

    def test_is_bridge_enabled(self):
        """Test bridge enabled detection."""
        # LOCAL_ONLY - bridge disabled
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_PHASE": "local-only"}, clear=False):
            assert MigrationConfig().is_bridge_enabled() is False

        # DUAL_WRITE - bridge enabled
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_PHASE": "dual-write"}, clear=False):
            assert MigrationConfig().is_bridge_enabled() is True

        # REMOTE_ONLY - bridge enabled
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_PHASE": "remote-only"}, clear=False):
            assert MigrationConfig().is_bridge_enabled() is True

        # REMOVED - bridge disabled
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_PHASE": "removed"}, clear=False):
            assert MigrationConfig().is_bridge_enabled() is False

    def test_get_effective_mode(self):
        """Test effective mode description."""
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_PHASE": "dual-write"}, clear=False):
            config = MigrationConfig()
            assert "dual-write" in config.get_effective_mode()

    def test_validate_success(self):
        """Test validation passes for valid config."""
        config = MigrationConfig(phase=MigrationPhase.LOCAL_ONLY)
        errors = config.validate()
        assert len(errors) == 0

    def test_validate_remote_requires_url(self):
        """Test validation fails when remote URL missing."""
        with patch.dict(os.environ, {
            "APPROVAL_MIGRATION_PHASE": "remote-only",
            "VPS_AGENT_WEB_URL": "",  # Force empty URL
        }, clear=False):
            config = MigrationConfig(
                hmac=HMACConfig(vps_active_secret="secret"),
            )
            errors = config.validate()
            assert any("VPS_AGENT_WEB_URL" in e for e in errors)

    def test_validate_remote_requires_secret(self):
        """Test validation fails when VPS secret missing."""
        with patch.dict(os.environ, {"APPROVAL_MIGRATION_PHASE": "remote-only"}, clear=False):
            config = MigrationConfig(
                vps_agent_web=VPSAgentWebConfig(url="https://test.com"),
                hmac=HMACConfig(vps_active_secret=None),
            )
            errors = config.validate()
            assert any("VPS_AGENT_HMAC_SECRET" in e for e in errors)

    def test_silence_warnings(self):
        """Test deprecation warning silencing."""
        with patch.dict(os.environ, {"MEMORY_APPROVAL_SILENCE_WARNING": "1"}):
            config = MigrationConfig()
            assert config.deprecation_warnings is False
