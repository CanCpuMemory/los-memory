"""Tests for attribution analysis engine.

Tests cover root cause analysis, factor detection, and report generation.
"""
import pytest

from memory_tool.attribution_engine import (
    AttributionEngine,
    AttributionReport,
    ContributingFactor,
    LogPatternMatcher,
    RootCauseCategory,
    TimeSeriesAnalyzer,
)


class TestTimeSeriesAnalyzer:
    """Test time series analysis."""

    def test_detect_spikes(self):
        """Test spike detection in time series."""
        analyzer = TimeSeriesAnalyzer()
        values = [1.0, 1.1, 1.0, 5.0, 1.0, 1.2]
        timestamps = ["2024-01-01T00:00:00Z"] * 6

        spikes = analyzer.detect_spikes(values, timestamps, threshold_std=2.0)

        assert len(spikes) > 0
        assert spikes[0][0] == 3  # Index of spike

    def test_no_spikes_in_stable_data(self):
        """Test no spikes in stable data."""
        analyzer = TimeSeriesAnalyzer()
        values = [1.0, 1.1, 1.0, 1.05, 1.0, 1.02]
        timestamps = ["2024-01-01T00:00:00Z"] * 6

        spikes = analyzer.detect_spikes(values, timestamps, threshold_std=2.0)

        assert len(spikes) == 0

    def test_detect_pattern_change(self):
        """Test pattern change detection."""
        analyzer = TimeSeriesAnalyzer()
        values = [1.0] * 10 + [5.0] * 10  # Sudden shift

        change_idx = analyzer.detect_pattern_change(values, window_size=3)

        assert change_idx is not None


class TestLogPatternMatcher:
    """Test log pattern matching."""

    def test_match_exception_pattern(self):
        """Test exception pattern matching."""
        matcher = LogPatternMatcher()
        log_text = "Error: NullPointerException in UserService"

        patterns = matcher.match_patterns(log_text)

        assert "exception" in patterns

    def test_match_timeout_pattern(self):
        """Test timeout pattern matching."""
        matcher = LogPatternMatcher()
        log_text = "Connection timed out after 30 seconds"

        patterns = matcher.match_patterns(log_text)

        assert "timeout" in patterns

    def test_error_score_calculation(self):
        """Test error severity score."""
        matcher = LogPatternMatcher()

        critical = matcher.calculate_error_score("Out of memory exception")
        minor = matcher.calculate_error_score("Info: Process started")

        assert critical > minor
        assert critical >= 0.4

    def test_no_match_for_clean_logs(self):
        """Test no patterns in clean logs."""
        matcher = LogPatternMatcher()
        log_text = "INFO: Application started successfully"

        patterns = matcher.match_patterns(log_text)

        assert len(patterns) == 0


