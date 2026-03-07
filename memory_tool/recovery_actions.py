"""Recovery actions for L1 auto-recovery system.

This module defines the base RecoveryAction class and concrete implementations
for common recovery operations like service restart, cache clearing, etc.
"""
from __future__ import annotations

import json
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .utils import utc_now


@dataclass
class RecoveryResult:
    """Result of a recovery action execution.

    Attributes:
        success: Whether the action succeeded
        output: stdout/output from the action
        error: Error message if failed
        duration_ms: Execution duration in milliseconds
        metadata: Additional execution metadata
    """
    success: bool
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class RecoveryAction(ABC):
    """Abstract base class for recovery actions.

    All recovery actions must inherit from this class and implement
    the execute method.

    Example:
        class RestartServiceAction(RecoveryAction):
            def execute(self, context: Dict[str, Any]) -> RecoveryResult:
                service = context.get('service', 'default')
                # ... implementation
                return RecoveryResult(success=True)
    """

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}
        self.enabled = True

    @abstractmethod
    def execute(self, context: Dict[str, Any]) -> RecoveryResult:
        """Execute the recovery action.

        Args:
            context: Execution context containing parameters like
                     service name, paths, URLs, etc.

        Returns:
            RecoveryResult with execution outcome
        """
        pass

    def validate_context(self, context: Dict[str, Any]) -> tuple[bool, str]:
        """Validate that required context parameters are present.

        Returns:
            Tuple of (is_valid, error_message)
        """
        return True, ""

    def enable(self) -> None:
        """Enable this action."""
        self.enabled = True

    def disable(self) -> None:
        """Disable this action."""
        self.enabled = False


class ShellCommandAction(RecoveryAction):
    """Recovery action that executes a shell command.

    Configuration:
        command: The shell command template (supports {{variable}} substitution)
        timeout: Command timeout in seconds (default: 60)
        cwd: Working directory for command execution

    Example:
        action = ShellCommandAction("restart_nginx", {
            "command": "systemctl restart {{service}}",
            "timeout": 30
        })
        result = action.execute({"service": "nginx"})
    """

    def _substitute_variables(self, template: str, context: Dict[str, Any]) -> str:
        """Replace {{variable}} placeholders with context values."""
        result = template
        for key, value in context.items():
            placeholder = f"{{{{{key}}}}}"
            result = result.replace(placeholder, str(value))
        return result

    def execute(self, context: Dict[str, Any]) -> RecoveryResult:
        """Execute shell command with variable substitution."""
        if not self.enabled:
            return RecoveryResult(
                success=False,
                error="Action is disabled"
            )

        # Validate
        is_valid, error_msg = self.validate_context(context)
        if not is_valid:
            return RecoveryResult(success=False, error=error_msg)

        # Get command template and substitute variables
        cmd_template = self.config.get("command", "")
        if not cmd_template:
            return RecoveryResult(
                success=False,
                error="No command configured"
            )

        command = self._substitute_variables(cmd_template, context)
        timeout = self.config.get("timeout", 60)
        cwd = self.config.get("cwd")

        start_time = time.time()
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd
            )
            duration_ms = int((time.time() - start_time) * 1000)

            success = result.returncode == 0
            output = result.stdout if success else result.stderr

            return RecoveryResult(
                success=success,
                output=output.strip() if output else "",
                error=f"Exit code: {result.returncode}" if not success else "",
                duration_ms=duration_ms,
                metadata={
                    "command": command,
                    "returncode": result.returncode
                }
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start_time) * 1000)
            return RecoveryResult(
                success=False,
                error=f"Command timed out after {timeout}s",
                duration_ms=duration_ms,
                metadata={"command": command, "timeout": timeout}
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return RecoveryResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                metadata={"command": command}
            )


class ClearCacheAction(RecoveryAction):
    """Recovery action that clears application cache.

    Configuration:
        cache_paths: List of cache directory paths to clear
        exclude_patterns: Patterns to exclude from deletion

    Example:
        action = ClearCacheAction("clear_redis_cache", {
            "cache_paths": ["/var/cache/redis"],
            "exclude_patterns": ["*.conf"]
        })
        result = action.execute({})
    """

    import glob

    def execute(self, context: Dict[str, Any]) -> RecoveryResult:
        """Clear cache directories."""
        if not self.enabled:
            return RecoveryResult(success=False, error="Action is disabled")

        cache_paths = self.config.get("cache_paths", [])
        if context.get("cache_path"):
            cache_paths.append(context["cache_path"])

        if not cache_paths:
            return RecoveryResult(
                success=False,
                error="No cache paths configured"
            )

        import shutil
        start_time = time.time()
        cleared = []
        errors = []

        for path in cache_paths:
            try:
                import os
                if os.path.exists(path):
                    shutil.rmtree(path)
                    cleared.append(path)
                else:
                    errors.append(f"Path does not exist: {path}")
            except Exception as e:
                errors.append(f"Failed to clear {path}: {e}")

        duration_ms = int((time.time() - start_time) * 1000)

        return RecoveryResult(
            success=len(errors) == 0,
            output=f"Cleared: {', '.join(cleared)}" if cleared else "Nothing cleared",
            error='; '.join(errors) if errors else "",
            duration_ms=duration_ms,
            metadata={"cleared_paths": cleared, "errors": errors}
        )


