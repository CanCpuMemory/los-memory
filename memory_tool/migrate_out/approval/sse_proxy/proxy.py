"""Main SSE Proxy coordinator for event streaming.

This module provides the main SSEProxy class that coordinates between
VPS Agent Web (remote) and local event sources, providing a unified
event stream to clients.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING, Callable, Dict, Generator, Optional, Set

if TYPE_CHECKING:
    from memory_tool.approval_events import ApprovalEvent, EventPublisher

    from ..config import SSEProxyConfig
    from ..vps_client import VPSAgentWebClient

from .buffer import EventBufferManager
from .connection import SSEConnectionError, SSEConnectionManager
from .transform import EventTransformer

logger = logging.getLogger(__name__)


class SSEProxy:
    """Main SSE proxy for bridging local and remote event streams.

    Manages persistent connection to VPS Agent Web SSE endpoint, buffers
events, and provides unified event stream to local consumers.

    Threading Model:
        - Main thread: Client subscription handling
        - Background thread: Remote SSE connection (single shared)
        - Lock: RLock for buffer and subscriber access

    Example:
        proxy = SSEProxy(config, vps_client)

        # Start background connection
        proxy.start()

        try:
            # Client subscribes to unified stream
            for event in proxy.subscribe(last_event_id="..."):
                yield event.to_sse_format()
        finally:
            proxy.stop()
    """

    def __init__(
        self,
        config: "SSEProxyConfig",
        vps_client: "VPSAgentWebClient",
        local_publisher: Optional["EventPublisher"] = None,
    ):
        """Initialize SSE proxy.

        Args:
            config: SSE proxy configuration
            vps_client: VPS Agent Web client for remote connection
            local_publisher: Optional local event publisher for dual-write mode
        """
        self.config = config
        self.vps_client = vps_client
        self.local_publisher = local_publisher

        # Components
        self._buffer = EventBufferManager(
            max_size=config.buffer_size,
            max_age_minutes=config.history_minutes,
        )
        self._connection = SSEConnectionManager(config, vps_client)

        # Threading
        self._lock = threading.RLock()
        self._subscribers: Dict[str, Callable[["ApprovalEvent"], None]] = {}
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

        # Client tracking for cleanup
        self._client_queues: Dict[str, deque] = {}
        self._client_last_seen: Dict[str, float] = {}

        # Metrics
        self._metrics = {
            "events_received": 0,
            "events_sent": 0,
            "reconnections": 0,
            "errors": 0,
            "clients_connected": 0,
        }

    def start(self) -> None:
        """Start background SSE connection worker."""
        with self._lock:
            if self._running:
                logger.debug("SSE proxy already running")
                return

            self._running = True
            self._worker_thread = threading.Thread(
                target=self._connection_worker,
                name="SSEProxyWorker",
                daemon=True,
            )
            self._worker_thread.start()
            logger.info("SSE proxy started")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop background connection gracefully.

        Args:
            timeout: Maximum time to wait for worker thread
        """
        with self._lock:
            self._running = False

        # Signal connection to disconnect
        self._connection.disconnect()

        # Wait for worker thread
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
            if self._worker_thread.is_alive():
                logger.warning("SSE proxy worker did not stop gracefully")

        logger.info("SSE proxy stopped")

    def subscribe(
        self,
        last_event_id: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """Subscribe to unified event stream.

        Yields SSE-formatted events from:
        1. Buffer replay (if last_event_id provided)
        2. Real-time remote events

        Args:
            last_event_id: Last event ID for replay
            client_id: Optional client identifier for tracking

        Yields:
            SSE-formatted event strings
        """
        # Generate client ID if not provided
        client_id = client_id or str(uuid.uuid4())

        # Create client queue
        client_queue: deque = deque()

        with self._lock:
            self._client_queues[client_id] = client_queue
            self._client_last_seen[client_id] = time.time()
            self._metrics["clients_connected"] += 1

        try:
            # 1. Replay buffered events
            if last_event_id:
                buffered = self._buffer.get_since(last_event_id)
                for event in buffered:
                    yield event.to_sse_format()

            # 2. Subscribe to real-time events
            while self._running:
                # Check for new events in client queue
                while client_queue:
                    event = client_queue.popleft()
                    yield event.to_sse_format()
                    self._metrics["events_sent"] += 1

                # Update last seen
                with self._lock:
                    self._client_last_seen[client_id] = time.time()

                # Wait for new events with timeout
                if self._buffer.wait_for_event(timeout=0.1):
                    self._buffer.clear_event_flag()

        except GeneratorExit:
            # Client disconnected
            logger.debug(f"Client {client_id} disconnected from SSE stream")
            raise
        finally:
            # Cleanup
            with self._lock:
                self._client_queues.pop(client_id, None)
                self._client_last_seen.pop(client_id, None)
                self._metrics["clients_connected"] -= 1

    def is_running(self) -> bool:
        """Check if proxy is running.

        Returns:
            True if background worker is running
        """
        return self._running and (
            self._worker_thread is not None and self._worker_thread.is_alive()
        )

    def get_metrics(self) -> Dict[str, int]:
        """Get proxy metrics.

        Returns:
            Dictionary with metrics
        """
        metrics = dict(self._metrics)
        metrics.update(self._buffer.get_metrics())
        metrics["connected"] = 1 if self._connection.is_connected() else 0
        metrics["reconnect_count"] = self._connection.get_reconnect_count()
        return metrics

    def cleanup_stale_clients(self, max_age_seconds: float = 60.0) -> int:
        """Clean up stale client queues.

        Args:
            max_age_seconds: Maximum age before considering stale

        Returns:
            Number of clients cleaned up
        """
        now = time.time()
        stale_clients = []

        with self._lock:
            for client_id, last_seen in list(self._client_last_seen.items()):
                if now - last_seen > max_age_seconds:
                    stale_clients.append(client_id)

            for client_id in stale_clients:
                self._client_queues.pop(client_id, None)
                self._client_last_seen.pop(client_id, None)
                logger.debug(f"Cleaned up stale client: {client_id}")

        return len(stale_clients)

    def _connection_worker(self) -> None:
        """Background worker maintaining SSE connection.

        Runs in separate thread, maintains connection to VPS Agent Web
        and distributes events to subscribers.
        """
        logger.debug("SSE connection worker started")

        while self._running:
            try:
                for event in self._connection.connect(
                    last_event_id=self._get_last_event_id()
                ):
                    if not self._running:
                        break

                    # Store in buffer
                    self._buffer.add(event)
                    self._metrics["events_received"] += 1

                    # Distribute to all client queues
                    self._distribute_event(event)

            except SSEConnectionError as e:
                if not e.is_transient:
                    logger.error(f"Non-transient SSE error: {e}")
                    self._metrics["errors"] += 1
                    break

                self._metrics["reconnections"] += 1
                # Connection manager handles reconnection delay

            except Exception as e:
                logger.exception(f"Unexpected error in SSE worker: {e}")
                self._metrics["errors"] += 1
                time.sleep(1.0)  # Brief pause before retry

        logger.debug("SSE connection worker stopped")

    def _distribute_event(self, event: "ApprovalEvent") -> None:
        """Distribute event to all client queues.

        Args:
            event: Event to distribute
        """
        with self._lock:
            # Add to all client queues
            for queue in self._client_queues.values():
                queue.append(event)

    def _get_last_event_id(self) -> Optional[str]:
        """Get last event ID from buffer for reconnection.

        Returns:
            Last event ID or None
        """
        latest = self._buffer.get_latest(count=1)
        return latest[0].event_id if latest else None
