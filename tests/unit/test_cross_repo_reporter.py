"""Unit tests for cross_repo_reporter module."""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Ensure scripts is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from cross_repo_reporter import (
    ChildSessionReport,
    CrossRepoReport,
    CrossRepoReporter,
)


class TestChildSessionReport:
    """Tests for ChildSessionReport dataclass."""

    def test_report_creation(self):
        """Test creating a ChildSessionReport."""
        report = ChildSessionReport(
            child_task_id="child-123",
            repo_name="test-repo",
            status="complete",
            execution_records=2,
            verification_records=1,
            result_records=1,
            acceptance_state="ACCEPTED",
        )
        
        assert report.child_task_id == "child-123"
        assert report.repo_name == "test-repo"
        assert report.status == "complete"
        assert report.execution_records == 2
        assert report.verification_records == 1
        assert report.result_records == 1
        assert report.acceptance_state == "ACCEPTED"

    def test_report_defaults(self):
        """Test ChildSessionReport with defaults."""
        report = ChildSessionReport(
            child_task_id="child-123",
            repo_name="test-repo",
            status="in_progress",
        )
        
        assert report.execution_records == 0
        assert report.verification_records == 0
        assert report.result_records == 0
        assert report.blocker_records == 0
        assert report.artifact_paths == []
        assert report.blockers == []


class TestCrossRepoReport:
    """Tests for CrossRepoReport dataclass."""

    def test_report_creation(self):
        """Test creating a CrossRepoReport."""
        child_reports = [
            ChildSessionReport(
                child_task_id="child-1",
                repo_name="repo-1",
                status="complete",
            ),
            ChildSessionReport(
                child_task_id="child-2",
                repo_name="repo-2",
                status="blocked",
            ),
        ]
        
        report = CrossRepoReport(
            trace_id="trace-123",
            parent_task_id="parent-123",
            timestamp=datetime.now().isoformat(),
            overall_status="partial",
            child_sessions=child_reports,
        )
        
        assert report.trace_id == "trace-123"
        assert report.parent_task_id == "parent-123"
        assert report.overall_status == "partial"
        assert len(report.child_sessions) == 2


class TestCrossRepoReporter:
    """Tests for CrossRepoReporter class."""

    def test_reporter_creation(self):
        """Test creating a CrossRepoReporter."""
        reporter = CrossRepoReporter(
            trace_id="trace-123",
            parent_task_id="parent-123",
        )
        
        assert reporter.trace_id == "trace-123"
        assert reporter.parent_task_id == "parent-123"
        assert reporter.db_path is not None

    def test_extract_child_task_id(self):
        """Test extracting child task ID from tags."""
        reporter = CrossRepoReporter(trace_id="trace-123")
        
        tags = ["stage:execution", "child:child-123", "trace:trace-123"]
        child_id = reporter._extract_child_task_id(tags)
        
        assert child_id == "child-123"

    def test_extract_child_task_id_not_found(self):
        """Test extracting child task ID when not present."""
        reporter = CrossRepoReporter(trace_id="trace-123")
        
        tags = ["stage:execution", "trace:trace-123"]
        child_id = reporter._extract_child_task_id(tags)
        
        assert child_id is None

    def test_extract_repo_name(self):
        """Test extracting repo name from title."""
        reporter = CrossRepoReporter(trace_id="trace-123")
        
        title = "[los-memory] Test execution"
        repo = reporter._extract_repo_name(title)
        
        assert repo == "los-memory"

    def test_extract_repo_name_no_brackets(self):
        """Test extracting repo name without brackets."""
        reporter = CrossRepoReporter(trace_id="trace-123")
        
        title = "Test execution"
        repo = reporter._extract_repo_name(title)
        
        assert repo == "unknown"

    def test_extract_stage(self):
        """Test extracting stage from tags."""
        reporter = CrossRepoReporter(trace_id="trace-123")
        
        tags = ["stage:execution", "kind:progress"]
        stage = reporter._extract_stage(tags)
        
        assert stage == "execution"

    def test_extract_kind(self):
        """Test extracting kind from tags."""
        reporter = CrossRepoReporter(trace_id="trace-123")
        
        tags = ["stage:execution", "kind:progress"]
        kind = reporter._extract_kind(tags)
        
        assert kind == "progress"

    def test_extract_acceptance(self):
        """Test extracting acceptance state from tags."""
        reporter = CrossRepoReporter(trace_id="trace-123")
        
        tags = ["acceptance:ACCEPTED", "stage:result"]
        acceptance = reporter._extract_acceptance(tags)
        
        assert acceptance == "ACCEPTED"


