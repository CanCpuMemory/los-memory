"""Unit tests for lsclaw records creation script."""
import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from create_lsclaw_records import (
    create_execution_progress_record,
    create_observation,
    create_result_artifact_record,
    create_verification_checkpoint_record,
    get_database_connection,
    write_json_records,
    TRACE_ID,
    PARENT_TASK_ID,
    CHILD_TASK_ID,
    SESSION_ID,
    REPO_NAME,
)


class TestGetDatabaseConnection:
    """Tests for database connection function."""

    @patch("create_lsclaw_records.resolve_db_path")
    @patch("create_lsclaw_records.connect_db")
    @patch("create_lsclaw_records.ensure_schema")
    def test_get_connection_default_path(self, mock_ensure, mock_connect, mock_resolve):
        """Test getting connection with default path."""
        mock_resolve.return_value = "/test/db.sqlite"
        mock_connect.return_value = MagicMock()
        
        conn, path = get_database_connection()
        
        assert path == "/test/db.sqlite"
        mock_connect.assert_called_once_with("/test/db.sqlite")
        mock_ensure.assert_called_once()

    def test_get_connection_custom_path(self):
        """Test getting connection with custom path."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        
        try:
            conn, path = get_database_connection(db_path)
            assert path == db_path
            assert conn is not None
            conn.close()
        finally:
            Path(db_path).unlink(missing_ok=True)


class TestCreateObservation:
    """Tests for create_observation function."""

    def test_create_observation_basic(self):
        """Test creating a basic observation."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Create the observations table
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
        
        try:
            result = create_observation(
                conn=conn,
                title="Test Title",
                summary="Test Summary",
                tags=["tag1", "tag2"],
                project="test-project",
                kind="test",
            )
            
            assert result["ok"] is True
            assert result["id"] == 1
            assert result["title"] == "Test Title"
            assert result["summary"] == "Test Summary"
            assert result["tags"] == ["tag1", "tag2"]
            
            # Verify in database
            row = conn.execute("SELECT * FROM observations WHERE id = 1").fetchone()
            assert row["title"] == "Test Title"
            assert row["project"] == "test-project"
            assert row["kind"] == "test"
            assert row["tags"] == "tag1,tag2"
        finally:
            conn.close()

    def test_create_observation_default_project(self):
        """Test creating observation with default project."""
        conn = sqlite3.connect(":memory:")
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
        
        try:
            result = create_observation(
                conn=conn,
                title="Test",
                summary="Test",
                tags=["test"],
            )
            
            row = conn.execute("SELECT * FROM observations WHERE id = 1").fetchone()
            assert TRACE_ID in row["project"]
            assert "hub-lite-" in row["project"]
        finally:
            conn.close()


class TestCreateExecutionProgressRecord:
    """Tests for execution progress record creation."""

    def test_create_progress_record(self):
        """Test creating execution/progress record."""
        conn = sqlite3.connect(":memory:")
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
        
        try:
            result = create_execution_progress_record(conn, dry_run=False)
            
            assert result["ok"] is True
            assert result["id"] == 1
            assert REPO_NAME in result["title"]
            assert "Ledger boundary validation" in result["title"]
            
            # Check tags
            assert "stage:execution" in result["tags"]
            assert "kind:progress" in result["tags"]
            assert f"trace:{TRACE_ID}" in result["tags"]
            assert f"parent:{PARENT_TASK_ID}" in result["tags"]
            assert f"child:{CHILD_TASK_ID}" in result["tags"]
        finally:
            conn.close()

    def test_create_progress_record_dry_run(self):
        """Test dry run mode for progress record."""
        conn = MagicMock()
        
        result = create_execution_progress_record(conn, dry_run=True)
        
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert REPO_NAME in result["title"]
        conn.execute.assert_not_called()


class TestCreateVerificationCheckpointRecord:
    """Tests for verification checkpoint record creation."""

    def test_create_checkpoint_record(self):
        """Test creating verification/checkpoint record."""
        conn = sqlite3.connect(":memory:")
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
        
        checkpoint_data = {
            "database": "PASS",
            "schema": "PASS",
            "cli": "PASS",
            "modules": "PASS",
        }
        
        try:
            result = create_verification_checkpoint_record(
                conn, checkpoint_data=checkpoint_data, dry_run=False
            )
            
            assert result["ok"] is True
            assert result["id"] == 1
            assert "Core ledger boundary validation" in result["title"]
            assert "PASS" in result["summary"]
            
            # Check tags
            assert "stage:verification" in result["tags"]
            assert "kind:checkpoint" in result["tags"]
            assert "result:PASS" in result["tags"]
        finally:
            conn.close()

    def test_create_checkpoint_record_dry_run(self):
        """Test dry run mode for checkpoint record."""
        conn = MagicMock()
        
        result = create_verification_checkpoint_record(conn, dry_run=True)
        
        assert result["ok"] is True
        assert result["dry_run"] is True
        conn.execute.assert_not_called()


