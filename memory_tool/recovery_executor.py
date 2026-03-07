"""Recovery executor for managing action execution.

This module provides RecoveryExecutor which manages the execution of
recovery actions with support for sequential/parallel strategies,
timeouts, retries, and logging.
"""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .recovery_actions import RecoveryAction, RecoveryResult, get_recovery_registry
from .utils import utc_now


@dataclass
class ExecutionConfig:
    """Configuration for recovery execution.

    Attributes:
        strategy: Execution strategy - 'sequential' or 'parallel'
        timeout: Total timeout in seconds for all actions
        max_retries: Maximum retry attempts per action
        retry_delay: Delay between retries in seconds
        continue_on_error: Whether to continue if one action fails
    """
    strategy: str = "sequential"  # or "parallel"
    timeout: int = 300
    max_retries: int = 2
    retry_delay: int = 5
    continue_on_error: bool = True


class RecoveryExecutor:
    """Executor for managing recovery action execution.

    Manages the execution lifecycle of recovery actions including:
    - Sequential or parallel execution strategies
    - Timeout and retry handling
    - Execution logging to database
    - Result aggregation

    Example:
        executor = RecoveryExecutor(conn)
        config = ExecutionConfig(strategy="sequential", timeout=120)
        results = executor.execute_actions(
            incident_id=123,
            actions=[action1, action2],
            context={"service": "nginx"},
            config=config
        )
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def execute_actions(
        self,
        incident_id: int,
        actions: List[RecoveryAction],
        context: Dict[str, Any],
        config: Optional[ExecutionConfig] = None,
    ) -> List[RecoveryResult]:
        """Execute multiple recovery actions.

        Args:
            incident_id: ID of the related incident
            actions: List of recovery actions to execute
            context: Execution context for all actions
            config: Execution configuration

        Returns:
            List of RecoveryResult for each action
        """
        if config is None:
            config = ExecutionConfig()

        if config.strategy == "parallel":
            return self._execute_parallel(incident_id, actions, context, config)
        else:
            return self._execute_sequential(incident_id, actions, context, config)

    def _execute_sequential(
        self,
        incident_id: int,
        actions: List[RecoveryAction],
        context: Dict[str, Any],
        config: ExecutionConfig,
    ) -> List[RecoveryResult]:
        """Execute actions sequentially."""
        results = []

        for action in actions:
            if not action.enabled:
                results.append(
                    RecoveryResult(success=False, error=f"Action {action.name} is disabled")
                )
                continue

            # Create execution record
            execution_id = self._create_execution(incident_id, action)

            # Execute with retries
            result = self._execute_with_retry(action, context, config)

            # Update execution record
            self._update_execution(execution_id, result)

            results.append(result)

            # Stop on error if configured
            if not result.success and not config.continue_on_error:
                break

        return results

    def _execute_parallel(
        self,
        incident_id: int,
        actions: List[RecoveryAction],
        context: Dict[str, Any],
        config: ExecutionConfig,
    ) -> List[RecoveryResult]:
        """Execute actions in parallel using threads."""
        results_map: Dict[int, RecoveryResult] = {}
        execution_ids: Dict[int, int] = {}  # action index -> execution_id

        # Create execution records
        for idx, action in enumerate(actions):
            execution_id = self._create_execution(incident_id, action)
            execution_ids[idx] = execution_id

        # Execute in parallel
        with ThreadPoolExecutor(max_workers=len(actions)) as executor:
            futures = {
                executor.submit(self._execute_single, action, context): idx
                for idx, action in enumerate(actions)
            }

            for future in as_completed(futures, timeout=config.timeout):
                idx = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = RecoveryResult(success=False, error=str(e))

                # Update execution record
                self._update_execution(execution_ids[idx], result)
                results_map[idx] = result

        # Return results in original order
        return [results_map[i] for i in range(len(actions))]

    def _execute_with_retry(
        self,
        action: RecoveryAction,
        context: Dict[str, Any],
        config: ExecutionConfig,
    ) -> RecoveryResult:
        """Execute action with retry logic."""
        import time

        last_result = None
        for attempt in range(config.max_retries + 1):
            result = self._execute_single(action, context)
            last_result = result

            if result.success:
                return result

            # Don't retry on last attempt
            if attempt < config.max_retries:
                time.sleep(config.retry_delay)

        return last_result

    def _execute_single(
        self,
        action: RecoveryAction,
        context: Dict[str, Any],
    ) -> RecoveryResult:
        """Execute a single action."""
        return action.execute(context)

    def _create_execution(
        self,
        incident_id: int,
        action: RecoveryAction,
    ) -> int:
        """Create execution record in database."""
        cursor = self.conn.execute(
            """
            INSERT INTO recovery_executions
            (incident_id, action_id, status, created_at)
            VALUES (?, ?, 'pending', ?)
            """,
            (incident_id, None, utc_now())
        )
        self.conn.commit()
        return cursor.lastrowid

    def _update_execution(
        self,
        execution_id: int,
        result: RecoveryResult,
    ) -> None:
        """Update execution record with result."""
        status = "success" if result.success else "failed"
        self.conn.execute(
            """
            UPDATE recovery_executions
            SET status = ?, started_at = COALESCE(started_at, ?),
                completed_at = ?, output_text = ?, error_message = ?
            WHERE id = ?
            """,
            (
                status,
                utc_now(),
                utc_now(),
                result.output,
                result.error,
                execution_id,
            )
        )
        self.conn.commit()

    def get_execution_history(
        self,
        incident_id: Optional[int] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get execution history.

        Args:
            incident_id: Filter by incident ID
            limit: Maximum results

        Returns:
            List of execution records
        """
        if incident_id:
            rows = self.conn.execute(
                """
                SELECT * FROM recovery_executions
                WHERE incident_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (incident_id, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM recovery_executions
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()

        return [dict(row) for row in rows]


class RecoveryPolicy:
    """Policy for binding triggers to recovery actions.

    Defines which actions should be executed when a trigger fires,
    including execution strategy and configuration.

    Example:
        policy = RecoveryPolicy(
            trigger_id="high_cpu",
            trigger_type="threshold",
            action_names=["restart_service", "clear_cache"],
            execution_strategy="sequential",
            timeout_seconds=120
        )
    """

    def __init__(
        self,
        trigger_id: str,
        trigger_type: str,
        action_names: List[str],
        execution_strategy: str = "sequential",
        timeout_seconds: int = 300,
        enabled: bool = True,
        description: str = "",
    ):
        self.trigger_id = trigger_id
        self.trigger_type = trigger_type
        self.action_names = action_names
        self.execution_strategy = execution_strategy
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        """Convert policy to dictionary."""
        return {
            "trigger_id": self.trigger_id,
            "trigger_type": self.trigger_type,
            "action_names": self.action_names,
            "execution_strategy": self.execution_strategy,
            "timeout_seconds": self.timeout_seconds,
            "enabled": self.enabled,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecoveryPolicy":
        """Create policy from dictionary."""
        return cls(
            trigger_id=data["trigger_id"],
            trigger_type=data["trigger_type"],
            action_names=data["action_names"],
            execution_strategy=data.get("execution_strategy", "sequential"),
            timeout_seconds=data.get("timeout_seconds", 300),
            enabled=data.get("enabled", True),
            description=data.get("description", ""),
        )


class RecoveryPolicyManager:
    """Manager for recovery policies.

    Handles CRUD operations for recovery policies and provides
    policy lookup by trigger.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_policy(
        self,
        policy: Optional[RecoveryPolicy] = None,
        trigger_id: Optional[str] = None,
        trigger_type: Optional[str] = None,
        action_names: Optional[List[str]] = None,
        execution_strategy: str = "sequential",
        timeout_seconds: int = 300,
        enabled: bool = True,
        description: str = "",
    ) -> int:
        """Create a recovery policy.

        Args:
            policy: RecoveryPolicy object (alternative to individual args)
            trigger_id: ID of the trigger to bind to
            trigger_type: Type of trigger (threshold, event, etc.)
            action_names: List of recovery action names to execute
            execution_strategy: "sequential" or "parallel"
            timeout_seconds: Maximum execution time
            enabled: Whether policy is active
            description: Policy description

        Returns:
            Policy ID
        """
        import json

        # Build policy from args if not provided
        if policy is None:
            if trigger_id is None or trigger_type is None or action_names is None:
                raise ValueError("Either policy object or trigger_id/trigger_type/action_names required")
            policy = RecoveryPolicy(
                trigger_id=trigger_id,
                trigger_type=trigger_type,
                action_names=action_names,
                execution_strategy=execution_strategy,
                timeout_seconds=timeout_seconds,
                enabled=enabled,
                description=description,
            )

        cursor = self.conn.execute(
            """
            INSERT INTO recovery_policies
            (trigger_id, trigger_type, action_ids, execution_strategy,
             timeout_seconds, enabled, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy.trigger_id,
                policy.trigger_type,
                json.dumps(policy.action_names),
                policy.execution_strategy,
                policy.timeout_seconds,
                policy.enabled,
                policy.description,
                utc_now(),
                utc_now(),
            )
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_policy(self, policy_id: int) -> Optional[RecoveryPolicy]:
        """Get policy by ID."""
        import json

        row = self.conn.execute(
            "SELECT * FROM recovery_policies WHERE id = ?",
            (policy_id,)
        ).fetchone()

        if not row:
            return None

        data = dict(row)
        data["action_names"] = json.loads(data["action_ids"])
        data["trigger_id"] = data["trigger_id"]

        return RecoveryPolicy.from_dict(data)

    def find_policy_by_trigger(
        self,
        trigger_id: str,
        trigger_type: str,
    ) -> Optional[RecoveryPolicy]:
        """Find active policy for a trigger."""
        import json

        row = self.conn.execute(
            """
            SELECT * FROM recovery_policies
            WHERE trigger_id = ? AND trigger_type = ? AND enabled = 1
            ORDER BY id DESC
            LIMIT 1
            """,
            (trigger_id, trigger_type)
        ).fetchone()

        if not row:
            return None

        data = dict(row)
        data["action_names"] = json.loads(data["action_ids"])

        return RecoveryPolicy.from_dict(data)

    def list_policies(self, enabled_only: bool = False) -> List[RecoveryPolicy]:
        """List all policies."""
        import json

        if enabled_only:
            rows = self.conn.execute(
                "SELECT * FROM recovery_policies WHERE enabled = 1 ORDER BY id"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM recovery_policies ORDER BY id"
            ).fetchall()

        policies = []
        for row in rows:
            data = dict(row)
            data["action_names"] = json.loads(data["action_ids"])
            policies.append(RecoveryPolicy.from_dict(data))

        return policies

    def update_policy(self, policy_id: int, **kwargs) -> bool:
        """Update a policy."""
        allowed_fields = {
            "action_names": "action_ids",
            "execution_strategy": "execution_strategy",
            "timeout_seconds": "timeout_seconds",
            "enabled": "enabled",
            "description": "description",
        }

        updates = []
        params = []
        for key, value in kwargs.items():
            if key in allowed_fields:
                db_field = allowed_fields[key]
                if key == "action_names":
                    import json
                    value = json.dumps(value)
                updates.append(f"{db_field} = ?")
                params.append(value)

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(utc_now())
        params.append(policy_id)

        self.conn.execute(
            f"UPDATE recovery_policies SET {', '.join(updates)} WHERE id = ?",
            params
        )
        self.conn.commit()
        return True

    def delete_policy(self, policy_id: int) -> bool:
        """Delete a policy."""
        cursor = self.conn.execute(
            "DELETE FROM recovery_policies WHERE id = ?",
            (policy_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0
