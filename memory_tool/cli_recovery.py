"""CLI commands for recovery management."""
from __future__ import annotations

import argparse
import json
import sqlite3
from typing import Any

from .auto_recovery import AutoRecoveryEngine
from .recovery_actions import get_recovery_registry
from .recovery_executor import ExecutionConfig, RecoveryExecutor, RecoveryPolicyManager


def add_recovery_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Add recovery management subcommands."""
    recovery_parser = subparsers.add_parser(
        "recovery",
        help="Recovery management commands [EXT]"
    )
    recovery_subparsers = recovery_parser.add_subparsers(
        dest="recovery_action",
        help="Recovery actions"
    )
    _add_recovery_list_actions_parser(recovery_subparsers)
    _add_recovery_execute_parser(recovery_subparsers)
    _add_recovery_list_policies_parser(recovery_subparsers)
    _add_recovery_create_policy_parser(recovery_subparsers)
    _add_recovery_delete_policy_parser(recovery_subparsers)
    _add_recovery_logs_parser(recovery_subparsers)
    _add_recovery_stats_parser(recovery_subparsers)


def _add_recovery_list_actions_parser(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser("list-actions", help="List available recovery actions")


def _add_recovery_execute_parser(subparsers: argparse._SubParsersAction) -> None:
    execute_parser = subparsers.add_parser(
        "execute",
        help="Manually execute recovery for an incident"
    )
    execute_parser.add_argument(
        "--incident-id", "-i",
        type=int,
        required=True,
        help="Incident ID to recover"
    )
    execute_parser.add_argument(
        "--actions", "-a",
        required=True,
        help="Comma-separated list of action names"
    )
    execute_parser.add_argument(
        "--context", "-c",
        help="Execution context as JSON"
    )
    execute_parser.add_argument(
        "--strategy", "-s",
        choices=["sequential", "parallel"],
        default="sequential",
        help="Execution strategy"
    )


def _add_recovery_list_policies_parser(subparsers: argparse._SubParsersAction) -> None:
    list_policies = subparsers.add_parser(
        "list-policies",
        help="List recovery policies"
    )
    list_policies.add_argument(
        "--enabled-only",
        action="store_true",
        help="Show only enabled policies"
    )


def _add_recovery_create_policy_parser(subparsers: argparse._SubParsersAction) -> None:
    create_policy = subparsers.add_parser(
        "create-policy",
        help="Create a recovery policy"
    )
    create_policy.add_argument(
        "--trigger-id", "-t",
        required=True,
        help="Trigger ID to bind"
    )
    create_policy.add_argument(
        "--trigger-type",
        required=True,
        choices=["threshold", "event", "manual", "composite"],
        help="Trigger type"
    )
    create_policy.add_argument(
        "--actions", "-a",
        required=True,
        help="Comma-separated action names"
    )
    create_policy.add_argument(
        "--strategy", "-s",
        choices=["sequential", "parallel"],
        default="sequential"
    )
    create_policy.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds"
    )
    create_policy.add_argument(
        "--description", "-d",
        default="",
        help="Policy description"
    )


def _add_recovery_delete_policy_parser(subparsers: argparse._SubParsersAction) -> None:
    delete_policy = subparsers.add_parser(
        "delete-policy",
        help="Delete a recovery policy"
    )
    delete_policy.add_argument(
        "policy_id",
        type=int,
        help="Policy ID to delete"
    )


def _add_recovery_logs_parser(subparsers: argparse._SubParsersAction) -> None:
    logs_parser = subparsers.add_parser(
        "logs",
        help="View recovery execution logs"
    )
    logs_parser.add_argument(
        "--incident-id", "-i",
        type=int,
        help="Filter by incident ID"
    )
    logs_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        help="Maximum results"
    )


def _add_recovery_stats_parser(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser("stats", help="Show recovery statistics")


def handle_recovery_command(
    conn: sqlite3.Connection,
    args: argparse.Namespace
) -> dict:
    """Handle recovery subcommands."""
    action = args.recovery_action
    registry = get_recovery_registry()
    executor = RecoveryExecutor(conn)
    policy_manager = RecoveryPolicyManager(conn)

    if action == "list-actions":
        return _handle_list_actions(conn, registry)
    if action == "execute":
        return _handle_execute(conn, args, registry, executor)
    if action == "list-policies":
        return _handle_list_policies(args, policy_manager)
    if action == "create-policy":
        return _handle_create_policy(args, policy_manager)
    if action == "delete-policy":
        return _handle_delete_policy(args, policy_manager)
    if action == "logs":
        return _handle_logs(args, executor)
    if action == "stats":
        return _handle_stats(conn)
    return {"success": False, "error": "No recovery action specified"}


def _handle_list_actions(
    conn: sqlite3.Connection,
    registry: Any,
) -> dict:
    rows = conn.execute(
        "SELECT name, action_type, description, enabled FROM recovery_actions ORDER BY name"
    ).fetchall()
    return {
        "success": True,
        "builtin_actions": registry.list_actions(),
        "configured_actions": [dict(row) for row in rows],
    }


def _handle_execute(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    registry: Any,
    executor: RecoveryExecutor,
) -> dict:
    action_names = [name.strip() for name in args.actions.split(",")]
    parse_result = _parse_execution_context(args.context)
    if parse_result.get("error"):
        return parse_result
    context = parse_result["context"]

    actions = []
    for name in action_names:
        row = conn.execute(
            "SELECT action_type, config FROM recovery_actions WHERE name = ?",
            (name,),
        ).fetchone()
        if not row:
            return {"success": False, "error": f"Action not found: {name}"}
        action = registry.create(row["action_type"], json.loads(row["config"]))
        actions.append(action)

    results = executor.execute_actions(
        incident_id=args.incident_id,
        actions=actions,
        context=context,
        config=ExecutionConfig(strategy=args.strategy),
    )
    return {
        "success": True,
        "incident_id": args.incident_id,
        "actions_executed": len(results),
        "all_succeeded": all(result.success for result in results),
        "results": [
            {
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "duration_ms": result.duration_ms,
            }
            for result in results
        ],
    }


def _parse_execution_context(raw_context: str | None) -> dict:
    if not raw_context:
        return {"context": {}}
    try:
        return {"context": json.loads(raw_context)}
    except json.JSONDecodeError as error:
        return {"success": False, "error": f"Invalid JSON context: {error}"}


def _handle_list_policies(
    args: argparse.Namespace,
    policy_manager: RecoveryPolicyManager,
) -> dict:
    policies = policy_manager.list_policies(enabled_only=args.enabled_only)
    return {
        "success": True,
        "count": len(policies),
        "policies": [policy.to_dict() for policy in policies],
    }


def _handle_create_policy(
    args: argparse.Namespace,
    policy_manager: RecoveryPolicyManager,
) -> dict:
    action_names = [name.strip() for name in args.actions.split(",")]
    policy_id = policy_manager.create_policy(
        trigger_id=args.trigger_id,
        trigger_type=args.trigger_type,
        action_names=action_names,
        execution_strategy=args.strategy,
        timeout_seconds=args.timeout,
        description=args.description,
    )
    return {
        "success": True,
        "policy_id": policy_id,
        "trigger_id": args.trigger_id,
        "actions": action_names,
    }


def _handle_delete_policy(
    args: argparse.Namespace,
    policy_manager: RecoveryPolicyManager,
) -> dict:
    success = policy_manager.delete_policy(args.policy_id)
    message = f"Policy {args.policy_id} deleted" if success else f"Policy {args.policy_id} not found"
    return {"success": success, "message": message}


def _handle_logs(
    args: argparse.Namespace,
    executor: RecoveryExecutor,
) -> dict:
    history = executor.get_execution_history(
        incident_id=args.incident_id,
        limit=args.limit,
    )
    return {"success": True, "count": len(history), "executions": history}


def _handle_stats(conn: sqlite3.Connection) -> dict:
    return {"success": True, "stats": AutoRecoveryEngine(conn).get_recovery_stats()}