class TestCrossRepoReporterWithDatabase:
    """Tests requiring database."""

    @pytest.fixture
    def setup_db(self, tmp_path):
        """Set up test database with sample records."""
        import sqlite3
        
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        
        # Create observations table
        conn.execute("""
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                project TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags TEXT NOT NULL,
                tags_text TEXT NOT NULL,
                raw TEXT NOT NULL,
                session_id INTEGER
            )
        """)
        
        # Insert test records
        trace_id = "trace-test-123"
        project = f"hub-lite-{trace_id}"
        
        # Los-memory records
        conn.execute(
            """
            INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(),
                project,
                "progress",
                "[los-memory] Execution started",
                "Starting validation",
                "stage:execution,kind:progress,trace:trace-test-123,parent:parent-123,child:los-memory-123,repo:los-memory",
                "stage:execution kind:progress",
                "Test",
                None,
            ),
        )
        
        conn.execute(
            """
            INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(),
                project,
                "checkpoint",
                "[los-memory] Verification complete",
                "All checks passed",
                "stage:verification,kind:checkpoint,result:PASS,trace:trace-test-123,parent:parent-123,child:los-memory-123",
                "stage:verification kind:checkpoint",
                "Test",
                None,
            ),
        )
        
        conn.execute(
            """
            INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(),
                project,
                "result",
                "[los-memory] Validation complete",
                "Ready for integration",
                "stage:result,kind:artifact,acceptance:ACCEPTED,trace:trace-test-123,parent:parent-123,child:los-memory-123",
                "stage:result kind:artifact",
                "Test",
                None,
            ),
        )
        
        # Blocked repo records
        conn.execute(
            """
            INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(),
                project,
                "progress",
                "[lsclaw] Execution started",
                "Starting validation",
                "stage:execution,kind:progress,trace:trace-test-123,parent:parent-123,child:lsclaw-123",
                "stage:execution kind:progress",
                "Test",
                None,
            ),
        )
        
        conn.execute(
            """
            INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(),
                project,
                "blocker",
                "[lsclaw] Blocker: PATH_UNRESOLVED",
                "Repository not found",
                "stage:result,kind:blocker,blockerType:PATH_UNRESOLVED,acceptance:BLOCKED,escalation:parent,trace:trace-test-123,parent:parent-123,child:lsclaw-123",
                "stage:result kind:blocker",
                "Test",
                None,
            ),
        )
        
        conn.commit()
        conn.close()
        
        return str(db_path)

    def test_generate_report(self, setup_db):
        """Test generating cross-repo report."""
        reporter = CrossRepoReporter(
            trace_id="trace-test-123",
            parent_task_id="parent-123",
            db_path=setup_db,
        )

        report = reporter.generate_report()

        assert report.trace_id == "trace-test-123"
        assert report.parent_task_id == "parent-123"
        assert report.overall_status == "blocked"  # One session has blockers
        assert report.aggregation["total_records"] == 5
        assert report.aggregation["total_child_sessions"] == 2
        
        # Check child sessions
        child_ids = {s.child_task_id for s in report.child_sessions}
        assert "los-memory-123" in child_ids
        assert "lsclaw-123" in child_ids
        
        # Check statuses
        los_memory = next(s for s in report.child_sessions if s.child_task_id == "los-memory-123")
        assert los_memory.status == "complete"
        assert los_memory.acceptance_state.upper() in ["ACCEPTED", "ACCEPT"]
        assert los_memory.execution_records == 1
        assert los_memory.verification_records == 1
        assert los_memory.result_records == 1

        lsclaw = next(s for s in report.child_sessions if s.child_task_id == "lsclaw-123")
        assert lsclaw.status == "blocked"
        assert lsclaw.blocker_records == 1

    def test_write_json_report(self, setup_db, tmp_path):
        """Test writing JSON report."""
        reporter = CrossRepoReporter(
            trace_id="trace-test-123",
            db_path=setup_db,
        )
        
        output_path = tmp_path / "report.json"
        result_path = reporter.write_json_report(output_path)
        
        assert result_path == output_path
        assert output_path.exists()
        
        with open(output_path) as f:
            data = json.load(f)
        
        assert data["trace_id"] == "trace-test-123"
        assert data["overall_status"] == "blocked"
        assert "child_sessions" in data
        assert "aggregation" in data

    def test_write_markdown_summary(self, setup_db, tmp_path):
        """Test writing Markdown summary."""
        reporter = CrossRepoReporter(
            trace_id="trace-test-123",
            db_path=setup_db,
        )
        
        output_path = tmp_path / "report.md"
        result_path = reporter.write_markdown_summary(output_path)
        
        assert result_path == output_path
        assert output_path.exists()
        
        content = output_path.read_text()
        
        assert "# Cross-Repo Integration Report" in content
        assert "trace-test-123" in content
        assert "blocked" in content
        assert "los-memory" in content
        assert "lsclaw" in content

    def test_write_dispatch_log(self, setup_db, tmp_path):
        """Test writing dispatch log."""
        reporter = CrossRepoReporter(
            trace_id="trace-test-123",
            parent_task_id="parent-123",
            db_path=setup_db,
        )
        
        output_path = tmp_path / "dispatch.json"
        result_path = reporter.write_dispatch_log(output_path)
        
        assert result_path == output_path
        assert output_path.exists()
        
        with open(output_path) as f:
            data = json.load(f)
        
        assert data["traceId"] == "trace-test-123"
        assert data["parentTaskId"] == "parent-123"
        assert "childSessions" in data
        assert "aggregation" in data

    def test_empty_database(self, tmp_path):
        """Test generating report with empty database."""
        import sqlite3
        
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                project TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags TEXT NOT NULL,
                tags_text TEXT NOT NULL,
                raw TEXT NOT NULL,
                session_id INTEGER
            )
        """)
        conn.commit()
        conn.close()
        
        reporter = CrossRepoReporter(
            trace_id="trace-empty",
            db_path=str(db_path),
        )
        
        report = reporter.generate_report()
        
        assert report.overall_status == "blocked"  # No sessions = blocked
        assert report.aggregation["total_records"] == 0
        assert report.aggregation["total_child_sessions"] == 0