class TestCreateResultArtifactRecord:
    """Tests for result artifact record creation."""

    def test_create_result_record_accepted(self):
        """Test creating result/artifact record with ACCEPTED state."""
        conn = sqlite3.connect(":memory:")
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
        
        try:
            result = create_result_artifact_record(
                conn, acceptance_state="ACCEPTED", dry_run=False
            )
            
            assert result["ok"] is True
            assert result["id"] == 1
            assert "complete" in result["title"]
            assert "ACCEPTED" in result["summary"]
            
            # Check tags
            assert "stage:result" in result["tags"]
            assert "kind:artifact" in result["tags"]
            assert "acceptance:ACCEPTED" in result["tags"]
        finally:
            conn.close()

    def test_create_result_record_blocked(self):
        """Test creating result/artifact record with BLOCKED state."""
        conn = sqlite3.connect(":memory:")
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
        
        try:
            result = create_result_artifact_record(
                conn, acceptance_state="BLOCKED", dry_run=False
            )
            
            assert result["ok"] is True
            assert "BLOCKED" in result["summary"]
            assert "acceptance:BLOCKED" in result["tags"]
        finally:
            conn.close()


class TestWriteJsonRecords:
    """Tests for JSON record writing."""

    def test_write_records_creates_files(self, tmp_path):
        """Test that write_json_records creates expected files."""
        records = [
            {
                "type": "execution/progress",
                "data": {
                    "ok": True,
                    "id": 1,
                    "title": "Test Progress",
                    "summary": "Test summary",
                    "tags": ["test"],
                },
            },
            {
                "type": "verification/checkpoint",
                "data": {
                    "ok": True,
                    "id": 2,
                    "title": "Test Checkpoint",
                    "summary": "Test summary",
                    "tags": ["test"],
                },
            },
        ]
        
        write_json_records(records, tmp_path)
        
        # Check that files were created
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 3  # 2 records + 1 manifest
        
        # Check manifest file exists
        manifest_files = list(tmp_path.glob("*manifest*.json"))
        assert len(manifest_files) == 1
        
        # Verify manifest content
        with open(manifest_files[0]) as f:
            manifest = json.load(f)
        
        assert manifest["traceId"] == TRACE_ID
        assert manifest["parentTaskId"] == PARENT_TASK_ID
        assert manifest["childTaskId"] == CHILD_TASK_ID
        assert manifest["repoName"] == REPO_NAME
        assert len(manifest["records"]) == 2

    def test_write_records_content(self, tmp_path):
        """Test that written records have correct content."""
        records = [
            {
                "type": "result/artifact",
                "data": {
                    "ok": True,
                    "id": 100,
                    "title": "Test Result",
                    "summary": "Test result summary",
                    "tags": ["stage:result", "acceptance:ACCEPTED"],
                },
            },
        ]
        
        write_json_records(records, tmp_path)
        
        # Find and verify the record file
        record_files = [f for f in tmp_path.glob("*.json") if "manifest" not in f.name]
        assert len(record_files) == 1
        
        with open(record_files[0]) as f:
            data = json.load(f)
        
        assert data["traceId"] == TRACE_ID
        assert data["parentTaskId"] == PARENT_TASK_ID
        assert data["stage"] == "result"
        assert data["kind"] == "artifact"
        assert data["observationId"] == 100
        assert data["agent"] == "kimi-k2p5"
        assert data["role"] == "writer"


class TestConstants:
    """Tests for module constants."""

    def test_trace_id_format(self):
        """Test that trace ID has expected format."""
        assert TRACE_ID.startswith("trace-parent-epic-")
        assert len(TRACE_ID) > len("trace-parent-epic-")

    def test_parent_task_id_format(self):
        """Test that parent task ID has expected format."""
        assert PARENT_TASK_ID.startswith("epic-")
        assert len(PARENT_TASK_ID) > len("epic-")

    def test_child_task_id_format(self):
        """Test that child task ID has expected format."""
        assert CHILD_TASK_ID.startswith("los-memory-")
        assert len(CHILD_TASK_ID) > len("los-memory-")

    def test_session_id_format(self):
        """Test that session ID has expected format."""
        assert SESSION_ID.startswith("child-")
        assert CHILD_TASK_ID in SESSION_ID

    def test_repo_name(self):
        """Test that repo name is correct."""
        assert REPO_NAME == "los-memory"
