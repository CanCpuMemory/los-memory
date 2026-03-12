"""Main adapter for Approval system migration to VPS Agent Web.

This module provides the main adapter layer that coordinates between
los-memory (local) and VPS Agent Web (remote) during the 12-month migration.
"""
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any, Dict, Generator, Optional

if TYPE_CHECKING:
    import sqlite3

from memory_tool.approval_api import ApprovalAPI

from .config import MigrationConfig, MigrationPhase
from .dual_write import DualWriteManager, DualWriteResult
from .hmac_bridge import HMACBridge
from .vps_client import VPSAgentWebClient


class ApprovalMigrationAdapter:
    """Main adapter for approval system migration.

    This adapter provides a unified interface that routes requests to either
    los-memory (local) or VPS Agent Web (remote) based on the migration phase.

    Migration phases:
    - LOCAL_ONLY: Use local los-memory only (Phase 1)
    - DUAL_WRITE: Write to both systems (Phase 2)
    - REMOTE_ONLY: Use VPS Agent Web only (Phase 3)
    - REMOVED: Feature removed (Phase 4)

    Example:
        config = MigrationConfig(phase=MigrationPhase.DUAL_WRITE)
        adapter = ApprovalMigrationAdapter(config, sqlite_conn)

        # Create request - will write to both systems in dual-write mode
        result = adapter.create_request(
            job_id="job-123",
            command="restart_service",
            risk_level="high"
        )
    """

    def __init__(
        self,
        config: MigrationConfig,
        local_conn: "sqlite3.Connection",
    ):
        config_errors = config.validate()
        if config_errors:
            raise RuntimeError("Invalid migration config: " + "; ".join(config_errors))
        self.config = config
        self.local_conn = local_conn
        self._local_api: Optional[ApprovalAPI] = None
        self._remote_client: Optional[VPSAgentWebClient] = None
        self._local_conn = local_conn
        self._hmac_bridge: Optional[HMACBridge] = None
        self._dual_write: Optional[DualWriteManager] = None

        # Initialize components based on phase
        if config.enable_local:
            self._local_api = ApprovalAPI(local_conn)

        if config.enable_remote:
            self._remote_client = VPSAgentWebClient(config.vps_agent_web)
            # Create shared nonce store for HMAC verification
            from memory_tool.approval_security import MemoryNonceStore
            self._nonce_store = MemoryNonceStore()
            self._hmac_bridge = HMACBridge(config.hmac, nonce_store=self._nonce_store)

        if config.phase == MigrationPhase.DUAL_WRITE:
            if self._remote_client and self._local_api:
                # Use connection factory for thread safety
                self._dual_write = DualWriteManager(
                    config=config.dual_write,
                    local_conn=lambda: local_conn,
                    remote_client=self._remote_client,
                )

    def _emit_deprecation_warning(self) -> None:
        """Emit deprecation warning if enabled."""
        if self.config.deprecation_warnings:
            warnings.warn(
                "The approval system is being migrated to VPS Agent Web. "
                f"Current phase: {self.config.get_effective_mode()}. "
                "See: https://docs.vps-agent-web.example.com/migration",
                DeprecationWarning,
                stacklevel=3,
            )

    def _ensure_not_removed(self) -> None:
        """Ensure feature is not removed."""
        if self.config.phase == MigrationPhase.REMOVED:
            raise RuntimeError(
                "Approval commands have been removed. "
                "Use VPS Agent Web directly: https://vps-agent-web.example.com"
            )

    def _require_local_api(self) -> ApprovalAPI:
        if not self._local_api:
            raise RuntimeError("Local API not configured")
        return self._local_api

    def _require_remote_client(self) -> VPSAgentWebClient:
        if not self._remote_client:
            raise RuntimeError("Remote client not configured")
        return self._remote_client

    def _require_dual_write_manager(self) -> DualWriteManager:
        if not self._dual_write:
            raise RuntimeError("Dual-write manager not initialized")
        return self._dual_write

    def _prepare_remote_hmac_headers(
        self,
        hmac_headers: Optional[Dict[str, str]],
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, str]]:
        remote_headers = hmac_headers
        if (
            hmac_headers
            and self._hmac_bridge
            and self.config.phase == MigrationPhase.DUAL_WRITE
        ):
            # In dual-write mode we verify local signature and re-sign outbound.
            remote_headers = self._hmac_bridge.verify_and_resign(
                hmac_headers, payload=payload
            )
        return remote_headers

    def _handle_decision_request(
        self,
        action: str,
        job_id: str,
        actor_id: str,
        version: int,
        hmac_headers: Optional[Dict[str, str]],
        reason: Optional[str],
    ) -> Dict[str, Any]:
        method_name = f"{action}_request"
        remote_headers = self._prepare_remote_hmac_headers(
            hmac_headers=hmac_headers,
            payload={
                "job_id": job_id,
                "action": action,
                "actor_id": actor_id,
                "version": version,
                "reason": reason or "",
            },
        )

        if self.config.phase == MigrationPhase.LOCAL_ONLY:
            local_api = self._require_local_api()
            local_method = getattr(local_api, method_name)
            return local_method(
                job_id=job_id,
                actor_id=actor_id,
                version=version,
                reason=reason,
                hmac_headers=hmac_headers,
            )

        if self.config.phase == MigrationPhase.DUAL_WRITE:
            dual_write = self._require_dual_write_manager()
            dual_method = getattr(dual_write, method_name)
            result = dual_method(
                job_id=job_id,
                actor_id=actor_id,
                version=version,
                hmac_headers=remote_headers,
                reason=reason,
            )
            return self._format_dual_write_result(result)

        remote_client = self._require_remote_client()
        remote_method = getattr(remote_client, method_name)
        return remote_method(
            job_id=job_id,
            actor_id=actor_id,
            version=version,
            hmac_headers=remote_headers,
            reason=reason,
        )

    def create_request(
        self,
        job_id: str,
        command: str,
        risk_level: str = "medium",
        requested_by: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create an approval request.

        Routes to appropriate backend based on migration phase.

        Args:
            job_id: Unique job identifier
            command: Command to execute upon approval
            risk_level: Risk level (low, medium, high, critical)
            requested_by: Requesting actor
            context: Additional context

        Returns:
            Response dictionary with request details
        """
        self._ensure_not_removed()
        self._emit_deprecation_warning()

        if self.config.phase == MigrationPhase.LOCAL_ONLY:
            local_api = self._require_local_api()
            return local_api.create_request(
                job_id=job_id,
                command=command,
                risk_level=risk_level,
                requested_by=requested_by,
                context=context,
            )

        elif self.config.phase == MigrationPhase.DUAL_WRITE:
            dual_write = self._require_dual_write_manager()
            result = dual_write.create_request(
                job_id=job_id,
                command=command,
                risk_level=risk_level,
                requested_by=requested_by,
                context=context,
            )
            return self._format_dual_write_result(result)

        else:  # REMOTE_ONLY
            remote_client = self._require_remote_client()
            return remote_client.create_request(
                job_id=job_id,
                command=command,
                risk_level=risk_level,
                requested_by=requested_by,
                context=context,
            )

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
            version: Expected version (optimistic lock)
            hmac_headers: HMAC signature headers
            reason: Optional approval reason

        Returns:
            Response dictionary with approval result
        """
        self._ensure_not_removed()
        self._emit_deprecation_warning()
        return self._handle_decision_request(
            action="approve",
            job_id=job_id,
            actor_id=actor_id,
            version=version,
            hmac_headers=hmac_headers,
            reason=reason,
        )

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
            version: Expected version (optimistic lock)
            hmac_headers: HMAC signature headers
            reason: Optional rejection reason

        Returns:
            Response dictionary with rejection result
        """
        self._ensure_not_removed()
        self._emit_deprecation_warning()
        return self._handle_decision_request(
            action="reject",
            job_id=job_id,
            actor_id=actor_id,
            version=version,
            hmac_headers=hmac_headers,
            reason=reason,
        )

    def get_request_status(self, job_id: str) -> Dict[str, Any]:
        """Get request status.

        Args:
            job_id: Job ID to query

        Returns:
            Request status
        """
        self._ensure_not_removed()

        if self.config.phase == MigrationPhase.REMOTE_ONLY:
            remote_client = self._require_remote_client()
            return remote_client.get_request_status(job_id)
        else:
            # For LOCAL_ONLY and DUAL_WRITE, prefer local
            local_api = self._require_local_api()
            return local_api.get_request_status(job_id)

    def list_pending_requests(self, limit: int = 100) -> Dict[str, Any]:
        """List pending approval requests.

        Args:
            limit: Maximum results

        Returns:
            List of pending requests
        """
        self._ensure_not_removed()

        if self.config.phase == MigrationPhase.REMOTE_ONLY:
            remote_client = self._require_remote_client()
            return remote_client.list_pending(limit=limit)
        else:
            local_api = self._require_local_api()
            return local_api.list_pending_requests(limit=limit)

    def list_requests(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """List all approval requests (alias for list_all_requests).

        Args:
            status: Filter by status
            limit: Maximum results

        Returns:
            List of requests
        """
        return self.list_all_requests(status=status, limit=limit)

    def list_all_requests(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """List all approval requests.

        Args:
            status: Filter by status
            limit: Maximum results

        Returns:
            List of requests
        """
        self._ensure_not_removed()

        if self.config.phase == MigrationPhase.REMOTE_ONLY:
            remote_client = self._require_remote_client()
            return remote_client.list_requests(status=status, limit=limit)
        else:
            local_api = self._require_local_api()
            return local_api.list_all_requests(status=status, limit=limit)

    def get_audit_log(self, job_id: str) -> Dict[str, Any]:
        """Get audit log for a job.

        Args:
            job_id: Job ID to query

        Returns:
            Audit log entries
        """
        self._ensure_not_removed()

        if self.config.phase == MigrationPhase.REMOTE_ONLY:
            remote_client = self._require_remote_client()
            return remote_client.get_audit_log(job_id)
        else:
            local_api = self._require_local_api()
            return local_api.get_audit_log(job_id)

    def run_auto_reject(self) -> Dict[str, Any]:
        """Run auto-reject scheduler.

        Returns:
            Results of auto-reject operation
        """
        self._ensure_not_removed()

        if self.config.phase == MigrationPhase.REMOTE_ONLY:
            # Auto-reject runs on VPS Agent Web side
            return {"success": True, "note": "Auto-reject runs on VPS Agent Web"}
        else:
            local_api = self._require_local_api()
            return local_api.run_auto_reject()

    def get_event_stream(
        self,
        last_event_id: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """Get SSE event stream.

        In REMOTE_ONLY mode, uses SSE proxy to stream events from
        VPS Agent Web with automatic reconnection and buffering.

        In other modes, uses local event stream.

        Args:
            last_event_id: Last event ID for replay
            client_id: Optional client identifier for tracking

        Yields:
            SSE-formatted event strings
        """
        self._ensure_not_removed()

        if self.config.phase == MigrationPhase.REMOTE_ONLY:
            # Use SSE proxy for full event streaming
            remote_client = self._require_remote_client()

            # Initialize SSE proxy if needed
            if not hasattr(self, "_sse_proxy") or self._sse_proxy is None:
                from .sse_proxy import SSEProxy

                self._sse_proxy = SSEProxy(
                    config=self.config.sse,
                    vps_client=remote_client,
                )
                self._sse_proxy.start()

            # Yield from proxy
            try:
                yield from self._sse_proxy.subscribe(
                    last_event_id=last_event_id,
                    client_id=client_id,
                )
            finally:
                # Cleanup stale clients periodically
                if self._sse_proxy:
                    self._sse_proxy.cleanup_stale_clients()

        else:
            # Use local event stream
            local_api = self._require_local_api()
            yield from local_api.get_event_stream(last_event_id)

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
            Event history
        """
        self._ensure_not_removed()

        if self.config.phase == MigrationPhase.REMOTE_ONLY:
            # VPS Agent Web may not support this directly
            return {
                "success": True,
                "note": "Event history available via SSE stream",
                "events": [],
            }
        else:
            local_api = self._require_local_api()
            return local_api.get_event_history(job_id=job_id, limit=limit)

    def health_check(self) -> Dict[str, Any]:
        """Check health of all configured backends.

        Returns:
            Health status dictionary
        """
        health = {
            "phase": self.config.phase.value,
            "local": {"configured": self._local_api is not None, "healthy": False},
            "remote": {"configured": self._remote_client is not None, "healthy": False},
        }

        if self._local_api:
            try:
                # Try a simple query
                self._local_api.list_pending_requests(limit=1)
                health["local"]["healthy"] = True
            except Exception as e:
                health["local"]["error"] = str(e)

        if self._remote_client:
            remote_health = self._remote_client.health_check()
            health["remote"]["healthy"] = remote_health.get("healthy", False)
            health["remote"]["details"] = remote_health

        health["overall_healthy"] = (
            health["local"]["healthy"] if self.config.enable_local else True
        ) and (health["remote"]["healthy"] if self.config.enable_remote else True)

        return health

    def get_migration_status(self) -> Dict[str, Any]:
        """Get migration status and statistics.

        Returns:
            Migration status dictionary
        """
        status = {
            "phase": self.config.phase.value,
            "mode": self.config.get_effective_mode(),
            "configuration": {
                "local_enabled": self.config.enable_local,
                "remote_enabled": self.config.enable_remote,
                "deprecation_warnings": self.config.deprecation_warnings,
            },
        }

        if self._dual_write:
            status["statistics"] = self._dual_write.get_migration_statistics()

        return status

    def _format_dual_write_result(self, result: DualWriteResult) -> Dict[str, Any]:
        """Format dual-write result for API response.

        The data priority follows the dual-write mode configuration:
        - REMOTE_PREFERRED: Prefer remote data
        - LOCAL_PREFERRED: Prefer local data
        - STRICT: Prefer local data (backward compatible)
        """
        from .config import DualWriteMode

        response = {
            "success": result.success,
            "source": "dual-write",
            "local": {
                "success": result.local_success,
                "result": result.local_result,
            },
            "remote": {
                "success": result.remote_success,
                "result": result.remote_result,
            },
        }

        if result.error_message:
            response["error"] = result.error_message

        # Determine which data source to use based on dual-write mode
        mode = self.config.dual_write.mode if self._dual_write else DualWriteMode.LOCAL_PREFERRED

        # Include the actual result data from successful operations
        # Priority depends on dual-write mode
        if mode == DualWriteMode.REMOTE_PREFERRED:
            # Prefer remote data
            if result.remote_success and result.remote_result:
                response["data"] = result.remote_result.get("data") or result.remote_result
            elif result.local_success and result.local_result:
                response["data"] = result.local_result.get("request") or result.local_result
        else:
            # LOCAL_PREFERRED and STRICT: Prefer local data (backward compatible)
            if result.local_success and result.local_result:
                response["data"] = result.local_result.get("request") or result.local_result
            elif result.remote_success and result.remote_result:
                response["data"] = result.remote_result.get("data") or result.remote_result

        return response
