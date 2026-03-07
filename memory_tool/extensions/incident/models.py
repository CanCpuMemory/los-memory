"""Incident management models for self-healing system."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from memory_tool.utils import utc_now


@dataclass
class Incident:
    """Incident record for system faults and recovery tracking."""

    id: int
    incident_type: str  # error, performance, availability
    severity: str  # p0, p1, p2, p3
    status: str  # detected, analyzing, recovering, resolved, closed
    title: str
    description: str
    source_observation_id: Optional[int]
    context_snapshot: Dict[str, Any]
    detected_at: str
    resolved_at: Optional[str]
    project: str

    # Status constants
    STATUS_DETECTED = "detected"
    STATUS_ANALYZING = "analyzing"
    STATUS_RECOVERING = "recovering"
    STATUS_RESOLVED = "resolved"
    STATUS_CLOSED = "closed"

    # Type constants
    TYPE_ERROR = "error"
    TYPE_PERFORMANCE = "performance"
    TYPE_AVAILABILITY = "availability"

    # Severity constants
    SEVERITY_P0 = "p0"  # Critical
    SEVERITY_P1 = "p1"  # High
    SEVERITY_P2 = "p2"  # Medium
    SEVERITY_P3 = "p3"  # Low

    VALID_STATUSES = [
        STATUS_DETECTED,
        STATUS_ANALYZING,
        STATUS_RECOVERING,
        STATUS_RESOLVED,
        STATUS_CLOSED,
    ]
    VALID_TYPES = [TYPE_ERROR, TYPE_PERFORMANCE, TYPE_AVAILABILITY]
    VALID_SEVERITIES = [SEVERITY_P0, SEVERITY_P1, SEVERITY_P2, SEVERITY_P3]


class IncidentManager:
    """Manager for incident lifecycle operations."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        incident_type: str,
        severity: str,
        title: str,
        description: str,
        project: str = "general",
        source_observation_id: Optional[int] = None,
        context_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Incident:
        """Create a new incident record."""
        # Validate inputs
        if incident_type not in Incident.VALID_TYPES:
            raise ValueError(
                f"Invalid incident_type: {incident_type}. "
                f"Must be one of: {Incident.VALID_TYPES}"
            )

        if severity not in Incident.VALID_SEVERITIES:
            raise ValueError(
                f"Invalid severity: {severity}. "
                f"Must be one of: {Incident.VALID_SEVERITIES}"
            )

        detected_at = utc_now()
        status = Incident.STATUS_DETECTED

        cursor = self.conn.execute(
            """
            INSERT INTO incidents
            (incident_type, severity, status, title, description,
             source_observation_id, context_snapshot, detected_at, project)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_type,
                severity,
                status,
                title,
                description,
                source_observation_id,
                json.dumps(context_snapshot) if context_snapshot else "{}",
                detected_at,
                project,
            ),
        )
        self.conn.commit()

        incident_id = cursor.lastrowid
        assert incident_id is not None

        return self.get(incident_id)

    def get(self, incident_id: int) -> Optional[Incident]:
        """Get incident by ID."""
        row = self.conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()

        if row is None:
            return None

        return self._row_to_incident(row)

    def update_status(
        self,
        incident_id: int,
        new_status: str,
        resolution_notes: Optional[str] = None,
    ) -> Optional[Incident]:
        """Update incident status with state machine validation."""
        if new_status not in Incident.VALID_STATUSES:
            raise ValueError(
                f"Invalid status: {new_status}. "
                f"Must be one of: {Incident.VALID_STATUSES}"
            )

        incident = self.get(incident_id)
        if incident is None:
            return None

        # Validate state transition
        if not self._is_valid_transition(incident.status, new_status):
            raise ValueError(
                f"Invalid status transition: {incident.status} -> {new_status}"
            )

        updates = {"status": new_status}

        # If resolving, set resolved_at timestamp
        if new_status in (Incident.STATUS_RESOLVED, Incident.STATUS_CLOSED):
            updates["resolved_at"] = utc_now()

        # Build update query
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values())
        values.append(incident_id)

        self.conn.execute(
            f"UPDATE incidents SET {set_clause} WHERE id = ?",
            values,
        )

        self.conn.commit()
        return self.get(incident_id)

    def list(
        self,
        status: Optional[str] = None,
        incident_type: Optional[str] = None,
        severity: Optional[str] = None,
        project: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Incident]:
        """List incidents with optional filtering."""
        conditions = []
        params = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if incident_type:
            conditions.append("incident_type = ?")
            params.append(incident_type)
        if severity:
            conditions.append("severity = ?")
            params.append(severity)
        if project:
            conditions.append("project = ?")
            params.append(project)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        rows = self.conn.execute(
            f"""
            SELECT * FROM incidents
            {where_clause}
            ORDER BY detected_at DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()

        return [self._row_to_incident(row) for row in rows]

    def link_observation(
        self,
        incident_id: int,
        observation_id: int,
        link_type: str = "related",
    ) -> bool:
        """Link an observation to an incident."""
        try:
            self.conn.execute(
                """
                INSERT INTO incident_observations
                (incident_id, observation_id, link_type, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (incident_id, observation_id, link_type, utc_now()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_linked_observations(self, incident_id: int) -> List[Dict[str, Any]]:
        """Get all observations linked to an incident."""
        rows = self.conn.execute(
            """
            SELECT o.*, io.link_type, io.created_at as linked_at
            FROM observations o
            JOIN incident_observations io ON o.id = io.observation_id
            WHERE io.incident_id = ?
            ORDER BY io.created_at DESC
            """,
            (incident_id,),
        ).fetchall()

        return [dict(row) for row in rows]

    def _is_valid_transition(self, from_status: str, to_status: str) -> bool:
        """Validate status state machine transitions."""
        # Define allowed transitions
        transitions = {
            Incident.STATUS_DETECTED: [
                Incident.STATUS_ANALYZING,
                Incident.STATUS_RESOLVED,
            ],
            Incident.STATUS_ANALYZING: [
                Incident.STATUS_RECOVERING,
                Incident.STATUS_RESOLVED,
            ],
            Incident.STATUS_RECOVERING: [
                Incident.STATUS_RESOLVED,
                Incident.STATUS_ANALYZING,
            ],
            Incident.STATUS_RESOLVED: [
                Incident.STATUS_CLOSED,
                Incident.STATUS_ANALYZING,
            ],
            Incident.STATUS_CLOSED: [
                Incident.STATUS_DETECTED  # Reopen
            ],
        }

        allowed = transitions.get(from_status, [])
        return to_status in allowed

    def _row_to_incident(self, row: sqlite3.Row) -> Incident:
        """Convert database row to Incident object."""
        return Incident(
            id=row["id"],
            incident_type=row["incident_type"],
            severity=row["severity"],
            status=row["status"],
            title=row["title"],
            description=row["description"],
            source_observation_id=row["source_observation_id"],
            context_snapshot=json.loads(row["context_snapshot"]),
            detected_at=row["detected_at"],
            resolved_at=row["resolved_at"],
            project=row["project"],
        )
