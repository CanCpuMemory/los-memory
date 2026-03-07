"""Internal API for L2 approval workflow.

This module provides the business logic for approval requests,
integrating HMAC security, SSE events, and storage.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

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
        # Check if job already has a request
        existing = self.store.get_by_job_id(job_id)
        if existing:
            raise ApprovalAPIError(
                "409_ALREADY_DECIDED",
                f"Approval request already exists for job {job_id}",
                {"existing_status": existing.status.value}
            )

        # Validate risk level
        valid_risks = ["low", "medium", "high", "critical"]
        if risk_level not in valid_risks:
            raise ApprovalAPIError(
                "400_BAD_REQUEST",
                f"Invalid risk_level: {risk_level}",
                {"valid_values": valid_risks}
            )

        # Create request
        request = ApprovalRequest(
            job_id=job_id,
            command=command,
            risk_level=risk_level,
            requested_by=requested_by,
            context=context or {},
        )

        created = self.store.create(request)

        # Publish event
        event = self.publisher.publish_pending(
            job_id=job_id,
            command=command,
            risk_level=risk_level,
            actor_id=requested_by,
        )

        return {
            "success": True,
            "request": created.to_dict(),
            "event_id": event.event_id,
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
        # Verify HMAC if configured
        if self.validator and hmac_headers:
            self._verify_hmac(job_id, "approve", actor_id, version, reason, hmac_headers)

        # Get request
        request = self.store.get_by_job_id(job_id)
        if not request:
            raise ApprovalAPIError(
                "404_JOB_NOT_FOUND",
                f"Approval request not found for job {job_id}"
            )

        # Check if already decided
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalAPIError(
                "409_ALREADY_DECIDED",
                f"Request already {request.status.value}",
                {"current_status": request.status.value}
            )

        # Attempt approval with optimistic lock
        success = self.store.approve(
            request_id=request.id,
            actor_id=actor_id,
            version=version,
            reason=reason,
        )

        if not success:
            # Version conflict - get current state
            current = self.store.get_by_id(request.id)
            raise ApprovalAPIError(
                "409_APPROVAL_VERSION_CONFLICT",
                "Request was modified concurrently",
                {
                    "current_version": current.version if current else None,
                    "expected_version": version,
                    "current_status": current.status.value if current else None,
                }
            )

        # Publish event
        event = self.publisher.publish_approved(
            job_id=job_id,
            actor_id=actor_id,
            version=version + 1,
            reason=reason,
        )

        return {
            "success": True,
            "job_id": job_id,
            "status": "approved",
            "version": version + 1,
            "event_id": event.event_id,
        }

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
        # Verify HMAC if configured
        if self.validator and hmac_headers:
            self._verify_hmac(job_id, "reject", actor_id, version, reason, hmac_headers)

        # Get request
        request = self.store.get_by_job_id(job_id)
        if not request:
            raise ApprovalAPIError(
                "404_JOB_NOT_FOUND",
                f"Approval request not found for job {job_id}"
            )

        # Check if already decided
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalAPIError(
                "409_ALREADY_DECIDED",
                f"Request already {request.status.value}",
                {"current_status": request.status.value}
            )

        # Attempt rejection with optimistic lock
        success = self.store.reject(
            request_id=request.id,
            actor_id=actor_id,
            version=version,
            reason=reason,
        )

        if not success:
            current = self.store.get_by_id(request.id)
            raise ApprovalAPIError(
                "409_APPROVAL_VERSION_CONFLICT",
                "Request was modified concurrently",
                {
                    "current_version": current.version if current else None,
                    "expected_version": version,
                }
            )

        # Publish event
        event = self.publisher.publish_rejected(
            job_id=job_id,
            actor_id=actor_id,
            version=version + 1,
            reason=reason,
        )

        return {
            "success": True,
            "job_id": job_id,
            "status": "rejected",
            "version": version + 1,
            "event_id": event.event_id,
        }

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
        rejected_ids = self.store.auto_reject_expired()

        # Publish events for auto-rejected
        for req_id in rejected_ids:
            request = self.store.get_by_id(req_id)
            if request:
                self.publisher.publish_timeout(
                    job_id=request.job_id,
                    timeout_hours=48,
                )

        return {
            "success": True,
            "rejected_count": len(rejected_ids),
            "rejected_job_ids": [
                self.store.get_by_id(rid).job_id
                for rid in rejected_ids
                if self.store.get_by_id(rid)
            ],
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
