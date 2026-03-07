"""Tests for trigger system."""
import pytest

from memory_tool.triggers import (
    CompositeTrigger,
    EventTrigger,
    ManualTrigger,
    ThresholdTrigger,
    TriggerRegistry,
    create_disk_space_trigger,
    create_error_rate_trigger,
    create_latency_trigger,
    create_memory_trigger,
)


class TestThresholdTrigger:
    """Test ThresholdTrigger functionality."""

    def test_threshold_trigger_gt(self):
        """Test greater than trigger."""
        trigger = ThresholdTrigger(
            name="high_cpu",
            metric_path="cpu.usage",
            operator=">",
            threshold=80.0
        )

        # Not triggered
        result = trigger.evaluate({"cpu": {"usage": 50.0}})
        assert result is None

        # Triggered
        result = trigger.evaluate({"cpu": {"usage": 90.0}})
        assert result is not None
        assert result["trigger_name"] == "high_cpu"
        assert result["actual_value"] == 90.0

    def test_threshold_trigger_lt(self):
        """Test less than trigger."""
        trigger = ThresholdTrigger(
            name="low_memory",
            metric_path="memory.free",
            operator="<",
            threshold=1024
        )

        result = trigger.evaluate({"memory": {"free": 512}})
        assert result is not None
        assert result["actual_value"] == 512

    def test_threshold_trigger_gte(self):
        """Test greater than or equal trigger."""
        trigger = ThresholdTrigger(
            name="max_connections",
            metric_path="connections.count",
            operator=">=",
            threshold=100
        )

        # At threshold - should trigger
        result = trigger.evaluate({"connections": {"count": 100}})
        assert result is not None

    def test_threshold_trigger_lte(self):
        """Test less than or equal trigger."""
        trigger = ThresholdTrigger(
            name="min_disk",
            metric_path="disk.free_percent",
            operator="<=",
            threshold=10
        )

        result = trigger.evaluate({"disk": {"free_percent": 5}})
        assert result is not None

    def test_threshold_trigger_eq(self):
        """Test equal trigger."""
        trigger = ThresholdTrigger(
            name="status_check",
            metric_path="status.code",
            operator="==",
            threshold=500
        )

        result = trigger.evaluate({"status": {"code": 500}})
        assert result is not None

        result = trigger.evaluate({"status": {"code": 200}})
        assert result is None

    def test_threshold_trigger_neq(self):
        """Test not equal trigger."""
        trigger = ThresholdTrigger(
            name="not_healthy",
            metric_path="health.status",
            operator="!=",
            threshold=1
        )

        result = trigger.evaluate({"health": {"status": 0}})
        assert result is not None

    def test_threshold_trigger_duration(self):
        """Test trigger with duration."""
        trigger = ThresholdTrigger(
            name="high_cpu_sustained",
            metric_path="cpu.usage",
            operator=">",
            threshold=80.0,
            duration=3
        )

        # First two evaluations should not trigger
        assert trigger.evaluate({"cpu": {"usage": 90.0}}) is None
        assert trigger.evaluate({"cpu": {"usage": 90.0}}) is None

        # Third evaluation should trigger
        result = trigger.evaluate({"cpu": {"usage": 90.0}})
        assert result is not None

    def test_threshold_trigger_duration_reset(self):
        """Test duration counter resets when condition not met."""
        trigger = ThresholdTrigger(
            name="high_cpu_sustained",
            metric_path="cpu.usage",
            operator=">",
            threshold=80.0,
            duration=3
        )

        # First evaluation
        assert trigger.evaluate({"cpu": {"usage": 90.0}}) is None

        # Reset counter
        assert trigger.evaluate({"cpu": {"usage": 50.0}}) is None

        # Should not trigger yet
        assert trigger.evaluate({"cpu": {"usage": 90.0}}) is None

    def test_threshold_trigger_missing_value(self):
        """Test trigger with missing metric value."""
        trigger = ThresholdTrigger(
            name="high_cpu",
            metric_path="cpu.usage",
            operator=">",
            threshold=80.0
        )

        result = trigger.evaluate({"memory": {"usage": 50.0}})
        assert result is None

    def test_threshold_trigger_non_numeric(self):
        """Test trigger with non-numeric value."""
        trigger = ThresholdTrigger(
            name="high_cpu",
            metric_path="cpu.usage",
            operator=">",
            threshold=80.0
        )

        result = trigger.evaluate({"cpu": {"usage": "high"}})
        assert result is None

    def test_threshold_trigger_disabled(self):
        """Test disabled trigger does not fire."""
        trigger = ThresholdTrigger(
            name="high_cpu",
            metric_path="cpu.usage",
            operator=">",
            threshold=80.0
        )
        trigger.disable()

        result = trigger.evaluate({"cpu": {"usage": 90.0}})
        assert result is None


