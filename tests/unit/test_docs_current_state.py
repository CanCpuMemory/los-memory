"""Tests for current-state documentation guardrails."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_current_state_marks_core_as_compatibility_layer() -> None:
    content = (ROOT / "docs" / "current" / "CURRENT_STATE.md").read_text(encoding="utf-8")
    assert "compatibility layer" in content
    assert "top-level `memory_tool.*` modules" in content


def test_readme_points_new_development_to_top_level_modules() -> None:
    content = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Historical `memory_tool.core.*` imports are compatibility shims" in content
    assert "top-level `memory_tool.*` modules" in content
