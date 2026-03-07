"""HTTP client for VPS Agent Web.

This module provides an HTTP client for communicating with VPS Agent Web
during the migration period.
"""
from __future__ import annotations

import json
import random
import socket
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from .config import VPSAgentWebConfig


class VPSAgentWebError(Exception):
    """Error from VPS Agent Web."""

    def __init__(
        self,
        message: str,
        status_code: int = 0,
        error_code: Optional[str] = None,
        response_body: Optional[Dict] = None,
    ):
        self.status_code = status_code
        self.error_code = error_code
        self.response_body = response_body or {}
        super().__init__(message)


class VPSAgentWebClient:
    """HTTP client for VPS Agent Web.

    This client handles communication with VPS Agent Web including:
    - Request signing and authentication
    - Retry logic with exponential backoff
    - Error handling and translation
    - Health checking

    Example:
        config = VPSAgentWebConfig(
            url="https://vps-agent-web.example.com",
            timeout_seconds=30
        )
        client = VPSAgentWebClient(config)

        # Create approval request
        result = client.create_request(
            job_id="job-123",
            command="restart_service",
            risk_level="high"
        )
    """

    def __init__(self, config: "VPSAgentWebConfig"):
        self.config = config
        self._base_url = config.url.rstrip("/")
        self._timeout = config.timeout_seconds
        self._retry_count = config.retry_count

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """Make HTTP request with retry logic.

        Args:
            method: HTTP method (GET, POST, PATCH, etc.)
            endpoint: API endpoint (without base URL)
            data: Request body data
            headers: Additional headers

        Returns:
            Tuple of (status_code, response_body)

        Raises:
            VPSAgentWebError: If request fails after retries
        """
        url = f"{self._base_url}{endpoint}"
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if headers:
            request_headers.update(headers)

        body = json.dumps(data).encode("utf-8") if data else None

        last_error: Optional[Exception] = None

        # Connection timeout is shorter than read timeout
        connection_timeout = min(5, self._timeout // 3)
        read_timeout = self._timeout

        for attempt in range(self._retry_count):
            try:
                req = Request(
                    url,
                    data=body,
                    headers=request_headers,
                    method=method,
                )

                # Use socket timeout for connection, then urlopen timeout for read
                original_timeout = socket.getdefaulttimeout()
                try:
                    socket.setdefaulttimeout(connection_timeout)
                    with urlopen(req, timeout=read_timeout) as response:
                        status = response.getcode()
                        content_type = response.headers.get("Content-Type", "")

                        # Check if response is JSON
                        if "application/json" not in content_type:
                            # Try to read anyway, might be error response
                            raw_body = response.read().decode("utf-8")
                            try:
                                response_body = json.loads(raw_body)
                            except json.JSONDecodeError:
                                response_body = {
                                    "error": {"message": f"Non-JSON response: {raw_body[:200]}"}
                                }
                        else:
                            response_body = json.loads(response.read().decode("utf-8"))

                        return status, response_body
                finally:
                    socket.setdefaulttimeout(original_timeout)

            except socket.timeout as e:
                last_error = e
                # Connection timeout - retry with backoff
                delay = self._calculate_backoff(attempt)
                time.sleep(delay)

            except HTTPError as e:
                status = e.code
                try:
                    error_body = json.loads(e.read().decode("utf-8"))
                except Exception:
                    error_body = {"error": {"message": str(e)}}

                # Don't retry on 4xx errors (client errors)
                if 400 <= status < 500:
                    raise VPSAgentWebError(
                        message=error_body.get("error", {}).get(
                            "message", f"HTTP {status}"
                        ),
                        status_code=status,
                        error_code=error_body.get("error", {}).get("code"),
                        response_body=error_body,
                    )

                # Retry on 5xx errors
                last_error = e
                delay = self._calculate_backoff(attempt)
                time.sleep(delay)

            except URLError as e:
                last_error = e
                delay = self._calculate_backoff(attempt)
                time.sleep(delay)

        # All retries exhausted
        error_msg = f"Request failed after {self._retry_count} attempts"
        if last_error:
            error_msg += f": {last_error}"
        raise VPSAgentWebError(
            message=error_msg,
            status_code=0,
        )

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter.

        Args:
            attempt: Current attempt number (0-based)

        Returns:
            Delay in seconds
        """
        # Exponential backoff: 1s, 2s, 4s
        base_delay = 2**attempt

        # Add +/- 25% jitter to prevent thundering herd
        jitter = base_delay * 0.25
        delay = base_delay + random.uniform(-jitter, jitter)

        return max(0.1, delay)  # Minimum 100ms delay

    def health_check(self) -> Dict[str, Any]:
        """Check VPS Agent Web health.

        Returns:
            Health status dictionary

        Raises:
            VPSAgentWebError: If health check fails
        """
        try:
            status, body = self._make_request("GET", "/healthz")
            return {
                "healthy": status == 200,
                "status": status,
                "details": body,
            }
        except VPSAgentWebError as e:
            return {
                "healthy": False,
                "error": str(e),
                "status": e.status_code,
            }

    def create_request(
        self,
        job_id: str,
        command: str,
        risk_level: str = "medium",
        requested_by: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create an approval request.

        Args:
            job_id: Unique job identifier
            command: Command to execute upon approval
            risk_level: Risk level (low, medium, high, critical)
            requested_by: Requesting actor ID
            context: Additional context

        Returns:
            Response from VPS Agent Web
        """
        data = {
            "job_id": job_id,
            "command": command,
            "risk_level": risk_level,
            "requested_by": requested_by,
            "context": context or {},
            "source": "los-memory-migration",
            "migration_metadata": {
                "original_system": "los-memory",
                "migration_phase": "dual-write",
            },
        }

        status, response = self._make_request("POST", "/api/v1/jobs", data=data)
        return response

    def approve_request(
        self,
        job_id: str,
        actor_id: str,
        version: int,
        hmac_headers: Optional[Dict[str, str]] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Approve a request.

        Args:
            job_id: Job ID to approve
            actor_id: Approving actor
            version: Expected version
            hmac_headers: HMAC signature headers
            reason: Optional approval reason

        Returns:
            Response from VPS Agent Web
        """
        data = {
            "job_id": job_id,
            "action": "approve",
            "actor_id": actor_id,
            "version": version,
            "reason": reason or "",
        }

        headers = {}
        if hmac_headers:
            headers.update(hmac_headers)

        status, response = self._make_request(
            "POST", f"/api/v1/jobs/{job_id}/approval", data=data, headers=headers
        )
        return response

    def reject_request(
        self,
        job_id: str,
        actor_id: str,
        version: int,
        hmac_headers: Optional[Dict[str, str]] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reject a request.

        Args:
            job_id: Job ID to reject
            actor_id: Rejecting actor
            version: Expected version
            hmac_headers: HMAC signature headers
            reason: Optional rejection reason

        Returns:
            Response from VPS Agent Web
        """
        data = {
            "job_id": job_id,
            "action": "reject",
            "actor_id": actor_id,
            "version": version,
            "reason": reason or "",
        }

        headers = {}
        if hmac_headers:
            headers.update(hmac_headers)

        status, response = self._make_request(
            "POST", f"/api/v1/jobs/{job_id}/approval", data=data, headers=headers
        )
        return response

    def get_request_status(self, job_id: str) -> Dict[str, Any]:
        """Get request status.

        Args:
            job_id: Job ID to query

        Returns:
            Request status
        """
        status, response = self._make_request("GET", f"/api/v1/jobs/{job_id}")
        return response

    def list_requests(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """List approval requests.

        Args:
            status: Filter by status
            limit: Maximum results

        Returns:
            List of requests
        """
        endpoint = f"/api/v1/jobs?limit={limit}"
        if status:
            endpoint += f"&status={status}"

        http_status, response = self._make_request("GET", endpoint)
        return response

    def list_pending(self, limit: int = 100) -> Dict[str, Any]:
        """List pending approval requests.

        Args:
            limit: Maximum results

        Returns:
            List of pending requests
        """
        return self.list_requests(status="pending", limit=limit)

    def get_event_stream_url(self, last_event_id: Optional[str] = None) -> str:
        """Get URL for SSE event stream.

        Args:
            last_event_id: Last event ID for replay

        Returns:
            Full URL for event stream
        """
        url = f"{self._base_url}/api/v1/events/stream"
        if last_event_id:
            url += f"?last_event_id={last_event_id}"
        return url

    def get_audit_log(self, job_id: str) -> Dict[str, Any]:
        """Get audit log for a job.

        Args:
            job_id: Job ID to query

        Returns:
            Audit log entries
        """
        status, response = self._make_request(
            "GET", f"/api/v1/jobs/{job_id}/audit"
        )
        return response
