"""CLI commands for L2 approval workflow management."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from typing import Any, Dict, Optional

from .approval_api import ApprovalAPI, ApprovalAPIError
from .approval_security import HMACConfig


def add_approval_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Add approval management subcommands."""
    approval_parser = subparsers.add_parser(
        "approval",
        help="L2 approval workflow management [DEPRECATED - Moving to VPS Agent Web]"
    )
    approval_subparsers = approval_parser.add_subparsers(
        dest="approval_action",
        help="Approval actions"
    )
    _add_approval_request_parser(approval_subparsers)
    _add_approval_approve_parser(approval_subparsers)
    _add_approval_reject_parser(approval_subparsers)
    _add_approval_list_parser(approval_subparsers)
    _add_approval_status_parser(approval_subparsers)
    _add_approval_watch_parser(approval_subparsers)
    _add_approval_audit_parser(approval_subparsers)
    _add_approval_auto_reject_parser(approval_subparsers)
    _add_approval_events_parser(approval_subparsers)


def _add_approval_request_parser(subparsers: argparse._SubParsersAction) -> None:
    request_parser = subparsers.add_parser(
        "request",
        help="Create a new approval request"
    )
    request_parser.add_argument(
        "--job-id", "-j",
        required=True,
        help="Unique job identifier"
    )
    request_parser.add_argument(
        "--command", "-c",
        required=True,
        help="Command to execute upon approval"
    )
    request_parser.add_argument(
        "--risk-level", "-r",
        choices=["low", "medium", "high", "critical"],
        default="medium",
        help="Risk level"
    )
    request_parser.add_argument(
        "--requested-by", "-u",
        help="Requesting actor/user"
    )
    request_parser.add_argument(
        "--context",
        help="Additional context as JSON"
    )


def _add_approval_approve_parser(subparsers: argparse._SubParsersAction) -> None:
    approve_parser = subparsers.add_parser(
        "approve",
        help="Approve a request"
    )
    approve_parser.add_argument(
        "job_id",
        help="Job ID to approve"
    )
    approve_parser.add_argument(
        "--actor", "-a",
        required=True,
        help="Approving actor"
    )
    approve_parser.add_argument(
        "--version", "-v",
        type=int,
        default=1,
        help="Expected version (optimistic lock)"
    )
    approve_parser.add_argument(
        "--reason", "-r",
        help="Approval reason"
    )
    approve_parser.add_argument(
        "--hmac-secret",
        help="HMAC secret for signing (env: APPROVAL_HMAC_SECRET)"
    )


def _add_approval_reject_parser(subparsers: argparse._SubParsersAction) -> None:
    reject_parser = subparsers.add_parser(
        "reject",
        help="Reject a request"
    )
    reject_parser.add_argument(
        "job_id",
        help="Job ID to reject"
    )
    reject_parser.add_argument(
        "--actor", "-a",
        required=True,
        help="Rejecting actor"
    )
    reject_parser.add_argument(
        "--version", "-v",
        type=int,
        default=1,
        help="Expected version (optimistic lock)"
    )
    reject_parser.add_argument(
        "--reason", "-r",
        help="Rejection reason"
    )
    reject_parser.add_argument(
        "--hmac-secret",
        help="HMAC secret for signing (env: APPROVAL_HMAC_SECRET)"
    )


def _add_approval_list_parser(subparsers: argparse._SubParsersAction) -> None:
    list_parser = subparsers.add_parser(
        "list",
        help="List approval requests"
    )
    list_parser.add_argument(
        "--status", "-s",
        choices=["pending", "approved", "rejected", "timeout"],
        help="Filter by status"
    )
    list_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        help="Maximum results"
    )
    list_parser.add_argument(
        "--pending-only", "-p",
        action="store_true",
        help="Show only pending requests"
    )


def _add_approval_status_parser(subparsers: argparse._SubParsersAction) -> None:
    status_parser = subparsers.add_parser(
        "status",
        help="Get request status"
    )
    status_parser.add_argument(
        "job_id",
        help="Job ID to query"
    )


def _add_approval_watch_parser(subparsers: argparse._SubParsersAction) -> None:
    watch_parser = subparsers.add_parser(
        "watch",
        help="Watch approval events (SSE stream)"
    )
    watch_parser.add_argument(
        "--last-event-id",
        help="Last event ID for replay"
    )
    watch_parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=60,
        help="Watch timeout in seconds"
    )


def _add_approval_audit_parser(subparsers: argparse._SubParsersAction) -> None:
    audit_parser = subparsers.add_parser(
        "audit",
        help="Get audit log for a request"
    )
    audit_parser.add_argument(
        "job_id",
        help="Job ID to query"
    )


