"""Regression tests for documentation command linting."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "check_docs_cli_commands.py"

spec = importlib.util.spec_from_file_location("check_docs_cli_commands", SCRIPT_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_lint_detects_multiline_session_start_description_violation() -> None:
    content = """```bash
los-memory session start \\
  --description "old"
```"""
    violations = module._find_violations(ROOT / "docs" / "manuals" / "example.md", content)
    assert violations
    assert any("--summary" in hint for _, _, _, hint in violations)


def test_lint_allows_multiline_session_start_compatibility_note() -> None:
    content = """迁移说明：旧命令

```bash
los-memory session start \\
  --description "old"
```"""
    violations = module._find_violations(ROOT / "docs" / "manuals" / "example.md", content)
    assert violations == []
