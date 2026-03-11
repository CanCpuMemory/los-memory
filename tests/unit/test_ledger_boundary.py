"""Unit tests for ledger boundary validation script."""
import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from validate_ledger_boundary import (
    BoundaryCheck,
    BoundaryReport,
    check_cli_entrypoint,
    check_database_connectivity,
    check_required_modules,
    check_schema_integrity,
    run_smoke_tests,
    validate_ledger_boundary,
)


class TestBoundaryCheck:
    """Tests for BoundaryCheck dataclass."""

    def test_boundary_check_creation(self):
        """Test creating a BoundaryCheck instance."""
        check = BoundaryCheck(
            name="test_check",
            status="pass",
            message="Test passed",
            details={"key": "value"}
        )
        assert check.name == "test_check"
        assert check.status == "pass"
        assert check.message == "Test passed"
        assert check.details == {"key": "value"}

    def test_boundary_check_defaults(self):
        """Test BoundaryCheck with default details."""
        check = BoundaryCheck(
            name="test_check",
            status="fail",
            message="Test failed"
        )
        assert check.details == {}


class TestCheckDatabaseConnectivity:
    """Tests for database connectivity check."""

    @patch("validate_ledger_boundary.connect_db")
    @patch("validate_ledger_boundary.ensure_schema")
    @patch("validate_ledger_boundary.ensure_fts")
    @patch("validate_ledger_boundary.get_schema_version")
    @patch("validate_ledger_boundary.resolve_db_path")
    def test_database_connectivity_pass(
        self, mock_resolve, mock_version, mock_fts, mock_schema, mock_connect
    ):
        """Test successful database connectivity check."""
        mock_resolve.return_value = "/test/db.sqlite"
        mock_connect.return_value = MagicMock()
        mock_version.return_value = 12

        result = check_database_connectivity()

        assert result.status == "pass"
        assert "Database connectivity verified" in result.message
        assert result.details["schema_version"] == 12
        assert result.details["path"] == "/test/db.sqlite"

    @patch("validate_ledger_boundary.connect_db")
    @patch("validate_ledger_boundary.resolve_db_path")
    def test_database_connectivity_fail(self, mock_resolve, mock_connect):
        """Test failed database connectivity check."""
        mock_resolve.return_value = "/test/db.sqlite"
        mock_connect.side_effect = Exception("Connection refused")

        result = check_database_connectivity()

        assert result.status == "fail"
        assert "Connection refused" in result.message


class TestCheckSchemaIntegrity:
    """Tests for schema integrity check."""

    @patch("validate_ledger_boundary.connect_db")
    @patch("validate_ledger_boundary.ensure_schema")
    @patch("validate_ledger_boundary.get_schema_version")
    @patch("validate_ledger_boundary.resolve_db_path")
    @patch("validate_ledger_boundary.SCHEMA_VERSION", 12)
    def test_schema_integrity_pass(
        self, mock_resolve, mock_version, mock_schema, mock_connect
    ):
        """Test schema integrity with current version."""
        mock_resolve.return_value = "/test/db.sqlite"
        conn = MagicMock()
        mock_connect.return_value = conn
        mock_version.return_value = 12

        # Mock table query
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {"name": "observations"},
            {"name": "sessions"},
            {"name": "checkpoints"},
            {"name": "meta"},
        ]
        conn.execute.return_value = cursor

        result = check_schema_integrity()

        assert result.status == "pass"
        assert "up to date" in result.message

    @patch("validate_ledger_boundary.connect_db")
    @patch("validate_ledger_boundary.ensure_schema")
    @patch("validate_ledger_boundary.get_schema_version")
    @patch("validate_ledger_boundary.resolve_db_path")
    @patch("validate_ledger_boundary.SCHEMA_VERSION", 12)
    def test_schema_integrity_missing_tables(
        self, mock_resolve, mock_version, mock_schema, mock_connect
    ):
        """Test schema integrity with missing tables."""
        mock_resolve.return_value = "/test/db.sqlite"
        conn = MagicMock()
        mock_connect.return_value = conn
        mock_version.return_value = 12

        # Mock table query with missing tables
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"name": "observations"}]  # Missing sessions, checkpoints, meta
        conn.execute.return_value = cursor

        result = check_schema_integrity()

        assert result.status == "fail"
        assert "Missing required tables" in result.message


class TestCheckCliEntrypoint:
    """Tests for CLI entrypoint check."""

    @patch("validate_ledger_boundary.subprocess.run")
    def test_cli_entrypoint_pass(self, mock_run):
        """Test successful CLI entrypoint check."""
        mock_run.return_value = MagicMock(returncode=0, stdout="help text")

        result = check_cli_entrypoint()

        assert result.status == "pass"
        assert "accessible" in result.message

    @patch("validate_ledger_boundary.subprocess.run")
    def test_cli_entrypoint_fail(self, mock_run):
        """Test failed CLI entrypoint check."""
        mock_run.return_value = MagicMock(returncode=1, stderr="error")

        result = check_cli_entrypoint()

        assert result.status == "fail"


