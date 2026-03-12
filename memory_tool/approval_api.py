"""Internal API for L2 approval workflow.

This module provides the business logic for approval requests,
integrating HMAC security, SSE events, and storage.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

from .approval_events import EventPublisher
from .approval_security import HMACConfig, HMACValidator, generate_hmac_headers
from .approval_store import ApprovalRequest, ApprovalStatus, ApprovalStore


# Error codes per spec
ERROR_CODES = {
    "VALIDATION_ERROR": ("400_BAD_REQUEST", 400),
    "UNAUTHORIZED": ("401_UNAUTHORIZED", 401),
    "JOB_NOT_FOUND": ("404_JOB_NOT_FOUND", 404),
    "VERSION_CONFLICT": ("409_APPROVAL_VERSION_CONFLICT", 409),
    "ALREADY_DECIDED": ("409_ALREADY_DECIDED", 409),
}


class ApprovalAPIError(Exception):
    """Approval API error with code and HTTP status."""

    def __init__(self, error_code: str, message: str, details: Optional[Dict] = None):
        self.error_code = error_code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": {
                "code": self.error_code,
                "message": self.message,
                **self.details,
            }
        }


class ApprovalAPI:
    """Internal API for approval workflow management.

    Integrates storage, events, and security for complete
    approval request lifecycle management.

    Example:
        api = ApprovalAPI(conn, hmac_config)

        # Create request
        result = api.create_request(
            job_id="job-123",
            command="restart_service",
            risk_level="high"
        )

        # Approve with HMAC verification
        result = api.approve_request(
            job_id="job-123",
            actor_id="user-456",
            version=1,
            hmac_headers={...}
        )
    """

    def __init__(
        self,
        conn,
        hmac_config: Optional[HMACConfig] = None,
    ):
        self.conn = conn
        self.store = ApprovalStore(conn)
        self.publisher = EventPublisher(conn)
        self.validator = HMACValidator(hmac_config) if hmac_config else None

    def create_request(
        self,
        job_id: str,
        command: str,
        risk_level: str = "medium",
        requested_by: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new approval request.

        Args:
            job_id: Unique job identifier
            command: Command to execute upon approval
            risk_level: Risk level (low, medium, high, critical)
            requested_by: Requesting actor
            context: Additional context

        Returns:
            Dict with request details and event info

        Raises:
            ApprovalAPIError: If validation fails or job exists
        """
        self._raise_if_request_exists(job_id)
        self._validate_create_risk_level(risk_level)
        request = self._build_create_request(
            job_id=job_id,
            command=command,
            risk_level=risk_level,
            requested_by=requested_by,
            context=context,
        )
        created, event = self._persist_create_request_and_event(request)

        # Publish to in-memory subscribers only after DB transaction commits.
        self.publisher.broadcast(event)
        return self._build_create_request_response(created, event.event_id)

    def _raise_if_request_exists(self, job_id: str) -> None:
        existing = self.store.get_by_job_id(job_id)
        if not existing:
            return
        raise ApprovalAPIError(
            "409_ALREADY_DECIDED",
            f"Approval request already exists for job {job_id}",
            {"existing_status": existing.status.value},
        )

    def _validate_create_risk_level(self, risk_level: str) -> None:
        valid_risks = ["low", "medium", "high", "critical"]
        if risk_level in valid_risks:
            return
        raise ApprovalAPIError(
            "400_BAD_REQUEST",
            f"Invalid risk_level: {risk_level}",
            {"valid_values": valid_risks},
        )

    def _build_create_request(
        self,
        job_id: str,
        command: str,
        risk_level: str,
        requested_by: Optional[str],
        context: Optional[Dict[str, Any]],
    ) -> ApprovalRequest:
        return ApprovalRequest(
            job_id=job_id,
            command=command,
            risk_level=risk_level,
            requested_by=requested_by,
            context=context or {},
        )

    def _persist_create_request_and_event(
        self,
        request: ApprovalRequest,
    ) -> Tuple[ApprovalRequest, Any]:
        try:
            with self.conn:
                created = self.store.create(request, commit=False)
                event = self.publisher.publish_pending(
                    job_id=request.job_id,
                    command=request.command,
                    risk_level=request.risk_level,
                    actor_id=request.requested_by,
                    commit=False,
                    broadcast=False,
                )
                return created, event
        except sqlite3.IntegrityError:
            raise ApprovalAPIError(
                "409_ALREADY_DECIDED",
                f"Approval request already exists for job {request.job_id}",
            )

    def _build_create_request_response(
        self,
        created: ApprovalRequest,
        event_id: str,
    ) -> Dict[str, Any]:
        return {
            "success": True,
            "request": created.to_dict(),
            "event_id": event_id,
        }

    def approve_request(
        self,
        job_id: str,
        actor_id: str,
        version: int,
        reason: Optional[str] = None,
        hmac_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Approve a request with optimistic locking.

        Args:
            job_id: Job ID to approve
            actor_id: Approving actor
            version: Expected current version (optimistic lock)
            reason: Optional approval reason
            hmac_headers: HMAC verification headers (if required)

        Returns:
            Dict with approval result

        Raises:
            ApprovalAPIError: If HMAC invalid, version conflict, etc.
        """
        return self._decide_request(
            job_id=job_id,
            actor_id=actor_id,
            version=version,
            reason=reason,
            hmac_headers=hmac_headers,
            action="approve",
            status="approved",
            store_action=self.store.approve,
            event_publisher=self.publisher.publish_approved,
            include_current_status_on_conflict=True,
        )

    def reject_request(
        self,
        job_id: str,
        actor_id: str,
        version: int,
        reason: Optional[str] = None,
        hmac_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Reject a request with optimistic locking.

        Args:
            job_id: Job ID to reject
            actor_id: Rejecting actor
            version: Expected current version (optimistic lock)
            reason: Optional rejection reason
            hmac_headers: HMAC verification headers (if required)

        Returns:
            Dict with rejection result

        Raises:
            ApprovalAPIError: If HMAC invalid, version conflict, etc.
        """
        return self._decide_request(
            job_id=job_id,
            actor_id=actor_id,
            version=version,
            reason=reason,
            hmac_headers=hmac_headers,
            action="reject",
            status="rejected",
            store_action=self.store.reject,
            event_publisher=self.publisher.publish_rejected,
            include_current_status_on_conflict=False,
        )

    def get_request_status(self, job_id: str) -> Dict[str, Any]:
        """Get approval request status.

        Args:
            job_id: Job ID to query

        Returns:
            Dict with request details

        Raises:
            ApprovalAPIError: If job not found
        """
        request = self.store.get_by_job_id(job_id)
        if not request:
            raise ApprovalAPIError(
                "404_JOB_NOT_FOUND",
                f"Approval request not found for job {job_id}"
            )

        return {
            "success": True,
            "request": request.to_dict(),
        }

    def list_pending_requests(
        self,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """List pending approval requests.

        Args:
            limit: Maximum results

        Returns:
            Dict with list of pending requests
        """
        requests = self.store.list_pending()

        return {
            "success": True,
            "count": len(requests),
            "requests": [r.to_dict() for r in requests[:limit]],
        }

    def list_all_requests(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """List all approval requests with optional filter.

        Args:
            status: Filter by status (optional)
            limit: Maximum results

        Returns:
            Dict with list of requests
        """
        requests = self.store.list_all(status=status, limit=limit)

        return {
            "success": True,
            "count": len(requests),
            "requests": [r.to_dict() for r in requests],
        }

    def get_event_stream(self, last_event_id: Optional[str] = None):
        """Get SSE event stream for real-time updates.

        Args:
            last_event_id: Last event ID for replay

        Yields:
            SSE-formatted event strings
        """
        return self.publisher.subscribe(last_event_id=last_event_id)

    def get_event_history(
        self,
        job_id: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Get event history.

        Args:
            job_id: Filter by job ID
            limit: Maximum results

        Returns:
            Dict with event history
        """
        events = self.publisher.get_event_history(job_id=job_id, limit=limit)

        return {
            "success": True,
            "count": len(events),
            "events": events,
        }

    def get_audit_log(
        self,
        job_id: str,
    ) -> Dict[str, Any]:
        """Get audit log for a job.

        Args:
            job_id: Job ID to query

        Returns:
            Dict with audit log entries
        """
        request = self.store.get_by_job_id(job_id)
        if not request:
            raise ApprovalAPIError(
                "404_JOB_NOT_FOUND",
                f"Approval request not found for job {job_id}"
            )

        log = self.store.get_audit_log(request_id=request.id)

        return {
            "success": True,
            "job_id": job_id,
            "entries": log,
        }

    def run_auto_reject(self) -> Dict[str, Any]:
        """Run auto-reject scheduler for expired requests.

        Returns:
            Dict with rejected job IDs
        """
        events = []
        rejected_job_ids: List[str] = []
        with self.conn:
            rejected_ids = self.store.auto_reject_expired(commit=False)

            # Persist events for auto-rejected in same transaction.
            for req_id in rejected_ids:
                request = self.store.get_by_id(req_id)
                if not request:
                    continue
                rejected_job_ids.append(request.job_id)
                event = self.publisher.publish_timeout(
                    job_id=request.job_id,
                    timeout_hours=48,
                    commit=False,
                    broadcast=False,
                )
                events.append(event)

        # Publish to in-memory subscribers only after DB transaction commits.
        for event in events:
            self.publisher.broadcast(event)

        return {
            "success": True,
            "rejected_count": len(rejected_ids),
            "rejected_job_ids": rejected_job_ids,
        }

    def _verify_hmac(
        self,
        job_id: str,
        action: str,
        actor_id: str,
        version: int,
        reason: Optional[str],
        headers: Dict[str, str],
    ) -> None:
        """Verify HMAC signature.

        Raises:
            ApprovalAPIError: If HMAC verification fails
        """
        if not self.validator:
            return

        payload = {
            "job_id": job_id,
            "action": action,
            "actor_id": actor_id,
            "version": version,
            "reason": reason or "",
        }

        try:
            timestamp = int(headers.get("X-Timestamp", 0))
        except (TypeError, ValueError):
            raise ApprovalAPIError(
                "400_BAD_REQUEST",
                "Invalid X-Timestamp header"
            )

        result = self.validator.verify(
            signature=headers.get("X-Signature", ""),
            timestamp=timestamp,
            nonce=headers.get("X-Nonce", ""),
            payload=payload,
        )

        if not result["valid"]:
            raise ApprovalAPIError(
                "401_UNAUTHORIZED",
                f"HMAC verification failed: {result.get('error', 'unknown')}",
                {"details": result.get("message", "")}
            )

    def _get_pending_request(self, job_id: str) -> ApprovalRequest:
        """Load request by job id and ensure it is still pending."""
        request = self.store.get_by_job_id(job_id)
        if not request:
            raise ApprovalAPIError(
                "404_JOB_NOT_FOUND",
                f"Approval request not found for job {job_id}"
            )
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalAPIError(
                "409_ALREADY_DECIDED",
                f"Request already {request.status.value}",
                {"current_status": request.status.value}
            )
        return request

    def _build_version_conflict_details(
        self,
        current: Optional[ApprovalRequest],
        expected_version: int,
        include_current_status: bool,
    ) -> Dict[str, Any]:
        details: Dict[str, Any] = {
            "current_version": current.version if current else None,
            "expected_version": expected_version,
        }
        if include_current_status:
            details["current_status"] = current.status.value if current else None
        return details

    def _decide_request(
        self,
        job_id: str,
        actor_id: str,
        version: int,
        reason: Optional[str],
        hmac_headers: Optional[Dict[str, str]],
        action: str,
        status: str,
        store_action: Callable[..., bool],
        event_publisher: Callable[..., Any],
        include_current_status_on_conflict: bool,
    ) -> Dict[str, Any]:
        """Apply approve/reject decision with optimistic locking and event write."""
        if self.validator and hmac_headers:
            self._verify_hmac(job_id, action, actor_id, version, reason, hmac_headers)

        request = self._get_pending_request(job_id)

        with self.conn:
            success = store_action(
                request_id=request.id,
                actor_id=actor_id,
                version=version,
                reason=reason,
                commit=False,
            )
            if not success:
                current = self.store.get_by_id(request.id)
                raise ApprovalAPIError(
                    "409_APPROVAL_VERSION_CONFLICT",
                    "Request was modified concurrently",
                    self._build_version_conflict_details(
                        current=current,
                        expected_version=version,
                        include_current_status=include_current_status_on_conflict,
                    ),
                )

            event = event_publisher(
                job_id=job_id,
                actor_id=actor_id,
                version=version + 1,
                reason=reason,
                commit=False,
                broadcast=False,
            )

        self.publisher.broadcast(event)
        return {
            "success": True,
            "job_id": job_id,
            "status": status,
            "version": version + 1,
            "event_id": event.event_id,
        }

    def generate_hmac_headers_for_request(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, str]:
        """Generate HMAC headers for client requests.

        Convenience method for clients that need to sign requests.

        Args:
            payload: Request payload

        Returns:
            Dict with HMAC headers

        Raises:
            ApprovalAPIError: If HMAC not configured
        """
        if not self.validator or not self.validator.config:
            raise ApprovalAPIError(
                "400_BAD_REQUEST",
                "HMAC not configured"
            )

        return generate_hmac_headers(
            payload=payload,
            secret=self.validator.config.active_secret,
            key_id=self.validator.config.key_id,
        )