class WebhookAction(RecoveryAction):
    """Recovery action that sends a webhook notification.

    Configuration:
        url: Webhook URL (supports {{variable}} substitution)
        method: HTTP method (default: POST)
        headers: Dict of HTTP headers
        timeout: Request timeout in seconds (default: 30)

    Example:
        action = WebhookAction("send_alert", {
            "url": "https://hooks.slack.com/{{webhook_path}}",
            "method": "POST",
            "headers": {"Content-Type": "application/json"}
        })
        result = action.execute({"webhook_path": "...", "message": "..."})
    """

    def _substitute_variables(self, template: str, context: Dict[str, Any]) -> str:
        """Replace {{variable}} placeholders with context values."""
        result = template
        for key, value in context.items():
            placeholder = f"{{{{{key}}}}}"
            result = result.replace(placeholder, str(value))
        return result

    def execute(self, context: Dict[str, Any]) -> RecoveryResult:
        """Send webhook notification."""
        if not self.enabled:
            return RecoveryResult(success=False, error="Action is disabled")

        url_template = self.config.get("url", "")
        if not url_template:
            return RecoveryResult(success=False, error="No URL configured")

        url = self._substitute_variables(url_template, context)
        method = self.config.get("method", "POST").upper()
        headers = self.config.get("headers", {})
        timeout = self.config.get("timeout", 30)

        # Prepare payload from context
        payload = {k: v for k, v in context.items() if not k.startswith('_')}

        start_time = time.time()
        try:
            import urllib.request
            import urllib.error

            data = json.dumps(payload).encode('utf-8')
            request = urllib.request.Request(
                url,
                data=data if method == "POST" else None,
                headers={**headers, "Content-Type": "application/json"},
                method=method
            )

            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_body = response.read().decode('utf-8')
                duration_ms = int((time.time() - start_time) * 1000)

                return RecoveryResult(
                    success=200 <= response.status < 300,
                    output=response_body,
                    duration_ms=duration_ms,
                    metadata={
                        "url": url,
                        "status_code": response.status
                    }
                )
        except urllib.error.HTTPError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return RecoveryResult(
                success=False,
                error=f"HTTP {e.code}: {e.reason}",
                duration_ms=duration_ms,
                metadata={"url": url, "status_code": e.code}
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return RecoveryResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                metadata={"url": url}
            )


class DatabaseFailoverAction(RecoveryAction):
    """Recovery action for database failover.

    Configuration:
        primary_connection: Primary DB connection string
        backup_connection: Backup DB connection string
        failover_command: Optional custom failover command

    Example:
        action = DatabaseFailoverAction("db_failover", {
            "primary_connection": "postgresql://primary/db",
            "backup_connection": "postgresql://backup/db"
        })
        result = action.execute({})
    """

    def execute(self, context: Dict[str, Any]) -> RecoveryResult:
        """Execute database failover."""
        if not self.enabled:
            return RecoveryResult(success=False, error="Action is disabled")

        primary = self.config.get("primary_connection", "")
        backup = self.config.get("backup_connection", "")

        if not backup:
            return RecoveryResult(
                success=False,
                error="No backup connection configured"
            )

        start_time = time.time()
        try:
            # Test backup connection
            import sqlite3
            conn = sqlite3.connect(backup)
            conn.execute("SELECT 1")
            conn.close()

            # If custom failover command exists, run it
            failover_cmd = self.config.get("failover_command")
            if failover_cmd:
                subprocess.run(failover_cmd, shell=True, check=True, timeout=60)

            duration_ms = int((time.time() - start_time) * 1000)

            return RecoveryResult(
                success=True,
                output=f"Failover to backup database successful",
                duration_ms=duration_ms,
                metadata={
                    "primary": primary,
                    "backup": backup,
                    "failover_command_used": bool(failover_cmd)
                }
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return RecoveryResult(
                success=False,
                error=f"Failover failed: {e}",
                duration_ms=duration_ms,
                metadata={"primary": primary, "backup": backup}
            )


class RecoveryActionRegistry:
    """Registry for managing recovery actions.

    Provides a central registry to create and manage recovery actions
    by their type/name.

    Example:
        registry = RecoveryActionRegistry()
        registry.register("restart_service", ShellCommandAction)

        action = registry.create("restart_service", config={"command": "..."})
        result = action.execute(context)
    """

    def __init__(self):
        self._actions: Dict[str, type] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register default action types."""
        self.register("shell", ShellCommandAction)
        self.register("restart_service", ShellCommandAction)
        self.register("clear_cache", ClearCacheAction)
        self.register("webhook", WebhookAction)
        self.register("send_alert", WebhookAction)
        self.register("database_failover", DatabaseFailoverAction)
        self.register("switch_database", DatabaseFailoverAction)

    def register(self, name: str, action_class: type) -> None:
        """Register a recovery action class."""
        if not issubclass(action_class, RecoveryAction):
            raise ValueError("Action class must inherit from RecoveryAction")
        self._actions[name] = action_class

    def create(self, name: str, config: Optional[Dict[str, Any]] = None) -> RecoveryAction:
        """Create a recovery action instance."""
        action_class = self._actions.get(name)
        if not action_class:
            raise ValueError(f"Unknown action type: {name}")
        return action_class(name, config)

    def list_actions(self) -> List[str]:
        """List all registered action types."""
        return list(self._actions.keys())

    def is_registered(self, name: str) -> bool:
        """Check if an action type is registered."""
        return name in self._actions


# Global registry instance
_action_registry: Optional[RecoveryActionRegistry] = None


def get_recovery_registry() -> RecoveryActionRegistry:
    """Get the global recovery action registry."""
    global _action_registry
    if _action_registry is None:
        _action_registry = RecoveryActionRegistry()
    return _action_registry


def reset_recovery_registry() -> None:
    """Reset the global registry (useful for testing)."""
    global _action_registry
    _action_registry = None