class TestEventTrigger:
    """Test EventTrigger functionality."""

    def test_event_trigger_pattern_match(self):
        """Test event pattern matching."""
        trigger = EventTrigger(
            name="error_events",
            event_pattern=r"error\..*"
        )

        result = trigger.evaluate({"event_type": "error.database"})
        assert result is not None
        assert result["event_type"] == "error.database"

    def test_event_trigger_no_match(self):
        """Test event pattern not matching."""
        trigger = EventTrigger(
            name="error_events",
            event_pattern=r"error\..*"
        )

        result = trigger.evaluate({"event_type": "info.user_login"})
        assert result is None

    def test_event_trigger_with_condition(self):
        """Test event trigger with additional condition."""
        trigger = EventTrigger(
            name="critical_errors",
            event_pattern=r"error\..*",
            condition=lambda ctx: ctx.get("severity") == "critical"
        )

        result = trigger.evaluate({
            "event_type": "error.database",
            "severity": "critical"
        })
        assert result is not None

        result = trigger.evaluate({
            "event_type": "error.database",
            "severity": "warning"
        })
        assert result is None

    def test_event_trigger_disabled(self):
        """Test disabled event trigger."""
        trigger = EventTrigger(
            name="error_events",
            event_pattern=r"error\..*"
        )
        trigger.disable()

        result = trigger.evaluate({"event_type": "error.database"})
        assert result is None


class TestManualTrigger:
    """Test ManualTrigger functionality."""

    def test_manual_trigger_always_fires(self):
        """Test manual trigger always fires when enabled."""
        trigger = ManualTrigger(name="manual_alert")

        result = trigger.evaluate({"any": "context"})
        assert result is not None
        assert result["trigger_name"] == "manual_alert"

    def test_manual_trigger_disabled(self):
        """Test disabled manual trigger."""
        trigger = ManualTrigger()
        trigger.disable()

        result = trigger.evaluate({"any": "context"})
        assert result is None


class TestCompositeTrigger:
    """Test CompositeTrigger functionality."""

    def test_composite_or_trigger(self):
        """Test OR composite trigger."""
        trigger1 = ThresholdTrigger("t1", "metric.a", ">", 10)
        trigger2 = ThresholdTrigger("t2", "metric.b", ">", 20)

        composite = CompositeTrigger(
            name="any_metric_high",
            triggers=[trigger1, trigger2],
            operator="OR"
        )

        # First trigger fires
        result = composite.evaluate({"metric": {"a": 15, "b": 5}})
        assert result is not None
        assert result["operator"] == "OR"

        # Second trigger fires
        result = composite.evaluate({"metric": {"a": 5, "b": 25}})
        assert result is not None

        # Neither fires
        result = composite.evaluate({"metric": {"a": 5, "b": 5}})
        assert result is None

    def test_composite_and_trigger(self):
        """Test AND composite trigger."""
        trigger1 = ThresholdTrigger("t1", "metric.a", ">", 10)
        trigger2 = ThresholdTrigger("t2", "metric.b", ">", 20)

        composite = CompositeTrigger(
            name="both_metrics_high",
            triggers=[trigger1, trigger2],
            operator="AND"
        )

        # Only first trigger fires
        result = composite.evaluate({"metric": {"a": 15, "b": 5}})
        assert result is None

        # Both triggers fire
        result = composite.evaluate({"metric": {"a": 15, "b": 25}})
        assert result is not None
        assert result["operator"] == "AND"

    def test_composite_disabled(self):
        """Test disabled composite trigger."""
        trigger1 = ThresholdTrigger("t1", "metric.a", ">", 10)
        composite = CompositeTrigger(
            name="test",
            triggers=[trigger1],
            operator="OR"
        )
        composite.disable()

        result = composite.evaluate({"metric": {"a": 15}})
        assert result is None