class TestCheckRequiredModules:
    """Tests for required modules check."""

    @patch("builtins.__import__")
    def test_required_modules_pass(self, mock_import):
        """Test all modules import successfully."""
        result = check_required_modules()

        assert result.status == "pass"
        assert result.details["module_count"] == 7

    @patch("builtins.__import__")
    def test_required_modules_fail(self, mock_import):
        """Test missing module detection."""
        def side_effect(name, *args, **kwargs):
            if name == "memory_tool.database":
                raise ImportError("No module named memory_tool.database")
            return MagicMock()

        mock_import.side_effect = side_effect

        result = check_required_modules()

        assert result.status == "fail"
        assert "memory_tool.database" in str(result.details["missing_modules"])


class TestRunSmokeTests:
    """Tests for smoke test execution."""

    @patch("validate_ledger_boundary.subprocess.run")
    def test_smoke_tests_run(self, mock_run):
        """Test smoke test execution."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="test session starts\ncollected 10 items",
            stderr=""
        )

        result = run_smoke_tests()

        assert result["ran"] is True

    @patch("validate_ledger_boundary.subprocess.run")
    def test_smoke_tests_timeout(self, mock_run):
        """Test smoke test timeout handling."""
        mock_run.side_effect = TimeoutError()

        result = run_smoke_tests()

        assert result["ran"] is False
        assert result["errors"] == 1


class TestValidateLedgerBoundary:
    """Tests for main validation function."""

    @patch("validate_ledger_boundary.check_database_connectivity")
    @patch("validate_ledger_boundary.check_schema_integrity")
    @patch("validate_ledger_boundary.check_cli_entrypoint")
    @patch("validate_ledger_boundary.check_required_modules")
    @patch("validate_ledger_boundary.run_smoke_tests")
    @patch("validate_ledger_boundary.connect_db")
    @patch("validate_ledger_boundary.get_schema_version")
    @patch("validate_ledger_boundary.resolve_db_path")
    def test_validation_pass(
        self, mock_resolve, mock_version, mock_connect, mock_smoke,
        mock_modules, mock_cli, mock_schema, mock_db
    ):
        """Test successful validation run."""
        mock_db.return_value = BoundaryCheck("db", "pass", "OK")
        mock_schema.return_value = BoundaryCheck("schema", "pass", "OK")
        mock_cli.return_value = BoundaryCheck("cli", "pass", "OK")
        mock_modules.return_value = BoundaryCheck("modules", "pass", "OK")
        mock_smoke.return_value = {"ran": True, "passed": 5}
        mock_resolve.return_value = "/test/db.sqlite"
        mock_connect.return_value = MagicMock()
        mock_version.return_value = 12

        result = validate_ledger_boundary(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123"
        )

        assert result.overall_status == "pass"
        assert result.trace_id == "trace-123"
        assert result.parent_task_id == "parent-123"
        assert result.child_task_id == "child-123"
        assert result.repo_name == "los-memory"

    @patch("validate_ledger_boundary.check_database_connectivity")
    @patch("validate_ledger_boundary.check_schema_integrity")
    @patch("validate_ledger_boundary.check_cli_entrypoint")
    @patch("validate_ledger_boundary.check_required_modules")
    @patch("validate_ledger_boundary.run_smoke_tests")
    @patch("validate_ledger_boundary.connect_db")
    @patch("validate_ledger_boundary.get_schema_version")
    @patch("validate_ledger_boundary.resolve_db_path")
    def test_validation_fail(
        self, mock_resolve, mock_version, mock_connect, mock_smoke,
        mock_modules, mock_cli, mock_schema, mock_db
    ):
        """Test validation with failures."""
        mock_db.return_value = BoundaryCheck("db", "fail", "Error")
        mock_schema.return_value = BoundaryCheck("schema", "pass", "OK")
        mock_cli.return_value = BoundaryCheck("cli", "pass", "OK")
        mock_modules.return_value = BoundaryCheck("modules", "pass", "OK")
        mock_smoke.return_value = {"ran": False}
        mock_resolve.return_value = "/test/db.sqlite"
        mock_connect.return_value = MagicMock()
        mock_version.return_value = 12

        result = validate_ledger_boundary(
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123"
        )

        assert result.overall_status == "fail"


class TestReportSerialization:
    """Tests for report serialization."""

    def test_report_to_dict(self):
        """Test report can be serialized to dict."""
        report = BoundaryReport(
            repo_name="test-repo",
            repo_path="/test",
            timestamp=datetime.now().isoformat(),
            trace_id="trace-123",
            parent_task_id="parent-123",
            child_task_id="child-123",
            overall_status="pass",
            checks=[
                BoundaryCheck("check1", "pass", "OK"),
                BoundaryCheck("check2", "pass", "OK"),
            ],
            schema_version=1,
            database_path="/test.db",
            test_results={"ran": True},
        )

        # Should be serializable to JSON
        json_str = json.dumps(report.__dict__, default=str)
        data = json.loads(json_str)

        assert data["repo_name"] == "test-repo"
        assert data["overall_status"] == "pass"
        assert len(data["checks"]) == 2


class TestHubLiteIntegration:
    """Tests for hub-lite integration specific scenarios."""

    def test_trace_id_propagation(self):
        """Test that trace_id is properly propagated through validation."""
        report = BoundaryReport(
            repo_name="los-memory",
            repo_path="/test",
            timestamp=datetime.now().isoformat(),
            trace_id="trace-parent-epic-20260309063153",
            parent_task_id="epic-20260309063153",
            child_task_id="los-memory-20260309063153",
            overall_status="pass",
            checks=[],
            schema_version=12,
            database_path="/test.db",
            test_results={},
        )

        assert report.trace_id == "trace-parent-epic-20260309063153"
        assert report.parent_task_id == "epic-20260309063153"
        assert report.child_task_id == "los-memory-20260309063153"

    def test_cross_repo_task_metadata(self):
        """Test task metadata format matches hub-lite contract."""
        report = validate_ledger_boundary(
            trace_id="trace-test",
            parent_task_id="parent-test",
            child_task_id="child-test"
        )

        # Verify all required fields for hub-lite integration
        assert report.trace_id == "trace-test"
        assert report.parent_task_id == "parent-test"
        assert report.child_task_id == "child-test"
        assert report.repo_name == "los-memory"
        assert report.overall_status in ["pass", "fail"]
        assert isinstance(report.checks, list)

    def test_hub_lite_record_structure(self):
        """Test that validation produces hub-lite compatible records."""
        report = BoundaryReport(
            repo_name="los-memory",
            repo_path="/test",
            timestamp=datetime.now().isoformat(),
            trace_id="trace-test",
            parent_task_id="parent-test",
            child_task_id="child-test",
            overall_status="pass",
            checks=[
                BoundaryCheck("database_connectivity", "pass", "OK"),
                BoundaryCheck("schema_integrity", "pass", "OK"),
            ],
            schema_version=12,
            database_path="/test.db",
            test_results={"ran": True, "passed": 10},
            metadata={"python_version": "3.9.6"}
        )

        # Serialize and verify structure
        data = json.loads(json.dumps(report.__dict__, default=str))

        # Required fields for hub-lite integration
        assert "trace_id" in data
        assert "parent_task_id" in data
        assert "child_task_id" in data
        assert "overall_status" in data
        assert "checks" in data
        assert "schema_version" in data

        # Each check should have required fields
        for check in data["checks"]:
            assert "name" in check
            assert "status" in check
            assert "message" in check

    def test_overall_status_determination(self):
        """Test overall status is correctly determined from checks."""
        # When all checks pass, overall should be pass
        with patch("validate_ledger_boundary.check_database_connectivity") as mock_db, \
             patch("validate_ledger_boundary.check_schema_integrity") as mock_schema, \
             patch("validate_ledger_boundary.check_cli_entrypoint") as mock_cli, \
             patch("validate_ledger_boundary.check_required_modules") as mock_modules, \
             patch("validate_ledger_boundary.run_smoke_tests") as mock_smoke, \
             patch("validate_ledger_boundary.connect_db"), \
             patch("validate_ledger_boundary.get_schema_version"), \
             patch("validate_ledger_boundary.resolve_db_path"):

            mock_db.return_value = BoundaryCheck("db", "pass", "OK")
            mock_schema.return_value = BoundaryCheck("schema", "pass", "OK")
            mock_cli.return_value = BoundaryCheck("cli", "pass", "OK")
            mock_modules.return_value = BoundaryCheck("modules", "pass", "OK")
            mock_smoke.return_value = {"ran": True}

            result = validate_ledger_boundary(
                trace_id="trace-test",
                parent_task_id="parent-test",
                child_task_id="child-test"
            )
            assert result.overall_status == "pass"

    def test_control_plane_ready_check(self):
        """Test that validation confirms control plane readiness."""
        # Verify the report includes all checks needed for control plane
        report = validate_ledger_boundary(
            trace_id="trace-test",
            parent_task_id="parent-test",
            child_task_id="child-test"
        )

        check_names = {c.name for c in report.checks}

        # Required checks for hub-lite control plane
        assert "database_connectivity" in check_names
        assert "schema_integrity" in check_names
        assert "cli_entrypoint" in check_names
        assert "required_modules" in check_names

    def test_schema_version_compatibility(self):
        """Test schema version is compatible with hub-lite requirements."""
        report = validate_ledger_boundary(
            trace_id="trace-test",
            parent_task_id="parent-test",
            child_task_id="child-test"
        )

        # Schema should be at version 12 for current hub-lite integration
        assert report.schema_version == 12

        # Verify schema check passed
        schema_check = next(c for c in report.checks if c.name == "schema_integrity")
        assert schema_check.status == "pass"
        assert "v12" in schema_check.message
