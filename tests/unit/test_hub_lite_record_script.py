"""Tests for the Hub-Lite record creation script."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "create_hub_lite_records.py"


@pytest.mark.unit
class TestHubLiteRecordScript:
    """Test the Hub-Lite record creation script."""

    def test_script_exists(self) -> None:
        """Test that the script file exists."""
        assert SCRIPT_PATH.exists(), f"Script not found: {SCRIPT_PATH}"

    def test_script_runs_successfully(self) -> None:
        """Test that the script runs without errors."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "SUCCESS: All records created successfully." in result.stdout

    def test_script_creates_records(self) -> None:
        """Test that the script creates the expected records."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 0
        assert "Total Records:   3" in result.stdout
        assert "[execution/progress]" in result.stdout
        assert "[verification/checkpoint]" in result.stdout
        assert "[result/artifact]" in result.stdout

    def test_script_outputs_acceptance_state(self) -> None:
        """Test that the script reports acceptance state."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 0
        assert "Acceptance:      ACCEPTED" in result.stdout


@pytest.mark.integration
class TestHubLiteRecordScriptArtifacts:
    """Test artifacts created by the Hub-Lite record script."""

    def test_log_file_created(self) -> None:
        """Test that the script creates a log file."""
        import glob

        log_pattern = str(ROOT / "logs" / "hub-lite-child-los-memory-20260309063153-implementation-*.json")
        log_files = glob.glob(log_pattern)

        assert len(log_files) > 0, "No implementation log files found"

        latest_log = max(log_files, key=lambda p: Path(p).stat().st_mtime)
        with open(latest_log) as f:
            log_data = json.load(f)

        assert log_data["trace_id"] == "trace-parent-epic-20260309063153"
        assert log_data["parent_task_id"] == "epic-20260309063153"
        assert log_data["child_task_id"] == "los-memory-20260309063153"
        assert log_data["repo_name"] == "los-memory"
        assert len(log_data["records"]) == 3

    def test_log_file_contains_all_record_types(self) -> None:
        """Test that the log file contains all required record types."""
        import glob
        
        log_pattern = str(ROOT / "logs" / "hub-lite-child-los-memory-20260309063153-implementation-*.json")
        log_files = glob.glob(log_pattern)
        assert len(log_files) > 0
        
        latest_log = max(log_files, key=lambda p: Path(p).stat().st_mtime)
        with open(latest_log) as f:
            log_data = json.load(f)
        
        stages = {r["stage"] for r in log_data["records"]}
        kinds = {r["kind"] for r in log_data["records"]}
        
        assert "execution" in stages
        assert "verification" in stages
        assert "result" in stages
        assert "progress" in kinds
        assert "checkpoint" in kinds
        assert "artifact" in kinds
