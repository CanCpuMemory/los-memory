"""Dual-write manager for migration period.

This module manages writing to both los-memory (local) and VPS Agent Web (remote)
during the migration transition period.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, TypeVar, Union

if TYPE_CHECKING:
    from .config import DualWriteConfig
    from .vps_client import VPSAgentWebClient

import sqlite3

from memory_tool.approval_api import ApprovalAPI

from .config import DualWriteMode

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Type alias for connection or factory
ConnectionSource = Union[sqlite3.Connection, Callable[[], sqlite3.Connection]]


@dataclass
class DualWriteResult:
    """Result of a dual-write operation."""

    success: bool
    local_success: bool
    remote_success: bool
    local_result: Optional[Dict[str, Any]] = None
    remote_result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "local_success": self.local_success,
            "remote_success": self.remote_success,
            "local_result": self.local_result,
            "remote_result": self.remote_result,
            "error_message": self.error_message,
        }


class DualWriteError(Exception):
    """Error during dual-write operation."""

    def __init__(
        self,
        message: str,
        local_result: Optional[Dict] = None,
        remote_result: Optional[Dict] = None,
    ):
        self.local_result = local_result
        self.remote_result = remote_result
        super().__init__(message)


class DualWriteManager:
    """Manages dual-write operations with configurable failure handling.

    This class coordinates writes to both los-memory (local SQLite) and
    VPS Agent Web (remote HTTP) during the migration period.

    Supports four modes:
    - STRICT: Both writes must succeed
    - LOCAL_PREFERRED: Local success = overall success
    - REMOTE_PREFERRED: Remote success = overall success
    - READ_ONLY: No writes allowed

    Thread Safety:
        This class is thread-safe. It uses thread-local storage for SQLite
        connections to prevent "objects created in a thread can only be used
        in that same thread" errors.

    Example:
        config = DualWriteConfig(mode=DualWriteMode.STRICT)
        manager = DualWriteManager(
            config=config,
            local_conn_factory=lambda: get_connection(),
            remote_client=vps_client
        )

        result = manager.create_request(
            job_id="job-123",
            command="restart_service",
            risk_level="high"
        )
    """

    def __init__(
        self,
        config: "DualWriteConfig",
        local_conn: ConnectionSource,
        remote_client: "VPSAgentWebClient",
    ):
        self.config = config
        self.remote_client = remote_client

        # Handle both connection factory and direct connection
        if callable(local_conn):
            self._conn_factory: Callable[[], sqlite3.Connection] = local_conn
            self._shared_conn: Optional[sqlite3.Connection] = None
        else:
            # For backward compatibility, store as factory
            self._shared_conn = local_conn
            self._conn_factory = lambda: self._shared_conn  # type: ignore

        # Thread-local storage for per-thread connections and APIs
        self._local = threading.local()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local connection or create new one."""
        if self._shared_conn is not None:
            # Using shared connection (backward compatibility mode)
            # Note: This is NOT thread-safe but maintains compatibility
            return self._shared_conn

        # Check if this thread already has a connection
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = self._conn_factory()
        return self._local.connection

    def _get_local_api(self) -> ApprovalAPI:
        """Get or create thread-local ApprovalAPI instance."""
        if not hasattr(self._local, "api") or self._local.api is None:
            self._local.api = ApprovalAPI(self._get_connection())
        return self._local.api

    def close_thread_connections(self) -> None:
        """Close connections for the current thread.

        Call this when done with operations in a thread to release resources.
        """
        if hasattr(self._local, "connection") and self._local.connection:
            try:
                self._local.connection.close()
            except Exception as e:
                logger.warning(f"Error closing thread connection: {e}")
            self._local.connection = None
            self._local.api = None

    def _execute_with_fallback(
        self,
        operation: str,
        local_fn: Callable[[], T],
        remote_fn: Callable[[], T],
    ) -> DualWriteResult:
        """Execute operation on both systems with fallback handling.

        Args:
            operation: Operation name for logging
            local_fn: Function to execute locally
            remote_fn: Function to execute remotely

        Returns:
            DualWriteResult with combined status
        """
        local_result: Optional[Dict] = None
        remote_result: Optional[Dict] = None
        local_success = False
        remote_success = False

        # Execute based on mode
        if self.config.mode == DualWriteMode.READ_ONLY:
            return DualWriteResult(
                success=False,
                local_success=False,
                remote_success=False,
                error_message="Read-only mode - writes not allowed",
            )

        # Try local first
        try:
            local_result = local_fn()
            local_success = local_result.get("success", True) if isinstance(local_result, dict) else True
        except Exception as e:
            logger.error(f"Local {operation} failed: {e}")
            local_result = {"success": False, "error": str(e)}
            local_success = False

        # Try remote
        try:
            remote_result = remote_fn()
            remote_success = remote_result.get("success", True) if isinstance(remote_result, dict) else True
        except Exception as e:
            logger.error(f"Remote {operation} failed: {e}")
            remote_result = {"success": False, "error": str(e)}
            remote_success = False

        # Determine overall success based on mode
        if self.config.mode == DualWriteMode.STRICT:
            success = local_success and remote_success
        elif self.config.mode == DualWriteMode.LOCAL_PREFERRED:
            success = local_success
        elif self.config.mode == DualWriteMode.REMOTE_PREFERRED:
            success = remote_success
        else:
            success = False

        # Build error message if needed
        error_message = None
        if not success:
            errors = []
            if not local_success and self.config.mode in (DualWriteMode.STRICT, DualWriteMode.LOCAL_PREFERRED):
                errors.append(f"Local: {local_result.get('error', 'failed')}")
            if not remote_success and self.config.mode in (DualWriteMode.STRICT, DualWriteMode.REMOTE_PREFERRED):
                errors.append(f"Remote: {remote_result.get('error', 'failed')}")
            error_message = "; ".join(errors) if errors else "Operation failed"

        return DualWriteResult(
            success=success,
            local_success=local_success,
            remote_success=remote_success,
            local_result=local_result,
            remote_result=remote_result,
            error_message=error_message,
        )

    def create_request(
        self,
        job_id: str,
        command: str,
        risk_level: str = "medium",
        requested_by: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> DualWriteResult:
        """Create approval request on both systems.

        Args:
            job_id: Unique job identifier
            command: Command to execute upon approval
            risk_level: Risk level (low, medium, high, critical)
            requested_by: Requesting actor
            context: Additional context

        Returns:
            DualWriteResult with results from both systems
        """
        return self._execute_with_fallback(
            operation="create_request",
            local_fn=lambda: self._get_local_api().create_request(
                job_id=job_id,
                command=command,
                risk_level=risk_level,
                requested_by=requested_by,
                context=context,
            ),
            remote_fn=lambda: self.remote_client.create_request(
                job_id=job_id,
                command=command,
                risk_level=risk_level,
                requested_by=requested_by,
                context=context,
            ),
        )

    def approve_request(
        self,
        job_id: str,
        actor_id: str,
        version: int,
        hmac_headers: Optional[Dict[str, str]] = None,
        reason: Optional[str] = None,
    ) -> DualWriteResult:
        """Approve request on both systems.

        Args:
            job_id: Job ID to approve
            actor_id: Approving actor
            version: Expected version
            hmac_headers: HMAC signature headers
            reason: Optional approval reason

        Returns:
            DualWriteResult with results from both systems
        """
        return self._execute_with_fallback(
            operation="approve_request",
            local_fn=lambda: self._get_local_api().approve_request(
                job_id=job_id,
                actor_id=actor_id,
                version=version,
                reason=reason,
                hmac_headers=hmac_headers,
            ),
            remote_fn=lambda: self.remote_client.approve_request(
                job_id=job_id,
                actor_id=actor_id,
                version=version,
                hmac_headers=hmac_headers,
                reason=reason,
            ),
        )

    def reject_request(
        self,
        job_id: str,
        actor_id: str,
        version: int,
        hmac_headers: Optional[Dict[str, str]] = None,
        reason: Optional[str] = None,
    ) -> DualWriteResult:
        """Reject request on both systems.

        Args:
            job_id: Job ID to reject
            actor_id: Rejecting actor
            version: Expected version
            hmac_headers: HMAC signature headers
            reason: Optional rejection reason

        Returns:
            DualWriteResult with results from both systems
        """
        return self._execute_with_fallback(
            operation="reject_request",
            local_fn=lambda: self._get_local_api().reject_request(
                job_id=job_id,
                actor_id=actor_id,
                version=version,
                reason=reason,
                hmac_headers=hmac_headers,
            ),
            remote_fn=lambda: self.remote_client.reject_request(
                job_id=job_id,
                actor_id=actor_id,
                version=version,
                hmac_headers=hmac_headers,
                reason=reason,
            ),
        )

    def get_request_status(
        self,
        job_id: str,
        prefer_remote: bool = False,
    ) -> Dict[str, Any]:
        """Get request status from preferred source.

        Args:
            job_id: Job ID to query
            prefer_remote: If True, try remote first

        Returns:
            Request status from the preferred source
        """
        if prefer_remote:
            # Try remote first, fallback to local
            try:
                return self.remote_client.get_request_status(job_id)
            except Exception as e:
                logger.warning(f"Remote status query failed, using local: {e}")
                return self._get_local_api().get_request_status(job_id)
        else:
            # Try local first, fallback to remote
            try:
                return self._get_local_api().get_request_status(job_id)
            except Exception as e:
                logger.warning(f"Local status query failed, using remote: {e}")
                return self.remote_client.get_request_status(job_id)

    def list_requests(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        prefer_remote: bool = False,
    ) -> Dict[str, Any]:
        """List requests from preferred source.

        Args:
            status: Filter by status
            limit: Maximum results
            prefer_remote: If True, query remote first

        Returns:
            List of requests from preferred source
        """
        if prefer_remote:
            try:
                return self.remote_client.list_requests(status=status, limit=limit)
            except Exception as e:
                logger.warning(f"Remote list failed, using local: {e}")
                if status:
                    return self._get_local_api().list_all_requests(status=status, limit=limit)
                else:
                    return self._get_local_api().list_all_requests(limit=limit)
        else:
            try:
                if status:
                    return self._get_local_api().list_all_requests(status=status, limit=limit)
                else:
                    return self._get_local_api().list_all_requests(limit=limit)
            except Exception as e:
                logger.warning(f"Local list failed, using remote: {e}")
                return self.remote_client.list_requests(status=status, limit=limit)

    def get_migration_statistics(self) -> Dict[str, Any]:
        """Get statistics about dual-write operations.

        Returns:
            Dictionary with migration statistics
        """
        local_count = 0
        remote_count = 0

        try:
            local_result = self._get_local_api().list_all_requests(limit=10000)
            local_count = local_result.get("count", 0)
        except Exception as e:
            logger.error(f"Failed to get local statistics: {e}")

        try:
            remote_result = self.remote_client.list_requests(limit=10000)
            remote_count = remote_result.get("count", 0)
        except Exception as e:
            logger.error(f"Failed to get remote statistics: {e}")

        return {
            "mode": self.config.mode.value,
            "local_requests_count": local_count,
            "remote_requests_count": remote_count,
            "sync_needed": local_count != remote_count,
            "sync_difference": abs(local_count - remote_count),
        }
