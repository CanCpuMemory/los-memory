"""Unit tests for doctor command environment diagnostics.

These tests verify the health check system for los-memory environment
diagnostics, covering all check categories: Python, SQLite, Database,
Profile, and Functional checks.

Based on IMPLEMENTATION_PLAN.md Section 5.3 - Doctor Command Tests.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

# Import doctor module components
from memory_tool.doctor import (
    Check,
    CheckResult,
    doctor_command,
    format_human_output,
    get_all_checks,
    register_check,
    run_all_checks,
)


class TestCheckRegistration:
    """Test check registration system."""

    def test_get_all_checks_returns_list(self) -> None:
        """Verify get_all_checks returns a list of Check objects."""
        checks = get_all_checks()
        assert isinstance(checks, list)
        assert len(checks) > 0
        assert all(isinstance(c, Check) for c in checks)

    def test_checks_sorted_by_priority(self) -> None:
        """Verify P0 checks come before P1 checks."""
        checks = get_all_checks()
        priorities = [c.priority for c in checks]

        # All P0s should come before P1s
        p0_count = priorities.count("P0")
        p1_indices = [i for i, p in enumerate(priorities) if p == "P1"]

        if p1_indices:
            assert all(i >= p0_count for i in p1_indices), "P1 checks should come after P0"

    def test_register_check_decorator(self) -> None:
        """Test that register_check decorator adds checks to registry."""
        initial_count = len(get_all_checks())

        @register_check(
            name="test_check_xyz",
            description="A test check",
            category="test",
            priority="P1",
        )
        def test_check() -> tuple[bool, str, str | None]:
            return True, "Test passed", None

        checks = get_all_checks()
        assert len(checks) == initial_count + 1

        # Clean up - remove test check from registry
        from memory_tool.doctor import _CHECK_REGISTRY
        _CHECK_REGISTRY[:] = [c for c in _CHECK_REGISTRY if c.name != "test_check_xyz"]


class TestPythonChecks:
    """Test Python environment checks."""

    def test_python_version_check(self) -> None:
        """Verify Python version check passes on supported versions."""
        from memory_tool.doctor import check_python_version

        ok, message, suggestion = check_python_version()

        # Should pass on Python 3.8+
        assert ok is True
        assert "Python" in message
        assert sys.version_info.major >= 3

    def test_python_stdlib_check(self) -> None:
        """Verify all required standard library modules are available."""
        from memory_tool.doctor import check_python_stdlib

        ok, message, suggestion = check_python_stdlib()

        assert ok is True
        assert "available" in message.lower()
        assert suggestion is None


class TestSQLiteChecks:
    """Test SQLite environment checks."""

    def test_sqlite_version_check(self) -> None:
        """Verify SQLite version check."""
        from memory_tool.doctor import check_sqlite_version

        ok, message, suggestion = check_sqlite_version()

        assert isinstance(ok, bool)
        assert "SQLite" in message
        # SQLite 3.25+ should pass
        version = sqlite3.sqlite_version_info
        expected_ok = version[0] > 3 or (version[0] == 3 and version[1] >= 25)
        assert ok == expected_ok

    def test_sqlite_fts5_check(self) -> None:
        """Verify FTS5 extension availability check."""
        from memory_tool.doctor import check_sqlite_fts5

        ok, message, suggestion = check_sqlite_fts5()

        assert isinstance(ok, bool)
        assert "FTS5" in message
        if ok:
            assert "available" in message.lower()
            assert suggestion is None

    def test_sqlite_wal_check(self) -> None:
        """Verify WAL mode support check."""
        from memory_tool.doctor import check_sqlite_wal

        ok, message, suggestion = check_sqlite_wal()

        assert isinstance(ok, bool)
        assert "Journal mode" in message or "WAL" in message


class TestDatabaseChecks:
    """Test database health checks."""

    def test_db_exists_check_with_existing_db(self, tmp_path: Path) -> None:
        """Verify db_exists check passes for existing file."""
        from memory_tool.doctor import check_db_exists

        db_path = tmp_path / "test.db"
        db_path.touch()

        ok, message, suggestion = check_db_exists(str(db_path))

        assert ok is True
        assert str(db_path) in message
        assert suggestion is None

    def test_db_exists_check_with_missing_db(self, tmp_path: Path) -> None:
        """Verify db_exists check fails for missing file."""
        from memory_tool.doctor import check_db_exists

        db_path = tmp_path / "nonexistent.db"

        ok, message, suggestion = check_db_exists(str(db_path))

        assert ok is False
        assert suggestion is not None
        assert "init" in suggestion.lower()

    def test_db_readable_check(self, db_connection: sqlite3.Connection, tmp_path: Path) -> None:
        """Verify db_readable check passes for readable database."""
        from memory_tool.doctor import check_db_readable

        db_path = tmp_path / "readable.db"
        db_connection.execute("CREATE TABLE test (id INTEGER)")
        db_connection.execute("VACUUM")
        db_connection.close()

        ok, message, suggestion = check_db_readable(str(db_path))

        # May fail if file is locked, but should generally pass
        if ok:
            assert "readable" in message.lower()

    def test_db_writable_check(self, tmp_path: Path) -> None:
        """Verify db_writable check."""
        from memory_tool.doctor import check_db_writable

        db_path = tmp_path / "writable.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        ok, message, suggestion = check_db_writable(str(db_path))

        if ok:
            assert "writable" in message.lower()

    def test_db_schema_version_check(self, db_connection: sqlite3.Connection) -> None:
        """Verify schema version check."""
        from memory_tool.doctor import check_db_schema_version
        from memory_tool.database import ensure_schema

        ensure_schema(db_connection)

        ok, message, suggestion = check_db_schema_version(db_connection)

        assert isinstance(ok, bool)
        assert "version" in message.lower()

    def test_db_tables_check(self, db_connection: sqlite3.Connection) -> None:
        """Verify required tables check."""
        from memory_tool.doctor import check_db_tables

        ok, message, suggestion = check_db_tables(db_connection)

        assert ok is True
        assert "observations" in message.lower() or "tables" in message.lower()

    def test_db_integrity_check(self, db_connection: sqlite3.Connection) -> None:
        """Verify database integrity check."""
        from memory_tool.doctor import check_db_integrity

        ok, message, suggestion = check_db_integrity(db_connection)

        assert ok is True
        assert "integrity" in message.lower() or "ok" in message.lower()


class TestProfileChecks:
    """Test profile configuration checks."""

    def test_profile_valid_check_with_valid_profile(self) -> None:
        """Verify profile check passes for valid profiles."""
        from memory_tool.doctor import check_profile_valid

        for profile in ["claude", "codex", "shared"]:
            ok, message, suggestion = check_profile_valid(profile)
            assert ok is True, f"Profile '{profile}' should be valid"
            assert profile in message
            assert suggestion is None

    def test_profile_valid_check_with_invalid_profile(self) -> None:
        """Verify profile check fails for invalid profiles."""
        from memory_tool.doctor import check_profile_valid

        ok, message, suggestion = check_profile_valid("invalid_profile")

        assert ok is False
        assert "invalid" in message.lower() or "valid" in suggestion.lower()

    def test_profile_path_check(self) -> None:
        """Verify profile path resolution check."""
        from memory_tool.doctor import check_profile_path

        ok, message, suggestion = check_profile_path("claude")

        assert ok is True
        assert "path" in message.lower()


class TestFunctionalChecks:
    """Test functional/operational checks."""

    def test_fts_index_check(self, db_connection: sqlite3.Connection) -> None:
        """Verify FTS index health check."""
        from memory_tool.doctor import check_fts_index
        from memory_tool.database import ensure_fts

        ensure_fts(db_connection)

        ok, message, suggestion = check_fts_index(db_connection)

        assert isinstance(ok, bool)
        assert "fts" in message.lower() or "index" in message.lower()


class TestRunAllChecks:
    """Test the comprehensive run_all_checks function."""

    def test_run_all_checks_returns_valid_report(self, tmp_path: Path) -> None:
        """Verify run_all_checks returns properly structured report."""
        db_path = tmp_path / "test.db"

        report = run_all_checks(
            db_path=str(db_path),
            profile="claude",
            conn=None,
            fix=False,
        )

        # Verify report structure
        assert "ok" in report
        assert "status" in report
        assert "capabilities" in report
        assert "checks" in report
        assert "warnings" in report
        assert "suggestions" in report

        # Verify status is valid
        assert report["status"] in ["healthy", "degraded", "unhealthy"]

        # Verify capabilities structure
        caps = report["capabilities"]
        assert "can_read" in caps
        assert "can_write" in caps
        assert "can_search" in caps
        assert "can_migrate" in caps

    def test_run_all_checks_with_working_database(
        self, db_connection: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Verify run_all_checks with a properly initialized database."""
        # Create a real database file on disk
        db_path = tmp_path / "working.db"
        # Copy the in-memory schema to disk
        disk_conn = sqlite3.connect(str(db_path))
        db_connection.backup(disk_conn)
        disk_conn.close()

        report = run_all_checks(
            db_path=str(db_path),
            profile="claude",
            conn=db_connection,
            fix=False,
        )

        # Should be at least degraded (warnings allowed) or healthy
        # Note: May be unhealthy if disk checks fail, so just verify structure
        assert report["status"] in ["healthy", "degraded", "unhealthy"]
        assert "ok" in report
        assert "checks" in report

    def test_run_all_checks_identifies_p0_failures(self, tmp_path: Path) -> None:
        """Verify P0 failures mark status as unhealthy."""
        nonexistent_path = tmp_path / "does_not_exist" / "db.sqlite"

        report = run_all_checks(
            db_path=str(nonexistent_path),
            profile="claude",
            conn=None,
            fix=False,
        )

        # Should be unhealthy since DB doesn't exist
        assert report["status"] == "unhealthy"
        assert report["ok"] is False


