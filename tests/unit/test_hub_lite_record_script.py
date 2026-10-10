"""Tests for the Hub-Lite record creation script.

Hermeticity matters here. Every test that runs the script points
``HUB_LITE_LOG_DIR`` at a pytest ``tmp_path`` and then asserts on the artifact
*that run* produced. These tests must never read the repository's own ``logs/``
directory: it is gitignored, so it does not exist on a clean checkout (which is
what CI uses), and locally it accumulates artifacts from unrelated historical
runs. Reading it is exactly what made this file pass locally and fail in CI —
the artifact tests globbed for a hard-coded, long-obsolete child-task id and
only ever matched stale files on the operator's machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "create_hub_lite_records.py"

# Contract pinned by `main()` in the script. Update together with it.
TRACE_ID = "trace-parent-epic-20260308235637"
PARENT_TASK_ID = "epic-20260308235637"
CHILD_TASK_ID = "los-memory-20260308235637"

ARTIFACT_GLOB = "hub-lite-child-*-implementation-*.json"


def run_script(log_dir: Path) -> subprocess.CompletedProcess:
    """Run the script with its log artifact redirected into ``log_dir``."""
    env = {**os.environ, "HUB_LITE_LOG_DIR": str(log_dir)}
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
    )


def run_script_and_load_artifact(log_dir: Path) -> tuple[subprocess.CompletedProcess, dict]:
    """Run the script and return its result plus the single artifact it wrote."""
    result = run_script(log_dir)
    artifacts = sorted(log_dir.glob(ARTIFACT_GLOB))
    assert len(artifacts) == 1, (
        f"expected exactly one artifact in the isolated log dir, got {artifacts} "
        f"(script exited {result.returncode})"
    )
    return result, json.loads(artifacts[0].read_text())


@pytest.mark.unit
class TestHubLiteRecordScript:
    """Test the Hub-Lite record creation script."""

    def test_script_exists(self) -> None:
        """Test that the script file exists."""
        assert SCRIPT_PATH.exists(), f"Script not found: {SCRIPT_PATH}"

    def test_script_runs_successfully(self, tmp_path: Path) -> None:
        """Test that the script runs without errors on a tree without `logs/`."""
        result, _ = run_script_and_load_artifact(tmp_path)
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "SUCCESS: All records created successfully." in result.stdout
        assert "WARNING: Acceptance state is BLOCKED" not in result.stdout

    def test_script_creates_records(self, tmp_path: Path) -> None:
        """Test that the script creates the expected records."""
        result, _ = run_script_and_load_artifact(tmp_path)
        assert result.returncode == 0
        assert "Total Records:   3" in result.stdout
        assert "[execution/progress]" in result.stdout
        assert "[verification/checkpoint]" in result.stdout
        assert "[result/artifact]" in result.stdout

    def test_script_outputs_acceptance_state(self, tmp_path: Path) -> None:
        """Test that the script reports acceptance state."""
        result, _ = run_script_and_load_artifact(tmp_path)
        assert result.returncode == 0
        assert "Acceptance:      ACCEPTED" in result.stdout

    def test_script_does_not_require_a_logs_directory_in_the_repo(self, tmp_path: Path) -> None:
        """The script's own structure check must not demand the gitignored `logs/`.

        Regression guard for the CI failure where a clean checkout had no
        `logs/`, so `dir_logs` was FAIL, acceptance went BLOCKED and the script
        exited non-zero.
        """
        result, artifact = run_script_and_load_artifact(tmp_path)
        assert result.returncode == 0
        checkpoint = next(r for r in artifact["records"] if r["stage"] == "verification")
        assert "dir_logs" not in checkpoint["metadata"]["checkpoint_data"]
        assert checkpoint["metadata"]["result"] == "PASS"


@pytest.mark.integration
class TestHubLiteRecordScriptArtifacts:
    """Test artifacts created by the Hub-Lite record script."""

    def test_log_file_created(self, tmp_path: Path) -> None:
        """Test that the script creates exactly one well-formed log file."""
        _, log_data = run_script_and_load_artifact(tmp_path)

        assert log_data["trace_id"] == TRACE_ID
        assert log_data["parent_task_id"] == PARENT_TASK_ID
        assert log_data["child_task_id"] == CHILD_TASK_ID
        assert log_data["repo_name"] == "los-memory"
        assert len(log_data["records"]) == 3

    def test_log_file_contains_all_record_types(self, tmp_path: Path) -> None:
        """Test that the log file contains all required record types."""
        _, log_data = run_script_and_load_artifact(tmp_path)

        stages = {r["stage"] for r in log_data["records"]}
        kinds = {r["kind"] for r in log_data["records"]}

        assert "execution" in stages
        assert "verification" in stages
        assert "result" in stages
        assert "progress" in kinds
        assert "checkpoint" in kinds
        assert "artifact" in kinds
