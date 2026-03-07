"""Tests for L2 approval system.

Tests cover HMAC security, SSE events, and approval storage.
"""
import json
import time
from unittest.mock import MagicMock

import pytest

from memory_tool.approval_events import ApprovalEvent, EventPublisher, create_sse_response_headers
from memory_tool.approval_security import (
    HMACConfig,
    HMACValidator,
    MemoryNonceStore,
    SQLiteNonceStore,
    generate_hmac_headers,
)
from memory_tool.approval_store import ApprovalRequest, ApprovalStatus, ApprovalStore


class TestMemoryNonceStore:
    """Test in-memory nonce store."""

    def test_add_and_exists(self):
        """Test adding and checking nonce."""
        store = MemoryNonceStore()

        assert store.add("nonce1") is True
        assert store.exists("nonce1") is True

    def test_duplicate_nonce_rejected(self):
        """Test duplicate nonce is rejected."""
        store = MemoryNonceStore()

        assert store.add("nonce1") is True
        assert store.add("nonce1") is False  # Duplicate

    def test_nonce_expires(self):
        """Test nonce expires after TTL."""
        store = MemoryNonceStore()

        store.add("nonce1", ttl=0)  # Immediate expiry
        time.sleep(0.1)

        assert store.exists("nonce1") is False

    def test_cleanup(self):
        """Test cleanup removes expired nonces."""
        store = MemoryNonceStore()

        store.add("nonce1", ttl=0)
        store.add("nonce2", ttl=3600)
        time.sleep(0.1)

        store.cleanup()

        assert store.exists("nonce1") is False
        assert store.exists("nonce2") is True


class TestSQLiteNonceStore:
    """Test SQLite-backed nonce store."""

    def test_add_and_exists(self, db_connection):
        """Test adding and checking nonce."""
        store = SQLiteNonceStore(db_connection)

        assert store.add("nonce1") is True
        assert store.exists("nonce1") is True

    def test_duplicate_nonce_rejected(self, db_connection):
        """Test duplicate nonce is rejected."""
        store = SQLiteNonceStore(db_connection)

        assert store.add("nonce1") is True
        assert store.add("nonce1") is False  # Duplicate

    def test_expired_nonce_removed_on_check(self, db_connection):
        """Test expired nonce is removed when checked."""
        store = SQLiteNonceStore(db_connection)

        store.add("nonce1", ttl=0)  # Immediate expiry
        time.sleep(0.1)

        assert store.exists("nonce1") is False


class TestHMACValidator:
    """Test HMAC signature validation."""

    @pytest.fixture
    def validator(self):
        config = HMACConfig(active_secret="test-secret-key-32-bytes-long!!", key_id="v1")
        return HMACValidator(config, MemoryNonceStore())

    def test_valid_signature(self, validator):
        """Test valid signature verification."""
        payload = {
            "job_id": "job-123",
            "action": "approve",
            "actor_id": "user-456",
            "version": 1,
            "reason": "verified",
        }
        timestamp = int(time.time())
        nonce = "test-nonce-123"

        signature = validator.generate_signature(payload, timestamp, nonce)

        result = validator.verify(signature, timestamp, nonce, payload)

        assert result["valid"] is True

    def test_invalid_signature(self, validator):
        """Test invalid signature is rejected."""
        payload = {
            "job_id": "job-123",
            "action": "approve",
            "actor_id": "user-456",
            "version": 1,
            "reason": "verified",
        }
        timestamp = int(time.time())

        result = validator.verify("invalid-signature", timestamp, "nonce", payload)

        assert result["valid"] is False
        assert result["error"] == "invalid_signature"

    def test_future_timestamp_rejected(self, validator):
        """Test future timestamp (>60s) is rejected."""
        payload = {"job_id": "job-123", "action": "approve", "actor_id": "user-456", "version": 1}
        timestamp = int(time.time()) + 120  # 2 minutes in future

        result = validator.verify("dummy", timestamp, "nonce", payload)

        assert result["valid"] is False
        assert result["error"] == "timestamp_in_future"

    def test_old_timestamp_rejected(self, validator):
        """Test old timestamp (>5min) is rejected."""
        payload = {"job_id": "job-123", "action": "approve", "actor_id": "user-456", "version": 1}
        timestamp = int(time.time()) - 400  # ~6.7 minutes ago

        result = validator.verify("dummy", timestamp, "nonce", payload)

        assert result["valid"] is False
        assert result["error"] == "timestamp_too_old"

    def test_replay_attack_prevention(self, validator):
        """Test nonce replay is rejected."""
        payload = {"job_id": "job-123", "action": "approve", "actor_id": "user-456", "version": 1}
        timestamp = int(time.time())
        nonce = "unique-nonce"

        signature = validator.generate_signature(payload, timestamp, nonce)

        # First verification succeeds
        result1 = validator.verify(signature, timestamp, nonce, payload)
        assert result1["valid"] is True

        # Replay with same nonce fails
        result2 = validator.verify(signature, timestamp, nonce, payload)
        assert result2["valid"] is False
        assert result2["error"] == "nonce_reused"

    def test_previous_key_accepted(self):
        """Test signature with previous key is accepted."""
        config = HMACConfig(
            active_secret="new-secret-key-32-bytes-long!!",
            previous_secret="old-secret-key-32-bytes-long!!",
            key_id="v2",
        )
        validator = HMACValidator(config, MemoryNonceStore())

        # Generate with old validator
        old_validator = HMACValidator(
            HMACConfig(active_secret="old-secret-key-32-bytes-long!!"),
            MemoryNonceStore(),
        )
        payload = {"job_id": "job-123", "action": "approve", "actor_id": "user-456", "version": 1}
        timestamp = int(time.time())
        nonce = "test-nonce"

        signature = old_validator.generate_signature(payload, timestamp, nonce)

        # Verify with new validator (has previous key)
        result = validator.verify(signature, timestamp, nonce, payload)

        assert result["valid"] is True


