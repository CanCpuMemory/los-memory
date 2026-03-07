"""Knowledge base for fault recovery experience沉淀.

This module provides a knowledge base system that:
- Extracts reusable solutions from resolved incidents
- Stores solution templates with success/failure tracking
- Provides search and matching for similar symptoms
- Tracks solution effectiveness for self-optimization
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .utils import utc_now


@dataclass
class KnowledgeEntry:
    """A knowledge entry representing a reusable solution.

    Attributes:
        incident_type: Type of incident (error, performance, availability)
        severity: Severity level (p0-p3)
        symptoms_pattern: Searchable symptoms description
        root_cause_summary: Brief root cause description
        solution_steps: List of solution steps/actions
        prerequisites: Conditions required for this solution
        success_count: Number of successful applications
        failure_count: Number of failed applications
        source_incident_ids: Original incidents this was extracted from
        tags: Categorization tags
        last_used_at: Last time this solution was applied
        created_at: Entry creation timestamp
        id: Database ID
    """
    incident_type: str
    severity: str
    symptoms_pattern: str
    root_cause_summary: str
    solution_steps: List[str] = field(default_factory=list)
    prerequisites: List[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    source_incident_ids: List[int] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    last_used_at: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    id: Optional[int] = None

    @property
    def success_rate(self) -> float:
        """Calculate success rate (0.0-1.0)."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total

    @property
    def confidence_score(self) -> float:
        """Calculate confidence based on usage and success."""
        base_confidence = 0.5
        usage_bonus = min((self.success_count + self.failure_count) * 0.05, 0.3)
        success_bonus = self.success_rate * 0.2
        return min(base_confidence + usage_bonus + success_bonus, 1.0)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "incident_type": self.incident_type,
            "severity": self.severity,
            "symptoms_pattern": self.symptoms_pattern,
            "root_cause_summary": self.root_cause_summary,
            "solution_steps": self.solution_steps,
            "prerequisites": self.prerequisites,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": self.success_rate,
            "confidence_score": self.confidence_score,
            "source_incident_ids": self.source_incident_ids,
            "tags": self.tags,
            "last_used_at": self.last_used_at,
            "created_at": self.created_at,
        }


