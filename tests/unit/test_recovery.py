"""Tests for L1 auto-recovery system.

Tests cover recovery actions, executor, and policy management.
"""
import pytest

from memory_tool.recovery_actions import (
    RecoveryActionRegistry,
    RecoveryResult,
    ShellCommandAction,
    WebhookAction,
    get_recovery_registry,
    reset_recovery_registry,
)
from memory_tool.recovery_executor import (
    ExecutionConfig,
    RecoveryExecutor,
    RecoveryPolicy,
    RecoveryPolicyManager,
)


class TestRecoveryResult:
    """Test RecoveryResult dataclass."""

    def test_success_result(self):
        """Test successful result creation."""
        result = RecoveryResult(success=True, output="done", duration_ms=100)
        assert result.success is True
        assert result.output == "done"
        assert result.duration_ms == 100

    def test_failure_result(self):
        """Test failure result creation."""
        result = RecoveryResult(success=False, error="timeout", duration_ms=5000)
        assert result.success is False
        assert result.error == "timeout"

    def test_default_metadata(self):
        """Test metadata defaults to empty dict."""
        result = RecoveryResult(success=True)
        assert result.metadata == {}


class TestShellCommandAction:
    """Test ShellCommandAction."""

    def test_variable_substitution(self):
        """Test {{variable}} substitution in commands."""
        action = ShellCommandAction("test", {
            "command": "echo {{message}}",
            "timeout": 5
        })

        cmd = action._substitute_variables("echo {{message}}", {"message": "hello"})
        assert cmd == "echo hello"

    def test_execute_success(self):
        """Test successful command execution."""
        action = ShellCommandAction("echo_test", {
            "command": "echo hello",
            "timeout": 5
        })

        result = action.execute({})
        assert result.success is True
        assert "hello" in result.output

    def test_execute_failure(self):
        """Test failed command execution."""
        action = ShellCommandAction("fail_test", {
            "command": "exit 1",
            "timeout": 5
        })

        result = action.execute({})
        assert result.success is False

    def test_disabled_action(self):
        """Test disabled action returns error."""
        action = ShellCommandAction("test", {"command": "echo test"})
        action.disable()

        result = action.execute({})
        assert result.success is False
        assert "disabled" in result.error.lower()


class TestWebhookAction:
    """Test WebhookAction."""

    def test_variable_substitution(self):
        """Test URL variable substitution."""
        action = WebhookAction("test", {"url": "https://api.example.com/{{path}}"})

        url = action._substitute_variables("https://api.example.com/{{path}}", {"path": "webhook"})
        assert url == "https://api.example.com/webhook"

    def test_no_url_configured(self):
        """Test error when no URL configured."""
        action = WebhookAction("test", {})
        result = action.execute({})

        assert result.success is False
        assert "No URL" in result.error


class TestRecoveryActionRegistry:
    """Test RecoveryActionRegistry."""

    def test_register_action(self):
        """Test registering a custom action."""
        registry = RecoveryActionRegistry()

        class CustomAction(ShellCommandAction):
            pass

        registry.register("custom", CustomAction)
        assert "custom" in registry.list_actions()

    def test_create_action(self):
        """Test creating an action instance."""
        registry = RecoveryActionRegistry()

        action = registry.create("shell", {"command": "ls"})
        assert isinstance(action, ShellCommandAction)

    def test_unknown_action_raises(self):
        """Test creating unknown action raises error."""
        registry = RecoveryActionRegistry()

        with pytest.raises(ValueError, match="Unknown action type"):
            registry.create("unknown_type", {})

    def test_is_registered(self):
        """Test checking if action is registered."""
        registry = RecoveryActionRegistry()

        assert registry.is_registered("shell") is True
        assert registry.is_registered("unknown") is False


