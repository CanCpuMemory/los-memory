"""Unit tests for hub_lite module."""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure memory_tool is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from memory_tool.hub_lite import (
    ExecutionRecord,
    HubLiteContext,
    HubLiteSession,
    create_execution_record,
    create_result_artifact,
    create_verification_checkpoint,
)


class TestHubLiteContext:
    """Tests for HubLiteContext dataclass."""

    def test_context_creation(self):
        """Test creating a HubLiteContext."""
        ctx = HubLiteContext(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
        )
        
        assert ctx.trace_id == "trace-123"
        assert ctx.parent_task_id == "parent-123"
        assert ctx.child_task_id == "child-123"
        assert ctx.repo_name == "los-memory"
        assert ctx.repo_path is not None
        assert Path(ctx.repo_path).exists()

    def test_context_with_session(self):
        """Test creating a context with session ID."""
        ctx = HubLiteContext(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            session_id="session-123",
        )
        
        assert ctx.session_id == "session-123"


class TestExecutionRecord:
    """Tests for ExecutionRecord dataclass."""

    def test_record_creation(self):
        """Test creating an ExecutionRecord."""
        record = ExecutionRecord(
            stage="execution",
            kind="progress",
            title="Test Title",
            summary="Test Summary",
        )
        
        assert record.stage == "execution"
        assert record.kind == "progress"
        assert record.title == "Test Title"
        assert record.summary == "Test Summary"
        assert record.observation_id is None
        assert record.timestamp is not None

    def test_record_to_dict(self):
        """Test converting record to dictionary."""
        record = ExecutionRecord(
            stage="verification",
            kind="checkpoint",
            title="Test",
            summary="Test",
            observation_id=42,
        )
        
        data = record.to_dict()
        
        assert data["stage"] == "verification"
        assert data["kind"] == "checkpoint"
        assert data["observation_id"] == 42


