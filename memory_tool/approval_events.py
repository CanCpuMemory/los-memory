"""SSE event streaming for approval workflows.

This module provides Server-Sent Events (SSE) for real-time approval
notifications, following P2 Approval Workflow Best Practices.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Set

from .utils import utc_now


# Event format constants
EVENT_HISTORY_MINUTES = 5  # Keep 5 minutes of history for replay


@dataclass
class ApprovalEvent:
    """An approval event for SSE streaming.

    Attributes:
        event_type: Type of event (approval.pending, approval.approved, etc.)
        event_id: Unique event ID for replay/ordering
        data: Event payload dictionary
        timestamp: ISO timestamp
    """
    event_type: str
    event_id: str
    data: Dict[str, Any]
    timestamp: str = field(default_factory=utc_now)

    def to_sse_format(self) -> str:
        """Convert to SSE format string."""
        lines = [
            f"event: {self.event_type}",
            f"id: {self.event_id}",
            f"data: {json.dumps(self.data)}",
            "",  # Empty line separates events
        ]
        return "\n".join(lines) + "\n"


class EventHistoryBuffer:
    """Circular buffer for event history with time-based eviction.

    Maintains last N minutes of events for replay on reconnection.
    """

    def __init__(self, max_age_minutes: int = EVENT_HISTORY_MINUTES):
        self.max_age_seconds = max_age_minutes * 60
        self._events: deque = deque()
        self._lock = threading.RLock()

    def add(self, event: ApprovalEvent) -> None:
        """Add event to buffer."""
        with self._lock:
            self._events.append(event)
            self._cleanup()

    def get_since(self, last_event_id: Optional[str] = None) -> List[ApprovalEvent]:
        """Get events since given event ID.

        If last_event_id is None, returns all events in buffer.
        If last_event_id not found, returns all events (client missed window).
        """
        with self._lock:
            events = list(self._events)

        if not last_event_id:
            return events

        # Find position of last seen event
        for i, event in enumerate(events):
            if event.event_id == last_event_id:
                return events[i + 1:]

        # Event ID not found - client missed the window, send all
        return events

    def _cleanup(self) -> None:
        """Remove events older than max age."""
        now = time.time()
        cutoff = now - self.max_age_seconds

        while self._events:
            # Parse ISO timestamp to epoch
            try:
                from datetime import datetime
                first = self._events[0]
                dt = datetime.fromisoformat(first.timestamp.replace('Z', '+00:00'))
                event_time = dt.timestamp()

                if event_time < cutoff:
                    self._events.popleft()
                else:
                    break
            except (ValueError, IndexError):
                break


class EventPublisher:
    """Publisher for approval events with SSE support.

    Manages event publishing, history, and subscriber notifications.
    Follows P2 Approval Workflow spec for event types and format.

    Example:
        publisher = EventPublisher(conn)

        # Publish an event
        publisher.publish_pending(
            job_id="job-123",
            command="restart",
            risk_level="high"
        )

        # Get SSE stream for client
        for sse_data in publisher.subscribe(last_event_id="..."):
            yield sse_data
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._history = EventHistoryBuffer()
        self._subscribers: Set[Callable[[ApprovalEvent], None]] = set()
        self._lock = threading.RLock()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Ensure event tables exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS approval_events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                job_id TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_approval_events_job
            ON approval_events(job_id)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_approval_events_created
            ON approval_events(created_at)
        """)
        self.conn.commit()

    def _generate_event_id(self) -> str:
        """Generate unique event ID."""
        import uuid
        return str(uuid.uuid4())

    def _persist_event(self, event: ApprovalEvent) -> None:
        """Persist event to database."""
        self.conn.execute(
            """
            INSERT INTO approval_events (id, event_type, job_id, data, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type,
                event.data.get("job_id", ""),
                json.dumps(event.data),
                event.timestamp,
            )
        )
        self.conn.commit()

    def publish(
        self,
        event_type: str,
        job_id: str,
        data: Dict[str, Any],
    ) -> ApprovalEvent:
        """Publish an approval event.

        Args:
            event_type: Event type (approval.pending, etc.)
            job_id: Associated job ID
            data: Event payload

        Returns:
            Created ApprovalEvent
        """
        event = ApprovalEvent(
            event_type=event_type,
            event_id=self._generate_event_id(),
            data={"job_id": job_id, **data},
        )

        # Persist to database
        self._persist_event(event)

        # Add to history buffer
        self._history.add(event)

        # Notify subscribers
        with self._lock:
            for callback in self._subscribers:
                try:
                    callback(event)
                except Exception:
                    pass  # Don't let subscriber errors break publishing

        return event

    def publish_pending(
        self,
        job_id: str,
        command: str,
        risk_level: str,
        actor_id: Optional[str] = None,
    ) -> ApprovalEvent:
        """Publish approval.pending event.

        Sent when a job requires manual approval.
        """
        return self.publish(
            event_type="approval.pending",
            job_id=job_id,
            data={
                "command": command,
                "risk_level": risk_level,
                "actor_id": actor_id,
                "status": "pending_approval",
            },
        )

    def publish_approved(
        self,
        job_id: str,
        actor_id: str,
        version: int,
        reason: Optional[str] = None,
    ) -> ApprovalEvent:
        """Publish approval.approved event.

        Sent when a job is approved.
        """
        return self.publish(
            event_type="approval.approved",
            job_id=job_id,
            data={
                "actor_id": actor_id,
                "version": version,
                "reason": reason,
                "status": "approved",
            },
        )

    def publish_rejected(
        self,
        job_id: str,
        actor_id: str,
        version: int,
        reason: Optional[str] = None,
    ) -> ApprovalEvent:
        """Publish approval.rejected event.

        Sent when a job is rejected.
        """
        return self.publish(
            event_type="approval.rejected",
            job_id=job_id,
            data={
                "actor_id": actor_id,
                "version": version,
                "reason": reason,
                "status": "rejected",
            },
        )

    def publish_timeout(
        self,
        job_id: str,
        timeout_hours: int = 48,
    ) -> ApprovalEvent:
        """Publish approval.rejected event for timeout.

        Sent when a pending job auto-rejects due to timeout.
        """
        return self.publish(
            event_type="approval.rejected",
            job_id=job_id,
            data={
                "actor_id": "system:auto-reject",
                "reason": f"timeout_after_{timeout_hours}h",
                "status": "rejected",
                "auto": True,
            },
        )

    def subscribe(
        self,
        last_event_id: Optional[str] = None,
    ) -> Iterator[str]:
        """Subscribe to events as SSE stream.

        Args:
            last_event_id: Last event ID client received (for replay)

        Yields:
            SSE-formatted event strings
        """
        # Send any missed events from history
        missed = self._history.get_since(last_event_id)
        for event in missed:
            yield event.to_sse_format()

        # Add to active subscribers for real-time events
        queue: deque = deque()

        def callback(event: ApprovalEvent) -> None:
            queue.append(event)

        with self._lock:
            self._subscribers.add(callback)

        try:
            while True:
                # Yield any queued events
                while queue:
                    yield queue.popleft().to_sse_format()
                time.sleep(0.1)  # Small delay to prevent busy-waiting
        finally:
            with self._lock:
                self._subscribers.discard(callback)

    def get_event_history(
        self,
        job_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get event history from database.

        Args:
            job_id: Filter by job ID (optional)
            limit: Maximum results

        Returns:
            List of event dictionaries
        """
        if job_id:
            rows = self.conn.execute(
                """
                SELECT * FROM approval_events
                WHERE job_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (job_id, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM approval_events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()

        return [
            {
                "event_id": row["id"],
                "event_type": row["event_type"],
                "job_id": row["job_id"],
                "data": json.loads(row["data"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def create_sse_response_headers() -> Dict[str, str]:
    """Create standard SSE HTTP response headers."""
    return {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