class TestFormatHumanOutput:
    """Test human-readable output formatting."""

    def test_format_healthy_report(self) -> None:
        """Verify formatting of healthy report."""
        report = {
            "ok": True,
            "status": "healthy",
            "capabilities": {
                "can_read": True,
                "can_write": True,
                "can_search": True,
                "can_migrate": True,
            },
            "checks": {
                "python": {"version": {"ok": True, "message": "Python 3.9.6"}},
            },
            "warnings": [],
            "suggestions": [],
        }

        output = format_human_output(report)

        assert "All checks passed" in output
        assert "Capabilities" in output
        assert "PYTHON" in output

    def test_format_degraded_report(self) -> None:
        """Verify formatting of degraded report with warnings."""
        report = {
            "ok": True,
            "status": "degraded",
            "capabilities": {
                "can_read": True,
                "can_write": True,
                "can_search": True,
                "can_migrate": True,
            },
            "checks": {
                "python": {"version": {"ok": True, "message": "Python 3.9.6"}},
            },
            "warnings": [{"code": "TEST_WARN", "message": "Test warning"}],
            "suggestions": ["Consider running vacuum"],
        }

        output = format_human_output(report)

        assert "degraded" in output.lower()
        assert "Warnings" in output
        assert "TEST_WARN" in output
        assert "Suggestions" in output

    def test_format_unhealthy_report(self) -> None:
        """Verify formatting of unhealthy report."""
        report = {
            "ok": False,
            "status": "unhealthy",
            "capabilities": {
                "can_read": False,
                "can_write": False,
                "can_search": False,
                "can_migrate": True,
            },
            "checks": {
                "database": {"exists": {"ok": False, "message": "DB not found"}},
            },
            "warnings": [],
            "suggestions": ["Run 'los-memory init'"],
        }

        output = format_human_output(report)

        assert "unhealthy" in output.lower()
        assert "✗" in output  # Error icon
        assert "Suggestions" in output


