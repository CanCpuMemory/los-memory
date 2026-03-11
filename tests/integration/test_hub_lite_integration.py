"""
Integration tests for Hub-Lite Parent Epic pattern.

These tests verify that los-memory can function as a control plane
for coordinating multi-repo child sessions with proper record keeping.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "memory_tool" / "memory_tool.py"


def _run_cli(db_path: Path, *args: str) -> dict:
    """Run CLI command and return JSON output."""
    cmd = [
        sys.executable,
        str(CLI_PATH),
        "--db",
        str(db_path),
        "--output",
        "json",
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.e2e
@pytest.mark.hub_lite
class TestHubLiteIntegration:
    """Test Hub-Lite Parent Epic integration pattern."""

    def test_parent_session_creation(self, tmp_path: Path) -> None:
        """Test creating a parent session with trace metadata."""
        db_path = tmp_path / "hub_lite.db"

        init_result = _run_cli(db_path, "init")
        assert init_result["ok"] is True

        trace_id = "trace-parent-epic-20260309000327"
        parent_task_id = "epic-20260309000327"

        session = _run_cli(
            db_path,
            "session",
            "start",
            "--project",
            f"hub-lite-{trace_id}",
            "--summary",
            f"Hub-Lite Parent Epic: {trace_id}",
        )
        assert session["ok"] is True
        assert session["session_id"] is not None

        session_list = _run_cli(db_path, "session", "list", "--limit", "10")
        assert session_list["ok"] is True
        assert len(session_list["sessions"]) == 1
        assert trace_id in session_list["sessions"][0].get("summary", "")

    def test_execution_progress_record(self, tmp_path: Path) -> None:
        """Test recording execution/progress observations."""
        db_path = tmp_path / "hub_lite.db"
        
        _run_cli(db_path, "init")
        
        trace_id = "trace-parent-epic-20260309000327"
        parent_task_id = "epic-20260309000327"
        child_task_id = "los-memory-20260309000327"
        
        progress = _run_cli(
            db_path,
            "observation",
            "add",
            "--title",
            "Validated los-memory repo path and structure",
            "--summary",
            "Repository structure validated for cross-repo integration",
            "--tags",
            f"stage:execution,kind:progress,trace:{trace_id},parent:{parent_task_id},child:{child_task_id}",
        )
        assert progress["ok"] is True
        assert progress["id"] is not None
        
        search = _run_cli(
            db_path,
            "memory",
            "search",
            "validated repo path",
            "--require-tags",
            f"trace:{trace_id},stage:execution",
        )
        assert search["ok"] is True
        assert len(search["results"]) == 1
        assert search["results"][0]["title"] == "Validated los-memory repo path and structure"

    def test_verification_checkpoint_record(self, tmp_path: Path) -> None:
        """Test recording verification/checkpoint observations."""
        db_path = tmp_path / "hub_lite.db"
        
        _run_cli(db_path, "init")
        
        trace_id = "trace-parent-epic-20260309000327"
        parent_task_id = "epic-20260309000327"
        child_task_id = "los-memory-20260309000327"
        
        checkpoint = _run_cli(
            db_path,
            "observation",
            "add",
            "--title",
            "Core Python API imports successful",
            "--summary",
            "Verification checkpoint: connect_db and run_search imports working. Result: PASS",
            "--tags",
            f"stage:verification,kind:checkpoint,result:PASS,trace:{trace_id},parent:{parent_task_id},child:{child_task_id}",
        )
        assert checkpoint["ok"] is True
        
        warning = _run_cli(
            db_path,
            "observation",
            "add",
            "--title",
            "Schema version check",
            "--summary",
            "Schema version 7 (expected 12), migration suggested. Result: WARNING",
            "--tags",
            f"stage:verification,kind:checkpoint,result:WARNING,trace:{trace_id},parent:{parent_task_id},child:{child_task_id}",
        )
        assert warning["ok"] is True
        
        checkpoints = _run_cli(
            db_path,
            "memory",
            "list",
            "--require-tags",
            f"trace:{trace_id},stage:verification",
        )
        assert checkpoints["ok"] is True
        assert len(checkpoints["results"]) == 2

    def test_result_artifact_record(self, tmp_path: Path) -> None:
        """Test recording result/artifact observations."""
        db_path = tmp_path / "hub_lite.db"
        
        _run_cli(db_path, "init")
        
        trace_id = "trace-parent-epic-20260309000327"
        parent_task_id = "epic-20260309000327"
        child_task_id = "los-memory-20260309000327"
        
        result = _run_cli(
            db_path,
            "observation",
            "add",
            "--title",
            "los-memory repo validation complete",
            "--summary",
            "Repository validated and ready for hub-lite integration. Acceptance State: ACCEPTED_WITH_NOTES",
            "--tags",
            f"stage:result,kind:artifact,acceptance:ACCEPTED_WITH_NOTES,trace:{trace_id},parent:{parent_task_id},child:{child_task_id}",
        )
        assert result["ok"] is True
        
        accepted = _run_cli(
            db_path,
            "memory",
            "search",
            "ACCEPTED_WITH_NOTES",
            "--require-tags",
            f"trace:{trace_id},stage:result",
        )
        assert accepted["ok"] is True
        assert len(accepted["results"]) == 1

    def test_blocker_escalation_record(self, tmp_path: Path) -> None:
        """Test recording blocker/escalation observations."""
        db_path = tmp_path / "hub_lite.db"
        
        _run_cli(db_path, "init")
        
        trace_id = "trace-parent-epic-20260309000327"
        parent_task_id = "epic-20260309000327"
        child_task_id = "lsclaw-20260309000327"
        
        blocker = _run_cli(
            db_path,
            "observation",
            "add",
            "--title",
            "Repository path not found",
            "--summary",
            "Blocker: PATH_UNRESOLVED - Repository path not yet resolved for lsclaw. Escalation target: parent",
            "--tags",
            f"stage:result,kind:blocker,blockerType:PATH_UNRESOLVED,acceptance:BLOCKED,escalation:parent,trace:{trace_id},parent:{parent_task_id},child:{child_task_id}",
        )
        assert blocker["ok"] is True
        
        blockers = _run_cli(
            db_path,
            "memory",
            "search",
            "PATH_UNRESOLVED",
            "--require-tags",
            f"trace:{trace_id},kind:blocker",
        )
        assert blockers["ok"] is True
        assert len(blockers["results"]) == 1

    def test_aggregation_query(self, tmp_path: Path) -> None:
        """Test aggregating records across child sessions."""
        db_path = tmp_path / "hub_lite.db"
        
        _run_cli(db_path, "init")
        
        trace_id = "trace-parent-epic-20260309000327"
        parent_task_id = "epic-20260309000327"
        
        children = [
            ("los-memory-20260309000327", "execution", "progress"),
            ("los-memory-20260309000327", "verification", "checkpoint"),
            ("los-memory-20260309000327", "result", "artifact"),
            ("lsclaw-20260309000327", "execution", "progress"),
            ("lsclaw-20260309000327", "result", "blocker"),
        ]
        
        for child_task_id, stage, kind in children:
            _run_cli(
                db_path,
                "observation",
                "add",
                "--title",
                f"Record for {child_task_id}",
                "--summary",
                f"Test record: stage={stage}, kind={kind}",
                "--tags",
                f"stage:{stage},kind:{kind},trace:{trace_id},parent:{parent_task_id},child:{child_task_id}",
            )
        
        all_records = _run_cli(
            db_path,
            "memory",
            "list",
            "--require-tags",
            f"trace:{trace_id}",
            "--limit",
            "20",
        )
        assert all_records["ok"] is True
        assert len(all_records["results"]) == 5
        
        execution_records = _run_cli(
            db_path,
            "memory",
            "list",
            "--require-tags",
            f"trace:{trace_id},stage:execution",
        )
        assert execution_records["ok"] is True
        assert len(execution_records["results"]) == 2
        
        progress_records = _run_cli(
            db_path,
            "memory",
            "list",
            "--require-tags",
            f"trace:{trace_id},kind:progress",
        )
        assert progress_records["ok"] is True
        assert len(progress_records["results"]) == 2

    def test_acceptance_criteria_verification(self, tmp_path: Path) -> None:
        """Test that all acceptance criteria can be recorded and verified."""
        db_path = tmp_path / "hub_lite.db"
        
        _run_cli(db_path, "init")
        
        trace_id = "trace-parent-epic-20260309000327"
        parent_task_id = "epic-20260309000327"
        child_task_id = "test-repo-20260309000327"
        
        parent = _run_cli(
            db_path,
            "observation",
            "add",
            "--title",
            "Parent session created",
            "--summary",
            "Parent session created with trace metadata",
            "--tags",
            f"criteria:parent-session,trace:{trace_id},parent:{parent_task_id}",
        )
        assert parent["ok"] is True
        
        child = _run_cli(
            db_path,
            "observation",
            "add",
            "--title",
            "Child session visible",
            "--summary",
            f"Child session {child_task_id} visible in los-memory",
            "--tags",
            f"criteria:child-visible,trace:{trace_id},parent:{parent_task_id},child:{child_task_id}",
        )
        assert child["ok"] is True
        
        exec_record = _run_cli(
            db_path,
            "observation",
            "add",
            "--title",
            "Execution progress",
            "--summary",
            "At least one execution/progress record emitted",
            "--tags",
            f"stage:execution,kind:progress,trace:{trace_id},parent:{parent_task_id},child:{child_task_id}",
        )
        assert exec_record["ok"] is True
        
        result_record = _run_cli(
            db_path,
            "observation",
            "add",
            "--title",
            "Result record",
            "--summary",
            "At least one result record emitted",
            "--tags",
            f"stage:result,kind:artifact,acceptance:ACCEPTED,trace:{trace_id},parent:{parent_task_id},child:{child_task_id}",
        )
        assert result_record["ok"] is True
        
        criteria_check = _run_cli(
            db_path,
            "memory",
            "list",
            "--require-tags",
            f"trace:{trace_id},parent:{parent_task_id}",
        )
        assert criteria_check["ok"] is True
        assert len(criteria_check["results"]) >= 4


@pytest.mark.unit
class TestHubLiteArtifactValidation:
    """Test validation of Hub-Lite artifacts."""

    def test_template_json_valid(self) -> None:
        """Test that the integration template is valid JSON."""
        template_path = ROOT / "docs" / "templates" / "hub-lite-parent-epic-integration.json"
        assert template_path.exists(), f"Template not found: {template_path}"
        
        with open(template_path) as f:
            template = json.load(f)
        
        assert template["traceId"] == "trace-parent-epic-20260309000327"
        assert template["parentTaskId"] == "epic-20260309000327"
        assert "childSessions" in template
        assert "recordSchema" in template

    def test_manual_exists(self) -> None:
        """Test that the first run manual exists."""
        manual_path = ROOT / "docs" / "manuals" / "hub-lite-parent-epic-first-run.md"
        assert manual_path.exists(), f"Manual not found: {manual_path}"
        
        content = manual_path.read_text()
        assert "trace-parent-epic-20260309000327" in content
        assert "Hub-Lite" in content

    def test_dispatch_log_valid(self) -> None:
        """Test that the dispatch log is valid JSON."""
        log_path = ROOT / "logs" / "hub-lite-parent-epic-dispatch-20260309000327.json"
        assert log_path.exists(), f"Dispatch log not found: {log_path}"
        
        with open(log_path) as f:
            log = json.load(f)
        
        assert log["traceId"] == "trace-parent-epic-20260309000327"
        assert log["parentTaskId"] == "epic-20260309000327"
        assert "childSessions" in log
        assert "aggregation" in log

    def test_control_plane_log_exists(self) -> None:
        """Test that the control plane log exists."""
        log_path = ROOT / "control-plane" / "logs" / "hub-lite-lsclaw-round1.md"
        assert log_path.exists(), f"Control plane log not found: {log_path}"
        
        content = log_path.read_text()
        assert "trace-parent-epic-20260309000327" in content
        assert "Hub-Lite" in content