class ResolutionExtractor:
    """Extract reusable solutions from resolved incidents.

    Analyzes incident observations and recovery actions to
    extract structured solution steps.
    """

    # Action patterns to identify solution steps
    ACTION_PATTERNS = {
        "restart": re.compile(r"restart|reboot|reload", re.I),
        "config_change": re.compile(r"config|configuration|setting", re.I),
        "scale": re.compile(r"scale|resize|increase|decrease.*(memory|cpu|disk)", re.I),
        "rollback": re.compile(r"rollback|revert|undo", re.I),
        "clear_cache": re.compile(r"clear.*cache|flush.*cache", re.I),
        "restart_service": re.compile(r"restart.*service|service.*restart", re.I),
        "database": re.compile(r"database|db.*|query|index", re.I),
        "network": re.compile(r"network|connection|firewall|dns", re.I),
    }

    def extract_from_incident(
        self,
        conn: sqlite3.Connection,
        incident_id: int,
    ) -> Optional[KnowledgeEntry]:
        """Extract knowledge entry from a resolved incident.

        Args:
            conn: Database connection
            incident_id: Incident to analyze

        Returns:
            KnowledgeEntry if extractable, None otherwise
        """
        # Get incident details
        row = conn.execute(
            """
            SELECT * FROM incidents WHERE id = ?
            """,
            (incident_id,)
        ).fetchone()

        if not row:
            return None

        incident = dict(row)

        # Only extract from resolved incidents
        if incident.get("status") != "resolved":
            return None

        # Get linked observations
        obs_rows = conn.execute(
            """
            SELECT o.* FROM observations o
            JOIN incident_observations io ON o.id = io.observation_id
            WHERE io.incident_id = ?
            ORDER BY o.timestamp
            """,
            (incident_id,)
        ).fetchall()

        # Get recovery executions
        recovery_rows = conn.execute(
            """
            SELECT * FROM recovery_executions
            WHERE incident_id = ? AND status = 'success'
            ORDER BY created_at
            """,
            (incident_id,)
        ).fetchall()

        # Build symptoms from observations
        symptoms_parts = []
        for obs in obs_rows:
            obs_dict = dict(obs)
            if obs_dict.get("title"):
                symptoms_parts.append(obs_dict["title"])
            if obs_dict.get("summary"):
                symptoms_parts.append(obs_dict["summary"])

        symptoms = " ".join(symptoms_parts) if symptoms_parts else incident.get("title", "")

        # Extract solution steps from recovery actions
        solution_steps = []
        for rec in recovery_rows:
            rec_dict = dict(rec)
            if rec_dict.get("output_text"):
                step = self._extract_step_from_output(rec_dict["output_text"])
                if step:
                    solution_steps.append(step)

        # If no recovery actions, try to extract from observations
        if not solution_steps:
            for obs in obs_rows:
                obs_dict = dict(obs)
                raw = obs_dict.get("raw", "")
                step = self._extract_step_from_text(raw)
                if step:
                    solution_steps.append(step)

        if not solution_steps:
            solution_steps = ["Investigate logs", "Apply appropriate fix"]

        # Get attribution report if available
        attr_row = conn.execute(
            """
            SELECT * FROM attribution_reports
            WHERE incident_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (incident_id,)
        ).fetchone()

        root_cause = "Unknown"
        if attr_row:
            root_cause = dict(attr_row).get("root_cause_description", "Unknown")
        else:
            root_cause = incident.get("description", "Unknown")[:200]

        # Build tags from incident and observations
        tags = [incident.get("incident_type", ""), incident.get("severity", "")]
        tags = [t for t in tags if t]

        return KnowledgeEntry(
            incident_type=incident.get("incident_type", "unknown"),
            severity=incident.get("severity", "p2"),
            symptoms_pattern=symptoms[:500],
            root_cause_summary=root_cause[:500],
            solution_steps=solution_steps[:10],
            prerequisites=[],
            success_count=1,  # Initial success
            failure_count=0,
            source_incident_ids=[incident_id],
            tags=tags,
            last_used_at=utc_now(),
        )

    def _extract_step_from_output(self, output: str) -> Optional[str]:
        """Extract solution step from recovery output."""
        output = output.strip()
        if len(output) < 5:
            return None

        # Check for known action patterns
        for action_name, pattern in self.ACTION_PATTERNS.items():
            if pattern.search(output):
                return f"{action_name}: {output[:100]}"

        return output[:100] if output else None

    def _extract_step_from_text(self, text: str) -> Optional[str]:
        """Extract solution step from text."""
        # Look for resolution keywords
        resolution_patterns = [
            r"(?:fixed|resolved|solved)\s+(?:by|with|via)\s+(.{10,100})",
            r"(?:solution|workaround)\s*:\s*(.{10,100})",
            r"(?:action taken)\s*:\s*(.{10,100})",
        ]

        for pattern in resolution_patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1).strip()

        return None


class KnowledgeBase:
    """Knowledge base for fault recovery solutions.

    Manages storage, search, and retrieval of reusable solutions
    with effectiveness tracking.

    Example:
        kb = KnowledgeBase(conn)

        # Add new entry
        entry = kb.add_entry(KnowledgeEntry(...))

        # Search for solutions
        results = kb.search("memory error timeout")

        # Record outcome
        kb.record_success(entry_id)
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.extractor = ResolutionExtractor()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Ensure knowledge base tables exist."""
        # Main knowledge entries table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                symptoms_pattern TEXT NOT NULL,
                root_cause_summary TEXT NOT NULL,
                solution_steps TEXT NOT NULL DEFAULT '[]',
                prerequisites TEXT DEFAULT '[]',
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                source_incident_ids TEXT DEFAULT '[]',
                tags TEXT DEFAULT '[]',
                last_used_at TEXT,
                created_at TEXT NOT NULL
            )
        """)

        # FTS virtual table for symptoms search
        self.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
            USING fts5(symptoms_pattern, root_cause_summary, content='knowledge_entries', content_rowid='id')
        """)

        # Triggers to keep FTS index in sync
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS knowledge_ai
            AFTER INSERT ON knowledge_entries BEGIN
                INSERT INTO knowledge_fts(rowid, symptoms_pattern, root_cause_summary)
                VALUES (new.id, new.symptoms_pattern, new.root_cause_summary);
            END
        """)
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS knowledge_ad
            AFTER DELETE ON knowledge_entries BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, symptoms_pattern, root_cause_summary)
                VALUES ('delete', old.id, old.symptoms_pattern, old.root_cause_summary);
            END
        """)
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS knowledge_au
            AFTER UPDATE ON knowledge_entries BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, symptoms_pattern, root_cause_summary)
                VALUES ('delete', old.id, old.symptoms_pattern, old.root_cause_summary);
                INSERT INTO knowledge_fts(rowid, symptoms_pattern, root_cause_summary)
                VALUES (new.id, new.symptoms_pattern, new.root_cause_summary);
            END
        """)

        # Indexes
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_type_severity
            ON knowledge_entries(incident_type, severity)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_success_rate
            ON knowledge_entries(success_count, failure_count)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_last_used
            ON knowledge_entries(last_used_at)
        """)

        self.conn.commit()

    def add_entry(self, entry: KnowledgeEntry) -> int:
        """Add a new knowledge entry.

        Args:
            entry: KnowledgeEntry to add

        Returns:
            ID of created entry
        """
        cursor = self.conn.execute(
            """
            INSERT INTO knowledge_entries
            (incident_type, severity, symptoms_pattern, root_cause_summary,
             solution_steps, prerequisites, success_count, failure_count,
             source_incident_ids, tags, last_used_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.incident_type,
                entry.severity,
                entry.symptoms_pattern,
                entry.root_cause_summary,
                json.dumps(entry.solution_steps),
                json.dumps(entry.prerequisites),
                entry.success_count,
                entry.failure_count,
                json.dumps(entry.source_incident_ids),
                json.dumps(entry.tags),
                entry.last_used_at or utc_now(),
                entry.created_at,
            )
        )
        self.conn.commit()
        entry.id = cursor.lastrowid
        return entry.id

    def extract_and_add(self, incident_id: int) -> Optional[int]:
        """Extract knowledge from incident and add to KB.

        Args:
            incident_id: Incident to extract from

        Returns:
            Entry ID if extracted, None otherwise
        """
        entry = self.extractor.extract_from_incident(self.conn, incident_id)
        if entry:
            return self.add_entry(entry)
        return None

    def get_entry(self, entry_id: int) -> Optional[KnowledgeEntry]:
        """Get knowledge entry by ID."""
        row = self.conn.execute(
            "SELECT * FROM knowledge_entries WHERE id = ?",
            (entry_id,)
        ).fetchone()

        if not row:
            return None

        return self._row_to_entry(row)

    def search(
        self,
        query: str,
        incident_type: Optional[str] = None,
        severity: Optional[str] = None,
        min_success_rate: float = 0.0,
        limit: int = 10,
    ) -> List[Tuple[KnowledgeEntry, float]]:
        """Search knowledge base for matching entries.

        Args:
            query: Search query for symptoms
            incident_type: Filter by incident type
            severity: Filter by severity
            min_success_rate: Minimum success rate (0.0-1.0)
            limit: Maximum results

        Returns:
            List of (entry, match_score) tuples
        """
        # Use FTS for text search
        try:
            fts_rows = self.conn.execute(
                """
                SELECT rowid, rank FROM knowledge_fts
                WHERE knowledge_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit * 2)
            ).fetchall()

            entry_ids = [row["rowid"] for row in fts_rows]
            fts_scores = {row["rowid"]: row["rank"] for row in fts_rows}
        except sqlite3.OperationalError:
            # FTS query error, fall back to simple search
            entry_ids = []
            fts_scores = {}

        if not entry_ids:
            # Fallback to LIKE search
            pattern = f"%{query}%"
            rows = self.conn.execute(
                """
                SELECT * FROM knowledge_entries
                WHERE symptoms_pattern LIKE ? OR root_cause_summary LIKE ?
                ORDER BY success_count DESC
                LIMIT ?
                """,
                (pattern, pattern, limit)
            ).fetchall()
        else:
            # Get full entries
            placeholders = ",".join("?" * len(entry_ids))
            rows = self.conn.execute(
                f"""
                SELECT * FROM knowledge_entries
                WHERE id IN ({placeholders})
                ORDER BY success_count DESC
                """,
                entry_ids
            ).fetchall()

        results = []
        for row in rows:
            entry = self._row_to_entry(row)

            # Apply filters
            if incident_type and entry.incident_type != incident_type:
                continue
            if severity and entry.severity != severity:
                continue
            if entry.success_rate < min_success_rate:
                continue

            # Calculate match score
            fts_score = fts_scores.get(entry.id, 0.5)
            confidence = entry.confidence_score
            match_score = (1.0 - fts_score) * 0.5 + confidence * 0.5

            results.append((entry, match_score))

        return sorted(results, key=lambda x: x[1], reverse=True)[:limit]

    def find_similar(
        self,
        symptoms: str,
        incident_type: Optional[str] = None,
        limit: int = 5,
    ) -> List[KnowledgeEntry]:
        """Find entries with similar symptoms.

        Args:
            symptoms: Symptoms description
            incident_type: Optional type filter
            limit: Maximum results

        Returns:
            List of matching entries
        """
        return [entry for entry, _ in self.search(
            query=symptoms,
            incident_type=incident_type,
            limit=limit
        )]

    def record_success(self, entry_id: int) -> bool:
        """Record successful application of a solution.

        Args:
            entry_id: Entry ID

        Returns:
            True if updated
        """
        cursor = self.conn.execute(
            """
            UPDATE knowledge_entries
            SET success_count = success_count + 1,
                last_used_at = ?
            WHERE id = ?
            """,
            (utc_now(), entry_id)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def record_failure(self, entry_id: int) -> bool:
        """Record failed application of a solution.

        Args:
            entry_id: Entry ID

        Returns:
            True if updated
        """
        cursor = self.conn.execute(
            """
            UPDATE knowledge_entries
            SET failure_count = failure_count + 1,
                last_used_at = ?
            WHERE id = ?
            """,
            (utc_now(), entry_id)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def list_entries(
        self,
        incident_type: Optional[str] = None,
        tag: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> List[KnowledgeEntry]:
        """List knowledge entries with optional filters."""
        conditions = []
        params = []

        if incident_type:
            conditions.append("incident_type = ?")
            params.append(incident_type)

        if tag:
            conditions.append("tags LIKE ?")
            params.append(f"%{tag}%")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = self.conn.execute(
            f"""
            SELECT * FROM knowledge_entries
            {where_clause}
            ORDER BY success_count DESC, created_at DESC
            LIMIT ?
            """,
            (*params, limit)
        ).fetchall()

        entries = [self._row_to_entry(row) for row in rows]

        # Filter by confidence
        if min_confidence > 0:
            entries = [e for e in entries if e.confidence_score >= min_confidence]

        return entries

    def get_statistics(self) -> Dict[str, Any]:
        """Get knowledge base statistics."""
        total = self.conn.execute(
            "SELECT COUNT(*) FROM knowledge_entries"
        ).fetchone()[0]

        type_dist = self.conn.execute(
            """
            SELECT incident_type, COUNT(*) as count
            FROM knowledge_entries
            GROUP BY incident_type
            """
        ).fetchall()

        success_stats = self.conn.execute(
            """
            SELECT
                SUM(success_count) as total_success,
                SUM(failure_count) as total_failure,
                AVG(success_count * 1.0 / NULLIF(success_count + failure_count, 0)) as avg_rate
            FROM knowledge_entries
            """
        ).fetchone()

        top_entries = self.conn.execute(
            """
            SELECT id, symptoms_pattern, success_count
            FROM knowledge_entries
            ORDER BY success_count DESC
            LIMIT 5
            """
        ).fetchall()

        return {
            "total_entries": total,
            "type_distribution": {row["incident_type"]: row["count"] for row in type_dist},
            "total_successful_applications": success_stats["total_success"] or 0,
            "total_failed_applications": success_stats["total_failure"] or 0,
            "average_success_rate": success_stats["avg_rate"] or 0.0,
            "top_solutions": [
                {"id": row["id"], "symptoms": row["symptoms_pattern"][:50], "success": row["success_count"]}
                for row in top_entries
            ],
        }

    def get_unused_entries(
        self,
        days: int = 90,
        limit: int = 20,
    ) -> List[KnowledgeEntry]:
        """Get entries not used recently (for cleanup)."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        rows = self.conn.execute(
            """
            SELECT * FROM knowledge_entries
            WHERE (last_used_at IS NULL OR last_used_at < ?)
            AND success_count < 2
            ORDER BY success_count ASC, created_at ASC
            LIMIT ?
            """,
            (cutoff, limit)
        ).fetchall()

        return [self._row_to_entry(row) for row in rows]

    def delete_entry(self, entry_id: int) -> bool:
        """Delete a knowledge entry."""
        cursor = self.conn.execute(
            "DELETE FROM knowledge_entries WHERE id = ?",
            (entry_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def _row_to_entry(self, row: sqlite3.Row) -> KnowledgeEntry:
        """Convert database row to KnowledgeEntry."""
        return KnowledgeEntry(
            id=row["id"],
            incident_type=row["incident_type"],
            severity=row["severity"],
            symptoms_pattern=row["symptoms_pattern"],
            root_cause_summary=row["root_cause_summary"],
            solution_steps=json.loads(row["solution_steps"]),
            prerequisites=json.loads(row["prerequisites"]) if row["prerequisites"] else [],
            success_count=row["success_count"],
            failure_count=row["failure_count"],
            source_incident_ids=json.loads(row["source_incident_ids"]),
            tags=json.loads(row["tags"]),
            last_used_at=row["last_used_at"],
            created_at=row["created_at"],
        )