class TestHubLiteSession:
    """Tests for HubLiteSession class."""

    def test_session_creation(self):
        """Test creating a HubLiteSession."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
        )
        
        assert session.context.trace_id == "trace-123"
        assert session.agent == "kimi-k2p5"
        assert session.role == "writer"
        assert session.records == []

    def test_build_tags(self):
        """Test tag building."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            session_id="session-123",
        )
        
        tags = session._build_tags("execution", "progress", ["custom:tag"])
        
        assert "stage:execution" in tags
        assert "kind:progress" in tags
        assert "trace:trace-123" in tags
        assert "parent:parent-123" in tags
        assert "child:child-123" in tags
        assert "session:session-123" in tags
        assert "repo:los-memory" in tags
        assert "agent:kimi-k2p5" in tags
        assert "role:writer" in tags
        assert "custom:tag" in tags

    def test_build_tags_with_result(self):
        """Test tag building with result."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
        )
        
        tags = session._build_tags("result", "artifact", result="PASS")
        
        assert "result:PASS" in tags


class TestHubLiteSessionWithDatabase:
    """Tests requiring database."""

    @pytest.fixture
    def temp_db(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test.db"
        return str(db_path)

    def test_create_execution_record(self, temp_db):
        """Test creating execution record."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            db_path=temp_db,
        )
        
        record = session.create_execution_record(
            title="Test Execution",
            summary="Test execution summary",
        )
        
        assert record.observation_id is not None
        assert record.observation_id > 0
        assert record.stage == "execution"
        assert record.kind == "progress"
        assert "Test Execution" in record.title
        assert "trace:trace-123" in record.tags
        assert len(session.records) == 1

    def test_create_verification_checkpoint_pass(self, temp_db):
        """Test creating verification checkpoint with PASS."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            db_path=temp_db,
        )
        
        record = session.create_verification_checkpoint(
            checkpoint_data={
                "database": "PASS",
                "schema": "PASS",
            }
        )
        
        assert record.observation_id is not None
        assert "result:PASS" in record.tags
        assert "PASS" in record.summary

    def test_create_verification_checkpoint_fail(self, temp_db):
        """Test creating verification checkpoint with FAIL."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            db_path=temp_db,
        )
        
        record = session.create_verification_checkpoint(
            checkpoint_data={
                "database": "PASS",
                "schema": "FAIL",
            }
        )
        
        assert record.observation_id is not None
        assert "result:FAIL" in record.tags
        assert "FAIL" in record.summary

    def test_create_verification_checkpoint_warning(self, temp_db):
        """Test creating verification checkpoint with WARNING."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            db_path=temp_db,
        )
        
        record = session.create_verification_checkpoint(
            checkpoint_data={
                "database": "PASS",
                "schema": "WARN",
            }
        )
        
        assert record.observation_id is not None
        assert "result:WARNING" in record.tags

    def test_create_result_artifact(self, temp_db):
        """Test creating result artifact."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            db_path=temp_db,
        )
        
        record = session.create_result_artifact(
            acceptance_state="ACCEPTED",
        )
        
        assert record.observation_id is not None
        assert record.stage == "result"
        assert record.kind == "artifact"
        assert "acceptance:ACCEPTED" in record.tags
        assert "ACCEPTED" in record.summary

    def test_create_blocker_record(self, temp_db):
        """Test creating blocker record."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            db_path=temp_db,
        )
        
        record = session.create_blocker_record(
            blocker_type="PATH_UNRESOLVED",
            summary="Repository path not found",
        )
        
        assert record.observation_id is not None
        assert record.stage == "result"
        assert record.kind == "blocker"
        assert "blockerType:PATH_UNRESOLVED" in record.tags
        assert "acceptance:BLOCKED" in record.tags
        assert "escalation:parent" in record.tags

    def test_generate_report(self, temp_db):
        """Test generating report."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            db_path=temp_db,
        )
        
        # Create some records
        session.create_execution_record("Test 1", "Summary 1")
        session.create_verification_checkpoint({"check": "PASS"})
        session.create_result_artifact("ACCEPTED")
        
        report = session.generate_report()
        
        assert report["trace_id"] == "trace-123"
        assert report["parent_task_id"] == "parent-123"
        assert report["child_task_id"] == "child-123"
        assert report["repo_name"] == "los-memory"
        assert report["record_count"] == 3
        assert len(report["records"]) == 3

    def test_write_report(self, temp_db, tmp_path):
        """Test writing report to file."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            db_path=temp_db,
        )
        
        session.create_execution_record("Test", "Summary")
        
        output_path = tmp_path / "report.json"
        result_path = session.write_report(output_path)
        
        assert result_path == output_path
        assert output_path.exists()
        
        # Verify content
        import json
        with open(output_path) as f:
            data = json.load(f)
        
        assert data["trace_id"] == "trace-123"


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    @pytest.fixture
    def temp_db(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test.db"
        return str(db_path)

    def test_create_execution_record(self, temp_db):
        """Test create_execution_record convenience function."""
        record = create_execution_record(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            title="Test",
            summary="Summary",
            db_path=temp_db,
        )
        
        assert record.observation_id is not None
        assert record.stage == "execution"
        assert record.kind == "progress"

    def test_create_verification_checkpoint(self, temp_db):
        """Test create_verification_checkpoint convenience function."""
        record = create_verification_checkpoint(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            checkpoint_data={"check": "PASS"},
            db_path=temp_db,
        )
        
        assert record.observation_id is not None
        assert record.stage == "verification"

    def test_create_result_artifact(self, temp_db):
        """Test create_result_artifact convenience function."""
        record = create_result_artifact(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            acceptance_state="ACCEPTED",
            db_path=temp_db,
        )
        
        assert record.observation_id is not None
        assert record.stage == "result"
        assert record.kind == "artifact"


class TestHubLiteIntegrationPatterns:
    """Tests for hub-lite integration patterns."""

    @pytest.fixture
    def temp_db(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test.db"
        return str(db_path)

    def test_frozen_contract_metadata(self, temp_db):
        """Test that records include frozen contract metadata."""
        session = HubLiteSession(
            trace_id="trace-parent-epic-20260309063153",
            parent_task_id="epic-20260309063153",
            child_task_id="los-memory-20260309063153",
            session_id="child-los-memory-20260309063153",
            db_path=temp_db,
        )
        
        record = session.create_execution_record(
            title="Test",
            summary="Test",
        )
        
        assert record.metadata["trace_id"] == "trace-parent-epic-20260309063153"
        assert record.metadata["parent_task_id"] == "epic-20260309063153"
        assert record.metadata["child_task_id"] == "los-memory-20260309063153"

    def test_all_required_stages(self, temp_db):
        """Test creating all required stage records."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            db_path=temp_db,
        )
        
        # Execution/progress record
        exec_record = session.create_execution_record(
            title="Execution",
            summary="Execution in progress",
        )
        
        # Verification/checkpoint record
        ver_record = session.create_verification_checkpoint(
            checkpoint_data={"test": "PASS"},
        )
        
        # Result/artifact record
        res_record = session.create_result_artifact(
            acceptance_state="ACCEPTED",
        )
        
        # Verify all created
        assert exec_record.observation_id is not None
        assert ver_record.observation_id is not None
        assert res_record.observation_id is not None
        
        # Verify stages
        assert exec_record.stage == "execution"
        assert ver_record.stage == "verification"
        assert res_record.stage == "result"
        
        # Verify kinds
        assert exec_record.kind == "progress"
        assert ver_record.kind == "checkpoint"
        assert res_record.kind == "artifact"

    def test_acceptance_states(self, temp_db):
        """Test all acceptance states."""
        session = HubLiteSession(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            db_path=temp_db,
        )
        
        # ACCEPTED
        record1 = session.create_result_artifact("ACCEPTED")
        assert "acceptance:ACCEPTED" in record1.tags
        
        # ACCEPTED_WITH_NOTES
        record2 = session.create_result_artifact("ACCEPTED_WITH_NOTES")
        assert "acceptance:ACCEPTED_WITH_NOTES" in record2.tags
        
        # BLOCKED (via blocker record)
        record3 = session.create_blocker_record(
            blocker_type="TEST",
            summary="Test blocker",
        )
        assert "acceptance:BLOCKED" in record3.tags