class TestGenerateHMACHeaders:
    """Test HMAC header generation."""

    def test_generates_required_headers(self):
        """Test all required headers are generated."""
        payload = {"job_id": "job-123", "action": "approve", "actor_id": "user-456", "version": 1}
        secret = "test-secret-key-32-bytes-long!!"

        headers = generate_hmac_headers(payload, secret, key_id="v1")

        assert "X-Signature" in headers
        assert "X-Timestamp" in headers
        assert "X-Nonce" in headers
        assert "X-Key-Id" in headers
        assert headers["X-Key-Id"] == "v1"


class TestApprovalEvent:
    """Test ApprovalEvent dataclass."""

    def test_to_sse_format(self):
        """Test SSE format conversion."""
        event = ApprovalEvent(
            event_type="approval.pending",
            event_id="evt-123",
            data={"job_id": "job-456", "status": "pending"},
            timestamp="2024-01-01T00:00:00Z",
        )

        sse = event.to_sse_format()

        assert "event: approval.pending" in sse
        assert "id: evt-123" in sse
        assert 'data: {"job_id": "job-456", "status": "pending"}' in sse


class TestEventPublisher:
    """Test EventPublisher."""

    def test_publish_pending(self, db_connection):
        """Test publishing pending event."""
        publisher = EventPublisher(db_connection)

        event = publisher.publish_pending(
            job_id="job-123",
            command="restart_service",
            risk_level="high",
            actor_id="system",
        )

        assert event.event_type == "approval.pending"
        assert event.data["job_id"] == "job-123"
        assert event.data["command"] == "restart_service"
        assert event.data["risk_level"] == "high"

    def test_publish_approved(self, db_connection):
        """Test publishing approved event."""
        publisher = EventPublisher(db_connection)

        event = publisher.publish_approved(
            job_id="job-123",
            actor_id="user-456",
            version=2,
            reason="verified safe",
        )

        assert event.event_type == "approval.approved"
        assert event.data["actor_id"] == "user-456"
        assert event.data["version"] == 2
        assert event.data["reason"] == "verified safe"

    def test_publish_rejected(self, db_connection):
        """Test publishing rejected event."""
        publisher = EventPublisher(db_connection)

        event = publisher.publish_rejected(
            job_id="job-123",
            actor_id="user-456",
            version=2,
            reason="too risky",
        )

        assert event.event_type == "approval.rejected"
        assert event.data["status"] == "rejected"
        assert event.data["reason"] == "too risky"

    def test_event_persisted_to_database(self, db_connection):
        """Test events are persisted."""
        publisher = EventPublisher(db_connection)

        publisher.publish_pending(job_id="job-123", command="test", risk_level="low")

        history = publisher.get_event_history(job_id="job-123")
        assert len(history) == 1
        assert history[0]["event_type"] == "approval.pending"

    def test_event_history_replay(self, db_connection):
        """Test event history for replay."""
        publisher = EventPublisher(db_connection)

        # Publish event
        event = publisher.publish_pending(job_id="job-123", command="test", risk_level="low")

        # Get history should include the event
        history = publisher.get_event_history(job_id="job-123")
        assert len(history) >= 1
        assert history[0]["event_type"] == "approval.pending"

    def test_create_sse_response_headers(self):
        """Test SSE response headers."""
        headers = create_sse_response_headers()

        assert headers["Content-Type"] == "text/event-stream"
        assert headers["Cache-Control"] == "no-cache"
        assert headers["Connection"] == "keep-alive"


class TestApprovalRequest:
    """Test ApprovalRequest dataclass."""

    def test_default_expiration(self):
        """Test default 48-hour expiration."""
        request = ApprovalRequest(job_id="job-123", command="restart")

        assert request.expires_at is not None

    def test_to_dict(self):
        """Test conversion to dictionary."""
        request = ApprovalRequest(
            job_id="job-123",
            command="restart",
            risk_level="high",
            status=ApprovalStatus.PENDING,
        )

        data = request.to_dict()

        assert data["job_id"] == "job-123"
        assert data["command"] == "restart"
        assert data["risk_level"] == "high"
        assert data["status"] == "pending"

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "id": 1,
            "job_id": "job-123",
            "command": "restart",
            "risk_level": "high",
            "status": "pending",
            "version": 2,
        }

        request = ApprovalRequest.from_dict(data)

        assert request.id == 1
        assert request.job_id == "job-123"
        assert request.version == 2


