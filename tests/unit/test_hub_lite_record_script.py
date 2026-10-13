"""Tests for the Hub-Lite record creation script.

Hermeticity matters here, and it has two halves. Every test that runs the script
points ``HUB_LITE_LOG_DIR`` at a pytest ``tmp_path`` and asserts on the artifact
*that run* produced: these tests must never read the repository's own ``logs/``
directory, because it is gitignored (so it does not exist on a clean checkout,
which is what CI uses) and locally it accumulates artifacts from unrelated
historical runs — that is what made this file pass locally and fail in CI.

The same tests also point ``MEMORY_DB_PATH`` at the ``tmp_path``. The script
writes *records*, not just an artifact, and its default target is the operator's
real ``shared`` profile database: measured 2026-10-14, a full suite run appended
18 rows (6 invocations x 3 records) to production memory on every run. The
hermeticity fix above had covered only the log artifact.
"""
from __future__ import annotations

import json
import os
import sqlite3
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
DB_FILENAME = "hub-lite.db"

ARTIFACT_GLOB = "hub-lite-child-*-implementation-*.json"


def run_script(work_dir: Path, extra_env: dict | None = None,
               args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run the script with both its write targets redirected into ``work_dir``.

    ``extra_env`` exists so a test can override ``HOME`` too: with ``HOME``
    pointed at a scratch directory, the ``shared`` profile default resolves
    *inside that scratch directory*, so a test can prove the script did not fall
    back to the profile default without ever being able to touch the real one.
    """
    env = {
        **os.environ,
        "HUB_LITE_LOG_DIR": str(work_dir),
        "MEMORY_DB_PATH": str(work_dir / DB_FILENAME),
        **(extra_env or {}),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *(args or [])],
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


def record_titles(db_path: Path) -> list[str]:
    """Titles of the observations the run wrote, straight from the database."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return [row[0] for row in connection.execute(
            "SELECT title FROM observations ORDER BY id")]
    finally:
        connection.close()


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


@pytest.mark.unit
class TestHubLiteRecordScriptWriteTarget:
    """Where the script's *records* land, not just its artifact.

    These pin the fix for the 2026-10-14 finding: the script's default target is
    the real ``shared`` profile database, so running the suite appended 18 rows
    to production memory each time. The tests below are the negative control
    that was missing — the old ones asserted the artifact and never asked which
    database the observations went into.
    """

    def test_records_go_to_the_configured_database(self, tmp_path: Path) -> None:
        db_path = tmp_path / DB_FILENAME

        result = run_script(tmp_path)

        assert result.returncode == 0, result.stderr
        assert db_path.exists(), "the run did not create the database it was pointed at"
        titles = record_titles(db_path)
        assert len(titles) == 3, titles
        assert any("Implementation phase started" in t for t in titles)
        assert any("Implementation verification" in t for t in titles)
        assert any("Implementation complete" in t for t in titles)

    def test_records_do_not_fall_back_to_the_profile_default(self, tmp_path: Path) -> None:
        """With ``MEMORY_DB_PATH`` set, the profile default must stay untouched.

        ``HOME`` is redirected to a scratch directory, so what the profile
        default resolves to is a path *inside this test's tmp_path*: if the
        script ever stops honouring ``MEMORY_DB_PATH``, the records appear there
        and this assertion fires — without the real ledger being at risk.
        """
        scratch_home = tmp_path / "home"
        scratch_home.mkdir()

        result = run_script(tmp_path, extra_env={"HOME": str(scratch_home)})

        assert result.returncode == 0, result.stderr
        profile_default = scratch_home / ".local" / "share" / "llm-memory" / "memory.db"
        assert not profile_default.exists(), (
            "the script ignored MEMORY_DB_PATH and wrote to the shared profile default")
        assert len(record_titles(tmp_path / DB_FILENAME)) == 3

    def test_db_flag_outranks_the_env_override(self, tmp_path: Path) -> None:
        explicit = tmp_path / "explicit.db"
        env_db = tmp_path / "from-env.db"

        result = run_script(tmp_path, args=["--db", str(explicit)],
                            extra_env={"MEMORY_DB_PATH": str(env_db)})

        assert result.returncode == 0, result.stderr
        assert len(record_titles(explicit)) == 3
        assert not env_db.exists(), "the explicit --db must win over MEMORY_DB_PATH"
