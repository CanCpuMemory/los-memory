"""Tests for documentation layout guardrails."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


def test_docs_indexes_exist() -> None:
    assert (DOCS / "README.md").exists()
    assert (DOCS / "archive" / "README.md").exists()
    assert (DOCS / "reports" / "README.md").exists()


def test_archived_session_docs_are_not_in_docs_root() -> None:
    archived_names = [
        "CROSS_PROJECT_ADOPTION_REVIEW_2026-02-25.md",
        "WORKSPACE_CLEANUP_REPORT.md",
        "child-session-los-memory-20260309000327.md",
        "child-session-los-memory-20260309000327-final.md",
        "child-session-los-memory-20260309000327-implementation.md",
        "child-session-los-memory-20260309063153-final.md",
        "child-session-los-memory-20260309063153-implementation.md",
    ]
    for name in archived_names:
        assert not (DOCS / name).exists()
    assert (DOCS / "archive" / "2026-02" / "CROSS_PROJECT_ADOPTION_REVIEW_2026-02-25.md").exists()
    assert (DOCS / "archive" / "2026-03" / "WORKSPACE_CLEANUP_REPORT.md").exists()
    assert (DOCS / "archive" / "2026-03" / "child-session-los-memory-20260309000327.md").exists()
    assert (DOCS / "archive" / "2026-03" / "child-session-los-memory-20260309000327-final.md").exists()
    assert (DOCS / "archive" / "2026-03" / "child-session-los-memory-20260309000327-implementation.md").exists()
    assert (DOCS / "archive" / "2026-03" / "child-session-los-memory-20260309063153-final.md").exists()
    assert (DOCS / "archive" / "2026-03" / "child-session-los-memory-20260309063153-implementation.md").exists()
    assert (DOCS / "archive" / "2026-03" / "README.md").exists()


def test_lsclaw_design_docs_live_under_docs_design() -> None:
    moved_names = [
        "EXTERNAL_ADOPTION_GUIDE.md",
        "LSCLAW_INTEGRATION_DESIGN_REVIEW.md",
        "LSCLAW_VPS_CI_INTEGRATION_PLAN.md",
        "SELF_HEALING_SYSTEM_DESIGN.md",
    ]
    for name in moved_names:
        assert not (DOCS / name).exists()
        assert (DOCS / "design" / name).exists()


def test_manual_docs_live_under_docs_manuals() -> None:
    moved_names = [
        "ADAPTER_INTEGRATION_GUIDE.md",
        "AI_USAGE_GUIDE.md",
        "CODEX_CLAUDE_INSTALL.md",
        "GENERAL_MEMORY.md",
        "LSCLAW_MEMORY_UPGRADE_GUIDE.md",
        "LSCLAW_PATCH_CHECKLIST.md",
    ]
    for name in moved_names:
        assert not (DOCS / name).exists()
        assert (DOCS / "manuals" / name).exists()


def test_top_level_plan_and_review_docs_point_back_to_current_truth_sources() -> None:
    doc_names = [
        "IMPLEMENTATION_PLAN.md",
        "TASK_BREAKDOWN.md",
        "ARCHITECTURE_BOUNDARY_REVIEW.md",
        "ARCHITECTURE_CONVERGENCE_REVIEW_v2.1.md",
    ]
    for name in doc_names:
        content = (DOCS / name).read_text(encoding="utf-8")
        assert "不是当前实现状态真相源" in content
        assert "`README.md` 和 `docs/current/CURRENT_STATE.md`" in content


def test_lsclaw_design_docs_point_back_to_current_truth_sources() -> None:
    doc_names = [
        "EXTERNAL_ADOPTION_GUIDE.md",
        "LSCLAW_INTEGRATION_DESIGN_REVIEW.md",
        "LSCLAW_VPS_CI_INTEGRATION_PLAN.md",
        "SELF_HEALING_SYSTEM_DESIGN.md",
    ]
    for name in doc_names:
        content = (DOCS / "design" / name).read_text(encoding="utf-8")
        assert "不是当前实现状态真相源" in content
        assert "`README.md` 和 `docs/current/CURRENT_STATE.md`" in content


def test_top_level_migration_policy_doc_points_back_to_current_truth_sources() -> None:
    content = (DOCS / "MIGRATION_APPROVAL.md").read_text(encoding="utf-8")
    assert "migration policy and timeline" in content
    assert "`README.md` and `docs/current/CURRENT_STATE.md`" in content
