"""Unit tests for HMAC bridge.

Tests for HMACBridge with nonce replay prevention and signature verification.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from memory_tool.approval_security import (
    MAX_AGE,
    MAX_CLOCK_SKEW,
    MemoryNonceStore,
    NonceStore,
    generate_hmac_headers,
)
from memory_tool.migrate_out.approval.config import HMACConfig
from memory_tool.migrate_out.approval.hmac_bridge import (
    HMACBridge,
    HMACVerificationError,
)


class TestMemoryNonceStore:
    """Tests for MemoryNonceStore from approval_security."""

    def test_add_and_exists(self):
        """Test adding and checking nonces."""
        store = MemoryNonceStore()

        # New nonce should be added successfully
        assert store.add("nonce1") is True

        # Now it should exist
        assert store.exists("nonce1") is True

    def test_duplicate_add_returns_false(self):
        """Test adding duplicate nonce returns False."""
        store = MemoryNonceStore()
        store.add("nonce1")

        # Second add should fail
        assert store.add("nonce1") is False

    def test_ttl_expiration(self):
        """Test nonce expires after TTL."""
        store = MemoryNonceStore()

        # Add with very short TTL
        assert store.add("nonce1", ttl=0.1) is True
        assert store.exists("nonce1") is True

        # Wait for expiration
        time.sleep(0.15)

        # Should not exist after expiration
        assert store.exists("nonce1") is False

    def test_cleanup(self):
        """Test cleanup of expired nonces."""
        store = MemoryNonceStore()

        store.add("nonce1", ttl=0.1)
        time.sleep(0.15)

        # Cleanup should remove expired
        store.cleanup()
        assert store.exists("nonce1") is False


class TestHMACBridge:
    """Tests for HMACBridge."""

    @pytest.fixture
    def hmac_config(self):
        """Fixture for HMACConfig."""
        return HMACConfig(
            legacy_active_secret="test-secret-32-bytes-long-key!!",
            legacy_previous_secret="previous-secret-32-bytes-long!!",
            vps_active_secret="vps-secret-32-bytes-long-key!!",
            vps_key_id="v1",
        )

    @pytest.fixture
    def bridge(self, hmac_config):
        """Fixture for HMACBridge with memory nonce store."""
        nonce_store = MemoryNonceStore()
        return HMACBridge(
            config=hmac_config,
            nonce_store=nonce_store,
        )

    @pytest.fixture
    def fresh_bridge(self, hmac_config):
        """Fixture for HMACBridge with fresh nonce store for integration tests."""
        nonce_store = MemoryNonceStore()
        return HMACBridge(
            config=hmac_config,
            nonce_store=nonce_store,
        )

    def test_initialization(self, bridge, hmac_config):
        """Test bridge initialization."""
        assert bridge.config == hmac_config
        assert bridge.is_legacy_configured() is True
        assert bridge.is_vps_configured() is True

    def test_generate_local_signature(self, bridge):
        """Test generating local signature."""
        payload = {
            "job_id": "123",
            "action": "approve",
            "actor_id": "user-1",
            "version": 1,
            "reason": "approved",
        }

        headers = bridge.generate_local_signature(payload)

        assert "X-Signature" in headers
        assert "X-Timestamp" in headers
        assert "X-Nonce" in headers
        assert "X-Key-Id" in headers

    def test_verify_local_success(self, bridge):
        """Test successful local verification."""
        payload = {
            "job_id": "123",
            "action": "approve",
            "actor_id": "user-1",
            "version": 1,
            "reason": "",
        }

        # Generate valid headers
        headers = bridge.generate_local_signature(payload)

        # Should verify successfully
        assert bridge.verify_local(headers, payload) is True

    def test_verify_local_missing_nonce(self, bridge):
        """Test verification fails with missing nonce."""
        payload = {"job_id": "123", "action": "approve"}

        headers = {
            "X-Signature": "test",
            "X-Timestamp": str(int(time.time())),
            # Missing X-Nonce
        }

        with pytest.raises(HMACVerificationError, match="Missing X-Nonce"):
            bridge.verify_local(headers, payload)

    def test_verify_local_invalid_timestamp(self, bridge):
        """Test verification fails with invalid timestamp."""
        payload = {"job_id": "123", "action": "approve"}

        headers = {
            "X-Signature": "test",
            "X-Timestamp": "invalid",
            "X-Nonce": "nonce-123",
        }

        with pytest.raises(HMACVerificationError, match="Invalid X-Timestamp"):
            bridge.verify_local(headers, payload)

    def test_verify_local_timestamp_in_future(self, bridge):
        """Test verification fails with future timestamp."""
        payload = {"job_id": "123", "action": "approve"}

        future_time = int(time.time()) + MAX_CLOCK_SKEW + 10

        headers = {
            "X-Signature": "test",
            "X-Timestamp": str(future_time),
            "X-Nonce": "nonce-future",
        }

        with pytest.raises(HMACVerificationError, match="future"):
            bridge.verify_local(headers, payload)

    def test_verify_local_timestamp_too_old(self, bridge):
        """Test verification fails with old timestamp."""
        payload = {"job_id": "123", "action": "approve"}

        old_time = int(time.time()) - MAX_AGE - 10

        headers = {
            "X-Signature": "test",
            "X-Timestamp": str(old_time),
            "X-Nonce": "nonce-old",
        }

        with pytest.raises(HMACVerificationError, match="too old"):
            bridge.verify_local(headers, payload)

    def test_verify_local_replay_attack(self, bridge):
        """Test verification fails on replay attack."""
        payload = {
            "job_id": "123",
            "action": "approve",
            "actor_id": "user-1",
            "version": 1,
        }

        headers = bridge.generate_local_signature(payload)

        # First verification should succeed
        assert bridge.verify_local(headers, payload) is True

        # Second verification with same nonce should fail
        with pytest.raises(HMACVerificationError, match="nonce_reused"):
            bridge.verify_local(headers, payload)

    def test_resign_for_remote(self, bridge):
        """Test re-signing for remote."""
        payload = {
            "job_id": "123",
            "action": "approve",
            "actor_id": "user-1",
            "version": 1,
        }

        local_headers = bridge.generate_local_signature(payload)

        # Re-sign for remote
        remote_headers = bridge.resign_for_remote(local_headers, payload)

        assert "X-Signature" in remote_headers
        assert "X-Timestamp" in remote_headers
        assert "X-Nonce" in remote_headers
        assert remote_headers["X-Key-Id"] == "v1"

    def test_verify_and_resign(self, bridge):
        """Test verify and resign workflow."""
        payload = {
            "job_id": "123",
            "action": "approve",
            "actor_id": "user-1",
            "version": 1,
        }

        local_headers = bridge.generate_local_signature(payload)

        # Verify and resign
        remote_headers = bridge.verify_and_resign(local_headers, payload)

        assert "X-Signature" in remote_headers
        assert "X-Timestamp" in remote_headers

    def test_case_insensitive_header_lookup(self, bridge):
        """Test case-insensitive header lookup."""
        payload = {
            "job_id": "123",
            "action": "approve",
            "actor_id": "user-1",
            "version": 1,
        }

        # Generate headers
        headers = bridge.generate_local_signature(payload)

        # Convert to lowercase
        lowercase_headers = {k.lower(): v for k, v in headers.items()}

        # Should still work with lowercase headers
        # Note: verify_local uses _get_header which is case-insensitive
        result = bridge.verify_local(lowercase_headers, payload)
        assert result is True

    def test_is_legacy_configured_without_secret(self):
        """Test is_legacy_configured returns False without secret."""
        config = HMACConfig(
            legacy_active_secret=None,
            vps_active_secret="vps-secret",
        )
        bridge = HMACBridge(config)

        assert bridge.is_legacy_configured() is False
        assert bridge.is_vps_configured() is True

    def test_is_vps_configured_without_secret(self):
        """Test is_vps_configured returns False without secret."""
        config = HMACConfig(
            legacy_active_secret="legacy-secret",
            vps_active_secret=None,
        )
        bridge = HMACBridge(config)

        assert bridge.is_legacy_configured() is True
        assert bridge.is_vps_configured() is False


class TestHMACBridgeIntegration:
    """Integration tests for HMACBridge with real components."""

    def test_full_verify_resign_flow(self):
        """Test complete verify and resign flow."""
        config = HMACConfig(
            legacy_active_secret="legacy-secret-32bytes-long!!!",
            vps_active_secret="vps-secret-32bytes-long!!!!!!!",
        )
        bridge = HMACBridge(config)

        payload = {
            "job_id": "job-123",
            "action": "approve",
            "actor_id": "actor-1",
            "version": 1,
            "reason": "approved by admin",
        }

        # Generate local signature
        local_headers = bridge.generate_local_signature(payload)

        # Verify locally
        assert bridge.verify_local(local_headers, payload) is True

        # Re-sign for remote
        remote_headers = bridge.resign_for_remote(local_headers, payload)

        assert "X-Signature" in remote_headers
        assert "X-Timestamp" in remote_headers
