"""Auto-recovery engine for L1 self-healing system.

This module provides AutoRecoveryEngine which binds triggers to recovery
actions and automatically executes recovery when triggers fire.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from .incidents import IncidentManager
from .recovery_actions import get_recovery_registry
from .recovery_executor import ExecutionConfig, RecoveryExecutor, RecoveryPolicyManager
from .triggers import TriggerRegistry
from .utils import utc_now


class AutoRecoveryEngine:
    """Engine for automatic recovery execution.

    Monitors triggers and executes recovery actions when they fire.
    Integrates with IncidentManager to create/update incidents.

    Example:
        engine = AutoRecoveryEngine(conn)
        engine.register_policy(
            trigger=threshold_trigger,
            action_names=["restart_service"],
            execution_strategy="sequential"
        )

        # Evaluate context and auto-recover if needed
        results = engine.evaluate_and_recover(context)
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.trigger_registry = TriggerRegistry()
        self.executor = RecoveryExecutor(conn)
        self.policy_manager = RecoveryPolicyManager(conn)
        self.incident_manager = IncidentManager(conn)
        self._recovery_registry = get_recovery_registry()

    def register_trigger(self, trigger) -> None:
        """Register a trigger with the engine."""
        self.trigger_registry.register(trigger)

    def register_policy(
        self,
        trigger_id: str,
        trigger_type: str,
        action_names: List[str],
        execution_strategy: str = "sequential",
        timeout_seconds: int = 300,
        description: str = "",
    ) -> int:
        """Register a recovery policy.

        Args:
            trigger_id: ID of the trigger to bind to
            trigger_type: Type of trigger (threshold, event, etc.)
            action_names: List of recovery action names to execute
            execution_strategy: "sequential" or "parallel"
            timeout_seconds: Maximum execution time
            description: Policy description

        Returns:
            Policy ID
        """
        from .recovery_executor import RecoveryPolicy

        policy = RecoveryPolicy(
            trigger_id=trigger_id,
            trigger_type=trigger_type,
            action_names=action_names,
            execution_strategy=execution_strategy,
            timeout_seconds=timeout_seconds,
            description=description,
        )
        return self.policy_manager.create_policy(policy)

    def evaluate_and_recover(
        self,
        context: Dict[str, Any],
        auto_create_incident: bool = True,
    ) -> Dict[str, Any]:
        """Evaluate triggers and execute recovery if needed.

        Args:
            context: Evaluation context with metrics/events
            auto_create_incident: Whether to create incident on trigger

        Returns:
            Dict with trigger results and recovery outcomes
        """
        results = {
            "evaluated_at": utc_now(),
            "triggers_fired": [],
            "recoveries_executed": [],
            "incidents_created": [],
        }

        # Evaluate all triggers
        triggered = self.trigger_registry.evaluate(context)

        for trigger_result in triggered:
            trigger_name = trigger_result["trigger_name"]
            trigger_type = trigger_result["trigger_type"]

            results["triggers_fired"].append({
                "name": trigger_name,
                "type": trigger_type,
            })

            # Create or get incident
            incident = None
            if auto_create_incident:
                incident = self._create_incident_from_trigger(trigger_result)
                results["incidents_created"].append({
                    "id": incident.id,
                    "title": incident.title,
                })

            # Find recovery policy
            policy = self.policy_manager.find_policy_by_trigger(
                trigger_id=trigger_name,
                trigger_type=trigger_type,
            )

            if not policy:
                results["recoveries_executed"].append({
                    "trigger": trigger_name,
                    "status": "no_policy",
                })
                continue

            # Execute recovery
            recovery_result = self._execute_recovery(
                incident_id=incident.id if incident else None,
                policy=policy,
                context=context,
            )

            results["recoveries_executed"].append({
                "trigger": trigger_name,
                "policy_id": policy.trigger_id,
                "status": recovery_result["status"],
                "action_results": recovery_result["action_results"],
            })

        return results

    def _create_incident_from_trigger(
        self,
        trigger_result: Dict[str, Any],
    ):
        """Create an incident from a trigger result."""
        trigger_name = trigger_result["trigger_name"]
        trigger_type = trigger_result["trigger_type"]

        # Determine incident type and severity based on trigger
        if trigger_type == "threshold":
            incident_type = "performance"
            severity = "p2"
        elif trigger_type == "event":
            incident_type = "error"
            severity = "p1"
        else:
            incident_type = "availability"
            severity = "p2"

        # Build title and description
        if "metric_path" in trigger_result:
            title = f"{trigger_name}: {trigger_result['metric_path']} {trigger_result['operator']} {trigger_result['threshold']}"
            description = f"Actual value: {trigger_result['actual_value']}"
        elif "event_type" in trigger_result:
            title = f"{trigger_name}: {trigger_result['event_type']}"
            description = "Event triggered auto-recovery"
        else:
            title = f"{trigger_name} triggered"
            description = "Auto-recovery initiated"

        return self.incident_manager.create(
            incident_type=incident_type,
            severity=severity,
            title=title,
            description=description,
            context_snapshot=trigger_result.get("context", {}),
        )

    def _execute_recovery(
        self,
        incident_id: Optional[int],
        policy,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute recovery actions for a policy."""
        # Get action instances
        actions = []
        for action_name in policy.action_names:
            try:
                # Load action from database config
                action = self._load_action(action_name)
                if action:
                    actions.append(action)
            except Exception as e:
                return {
                    "status": "error",
                    "error": f"Failed to load action {action_name}: {e}",
                    "action_results": [],
                }

        if not actions:
            return {
                "status": "no_actions",
                "action_results": [],
            }

        # Update incident status to recovering
        if incident_id:
            self.incident_manager.update_status(incident_id, "recovering")

        # Execute actions
        config = ExecutionConfig(
            strategy=policy.execution_strategy,
            timeout=policy.timeout_seconds,
        )

        action_results = self.executor.execute_actions(
            incident_id=incident_id,
            actions=actions,
            context=context,
            config=config,
        )

        # Determine overall status
        all_success = all(r.success for r in action_results)
        status = "success" if all_success else "partial" if any(r.success for r in action_results) else "failed"

        # Update incident status based on result
        if incident_id:
            if all_success:
                self.incident_manager.update_status(incident_id, "resolved")
            else:
                # Stay in recovering for manual intervention
                pass

        return {
            "status": status,
            "action_results": [
                {
                    "success": r.success,
                    "output": r.output,
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                }
                for r in action_results
            ],
        }

    def _load_action(self, action_name: str):
        """Load recovery action from database configuration."""
        row = self.conn.execute(
            """
            SELECT action_type, config FROM recovery_actions
            WHERE name = ? AND enabled = 1
            """,
            (action_name,)
        ).fetchone()

        if not row:
            return None

        config = json.loads(row["config"])
        return self._recovery_registry.create(row["action_type"], config)

    def get_recovery_stats(self) -> Dict[str, Any]:
        """Get recovery execution statistics."""
        total = self.conn.execute(
            "SELECT COUNT(*) FROM recovery_executions"
        ).fetchone()[0]

        success = self.conn.execute(
            "SELECT COUNT(*) FROM recovery_executions WHERE status = 'success'"
        ).fetchone()[0]

        failed = self.conn.execute(
            "SELECT COUNT(*) FROM recovery_executions WHERE status = 'failed'"
        ).fetchone()[0]

        # Average duration
        avg_duration = self.conn.execute(
            """
            SELECT AVG(
                (julianday(completed_at) - julianday(started_at)) * 24 * 60 * 60 * 1000
            )
            FROM recovery_executions
            WHERE completed_at IS NOT NULL
            """
        ).fetchone()[0] or 0

        return {
            "total_executions": total,
            "successful": success,
            "failed": failed,
            "success_rate": success / total if total > 0 else 0,
            "avg_duration_ms": int(avg_duration),
        }