class TestTriggerRegistry:
    """Test TriggerRegistry functionality."""

    def test_register_trigger(self):
        """Test registering a trigger."""
        registry = TriggerRegistry()
        trigger = ThresholdTrigger("test", "metric", ">", 10)

        registry.register(trigger)
        assert registry.get("test") is trigger

    def test_unregister_trigger(self):
        """Test unregistering a trigger."""
        registry = TriggerRegistry()
        trigger = ThresholdTrigger("test", "metric", ">", 10)

        registry.register(trigger)
        assert registry.unregister("test") is True
        assert registry.get("test") is None
        assert registry.unregister("test") is False

    def test_list_triggers(self):
        """Test listing registered triggers."""
        registry = TriggerRegistry()

        registry.register(ThresholdTrigger("t1", "metric", ">", 10))
        registry.register(ThresholdTrigger("t2", "metric", "<", 5))

        triggers = registry.list_triggers()
        assert len(triggers) == 2

    def test_evaluate_all_triggers(self):
        """Test evaluating all registered triggers."""
        registry = TriggerRegistry()

        registry.register(ThresholdTrigger("high", "value", ">", 100))
        registry.register(ThresholdTrigger("low", "value", "<", 10))

        results = registry.evaluate({"value": 150})
        assert len(results) == 1
        assert results[0]["trigger_name"] == "high"

    def test_evaluate_by_type(self):
        """Test evaluating triggers by type."""
        registry = TriggerRegistry()

        registry.register(ThresholdTrigger("threshold_test", "value", ">", 100))
        registry.register(EventTrigger("event_test", r"error.*"))

        # Evaluate only threshold triggers
        results = registry.evaluate_by_type("threshold", {"value": 150})
        assert len(results) == 1
        assert results[0]["trigger_name"] == "threshold_test"

    def test_clear_registry(self):
        """Test clearing all triggers."""
        registry = TriggerRegistry()
        registry.register(ThresholdTrigger("test", "metric", ">", 10))

        registry.clear()
        assert len(registry.list_triggers()) == 0


class TestTriggerFactories:
    """Test predefined trigger factory functions."""

    def test_create_error_rate_trigger(self):
        """Test error rate trigger factory."""
        trigger = create_error_rate_trigger()

        assert trigger.name == "high_error_rate"
        assert trigger.metric_path == "error_rate"
        assert trigger.operator == ">"
        assert trigger.threshold == 0.05

        result = trigger.evaluate({"error_rate": 0.08})
        assert result is not None

    def test_create_latency_trigger(self):
        """Test latency trigger factory."""
        trigger = create_latency_trigger()

        assert trigger.name == "high_latency"
        assert trigger.metric_path == "latency_p99"
        assert trigger.threshold == 1000

        result = trigger.evaluate({"latency_p99": 1500})
        assert result is not None

    def test_create_disk_space_trigger(self):
        """Test disk space trigger factory."""
        trigger = create_disk_space_trigger()

        assert trigger.name == "low_disk_space"
        assert trigger.metric_path == "disk_usage_percent"
        assert trigger.threshold == 90

        result = trigger.evaluate({"disk_usage_percent": 95})
        assert result is not None

    def test_create_memory_trigger(self):
        """Test memory trigger factory."""
        trigger = create_memory_trigger()

        assert trigger.name == "high_memory_usage"
        assert trigger.metric_path == "memory_usage_percent"
        assert trigger.threshold == 85

        result = trigger.evaluate({"memory_usage_percent": 90})
        assert result is not None

    def test_custom_trigger_parameters(self):
        """Test creating triggers with custom parameters."""
        trigger = create_error_rate_trigger(
            name="custom_error_rate",
            threshold=0.10
        )

        assert trigger.name == "custom_error_rate"
        assert trigger.threshold == 0.10

        # Should not trigger at 0.08 with 0.10 threshold
        result = trigger.evaluate({"error_rate": 0.08})
        assert result is None
