"""Approval request storage with optimistic locking.

This module provides storage for approval requests with:
- Optimistic locking (version field)
- 48-hour auto-reject scheduler
- Audit logging
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from .utils import utc_now


class ApprovalStatus(Enum):
    """Approval request status."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


@dataclass
class ApprovalRequest:
    """An approval request for L2 recovery.

    Attributes:
        id: Request ID (auto-generated)
        job_id: Associated job/recovery ID
        command: Command to execute
        risk_level: Risk level (low, medium, high, critical)
        status: Current status
        version: Optimistic lock version
        requested_by: User/system requesting approval
        approved_by: User who approved/rejected
        reason: Approval/rejection reason
        created_at: Creation timestamp
        updated_at: Last update timestamp
        expires_at: Auto-reject expiration time
        context: Additional context dictionary
    """
    job_id: str
    command: str
    risk_level: str = "medium"
    status: ApprovalStatus = ApprovalStatus.PENDING
    version: int = 1
    requested_by: Optional[str] = None
    approved_by: Optional[str] = None
    reason: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    expires_at: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None

    def __post_init__(self):
        """Set expiration if not provided (48 hours default)."""
        if self.expires_at is None:
            # Default 48 hour expiration
            expiry = datetime.now() + timedelta(hours=48)
            self.expires_at = expiry.isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "job_id": self.job_id,
            "command": self.command,
            "risk_level": self.risk_level,
            "status": self.status.value,
            "version": self.version,
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApprovalRequest":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            job_id=data["job_id"],
            command=data["command"],
            risk_level=data.get("risk_level", "medium"),
            status=ApprovalStatus(data.get("status", "pending")),
            version=data.get("version", 1),
            requested_by=data.get("requested_by"),
            approved_by=data.get("approved_by"),
            reason=data.get("reason"),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            expires_at=data.get("expires_at"),
            context=data.get("context", {}),
        )


@dataclass
class ApprovalAuditLog:
    """Audit log entry for approval actions."""
    request_id: int
    action: str  # created, approved, rejected, timeout
    actor_id: Optional[str]
    previous_status: Optional[str]
    new_status: str
    version: int
    reason: Optional[str] = None
    timestamp: str = field(default_factory=utc_now)
    id: Optional[int] = None


