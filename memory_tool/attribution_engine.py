"""Fault attribution analysis engine.

This module provides root cause analysis for incidents by:
- Time window analysis (before/after incident)
- Correlation analysis (metrics and events)
- Log pattern matching
- Change association (recent deployments)
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .utils import utc_now
from .incidents import IncidentManager


class RootCauseCategory(Enum):
    """Root cause categories."""
    CODE_BUG = "code_bug"
    CONFIG_ERROR = "config_error"
    DEPLOYMENT_ISSUE = "deployment_issue"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    EXTERNAL_DEPENDENCY = "external_dependency"
    SECURITY_INCIDENT = "security_incident"
    HUMAN_ERROR = "human_error"
    UNKNOWN = "unknown"


@dataclass
class ContributingFactor:
    """A contributing factor to an incident."""
    factor_type: str  # metric_spike, log_pattern, deployment, etc.
    description: str
    evidence: str
    confidence: float  # 0.0-1.0
    timestamp: Optional[str] = None
    observation_id: Optional[int] = None


@dataclass
class AttributionReport:
    """Attribution analysis report for an incident.

    Attributes:
        incident_id: Associated incident ID
        root_cause_category: Primary root cause category
        root_cause_description: Human-readable root cause
        confidence_score: Overall confidence (0.0-1.0)
        contributing_factors: List of contributing factors
        evidence_observation_ids: Linked observation IDs
        recommended_prevention: Prevention recommendations
        time_window_minutes: Analysis time window
        created_at: Report creation timestamp
        id: Database ID (optional)
    """
    incident_id: int
    root_cause_category: RootCauseCategory
    root_cause_description: str = ""
    confidence_score: float = 0.0
    contributing_factors: List[ContributingFactor] = field(default_factory=list)
    evidence_observation_ids: List[int] = field(default_factory=list)
    recommended_prevention: List[str] = field(default_factory=list)
    time_window_minutes: int = 30
    created_at: str = field(default_factory=utc_now)
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "incident_id": self.incident_id,
            "root_cause_category": self.root_cause_category.value,
            "root_cause_description": self.root_cause_description,
            "confidence_score": self.confidence_score,
            "contributing_factors": [
                {
                    "factor_type": f.factor_type,
                    "description": f.description,
                    "evidence": f.evidence,
                    "confidence": f.confidence,
                    "timestamp": f.timestamp,
                    "observation_id": f.observation_id,
                }
                for f in self.contributing_factors
            ],
            "evidence_observation_ids": self.evidence_observation_ids,
            "recommended_prevention": self.recommended_prevention,
            "time_window_minutes": self.time_window_minutes,
            "created_at": self.created_at,
        }


class TimeSeriesAnalyzer:
    """Analyze time series data for anomalies."""

    def detect_spikes(
        self,
        values: List[float],
        timestamps: List[str],
        threshold_std: float = 2.0,
    ) -> List[Tuple[int, float]]:
        """Detect spikes in time series data.

        Returns list of (index, z_score) tuples for detected spikes.
        """
        if len(values) < 3:
            return []

        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = variance ** 0.5

        if std == 0:
            return []

        spikes = []
        for i, v in enumerate(values):
            z_score = abs(v - mean) / std
            if z_score > threshold_std:
                spikes.append((i, z_score))

        return spikes

    def detect_pattern_change(
        self,
        values: List[float],
        window_size: int = 5,
    ) -> Optional[int]:
        """Detect significant pattern changes.

        Returns index where pattern change was detected, or None.
        """
        if len(values) < window_size * 2:
            return None

        # Simple change point detection using mean difference
        for i in range(window_size, len(values) - window_size):
            before_mean = sum(values[i - window_size:i]) / window_size
            after_mean = sum(values[i:i + window_size]) / window_size

            if abs(after_mean - before_mean) > abs(before_mean) * 0.5:
                return i

        return None


class LogPatternMatcher:
    """Match log patterns for error identification."""

    ERROR_PATTERNS = {
        "exception": re.compile(r"\b(exception|error|traceback)\b", re.I),
        "timeout": re.compile(r"\b(timeout|timed out)\b", re.I),
        "oom": re.compile(r"\b(out of memory|oom|memory limit)\b", re.I),
        "connection_failed": re.compile(r"\b(connection refused|connection failed)\b", re.I),
        "permission_denied": re.compile(r"\b(permission denied|access denied|unauthorized)\b", re.I),
        "deployment": re.compile(r"\b(deploy|deployment|rolling update)\b", re.I),
        "restart": re.compile(r"\b(restart|restarting|reboot)\b", re.I),
    }

    def match_patterns(self, log_text: str) -> Dict[str, List[str]]:
        """Match error patterns in log text.

        Returns dict of pattern_name -> list of matches.
        """
        results = {}
        for name, pattern in self.ERROR_PATTERNS.items():
            matches = pattern.findall(log_text)
            if matches:
                results[name] = matches
        return results

    def calculate_error_score(self, log_text: str) -> float:
        """Calculate error severity score (0.0-1.0)."""
        score = 0.0
        text_lower = log_text.lower()

        # Critical patterns
        if "out of memory" in text_lower or "oom" in text_lower:
            score += 0.4
        if "exception" in text_lower:
            score += 0.3
        if "timeout" in text_lower:
            score += 0.2
        if "error" in text_lower:
            score += 0.1

        return min(score, 1.0)


class AttributionEngine:
    """Engine for root cause attribution analysis.

    Analyzes incidents to identify root causes and contributing factors
    using multiple analysis methods.

    Example:
        engine = AttributionEngine(conn)

        # Analyze an incident
        report = engine.analyze_incident(
            incident_id=123,
            time_window_minutes=30
        )

        # Save report
        engine.save_report(report)
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.incident_manager = IncidentManager(conn)
        self.time_analyzer = TimeSeriesAnalyzer()
        self.log_matcher = LogPatternMatcher()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Ensure attribution tables exist."""
        # Attribution reports table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS attribution_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                root_cause_category TEXT NOT NULL,
                root_cause_description TEXT NOT NULL,
                confidence_score REAL NOT NULL DEFAULT 0.0,
                contributing_factors TEXT NOT NULL DEFAULT '[]',
                evidence_observation_ids TEXT NOT NULL DEFAULT '[]',
                recommended_prevention TEXT NOT NULL DEFAULT '[]',
                time_window_minutes INTEGER DEFAULT 30,
                created_at TEXT NOT NULL
            )
        """)

        # Incident attributions linking table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS incident_attributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                attribution_report_id INTEGER NOT NULL REFERENCES attribution_reports(id) ON DELETE CASCADE,
                factor_type TEXT NOT NULL,
                factor_description TEXT NOT NULL,
                confidence REAL NOT NULL,
                observation_id INTEGER REFERENCES observations(id),
                created_at TEXT NOT NULL
            )
        """)

        # Indexes
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_attribution_incident
            ON attribution_reports(incident_id)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_attribution_category
            ON attribution_reports(root_cause_category)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_incident_attributions_report
            ON incident_attributions(attribution_report_id)
        """)
        self.conn.commit()

    def analyze_incident(
        self,
        incident_id: int,
        time_window_minutes: int = 30,
    ) -> AttributionReport:
        """Perform attribution analysis on an incident.

        Args:
            incident_id: Incident to analyze
            time_window_minutes: Time window before incident to analyze

        Returns:
            AttributionReport with findings
        """
        # Get incident details
        incident = self.incident_manager.get(incident_id)
        if not incident:
            raise ValueError(f"Incident {incident_id} not found")

        # Get observations in time window
        observations = self._get_observations_in_window(
            incident.detected_at,
            time_window_minutes
        )

        factors = []
        evidence_ids = []

        # Analyze observations
        for obs in observations:
            factor = self._analyze_observation(obs)
            if factor:
                factors.append(factor)
                evidence_ids.append(obs["id"])

        # Determine root cause
        category, description, confidence = self._determine_root_cause(
            incident, factors
        )

        # Generate recommendations
        recommendations = self._generate_recommendations(category, factors)

        return AttributionReport(
            incident_id=incident_id,
            root_cause_category=category,
            root_cause_description=description,
            confidence_score=confidence,
            contributing_factors=factors,
            evidence_observation_ids=evidence_ids,
            recommended_prevention=recommendations,
            time_window_minutes=time_window_minutes,
        )

    def _get_observations_in_window(
        self,
        incident_time: str,
        window_minutes: int,
    ) -> List[Dict[str, Any]]:
        """Get observations within time window before incident."""
        # Parse incident time
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(incident_time.replace('Z', '+00:00'))
            window_start = (dt - timedelta(minutes=window_minutes)).isoformat()
        except (ValueError, AttributeError):
            return []

        rows = self.conn.execute(
            """
            SELECT * FROM observations
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp DESC
            """,
            (window_start, incident_time)
        ).fetchall()

        return [dict(row) for row in rows]

    def _analyze_observation(self, obs: Dict[str, Any]) -> Optional[ContributingFactor]:
        """Analyze a single observation for contributing factors."""
        kind = obs.get("kind", "").lower()
        raw = obs.get("raw", "")
        title = obs.get("title", "")
        summary = obs.get("summary", "")

        combined_text = f"{title} {summary} {raw}"

        # Check for error patterns
        error_score = self.log_matcher.calculate_error_score(combined_text)
        if error_score > 0.3:
            patterns = self.log_matcher.match_patterns(combined_text)
            return ContributingFactor(
                factor_type="log_pattern",
                description=f"Error pattern detected: {list(patterns.keys())}",
                evidence=combined_text[:200],
                confidence=error_score,
                timestamp=obs.get("timestamp"),
                observation_id=obs.get("id"),
            )

        # Check for specific observation types
        if kind == "error" or "error" in title.lower():
            return ContributingFactor(
                factor_type="error_observation",
                description=f"Error observation: {title}",
                evidence=summary,
                confidence=0.7,
                timestamp=obs.get("timestamp"),
                observation_id=obs.get("id"),
            )

        if kind == "deployment" or "deploy" in title.lower():
            return ContributingFactor(
                factor_type="deployment",
                description=f"Recent deployment: {title}",
                evidence=summary,
                confidence=0.6,
                timestamp=obs.get("timestamp"),
                observation_id=obs.get("id"),
            )

        return None

    def _determine_root_cause(
        self,
        incident,
        factors: List[ContributingFactor],
    ) -> Tuple[RootCauseCategory, str, float]:
        """Determine root cause from factors.

        Returns (category, description, confidence).
        """
        if not factors:
            return (
                RootCauseCategory.UNKNOWN,
                "Insufficient data for root cause determination",
                0.0
            )

        # Count factor types
        factor_counts = {}
        for f in factors:
            factor_counts[f.factor_type] = factor_counts.get(f.factor_type, 0) + 1

        # Determine category based on factors
        category = RootCauseCategory.UNKNOWN
        descriptions = []
        total_confidence = 0.0

        for factor in factors:
            total_confidence += factor.confidence

            if factor.factor_type == "log_pattern":
                if "oom" in factor.description.lower():
                    category = RootCauseCategory.RESOURCE_EXHAUSTION
                elif "timeout" in factor.description.lower():
                    category = RootCauseCategory.EXTERNAL_DEPENDENCY
                elif "permission" in factor.description.lower():
                    category = RootCauseCategory.SECURITY_INCIDENT
                else:
                    category = RootCauseCategory.CODE_BUG

            elif factor.factor_type == "deployment":
                category = RootCauseCategory.DEPLOYMENT_ISSUE

            elif factor.factor_type == "error_observation":
                if category == RootCauseCategory.UNKNOWN:
                    category = RootCauseCategory.CODE_BUG

            descriptions.append(factor.description)

        avg_confidence = total_confidence / len(factors) if factors else 0.0

        # Build description
        description = f"Primary: {category.value}. "
        if descriptions:
            description += f"Key factors: {'; '.join(descriptions[:3])}"

        return category, description, min(avg_confidence * 1.2, 0.95)

    def _generate_recommendations(
        self,
        category: RootCauseCategory,
        factors: List[ContributingFactor],
    ) -> List[str]:
        """Generate prevention recommendations."""
        recommendations = []

        if category == RootCauseCategory.DEPLOYMENT_ISSUE:
            recommendations.append("Implement canary deployments with automated rollback")
            recommendations.append("Add pre-deployment health checks")
            recommendations.append("Require approval for high-risk deployments")

        elif category == RootCauseCategory.RESOURCE_EXHAUSTION:
            recommendations.append("Set up resource usage alerts at 80% threshold")
            recommendations.append("Implement auto-scaling policies")
            recommendations.append("Review memory limits and optimize resource allocation")

        elif category == RootCauseCategory.CODE_BUG:
            recommendations.append("Improve test coverage for error handling paths")
            recommendations.append("Add integration tests for critical workflows")
            recommendations.append("Review recent code changes for error-prone patterns")

        elif category == RootCauseCategory.EXTERNAL_DEPENDENCY:
            recommendations.append("Implement circuit breakers for external services")
            recommendations.append("Add timeout and retry policies")
            recommendations.append("Set up dependency health monitoring")

        elif category == RootCauseCategory.CONFIG_ERROR:
            recommendations.append("Validate configuration changes in staging environment")
            recommendations.append("Implement configuration schema validation")
            recommendations.append("Use infrastructure as code with code review")

        # Add factor-specific recommendations
        for factor in factors:
            if factor.factor_type == "deployment":
                recommendations.append("Review deployment checklist and add verification steps")

        return list(set(recommendations))  # Remove duplicates

    def save_report(self, report: AttributionReport) -> int:
        """Save attribution report to database."""
        cursor = self.conn.execute(
            """
            INSERT INTO attribution_reports
            (incident_id, root_cause_category, root_cause_description,
             confidence_score, contributing_factors, evidence_observation_ids,
             recommended_prevention, time_window_minutes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.incident_id,
                report.root_cause_category.value,
                report.root_cause_description,
                report.confidence_score,
                json.dumps([f.__dict__ for f in report.contributing_factors]),
                json.dumps(report.evidence_observation_ids),
                json.dumps(report.recommended_prevention),
                report.time_window_minutes,
                report.created_at,
            )
        )
        self.conn.commit()
        report.id = cursor.lastrowid

        # Save individual attributions
        for factor in report.contributing_factors:
            self.conn.execute(
                """
                INSERT INTO incident_attributions
                (incident_id, attribution_report_id, factor_type, factor_description,
                 confidence, observation_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.incident_id,
                    report.id,
                    factor.factor_type,
                    factor.description,
                    factor.confidence,
                    factor.observation_id,
                    report.created_at,
                )
            )
        self.conn.commit()

        return report.id

    def get_report(self, report_id: int) -> Optional[AttributionReport]:
        """Get attribution report by ID."""
        row = self.conn.execute(
            "SELECT * FROM attribution_reports WHERE id = ?",
            (report_id,)
        ).fetchone()

        if not row:
            return None

        return self._row_to_report(row)

    def get_report_for_incident(self, incident_id: int) -> Optional[AttributionReport]:
        """Get attribution report for an incident."""
        row = self.conn.execute(
            """
            SELECT * FROM attribution_reports
            WHERE incident_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (incident_id,)
        ).fetchone()

        if not row:
            return None

        return self._row_to_report(row)

    def list_reports(
        self,
        category: Optional[RootCauseCategory] = None,
        limit: int = 50,
    ) -> List[AttributionReport]:
        """List attribution reports with optional filter."""
        if category:
            rows = self.conn.execute(
                """
                SELECT * FROM attribution_reports
                WHERE root_cause_category = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (category.value, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM attribution_reports
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()

        return [self._row_to_report(row) for row in rows]

    def get_statistics(self) -> Dict[str, Any]:
        """Get attribution statistics."""
        # Category distribution
        category_rows = self.conn.execute(
            """
            SELECT root_cause_category, COUNT(*) as count
            FROM attribution_reports
            GROUP BY root_cause_category
            """
        ).fetchall()

        # Average confidence
        confidence_row = self.conn.execute(
            """
            SELECT AVG(confidence_score) as avg_confidence,
                   COUNT(*) as total
            FROM attribution_reports
            """
        ).fetchone()

        return {
            "total_reports": confidence_row["total"],
            "average_confidence": confidence_row["avg_confidence"] or 0.0,
            "category_distribution": {
                row["root_cause_category"]: row["count"]
                for row in category_rows
            },
        }

    def _row_to_report(self, row: sqlite3.Row) -> AttributionReport:
        """Convert database row to AttributionReport."""
        factors_data = json.loads(row["contributing_factors"])
        factors = [
            ContributingFactor(
                factor_type=f["factor_type"],
                description=f["description"],
                evidence=f["evidence"],
                confidence=f["confidence"],
                timestamp=f.get("timestamp"),
                observation_id=f.get("observation_id"),
            )
            for f in factors_data
        ]

        return AttributionReport(
            id=row["id"],
            incident_id=row["incident_id"],
            root_cause_category=RootCauseCategory(row["root_cause_category"]),
            root_cause_description=row["root_cause_description"],
            confidence_score=row["confidence_score"],
            contributing_factors=factors,
            evidence_observation_ids=json.loads(row["evidence_observation_ids"]),
            recommended_prevention=json.loads(row["recommended_prevention"]),
            time_window_minutes=row["time_window_minutes"],
            created_at=row["created_at"],
        )