class TestApprovalStore:
    """Test ApprovalStore."""

    @pytest.fixture
    def store(self, db_connection):
        return ApprovalStore(db_connection)

    def test_create(self, store):
        """Test creating approval request."""
        request = ApprovalRequest(
            job_id="job-123",
            command="restart_service",
            risk_level="high",
            requested_by="system",
        )

        created = store.create(request)

        assert created.id is not None
        assert created.status == ApprovalStatus.PENDING

    def test_get_by_id(self, store):
        """Test retrieving by ID."""
        request = ApprovalRequest(job_id="job-123", command="restart")
        created = store.create(request)

        fetched = store.get_by_id(created.id)

        assert fetched is not None
        assert fetched.job_id == "job-123"

    def test_get_by_job_id(self, store):
        """Test retrieving by job ID."""
        request = ApprovalRequest(job_id="job-456", command="restart")
        store.create(request)

        fetched = store.get_by_job_id("job-456")

        assert fetched is not None
        assert fetched.command == "restart"

    def test_list_pending(self, store):
        """Test listing pending requests."""
        store.create(ApprovalRequest(job_id="job-1", command="cmd1"))
        store.create(ApprovalRequest(job_id="job-2", command="cmd2"))

        pending = store.list_pending()

        assert len(pending) >= 2

    def test_approve_success(self, store):
        """Test successful approval with optimistic lock."""
        request = store.create(ApprovalRequest(job_id="job-123", command="restart"))

        success = store.approve(
            request_id=request.id,
            actor_id="user-456",
            version=request.version,
            reason="verified",
        )

        assert success is True

        fetched = store.get_by_id(request.id)
        assert fetched.status == ApprovalStatus.APPROVED
        assert fetched.approved_by == "user-456"
        assert fetched.version == 2

    def test_approve_version_conflict(self, store):
        """Test approval fails on version conflict."""
        request = store.create(ApprovalRequest(job_id="job-123", command="restart"))

        # First approval succeeds
        store.approve(request.id, "user-1", request.version)

        # Second approval with old version fails
        success = store.approve(request.id, "user-2", request.version)

        assert success is False

    def test_reject_success(self, store):
        """Test successful rejection."""
        request = store.create(ApprovalRequest(job_id="job-123", command="restart"))

        success = store.reject(
            request_id=request.id,
            actor_id="user-456",
            version=request.version,
            reason="too risky",
        )

        assert success is True

        fetched = store.get_by_id(request.id)
        assert fetched.status == ApprovalStatus.REJECTED

    def test_auto_reject_expired(self, store):
        """Test auto-reject of expired requests."""
        request = ApprovalRequest(
            job_id="job-123",
            command="restart",
            expires_at="2020-01-01T00:00:00Z",  # Already expired
        )
        store.create(request)

        rejected_ids = store.auto_reject_expired()

        assert len(rejected_ids) == 1

        fetched = store.get_by_id(rejected_ids[0])
        assert fetched.status == ApprovalStatus.TIMEOUT
        assert fetched.approved_by == "system:auto-reject"

    def test_audit_log_created(self, store):
        """Test audit log is created for actions."""
        request = store.create(ApprovalRequest(job_id="job-123", command="restart"))

        store.approve(request.id, "user-456", request.version, "verified")

        audit_log = store.get_audit_log(request_id=request.id)

        assert len(audit_log) >= 2  # Created + approved
        actions = [entry["action"] for entry in audit_log]
        assert "created" in actions
        assert "approved" in actions


class TestSchemaV10:
    """Test database schema v10."""

    def test_approval_requests_table_exists(self, db_connection):
        """Test approval_requests table exists."""
        row = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='approval_requests'"
        ).fetchone()
        assert row is not None

    def test_approval_audit_log_table_exists(self, db_connection):
        """Test approval_audit_log table exists."""
        row = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='approval_audit_log'"
        ).fetchone()
        assert row is not None

    def test_approval_events_table_exists(self, db_connection):
        """Test approval_events table exists."""
        row = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='approval_events'"
        ).fetchone()
        assert row is not None

    def test_approval_nonces_table_exists(self, db_connection):
        """Test approval_nonces table exists."""
        row = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='approval_nonces'"
        ).fetchone()
        assert row is not None

    def test_approval_requests_indexes_exist(self, db_connection):
        """Test approval request indexes exist."""
        indexes = [
            "idx_approval_status",
            "idx_approval_expires",
            "idx_approval_job",
        ]
        for idx in indexes:
            row = db_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (idx,),
            ).fetchone()
            assert row is not None, f"Index {idx} not found"

    def test_schema_version_at_least_10(self, db_connection):
        """Test schema version is at least 10."""
        from memory_tool.database import get_schema_version

        version = get_schema_version(db_connection)
        assert version >= 10  # v11 adds attribution tables