class TestAttributionEngine:
    """Test attribution engine."""

    @pytest.fixture
    def engine(self, db_connection):
        return AttributionEngine(db_connection)

    @pytest.fixture
    def incident_with_obs(self, db_connection):
        """Create incident with observations for analysis."""
        from memory_tool.incidents import IncidentManager
        from datetime import datetime, timedelta, timezone

        # Create observation BEFORE incident (within analysis window)
        # Use ISO format with timezone like the utils.utc_now() does
        obs_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db_connection.execute(
            """
            INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (obs_time, "test", "error", "OutOfMemoryError", "Java heap space",
             '["error", "memory"]', "error memory", "Exception: OutOfMemoryError"),
        )
        obs_id = db_connection.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Create incident AFTER observation
        manager = IncidentManager(db_connection)
        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Service crashed",
            description="Out of memory error detected",
            context_snapshot={"error_count": 5},
        )

        return incident, obs_id

    def test_analyze_incident_finds_root_cause(self, engine, incident_with_obs):
        """Test incident analysis finds root cause."""
        incident, _ = incident_with_obs

        report = engine.analyze_incident(incident.id, time_window_minutes=60)

        assert report.incident_id == incident.id
        assert report.root_cause_category != RootCauseCategory.UNKNOWN
        assert report.confidence_score > 0

    def test_analyze_incident_not_found(self, engine):
        """Test analyze non-existent incident raises error."""
        with pytest.raises(ValueError, match="not found"):
            engine.analyze_incident(99999)

    def test_save_and_retrieve_report(self, engine, incident_with_obs):
        """Test saving and retrieving attribution report."""
        incident, _ = incident_with_obs

        report = engine.analyze_incident(incident.id)
        report_id = engine.save_report(report)

        retrieved = engine.get_report(report_id)

        assert retrieved is not None
        assert retrieved.incident_id == incident.id
        assert retrieved.id == report_id

    def test_get_report_for_incident(self, engine, incident_with_obs):
        """Test get report by incident ID."""
        incident, _ = incident_with_obs

        report = engine.analyze_incident(incident.id)
        engine.save_report(report)

        retrieved = engine.get_report_for_incident(incident.id)

        assert retrieved is not None
        assert retrieved.incident_id == incident.id

    def test_list_reports(self, engine, incident_with_obs):
        """Test listing attribution reports."""
        incident, _ = incident_with_obs

        report = engine.analyze_incident(incident.id)
        engine.save_report(report)

        reports = engine.list_reports(limit=10)

        assert len(reports) > 0

    def test_list_reports_by_category(self, engine, incident_with_obs):
        """Test filtering reports by category."""
        incident, _ = incident_with_obs

        report = engine.analyze_incident(incident.id)
        engine.save_report(report)

        # Filter by actual category found
        reports = engine.list_reports(
            category=report.root_cause_category,
            limit=10
        )

        assert len(reports) >= 1

    def test_get_statistics(self, engine, incident_with_obs):
        """Test getting attribution statistics."""
        incident, _ = incident_with_obs

        report = engine.analyze_incident(incident.id)
        engine.save_report(report)

        stats = engine.get_statistics()

        assert "total_reports" in stats
        assert "average_confidence" in stats
        assert "category_distribution" in stats
        assert stats["total_reports"] >= 1


class TestAttributionReport:
    """Test AttributionReport dataclass."""

    def test_to_dict(self):
        """Test report serialization."""
        factor = ContributingFactor(
            factor_type="log_pattern",
            description="Out of memory error",
            evidence="Exception in thread",
            confidence=0.8,
        )

        report = AttributionReport(
            incident_id=1,
            root_cause_category=RootCauseCategory.RESOURCE_EXHAUSTION,
            root_cause_description="Memory leak caused crash",
            confidence_score=0.85,
            contributing_factors=[factor],
            evidence_observation_ids=[101],
            recommended_prevention=["Add memory monitoring"],
        )

        data = report.to_dict()

        assert data["incident_id"] == 1
        assert data["root_cause_category"] == "resource_exhaustion"
        assert data["confidence_score"] == 0.85
        assert len(data["contributing_factors"]) == 1


class TestRootCauseCategory:
    """Test root cause categories."""

    def test_category_values(self):
        """Test category enum values."""
        assert RootCauseCategory.CODE_BUG.value == "code_bug"
        assert RootCauseCategory.DEPLOYMENT_ISSUE.value == "deployment_issue"
        assert RootCauseCategory.INFRASTRUCTURE_FAILURE.value == "infrastructure_failure"


class TestSchemaV11:
    """Test database schema v11."""

    def test_attribution_reports_table_exists(self, db_connection):
        """Test attribution_reports table exists."""
        row = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='attribution_reports'"
        ).fetchone()
        assert row is not None

    def test_incident_attributions_table_exists(self, db_connection):
        """Test incident_attributions table exists."""
        row = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incident_attributions'"
        ).fetchone()
        assert row is not None

    def test_attribution_indexes_exist(self, db_connection):
        """Test attribution indexes exist."""
        indexes = [
            "idx_attribution_incident",
            "idx_attribution_category",
            "idx_incident_attributions_report",
        ]
        for idx in indexes:
            row = db_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (idx,),
            ).fetchone()
            assert row is not None, f"Index {idx} not found"

    def test_schema_version_at_least_11(self, db_connection):
        """Test schema version is at least 11."""
        from memory_tool.database import get_schema_version

        version = get_schema_version(db_connection)
        assert version >= 11  # v12 adds knowledge base tables
