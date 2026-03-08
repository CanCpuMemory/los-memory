"""Unit tests for SSE proxy components.

Tests for EventBufferManager, SSEConnectionManager, EventTransformer, and SSEProxy.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from unittest.mock import MagicMock, Mock, patch

import pytest

from memory_tool.approval_events import ApprovalEvent
from memory_tool.migrate_out.approval.config import SSEProxyConfig
from memory_tool.migrate_out.approval.sse_proxy.buffer import EventBufferManager
from memory_tool.migrate_out.approval.sse_proxy.connection import (
    SSEConnectionError,
    SSEConnectionManager,
)
from memory_tool.migrate_out.approval.sse_proxy.proxy import SSEProxy
from memory_tool.migrate_out.approval.sse_proxy.transform import EventTransformer


class TestApprovalEvent:
    """Tests for ApprovalEvent dataclass."""

    def test_event_creation(self):
        """Test creating an event."""
        event = ApprovalEvent(
            event_type="approval.pending",
            event_id="evt-123",
            data={"job_id": "123", "status": "pending"},
            timestamp="2024-01-15T10:00:00Z",
        )

        assert event.event_type == "approval.pending"
        assert event.event_id == "evt-123"
        assert event.data["job_id"] == "123"

    def test_event_to_sse_format(self):
        """Test converting to SSE format."""
        event = ApprovalEvent(
            event_type="approval.pending",
            event_id="evt-123",
            data={"job_id": "123"},
            timestamp="2024-01-15T10:00:00Z",
        )

        sse = event.to_sse_format()

        assert "event: approval.pending" in sse
        assert "id: evt-123" in sse
        assert '"job_id": "123"' in sse
        assert sse.endswith("\n\n")


class TestEventBufferManager:
    """Tests for EventBufferManager."""

    @pytest.fixture
    def buffer(self):
        """Fixture for EventBufferManager."""
        return EventBufferManager(max_size=100, max_age_minutes=5)

    def test_add_event(self, buffer):
        """Test adding an event to buffer."""
        event = ApprovalEvent(
            event_type="test",
            event_id="1",
            data={"msg": "hello"},
        )

        buffer.add(event)

        assert buffer.size() == 1
        assert buffer.get_event("1") == event

    def test_get_since(self, buffer):
        """Test getting events since a specific ID."""
        events = [
            ApprovalEvent(event_type="test", event_id="evt-1", data={"seq": 1}),
            ApprovalEvent(event_type="test", event_id="evt-2", data={"seq": 2}),
            ApprovalEvent(event_type="test", event_id="evt-3", data={"seq": 3}),
        ]
        for e in events:
            buffer.add(e)

        result = buffer.get_since("evt-2")

        assert len(result) == 1
        assert result[0].event_id == "evt-3"

    def test_get_since_not_found(self, buffer):
        """Test getting events when ID not found."""
        events = [
            ApprovalEvent(event_type="test", event_id="evt-1", data={"seq": 1}),
            ApprovalEvent(event_type="test", event_id="evt-2", data={"seq": 2}),
        ]
        for e in events:
            buffer.add(e)

        result = buffer.get_since("evt-unknown")

        # Should return all events when ID not found
        assert len(result) == 2

    def test_buffer_size_limit(self, buffer):
        """Test buffer respects max size."""
        # Add more events than max
        for i in range(150):
            buffer.add(ApprovalEvent(
                event_type="test",
                event_id=f"evt-{i}",
                data={"seq": i},
            ))

        # Buffer should not exceed max_size
        assert buffer.size() <= 100

    def test_contains(self, buffer):
        """Test contains method."""
        event = ApprovalEvent(
            event_type="test",
            event_id="evt-1",
            data={},
        )
        buffer.add(event)

        assert buffer.contains("evt-1") is True
        assert buffer.contains("evt-2") is False

    def test_get_latest(self, buffer):
        """Test getting latest events."""
        for i in range(5):
            buffer.add(ApprovalEvent(
                event_type="test",
                event_id=f"evt-{i}",
                data={"seq": i},
            ))

        latest = buffer.get_latest(count=2)

        assert len(latest) == 2
        assert latest[-1].event_id == "evt-4"

    def test_get_metrics(self, buffer):
        """Test getting buffer metrics."""
        for i in range(10):
            buffer.add(ApprovalEvent(
                event_type="test",
                event_id=f"evt-{i}",
                data={},
            ))

        metrics = buffer.get_metrics()

        assert metrics["size"] == 10
        assert metrics["total_added"] == 10

    def test_thread_safe_add(self, buffer):
        """Test thread-safe add operations."""
        errors = []

        def add_events(thread_id: int):
            try:
                for i in range(50):
                    buffer.add(ApprovalEvent(
                        event_type="test",
                        event_id=f"t{thread_id}-e{i}",
                        data={},
                    ))
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=add_events, args=(i,))
            for i in range(5)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Buffer max_size is 100, so excess events are dropped
        assert buffer.size() == 100


class TestEventTransformer:
    """Tests for EventTransformer."""

    def test_parse_sse_message(self):
        """Test parsing SSE message."""
        message = """event: approval.pending