def _add_approval_auto_reject_parser(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser(
        "auto-reject",
        help="Run auto-reject scheduler for expired requests"
    )


def _add_approval_events_parser(subparsers: argparse._SubParsersAction) -> None:
    events_parser = subparsers.add_parser(
        "events",
        help="Get event history"
    )
    events_parser.add_argument(
        "--job-id", "-j",
        help="Filter by job ID"
    )
    events_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=50,
        help="Maximum results"
    )


def _get_api(conn: sqlite3.Connection, hmac_secret: Optional[str] = None) -> ApprovalAPI:
    """Create ApprovalAPI instance with optional HMAC config."""
    config = None
    if hmac_secret:
        config = HMACConfig(active_secret=hmac_secret, key_id="v1")
    return ApprovalAPI(conn, hmac_config=config)


def handle_approval_command(
    conn: sqlite3.Connection,
    args: argparse.Namespace
) -> Dict[str, Any]:
    """Handle approval subcommands."""
    if not hasattr(args, 'approval_action') or not args.approval_action:
        return {
            "success": False,
            "error": "No approval action specified. Use: request, approve, reject, list, status, watch, audit, auto-reject, events"
        }

    try:
        handlers = {
            "request": _handle_request,
            "approve": _handle_approve,
            "reject": _handle_reject,
            "list": _handle_list,
            "status": _handle_status,
            "watch": _handle_watch,
            "audit": _handle_audit,
            "auto-reject": _handle_auto_reject,
            "events": _handle_events,
        }
        handler = handlers.get(args.approval_action)
        if not handler:
            return {"success": False, "error": f"Unknown action: {args.approval_action}"}
        return handler(conn, args)

    except ApprovalAPIError as e:
        return e.to_dict()
    except Exception as e:
        return {
            "success": False,
            "error": "INTERNAL_ERROR",
            "message": str(e)
        }


def _handle_request(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    """Handle approval request creation."""
    api = _get_api(conn)

    context = {}
    if args.context:
        try:
            context = json.loads(args.context)
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON context: {e}"}

    return api.create_request(
        job_id=args.job_id,
        command=args.command,
        risk_level=args.risk_level,
        requested_by=args.requested_by,
        context=context,
    )


def _handle_approve(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    """Handle approval."""
    # Get HMAC secret from args or environment
    hmac_secret = args.hmac_secret or os.environ.get('APPROVAL_HMAC_SECRET')

    api = _get_api(conn, hmac_secret)

    # Generate HMAC headers if secret provided
    hmac_headers = None
    if hmac_secret:
        payload = {
            "job_id": args.job_id,
            "action": "approve",
            "actor_id": args.actor,
            "version": args.version,
            "reason": args.reason or "",
        }
        hmac_headers = api.generate_hmac_headers_for_request(payload)

    return api.approve_request(
        job_id=args.job_id,
        actor_id=args.actor,
        version=args.version,
        reason=args.reason,
        hmac_headers=hmac_headers,
    )


def _handle_reject(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    """Handle rejection."""
    hmac_secret = args.hmac_secret or os.environ.get('APPROVAL_HMAC_SECRET')
    api = _get_api(conn, hmac_secret)

    hmac_headers = None
    if hmac_secret:
        payload = {
            "job_id": args.job_id,
            "action": "reject",
            "actor_id": args.actor,
            "version": args.version,
            "reason": args.reason or "",
        }
        hmac_headers = api.generate_hmac_headers_for_request(payload)

    return api.reject_request(
        job_id=args.job_id,
        actor_id=args.actor,
        version=args.version,
        reason=args.reason,
        hmac_headers=hmac_headers,
    )


def _handle_list(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    """Handle list requests."""
    api = _get_api(conn)

    if args.pending_only:
        return api.list_pending_requests(limit=args.limit)
    else:
        return api.list_all_requests(status=args.status, limit=args.limit)


def _handle_status(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    """Handle get status."""
    api = _get_api(conn)
    return api.get_request_status(args.job_id)


def _handle_watch(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    """Handle watch events (streaming)."""
    api = _get_api(conn)

    # Note: This is a streaming endpoint, typically used via HTTP SSE
    # For CLI, we'll print events for a limited time
    print(f"Watching approval events for {args.timeout}s...")
    print("Press Ctrl+C to stop\n")

    start_time = time.time()
    try:
        for event in api.get_event_stream(last_event_id=args.last_event_id):
            print(event)
            if time.time() - start_time > args.timeout:
                break
    except KeyboardInterrupt:
        pass

    return {"success": True, "message": "Watch ended"}


def _handle_audit(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    """Handle get audit log."""
    api = _get_api(conn)
    return api.get_audit_log(args.job_id)


def _handle_auto_reject(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    """Handle auto-reject scheduler."""
    api = _get_api(conn)
    return api.run_auto_reject()


def _handle_events(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    """Handle get event history."""
    api = _get_api(conn)
    return api.get_event_history(job_id=args.job_id, limit=args.limit)