class TestReportAggregation:
    """Tests for report aggregation logic."""

    @pytest.fixture
    def setup_complex_db(self, tmp_path):
        """Set up database with complex scenario."""
        import sqlite3
        
        db_path = tmp_path / "complex.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        
        conn.execute("""
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                project TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags TEXT NOT NULL,
                tags_text TEXT NOT NULL,
                raw TEXT NOT NULL,
                session_id INTEGER
            )
        """)
        
        trace_id = "trace-complex"
        project = f"hub-lite-{trace_id}"
        
        # Three repos: complete, partial, blocked
        repos = [
            ("repo1", "complete", "ACCEPTED", 3),
            ("repo2", "in_progress", None, 2),
            ("repo3", "blocked", "BLOCKED", 2),
        ]
        
        for repo_name, status, acceptance, record_count in repos:
            for i in range(record_count):
                if i == 0:
                    stage, kind = "execution", "progress"
                elif i == record_count - 1 and status == "complete":
                    stage, kind = "result", "artifact"
                elif i == record_count - 1 and status == "blocked":
                    stage, kind = "result", "blocker"
                else:
                    stage, kind = "verification", "checkpoint"
                
                tags = f"stage:{stage},kind:{kind},trace:{trace_id},parent:parent-123,child:{repo_name}-123"
                if acceptance:
                    tags += f",acceptance:{acceptance}"
                
                conn.execute(
                    """
                    INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.now().isoformat(),
                        project,
                        kind,
                        f"[{repo_name}] Record {i}",
                        f"Test record {i}",
                        tags,
                        f"stage:{stage} kind:{kind}",
                        "Test",
                        None,
                    ),
                )
        
        conn.commit()
        conn.close()
        
        return str(db_path)

    def test_complex_aggregation(self, setup_complex_db):
        """Test aggregation with multiple sessions."""
        reporter = CrossRepoReporter(
            trace_id="trace-complex",
            db_path=setup_complex_db,
        )
        
        report = reporter.generate_report()
        
        # Overall status should be blocked (has one blocked session)
        assert report.overall_status == "blocked"
        
        # Aggregation counts
        assert report.aggregation["total_records"] == 7
        assert report.aggregation["total_child_sessions"] == 3
        assert report.aggregation["complete_sessions"] == 1
        assert report.aggregation["blocked_sessions"] == 1
        assert report.aggregation["in_progress_sessions"] == 1

    def test_all_complete_status(self, tmp_path):
        """Test overall status when all complete."""
        import sqlite3
        
        db_path = tmp_path / "all_complete.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        
        conn.execute("""
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                project TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags TEXT NOT NULL,
                tags_text TEXT NOT NULL,
                raw TEXT NOT NULL,
                session_id INTEGER
            )
        """)
        
        trace_id = "trace-complete"
        project = f"hub-lite-{trace_id}"
        
        # All repos complete
        for repo_name in ["repo1", "repo2"]:
            conn.execute(
                """
                INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(),
                    project,
                    "result",
                    f"[{repo_name}] Complete",
                    "Done",
                    f"stage:result,kind:artifact,acceptance:ACCEPTED,trace:{trace_id},child:{repo_name}-123",
                    "stage:result kind:artifact",
                    "Test",
                    None,
                ),
            )
        
        conn.commit()
        conn.close()
        
        reporter = CrossRepoReporter(
            trace_id="trace-complete",
            db_path=str(db_path),
        )
        
        report = reporter.generate_report()
        
        assert report.overall_status == "complete"
        assert report.aggregation["complete_sessions"] == 2
