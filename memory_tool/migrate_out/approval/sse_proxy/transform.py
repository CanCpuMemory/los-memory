"""Event format transformation for SSE proxy.

This module handles transformation between local los-memory event formats
and remote VPS Agent Web event formats.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from memory_tool.approval_events import ApprovalEvent
from memory_tool.utils import utc_now


class EventTransformer:
    """Transforms events between local and remote formats.

    Handles:
    - SSE message parsing
    - Event type mapping (remote -> local)
    - Field name normalization (camelCase -> snake_case)
    - Data structure conversion

    Example:
        # Parse SSE message from remote
        sse_message = '''event: approval.pending
        id: uuid-123
        data: {"jobId": "job-456", "riskLevel": "high"}'''

        parsed = EventTransformer.parse_sse_message(sse_message)
        event = EventTransformer.transform_remote_to_local(parsed)

        print(event.event_type)  # "approval.pending"
        print(event.data["job_id"])  # "job-456"
    """

    # Event type mapping: remote -> local
    # Remote may use different naming conventions
    EVENT_TYPE_MAP = {
        # Standard mappings
        "approval.pending": "approval.pending",
        "approval.approved": "approval.approved",
        "approval.rejected": "approval.rejected",
        "approval.timeout": "approval.rejected",
        # Alternative remote naming
        "job.created": "approval.pending",
        "job.approved": "approval.approved",
        "job.rejected": "approval.rejected",
        "job.pending": "approval.pending",
        # VPS Agent Web specific
        "vps.job.pending": "approval.pending",
        "vps.job.approved": "approval.approved",
        "vps.job.rejected": "approval.rejected",
    }

    # Field name mapping: remote (camelCase) -> local (snake_case)
    FIELD_NAME_MAP = {
        "jobId": "job_id",
        "jobID": "job_id",
        "actorId": "actor_id",
        "actorID": "actor_id",
        "requestId": "request_id",
        "requestID": "request_id",
        "riskLevel": "risk_level",
        "risk": "risk_level",
        "requestedBy": "requested_by",
        "approvedBy": "actor_id",
        "rejectedBy": "actor_id",
        "timestamp": "timestamp",
        "createdAt": "timestamp",
        "updatedAt": "updated_at",
        "version": "version",
        "reason": "reason",
        "command": "command",
        "context": "context",
    }

    @classmethod
    def parse_sse_message(cls, raw_message: str) -> Optional[Dict[str, Any]]:
        """Parse raw SSE message string into dictionary.

        SSE Format:
            event: approval.pending
            id: uuid-123
            data: {"key": "value", ...}

        Args:
            raw_message: Raw SSE message string

        Returns:
            Parsed event dictionary or None if invalid/empty
        """
        if not raw_message or not raw_message.strip():
            return None

        lines = raw_message.strip().split("\n")
        event: Dict[str, Any] = {}
        data_lines = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # SSE format: "field: value" or "field" (with no value)
            if ":" in line:
                field, value = line.split(":", 1)
                field = field.strip()
                value = value.strip()

                # Handle data field specially (can be multi-line)
                if field == "data":
                    data_lines.append(value)
                else:
                    event[field] = value
            else:
                # Field with no value (e.g., comment without colon)
                event[line] = ""

        # Parse data field as JSON if present
        if data_lines:
            data_str = "\n".join(data_lines)
            try:
                event["data"] = json.loads(data_str)
            except json.JSONDecodeError:
                # Not valid JSON, store as string
                event["data"] = data_str

        return event if event else None

    @classmethod
    def transform_remote_to_local(
        cls,
        remote_event: Dict[str, Any],
    ) -> ApprovalEvent:
        """Transform remote event format to local ApprovalEvent.

        Args:
            remote_event: Remote event dictionary from SSE

        Returns:
            Local ApprovalEvent
        """
        # Extract fields
        remote_type = remote_event.get("event", remote_event.get("type", ""))
        event_id = remote_event.get("id", str(uuid.uuid4()))
        data = remote_event.get("data", {})

        # Map event type
        local_type = cls.EVENT_TYPE_MAP.get(remote_type, remote_type)

        # Normalize data fields
        normalized_data = cls._normalize_data(data)

        # Parse or generate timestamp
        timestamp = remote_event.get("timestamp")
        if not timestamp:
            # Try to get from data
            timestamp = normalized_data.get("timestamp")
        if not timestamp:
            timestamp = utc_now()

        return ApprovalEvent(
            event_type=local_type,
            event_id=event_id,
            data=normalized_data,
            timestamp=timestamp,
        )

    @classmethod
    def transform_local_to_remote(
        cls,
        local_event: ApprovalEvent,
    ) -> Dict[str, Any]:
        """Transform local ApprovalEvent to remote format.

        Args:
            local_event: Local ApprovalEvent

        Returns:
            Remote event dictionary
        """
        # Reverse event type mapping
        remote_type = None
        for remote, local in cls.EVENT_TYPE_MAP.items():
            if local == local_event.event_type:
                remote_type = remote
                break

        if not remote_type:
            remote_type = local_event.event_type

        # Convert data to camelCase
        remote_data = cls._convert_to_remote_case(local_event.data)

        return {
            "event": remote_type,
            "id": local_event.event_id,
            "data": remote_data,
            "timestamp": local_event.timestamp,
        }

    @classmethod
    def _normalize_data(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize data field names from remote to local format.

        Converts camelCase to snake_case and maps field names.

        Args:
            data: Raw data dictionary from remote

        Returns:
            Normalized data dictionary
        """
        if not isinstance(data, dict):
            return {}

        normalized = {}
        for key, value in data.items():
            # Map field name
            normalized_key = cls.FIELD_NAME_MAP.get(key, key)

            # Convert camelCase to snake_case if not already mapped
            if normalized_key == key:
                normalized_key = cls._camel_to_snake(key)

            normalized[normalized_key] = value

        return normalized

    @classmethod
    def _convert_to_remote_case(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert local snake_case data to remote camelCase.

        Args:
            data: Local data dictionary

        Returns:
            Remote format data dictionary
        """
        if not isinstance(data, dict):
            return {}

        remote = {}
        for key, value in data.items():
            # Reverse field mapping
            remote_key = None
            for remote_k, local_k in cls.FIELD_NAME_MAP.items():
                if local_k == key:
                    remote_key = remote_k
                    break

            if not remote_key:
                remote_key = cls._snake_to_camel(key)

            remote[remote_key] = value

        return remote

    @classmethod
    def _camel_to_snake(cls, name: str) -> str:
        """Convert camelCase to snake_case.

        Args:
            name: camelCase string

        Returns:
            snake_case string
        """
        result = []
        for i, char in enumerate(name):
            if char.isupper() and i > 0:
                result.append("_")
            result.append(char.lower())
        return "".join(result)

    @classmethod
    def _snake_to_camel(cls, name: str) -> str:
        """Convert snake_case to camelCase.

        Args:
            name: snake_case string

        Returns:
            camelCase string
        """
        components = name.split("_")
        return components[0] + "".join(x.title() for x in components[1:])

    @classmethod
    def create_sse_message(
        cls,
        event_type: str,
        event_id: str,
        data: Dict[str, Any],
    ) -> str:
        """Create SSE-formatted message string.

        Args:
            event_type: Event type
            event_id: Event ID
            data: Event data dictionary

        Returns:
            SSE-formatted message string
        """
        lines = [
            f"event: {event_type}",
            f"id: {event_id}",
            f"data: {json.dumps(data)}",
            "",  # Empty line to end event
        ]
        return "\n".join(lines) + "\n"