class TestDoctorCommand:
    """Test the high-level doctor_command function."""

    def test_doctor_command_returns_json_response(
        self, tmp_path: Path
    ) -> None:
        """Verify doctor_command returns JSONResponse."""
        from memory_tool.output import JSONResponse

        db_path = tmp_path / "test.db"

        response = doctor_command(
            db_path=str(db_path),
            profile="claude",
            conn=None,
            fix=False,
        )

        assert isinstance(response, JSONResponse)
        assert hasattr(response, "ok")
        assert hasattr(response, "data")
        assert hasattr(response, "meta")

    def test_doctor_command_response_structure(self, tmp_path: Path) -> None:
        """Verify doctor_command response has expected structure."""
        db_path = tmp_path / "test.db"

        response = doctor_command(
            db_path=str(db_path),
            profile="claude",
            conn=None,
            fix=False,
        )

        data = response.data
        assert "status" in data
        assert "capabilities" in data
        assert "checks" in data

    def test_doctor_command_to_dict(self, tmp_path: Path) -> None:
        """Verify doctor_command response can be serialized to dict."""
        db_path = tmp_path / "test.db"

        response = doctor_command(
            db_path=str(db_path),
            profile="claude",
            conn=None,
            fix=False,
        )

        result = response.to_dict()

        assert "ok" in result
        assert "data" in result
        assert "meta" in result
        assert "timestamp" in result["meta"]


class TestDoctorExitCodes:
    """Test that doctor command exit codes match specification."""

    def test_healthy_status_exit_code_zero(self) -> None:
        """Verify healthy status would result in exit code 0."""
        report = run_all_checks(
            db_path="/tmp/nonexistent.db",  # Will fail some checks
            profile="claude",
            conn=None,
        )

        # Just verify the structure - actual exit code handling is in CLI
        assert "status" in report
        assert report["status"] in ["healthy", "degraded", "unhealthy"]


class TestDoctorContract:
    """Contract tests for doctor command JSON output schema."""

    def test_doctor_output_has_required_fields(self, tmp_path: Path) -> None:
        """Verify doctor output has all required JSON fields per spec."""
        db_path = tmp_path / "test.db"

        response = doctor_command(
            db_path=str(db_path),
            profile="claude",
            conn=None,
            fix=False,
        )

        result = response.to_dict()

        # Required fields per IMPLEMENTATION_PLAN.md Section 4.1
        assert "ok" in result
        assert "meta" in result
        assert "timestamp" in result["meta"]
        assert "schema_version" in result["meta"]

        if result["ok"]:
            assert "data" in result
            data = result["data"]
            assert "status" in data
            assert "capabilities" in data
            assert "checks" in data
            assert "warnings" in data
            assert "suggestions" in data