class TestRecoveryExecutor:
    """Test RecoveryExecutor."""

    def test_sequential_execution(self, db_connection):
        """Test sequential action execution."""
        executor = RecoveryExecutor(db_connection)

        actions = [
            ShellCommandAction("cmd1", {"command": "echo first"}),
            ShellCommandAction("cmd2", {"command": "echo second"}),
        ]

        results = executor.execute_actions(
            incident_id=1,
            actions=actions,
            context={},
            config=ExecutionConfig(strategy="sequential")
        )

        assert len(results) == 2
        assert all(r.success for r in results)

    def test_continue_on_error_false(self, db_connection):
        """Test stopping on error when configured."""
        executor = RecoveryExecutor(db_connection)

        actions = [
            ShellCommandAction("fail", {"command": "exit 1"}),
            ShellCommandAction("cmd2", {"command": "echo second"}),
        ]

        results = executor.execute_actions(
            incident_id=1,
            actions=actions,
            context={},
            config=ExecutionConfig(continue_on_error=False)
        )

        # Should stop after first failure
        assert results[0].success is False

    def test_execution_history(self, db_connection):
        """Test recording execution history."""
        executor = RecoveryExecutor(db_connection)

        # Create and execute
        action = ShellCommandAction("test", {"command": "echo test"})
        executor.execute_actions(
            incident_id=1,
            actions=[action],
            context={}
        )

        history = executor.get_execution_history(incident_id=1)
        assert len(history) >= 1


class TestRecoveryPolicy:
    """Test RecoveryPolicy."""

    def test_policy_to_dict(self):
        """Test converting policy to dictionary."""
        policy = RecoveryPolicy(
            trigger_id="high_cpu",
            trigger_type="threshold",
            action_names=["restart_service"],
            execution_strategy="sequential",
            timeout_seconds=120,
            description="Test policy"
        )

        data = policy.to_dict()
        assert data["trigger_id"] == "high_cpu"
        assert data["action_names"] == ["restart_service"]
        assert data["execution_strategy"] == "sequential"

    def test_policy_from_dict(self):
        """Test creating policy from dictionary."""
        data = {
            "trigger_id": "low_disk",
            "trigger_type": "threshold",
            "action_names": ["clear_cache", "send_alert"],
            "execution_strategy": "parallel",
            "timeout_seconds": 60,
            "enabled": True,
            "description": "Disk cleanup policy"
        }

        policy = RecoveryPolicy.from_dict(data)
        assert policy.trigger_id == "low_disk"
        assert policy.action_names == ["clear_cache", "send_alert"]
        assert policy.execution_strategy == "parallel"


class TestRecoveryPolicyManager:
    """Test RecoveryPolicyManager."""

    def test_create_and_get_policy(self, db_connection):
        """Test creating and retrieving a policy."""
        manager = RecoveryPolicyManager(db_connection)

        policy_id = manager.create_policy(
            trigger_id="test_trigger",
            trigger_type="threshold",
            action_names=["action1", "action2"],
            execution_strategy="sequential",
            timeout_seconds=300,
            description="Test policy"
        )

        fetched = manager.get_policy(policy_id)
        assert fetched is not None
        assert fetched.trigger_id == "test_trigger"
        assert fetched.action_names == ["action1", "action2"]

    def test_list_policies(self, db_connection):
        """Test listing policies."""
        manager = RecoveryPolicyManager(db_connection)

        manager.create_policy(
            trigger_id="trigger1",
            trigger_type="threshold",
            action_names=["action1"],
        )
        manager.create_policy(
            trigger_id="trigger2",
            trigger_type="event",
            action_names=["action2"],
        )

        policies = manager.list_policies()
        assert len(policies) >= 2

    def test_find_policy_by_trigger(self, db_connection):
        """Test finding policy by trigger."""
        manager = RecoveryPolicyManager(db_connection)

        manager.create_policy(
            trigger_id="high_memory",
            trigger_type="threshold",
            action_names=["restart_service"],
        )

        found = manager.find_policy_by_trigger("high_memory", "threshold")
        assert found is not None
        assert found.action_names == ["restart_service"]

        not_found = manager.find_policy_by_trigger("unknown", "event")
        assert not_found is None

    def test_update_policy(self, db_connection):
        """Test updating a policy."""
        manager = RecoveryPolicyManager(db_connection)

        policy_id = manager.create_policy(
            trigger_id="test",
            trigger_type="threshold",
            action_names=["action1"],
        )

        success = manager.update_policy(policy_id, action_names=["action2"])
        assert success is True

        updated = manager.get_policy(policy_id)
        assert updated.action_names == ["action2"]

    def test_delete_policy(self, db_connection):
        """Test deleting a policy."""
        manager = RecoveryPolicyManager(db_connection)

        policy_id = manager.create_policy(
            trigger_id="to_delete",
            trigger_type="threshold",
            action_names=["action1"],
        )

        success = manager.delete_policy(policy_id)
        assert success is True

        not_found = manager.get_policy(policy_id)
        assert not_found is None
