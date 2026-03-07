"""CLI commands for recovery management."""
from __future__ import annotations

import argparse
import json
import sqlite3
from typing import Any, Dict, List, Optional

from .auto_recovery import AutoRecoveryEngine
from .recovery_actions import get_recovery_registry
from .recovery_executor import RecoveryExecutor, RecoveryPolicyManager
from .utils import utc_now


def add_recovery_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Add recovery management subcommands."""
    recovery_parser = subparsers.add_parser(
        "recovery",
        help="Recovery management commands"
    )
    recovery_subparsers = recovery_parser.add_subparsers(
        dest="recovery_action",
        help="Recovery actions"
    )

    # List available recovery actions
    list_actions = recovery_subparsers.add_parser(
        "list-actions",
        help="List available recovery actions"
    )

    # Execute recovery manually
    execute_parser = recovery_subparsers.add_parser(
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

    # List recovery policies
    list_policies = recovery_subparsers.add_parser(
        "list-policies",
        help="List recovery policies"
    )
    list_policies.add_argument(
        "--enabled-only",
        action="store_true",
        help="Show only enabled policies"
    )

    # Create recovery policy
    create_policy = recovery_subparsers.add_parser(
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

    # Delete policy
    delete_policy = recovery_subparsers.add_parser(
        "delete-policy",
        help="Delete a recovery policy"
    )
    delete_policy.add_argument(
        "policy_id",
        type=int,
        help="Policy ID to delete"
    )

    # View recovery logs
    logs_parser = recovery_subparsers.add_parser(
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

    # Recovery statistics
    stats_parser = recovery_subparsers.add_parser(
        "stats",
        help="Show recovery statistics"
    )


def handle_recovery_command(
    conn: sqlite3.Connection,
    args: argparse.Namespace
) -> dict:
    """Handle recovery subcommands."""
    registry = get_recovery_registry()
    executor = RecoveryExecutor(conn)
    policy_manager = RecoveryPolicyManager(conn)

    if args.recovery_action == "list-actions":
        actions = registry.list_actions()
        # Also get custom actions from DB
        rows = conn.execute(
            "SELECT name, action_type, description, enabled FROM recovery_actions ORDER BY name"
        ).fetchall()
        return {
            "success": True,
            "builtin_actions": actions,
            "configured_actions": [dict(row) for row in rows]
        }

    elif args.recovery_action == "execute":
        # Parse action names
        action_names = [a.strip() for a in args.actions.split(",")]

        # Parse context
        context = {}
        if args.context:
            try:
                context = json.loads(args.context)
            except json.JSONDecodeError as e:
                return {"success": False, "error": f"Invalid JSON context: {e}"}

        # Load actions
        from .recovery_actions import get_recovery_registry
        registry = get_recovery_registry()

        actions = []
        for name in action_names:
            # Load from DB
            row = conn.execute(
                "SELECT action_type, config FROM recovery_actions WHERE name = ?",
                (name,)
            ).fetchone()
            if not row:
                return {"success": False, "error": f"Action not found: {name}"}

            config = json.loads(row["config"])
            action = registry.create(row["action_type"], config)
            actions.append(action)

        # Execute
        from .recovery_executor import ExecutionConfig
        config = ExecutionConfig(strategy=args.strategy)

        results = executor.execute_actions(
            incident_id=args.incident_id,
            actions=actions,
            context=context,
            config=config
        )

        return {
            "success": True,
            "incident_id": args.incident_id,
            "actions_executed": len(results),
            "all_succeeded": all(r.success for r in results),
            "results": [
                {
                    "success": r.success,
                    "output": r.output,
                    "error": r.error,
                    "duration_ms": r.duration_ms
                }
                for r in results
            ]
        }

    elif args.recovery_action == "list-policies":
        policies = policy_manager.list_policies(enabled_only=args.enabled_only)
        return {
            "success": True,
            "count": len(policies),
            "policies": [p.to_dict() for p in policies]
        }

    elif args.recovery_action == "create-policy":
        action_names = [a.strip() for a in args.actions.split(",")]

        policy_id = policy_manager.create_policy(
            trigger_id=args.trigger_id,
            trigger_type=args.trigger_type,
            action_names=action_names,
            execution_strategy=args.strategy,
            timeout_seconds=args.timeout,
            description=args.description
        )

        return {
            "success": True,
            "policy_id": policy_id,
            "trigger_id": args.trigger_id,
            "actions": action_names
        }

    elif args.recovery_action == "delete-policy":
        success = policy_manager.delete_policy(args.policy_id)
        return {
            "success": success,
            "message": f"Policy {args.policy_id} deleted" if success else f"Policy {args.policy_id} not found"
        }

    elif args.recovery_action == "logs":
        history = executor.get_execution_history(
            incident_id=args.incident_id,
            limit=args.limit
        )
        return {
            "success": True,
            "count": len(history),
            "executions": history
        }

    elif args.recovery_action == "stats":
        engine = AutoRecoveryEngine(conn)
        stats = engine.get_recovery_stats()
        return {
            "success": True,
            "stats": stats
        }

    else:
        return {
            "success": False,
            "error": "No recovery action specified"
        }