class ApprovalStore:
    """Store for approval requests with optimistic locking.

    Manages approval request lifecycle with:
    - CRUD operations
    - Optimistic locking for concurrent updates
    - Automatic timeout handling
    - Audit logging

    Example:
        store = ApprovalStore(conn)

        # Create request
        request = store.create(ApprovalRequest(
            job_id="job-123",
            command="restart_service",
            risk_level="high"
        ))

        # Approve with optimistic lock
        success = store.approve(
            request_id=request.id,
            actor_id="user-456",
            version=request.version,
            reason="Verified safe"
        )

        if not success:
            # Version conflict - request was modified
            pass
    """

    AUTO_REJECT_HOURS = 48  # Auto-reject after 48 hours

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Ensure approval tables exist."""
        # Main approval requests table
        self.conn.execute("""
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
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_approval_status
            ON approval_requests(status)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_approval_expires
            ON approval_requests(expires_at)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_approval_job
            ON approval_requests(job_id)
        """)

        # Audit log table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS approval_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                actor_id TEXT,
                previous_status TEXT,
                new_status TEXT NOT NULL,
                version INTEGER NOT NULL,
                reason TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (request_id) REFERENCES approval_requests(id)
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_request
            ON approval_audit_log(request_id)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp
            ON approval_audit_log(timestamp)
        """)
        self.conn.commit()

    def _log_audit(
        self,
        request_id: int,
        action: str,
        actor_id: Optional[str],
        previous_status: Optional[str],
        new_status: str,
        version: int,
        reason: Optional[str] = None,
    ) -> None:
        """Create audit log entry."""
        self.conn.execute(
            """
            INSERT INTO approval_audit_log
            (request_id, action, actor_id, previous_status, new_status, version, reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (request_id, action, actor_id, previous_status, new_status, version, reason, utc_now()),
        )

    def create(self, request: ApprovalRequest) -> ApprovalRequest:
        """Create a new approval request.

        Args:
            request: ApprovalRequest to create

        Returns:
            Created request with ID assigned
        """
        cursor = self.conn.execute(
            """
            INSERT INTO approval_requests
            (job_id, command, risk_level, status, version, requested_by,
             approved_by, reason, created_at, updated_at, expires_at, context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.job_id,
                request.command,
                request.risk_level,
                request.status.value,
                request.version,
                request.requested_by,
                request.approved_by,
                request.reason,
                request.created_at,
                request.updated_at,
                request.expires_at,
                json.dumps(request.context),
            )
        )
        self.conn.commit()

        request.id = cursor.lastrowid

        # Log creation
        self._log_audit(
            request_id=request.id,
            action="created",
            actor_id=request.requested_by,
            previous_status=None,
            new_status=request.status.value,
            version=request.version,
        )
        self.conn.commit()

        return request

    def get_by_id(self, request_id: int) -> Optional[ApprovalRequest]:
        """Get approval request by ID."""
        row = self.conn.execute(
            "SELECT * FROM approval_requests WHERE id = ?",
            (request_id,)
        ).fetchone()

        if not row:
            return None

        return self._row_to_request(row)

    def get_by_job_id(self, job_id: str) -> Optional[ApprovalRequest]:
        """Get approval request by job ID."""
        row = self.conn.execute(
            "SELECT * FROM approval_requests WHERE job_id = ?",
            (job_id,)
        ).fetchone()

        if not row:
            return None

        return self._row_to_request(row)

    def list_pending(self) -> List[ApprovalRequest]:
        """List all pending approval requests."""
        rows = self.conn.execute(
            """
            SELECT * FROM approval_requests
            WHERE status = 'pending'
            ORDER BY created_at ASC
            """
        ).fetchall()

        return [self._row_to_request(row) for row in rows]

    def list_all(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[ApprovalRequest]:
        """List approval requests with optional filter."""
        if status:
            rows = self.conn.execute(
                """
                SELECT * FROM approval_requests
                WHERE status = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (status, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM approval_requests
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()

        return [self._row_to_request(row) for row in rows]

    def approve(
        self,
        request_id: int,
        actor_id: str,
        version: int,
        reason: Optional[str] = None,
    ) -> bool:
        """Approve a request with optimistic locking.

        Args:
            request_id: Request ID
            actor_id: Approving actor
            version: Expected current version
            reason: Optional approval reason

        Returns:
            True if successful, False if version conflict
        """
        now = utc_now()

        # Try to update with expected version
        cursor = self.conn.execute(
            """
            UPDATE approval_requests
            SET status = 'approved',
                approved_by = ?,
                reason = COALESCE(?, reason),
                version = version + 1,
                updated_at = ?
            WHERE id = ? AND version = ? AND status = 'pending'
            """,
            (actor_id, reason, now, request_id, version)
        )
        self.conn.commit()

        if cursor.rowcount == 0:
            # No rows updated - version conflict or not pending
            return False

        # Log approval
        self._log_audit(
            request_id=request_id,
            action="approved",
            actor_id=actor_id,
            previous_status="pending",
            new_status="approved",
            version=version + 1,
            reason=reason,
        )
        self.conn.commit()

        return True

    def reject(
        self,
        request_id: int,
        actor_id: str,
        version: int,
        reason: Optional[str] = None,
    ) -> bool:
        """Reject a request with optimistic locking.

        Args:
            request_id: Request ID
            actor_id: Rejecting actor
            version: Expected current version
            reason: Optional rejection reason

        Returns:
            True if successful, False if version conflict
        """
        now = utc_now()

        cursor = self.conn.execute(
            """
            UPDATE approval_requests
            SET status = 'rejected',
                approved_by = ?,
                reason = COALESCE(?, reason),
                version = version + 1,
                updated_at = ?
            WHERE id = ? AND version = ? AND status = 'pending'
            """,
            (actor_id, reason, now, request_id, version)
        )
        self.conn.commit()

        if cursor.rowcount == 0:
            return False

        self._log_audit(
            request_id=request_id,
            action="rejected",
            actor_id=actor_id,
            previous_status="pending",
            new_status="rejected",
            version=version + 1,
            reason=reason,
        )
        self.conn.commit()

        return True

    def get_expired_pending(self) -> List[ApprovalRequest]:
        """Get pending requests that have exceeded timeout."""
        now = utc_now()
        rows = self.conn.execute(
            """
            SELECT * FROM approval_requests
            WHERE status = 'pending' AND expires_at < ?
            """,
            (now,)
        ).fetchall()

        return [self._row_to_request(row) for row in rows]

    def auto_reject_expired(self) -> List[int]:
        """Auto-reject all expired pending requests.

        Returns:
            List of rejected request IDs
        """
        expired = self.get_expired_pending()
        rejected_ids = []

        for request in expired:
            cursor = self.conn.execute(
                """
                UPDATE approval_requests
                SET status = 'timeout',
                    approved_by = 'system:auto-reject',
                    reason = 'timeout_after_48h',
                    version = version + 1,
                    updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (utc_now(), request.id)
            )

            if cursor.rowcount > 0:
                rejected_ids.append(request.id)
                self._log_audit(
                    request_id=request.id,
                    action="timeout",
                    actor_id="system:auto-reject",
                    previous_status="pending",
                    new_status="timeout",
                    version=request.version + 1,
                    reason="Auto-rejected after 48 hours",
                )

        self.conn.commit()
        return rejected_ids

    def get_audit_log(
        self,
        request_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get audit log entries."""
        if request_id:
            rows = self.conn.execute(
                """
                SELECT * FROM approval_audit_log
                WHERE request_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (request_id, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM approval_audit_log
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()

        return [dict(row) for row in rows]

    def _row_to_request(self, row: sqlite3.Row) -> ApprovalRequest:
        """Convert database row to ApprovalRequest."""
        return ApprovalRequest(
            id=row["id"],
            job_id=row["job_id"],
            command=row["command"],
            risk_level=row["risk_level"],
            status=ApprovalStatus(row["status"]),
            version=row["version"],
            requested_by=row["requested_by"],
            approved_by=row["approved_by"],
            reason=row["reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
            context=json.loads(row["context"]) if row["context"] else {},
        )
