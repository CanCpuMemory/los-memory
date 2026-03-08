"""Event buffer management for SSE proxy.

This module provides thread-safe event buffering with time-based eviction
for replay support on client reconnection.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from memory_tool.approval_events import ApprovalEvent

logger = logging.getLogger(__name__)


class EventBufferManager:
    """Thread-safe event buffer with time-based eviction.

    Combines circular buffer with fast ID lookup for efficient event replay
    on client reconnection. Automatically evicts old events based on time.

    Thread Safety:
        All methods use RLock for thread-safe access across multiple
        producer and consumer threads.

    Example:
        buffer = EventBufferManager(max_size=1000, max_age_minutes=5)

        # Producer thread
        buffer.add(event)

        # Consumer thread
        missed = buffer.get_since(last_event_id="uuid-123")
        for event in missed:
            process(event)
    """

    def __init__(
        self,
        max_size: int = 1000,
        max_age_minutes: int = 5,
    ):
        """Initialize event buffer.

        Args:
            max_size: Maximum number of events to keep in buffer
            max_age_minutes: Maximum age of events before eviction
        """
        self.max_size = max_size
        self.max_age_seconds = max_age_minutes * 60

        # Circular buffer for events
        self._buffer: deque = deque(maxlen=max_size)

        # Fast ID lookup index
        self._index: Dict[str, "ApprovalEvent"] = {}

        # Thread safety
        self._lock = threading.RLock()
        self._event_available = threading.Event()

        # Metrics
        self._total_added = 0
        self._total_evicted = 0

    def add(self, event: "ApprovalEvent") -> None:
        """Add event to buffer (thread-safe).

        Args:
            event: ApprovalEvent to add to buffer
        """
        with self._lock:
            # Add to circular buffer
            if len(self._buffer) >= self.max_size:
                # Deque will automatically evict oldest, but we need to
                # clean up the index
                self._evict_oldest_if_needed()

            self._buffer.append(event)
            self._index[event.event_id] = event
            self._total_added += 1

            # Cleanup old events
            self._cleanup()

            # Signal waiting consumers
            self._event_available.set()

    def get_since(
        self,
        last_event_id: Optional[str] = None,
    ) -> List["ApprovalEvent"]:
        """Get events since given event ID.

        If last_event_id is not found in buffer, returns all available
        events (client missed the window).

        Args:
            last_event_id: Last event ID client has seen, or None for all

        Returns:
            List of events since last_event_id (or all events)
        """
        with self._lock:
            events = list(self._buffer)

            if not last_event_id:
                return events

            # Fast lookup using index
            if last_event_id in self._index:
                # Find position in deque
                for i, e in enumerate(events):
                    if e.event_id == last_event_id:
                        return events[i + 1:]

            # ID not found - client missed window, return all
            logger.warning(
                f"Event ID {last_event_id} not in buffer, "
                f"returning all {len(events)} events"
            )
            return events

    def get_latest(self, count: int = 1) -> List["ApprovalEvent"]:
        """Get latest N events.

        Args:
            count: Number of latest events to retrieve

        Returns:
            List of latest events (newest first)
        """
        with self._lock:
            events = list(self._buffer)
            return events[-count:] if events else []

    def wait_for_event(self, timeout: Optional[float] = None) -> bool:
        """Wait for a new event to be added.

        Args:
            timeout: Maximum time to wait in seconds, or None for indefinite

        Returns:
            True if event available, False if timeout
        """
        return self._event_available.wait(timeout=timeout)

    def clear_event_flag(self) -> None:
        """Clear the event available flag.

        Call this after processing events to wait for next batch.
        """
        self._event_available.clear()

    def contains(self, event_id: str) -> bool:
        """Check if event ID is in buffer.

        Args:
            event_id: Event ID to check

        Returns:
            True if event is in buffer
        """
        with self._lock:
            return event_id in self._index

    def get_event(self, event_id: str) -> Optional["ApprovalEvent"]:
        """Get specific event by ID.

        Args:
            event_id: Event ID to retrieve

        Returns:
            Event if found, None otherwise
        """
        with self._lock:
            return self._index.get(event_id)

    def size(self) -> int:
        """Get current buffer size.

        Returns:
            Number of events in buffer
        """
        with self._lock:
            return len(self._buffer)

    def get_metrics(self) -> Dict[str, int]:
        """Get buffer metrics.

        Returns:
            Dictionary with buffer statistics
        """
        with self._lock:
            return {
                "size": len(self._buffer),
                "max_size": self.max_size,
                "total_added": self._total_added,
                "total_evicted": self._total_evicted,
                "index_size": len(self._index),
            }

    def _evict_oldest_if_needed(self) -> None:
        """Evict oldest event if buffer is full.

        Must be called with lock held.
        """
        if self._buffer:
            oldest = self._buffer[0]
            self._index.pop(oldest.event_id, None)
            self._total_evicted += 1

    def _cleanup(self) -> None:
        """Remove expired events based on age.

        Must be called with lock held.
        """
        if not self._buffer:
            return

        now = time.time()
        cutoff = now - self.max_age_seconds

        # Remove old events from front of deque
        while self._buffer:
            first = self._buffer[0]

            # Parse timestamp
            try:
                dt = datetime.fromisoformat(first.timestamp.replace("Z", "+00:00"))
                event_time = dt.timestamp()
            except (ValueError, AttributeError):
                # Can't parse, assume old and remove
                event_time = 0

            if event_time < cutoff:
                self._buffer.popleft()
                self._index.pop(first.event_id, None)
                self._total_evicted += 1
            else:
                break

    def clear(self) -> None:
        """Clear all events from buffer."""
        with self._lock:
            self._buffer.clear()
            self._index.clear()
            self._event_available.clear()