id: evt-123
data: {"job_id": "123", "status": "pending"}"""

        result = EventTransformer.parse_sse_message(message)

        assert result["event"] == "approval.pending"
        assert result["id"] == "evt-123"
        assert result["data"]["job_id"] == "123"

    def test_transform_remote_to_local(self):
        """Test transforming remote event to local ApprovalEvent."""
        remote_event = {
            "event": "approval.approved",
            "id": "evt-456",
            "data": {
                "jobId": "456",
                "actorId": "user-1",
                "version": 2,
            },
        }

        event = EventTransformer.transform_remote_to_local(remote_event)

        assert event.event_type == "approval.approved"
        assert event.event_id == "evt-456"
        assert event.data["job_id"] == "456"
        assert event.data["actor_id"] == "user-1"

    def test_transform_local_to_remote(self):
        """Test transforming local ApprovalEvent to remote format."""
        local_event = ApprovalEvent(
            event_type="approval.pending",
            event_id="evt-123",
            data={
                "job_id": "123",
                "actor_id": "user-1",
                "risk_level": "high",
            },
            timestamp="2024-01-15T10:00:00Z",
        )

        remote = EventTransformer.transform_local_to_remote(local_event)

        assert remote["event"] == "approval.pending"
        assert remote["id"] == "evt-123"
        assert remote["timestamp"] == "2024-01-15T10:00:00Z"

    def test_create_sse_message(self):
        """Test creating SSE message."""
        message = EventTransformer.create_sse_message(
            event_type="approval.pending",
            event_id="evt-123",
            data={"job_id": "123"},
        )

        assert "event: approval.pending" in message
        assert "id: evt-123" in message
        assert "data: {\"job_id\": \"123\"}" in message

    def test_camel_to_snake(self):
        """Test camelCase to snake_case conversion."""
        result = EventTransformer._camel_to_snake("riskLevel")
        assert result == "risk_level"

        result = EventTransformer._camel_to_snake("jobId")
        assert result == "job_id"

    def test_snake_to_camel(self):
        """Test snake_case to camelCase conversion."""
        result = EventTransformer._snake_to_camel("risk_level")
        assert result == "riskLevel"

        result = EventTransformer._snake_to_camel("job_id")
        assert result == "jobId"


class TestSSEConnectionManager:
    """Tests for SSEConnectionManager."""

    @pytest.fixture
    def conn_manager(self):
        """Fixture for SSEConnectionManager."""
        config = SSEProxyConfig()
        mock_vps_client = MagicMock()
        mock_vps_client.get_event_stream_url.return_value = "https://test.example.com/events"
        return SSEConnectionManager(config, mock_vps_client)

    def test_initial_state(self, conn_manager):
        """Test initial connection state."""
        assert conn_manager.is_connected() is False
        assert conn_manager.get_reconnect_count() == 0

    def test_disconnect(self, conn_manager):
        """Test disconnection."""
        conn_manager._connected = True

        conn_manager.disconnect()

        assert conn_manager.is_connected() is False
        assert conn_manager._should_stop is True

    def test_calculate_backoff(self, conn_manager):
        """Test backoff calculation."""
        # Test exponential growth
        assert conn_manager._calculate_backoff() >= 0.75  # Base 1s with -25% jitter
        assert conn_manager._calculate_backoff() <= 1.25  # Base 1s with +25% jitter

        # Increment reconnect count
        conn_manager._reconnect_count = 3
        delay = conn_manager._calculate_backoff()
        assert delay >= 6.0  # 8 * 0.75
        assert delay <= 10.0  # 8 * 1.25

        # Test max cap
        conn_manager._reconnect_count = 10
        delay = conn_manager._calculate_backoff()
        assert delay <= 40.0  # Max base is 30s, plus jitter can add up to 7.5s

    def test_connection_error_classification(self, conn_manager):
        """Test connection error classification."""
        from memory_tool.migrate_out.approval.sse_proxy.connection import SSEConnectionError

        # Create error directly to test classification logic
        error_500 = SSEConnectionError("HTTP 500", is_transient=True)
        error_400 = SSEConnectionError("HTTP 400", is_transient=False)

        assert error_500.is_transient is True
        assert error_400.is_transient is False


class TestSSEProxy:
    """Tests for SSEProxy."""

    @pytest.fixture
    def proxy(self):
        """Fixture for SSEProxy."""
        config = SSEProxyConfig(
            enabled=True,
            buffer_size=100,
            history_minutes=5,
        )
        mock_vps_client = MagicMock()
        return SSEProxy(
            config=config,
            vps_client=mock_vps_client,
        )

    def test_initialization(self, proxy):
        """Test proxy initialization."""
        assert proxy.config is not None
        assert proxy._buffer is not None
        assert proxy._connection is not None

    def test_start_stop(self, proxy):
        """Test starting and stopping the proxy."""
        with patch.object(proxy._connection, "connect") as mock_connect:
            mock_connect.return_value = iter([])

            proxy.start()

            # Give background thread time to start
            time.sleep(0.1)

            assert proxy.is_running() is True

            proxy.stop()

            assert proxy.is_running() is False

    def test_get_metrics(self, proxy):
        """Test getting proxy metrics."""
        metrics = proxy.get_metrics()

        assert "events_received" in metrics
        assert "events_sent" in metrics
        assert "clients_connected" in metrics

    def test_cleanup_stale_clients(self, proxy):
        """Test cleaning up stale clients."""
        # Add a fake client
        proxy._client_queues["client-1"] = deque()
        proxy._client_last_seen["client-1"] = time.time() - 120  # 2 minutes ago

        count = proxy.cleanup_stale_clients(max_age_seconds=60)

        assert count == 1
        assert "client-1" not in proxy._client_queues


class TestSSEConnectionError:
    """Tests for SSEConnectionError."""

    def test_error_creation(self):
        """Test creating connection error."""
        error = SSEConnectionError("Connection failed", is_transient=True)

        assert str(error) == "Connection failed"
        assert error.is_transient is True

    def test_error_with_cause(self):
        """Test error with cause."""
        original = Exception("Network down")
        error = SSEConnectionError("Connection failed", cause=original)

        assert error.cause == original
