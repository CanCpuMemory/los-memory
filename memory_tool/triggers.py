"""Trigger system for incident detection."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from .utils import utc_now


class Trigger(ABC):
    """Abstract base class for all triggers."""

    def __init__(self, name: str, trigger_type: str):
        self.name = name
        self.trigger_type = trigger_type
        self.enabled = True

    @abstractmethod
    def evaluate(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Evaluate trigger condition.

        Returns:
            Dict with trigger details if triggered, None otherwise
        """
        pass

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False


class ThresholdTrigger(Trigger):
    """
    Trigger based on numeric threshold comparison.

    Supports operators: >, <, >=, <=, ==, !=
    """

    def __init__(
        self,
        name: str,
        metric_path: str,  # Dot-notation path to metric in context
        operator: str,     # >, <, >=, <=, ==, !=
        threshold: float,
        duration: int = 1,  # Number of consecutive evaluations before triggering
    ):
        super().__init__(name, "threshold")
        self.metric_path = metric_path
        self.operator = operator
        self.threshold = threshold
        self.duration = duration
        self._consecutive_count = 0

    def evaluate(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None

        value = self._get_nested_value(context, self.metric_path)
        if value is None:
            return None

        try:
            value = float(value)
        except (TypeError, ValueError):
            return None

        triggered = self._compare(value)

        if triggered:
            self._consecutive_count += 1
            if self._consecutive_count >= self.duration:
                return {
                    "trigger_name": self.name,
                    "trigger_type": self.trigger_type,
                    "metric_path": self.metric_path,
                    "operator": self.operator,
                    "threshold": self.threshold,
                    "actual_value": value,
                    "context": context,
                }
        else:
            self._consecutive_count = 0

        return None

    def _get_nested_value(
        self,
        context: Dict[str, Any],
        path: str
    ) -> Any:
        """Get value from nested dict using dot notation."""
        keys = path.split(".")
        value = context
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value

    def _compare(self, value: float) -> bool:
        """Compare value against threshold."""
        ops = {
            ">": lambda x, y: x > y,
            "<": lambda x, y: x < y,
            ">=": lambda x, y: x >= y,
            "<=": lambda x, y: x <= y,
            "==": lambda x, y: x == y,
            "!=": lambda x, y: x != y,
        }
        op_func = ops.get(self.operator)
        if op_func:
            return op_func(value, self.threshold)
        return False


class EventTrigger(Trigger):
    """Trigger based on specific event patterns."""

    def __init__(
        self,
        name: str,
        event_pattern: str,  # Regex pattern to match event type/name
        condition: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ):
        super().__init__(name, "event")
        self.event_pattern = re.compile(event_pattern)
        self.condition = condition

    def evaluate(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None

        event_type = context.get("event_type", "")
        if not self.event_pattern.match(event_type):
            return None

        # Check additional condition if provided
        if self.condition and not self.condition(context):
            return None

        return {
            "trigger_name": self.name,
            "trigger_type": self.trigger_type,
            "event_type": event_type,
            "context": context,
        }


class ManualTrigger(Trigger):
    """Trigger for manual incident creation."""

    def __init__(self, name: str = "manual"):
        super().__init__(name, "manual")

    def evaluate(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Manual triggers always fire when explicitly evaluated."""
        if not self.enabled:
            return None

        return {
            "trigger_name": self.name,
            "trigger_type": self.trigger_type,
            "context": context,
        }


class CompositeTrigger(Trigger):
    """
    Composite trigger that combines multiple triggers with AND/OR logic.
    """

    def __init__(
        self,
        name: str,
        triggers: List[Trigger],
        operator: str = "OR",  # "AND" or "OR"
    ):
        super().__init__(name, "composite")
        self.triggers = triggers
        self.operator = operator

    def evaluate(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None

        results = []
        for trigger in self.triggers:
            result = trigger.evaluate(context)
            results.append(result)

        if self.operator == "OR":
            # OR: any trigger fires
            triggered = any(r is not None for r in results)
        else:
            # AND: all triggers must fire
            triggered = all(r is not None for r in results)

        if triggered:
            return {
                "trigger_name": self.name,
                "trigger_type": self.trigger_type,
                "operator": self.operator,
                "sub_triggers": [r for r in results if r is not None],
                "context": context,
            }

        return None


class TriggerRegistry:
    """Registry for managing and evaluating triggers."""

    def __init__(self):
        self._triggers: Dict[str, Trigger] = {}

    def register(self, trigger: Trigger) -> None:
        """Register a trigger."""
        self._triggers[trigger.name] = trigger

    def unregister(self, name: str) -> bool:
        """Unregister a trigger by name."""
        if name in self._triggers:
            del self._triggers[name]
            return True
        return False

    def get(self, name: str) -> Optional[Trigger]:
        """Get trigger by name."""
        return self._triggers.get(name)

    def list_triggers(self) -> List[Trigger]:
        """List all registered triggers."""
        return list(self._triggers.values())

    def evaluate(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Evaluate all triggers against context.

        Returns:
            List of trigger results for all triggered triggers.
        """
        triggered = []
        for trigger in self._triggers.values():
            result = trigger.evaluate(context)
            if result is not None:
                triggered.append(result)
        return triggered

    def evaluate_by_type(
        self,
        trigger_type: str,
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Evaluate triggers of a specific type."""
        triggered = []
        for trigger in self._triggers.values():
            if trigger.trigger_type == trigger_type:
                result = trigger.evaluate(context)
                if result is not None:
                    triggered.append(result)
        return triggered

    def clear(self) -> None:
        """Clear all registered triggers."""
        self._triggers.clear()


# Predefined trigger factories
def create_error_rate_trigger(
    name: str = "high_error_rate",
    threshold: float = 0.05,  # 5% error rate
    window: str = "5m",
) -> ThresholdTrigger:
    """Create a trigger for high error rate."""
    return ThresholdTrigger(
        name=name,
        metric_path="error_rate",
        operator=">",
        threshold=threshold,
    )


def create_latency_trigger(
    name: str = "high_latency",
    threshold_ms: float = 1000,  # 1 second
) -> ThresholdTrigger:
    """Create a trigger for high latency."""
    return ThresholdTrigger(
        name=name,
        metric_path="latency_p99",
        operator=">",
        threshold=threshold_ms,
    )


def create_disk_space_trigger(
    name: str = "low_disk_space",
    threshold_percent: float = 90,  # 90% full
) -> ThresholdTrigger:
    """Create a trigger for low disk space."""
    return ThresholdTrigger(
        name=name,
        metric_path="disk_usage_percent",
        operator=">",
        threshold=threshold_percent,
    )


def create_memory_trigger(
    name: str = "high_memory_usage",
    threshold_percent: float = 85,  # 85% memory usage
) -> ThresholdTrigger:
    """Create a trigger for high memory usage."""
    return ThresholdTrigger(
        name=name,
        metric_path="memory_usage_percent",
        operator=">",
        threshold=threshold_percent,
    )
