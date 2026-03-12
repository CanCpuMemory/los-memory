from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "memory_tool" / "memory_tool.py"


def _run_cli(db_path: Path, *args: str) -> dict:
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
def test_lsclaw_integration_smoke(tmp_path: Path) -> None:
    db_path = tmp_path / "smoke.db"
    review_file = tmp_path / "review-feedback.json"

    init_result = _run_cli(db_path, "init")
    assert init_result["ok"] is True

    scoped_obs = _run_cli(
        db_path,
        "observation",
        "add",
        "--project",
        "lsclaw",
        "--kind",
        "decision",
        "--title",
        "Scoped decision",
        "--summary",
        "review pipeline hardened",
        "--tags",
        "tenant:t1,user:u1,session:s1,taskType:review",
    )
    other_obs = _run_cli(
        db_path,
        "observation",
        "add",
        "--project",
        "lsclaw",
        "--kind",
        "decision",
        "--title",
        "Other tenant decision",
        "--summary",
        "review pipeline hardened",
        "--tags",
        "tenant:t2,user:u2,session:s9,taskType:review",
    )

    assert scoped_obs["ok"] is True
    assert other_obs["ok"] is True
    scoped_id = scoped_obs["id"]

    scoped_search = _run_cli(
        db_path,
        "memory",
        "search",
        "pipeline",
        "--require-tags",
        "tenant:t1,user:u1",
        "--limit",
        "10",
    )
    assert scoped_search["ok"] is True
    results = scoped_search["results"]
    assert len(results) == 1
    assert results[0]["title"] == "Scoped decision"

    stats = _run_cli(db_path, "admin", "manage", "stats")
    assert stats["ok"] is True
    assert stats["action"] == "stats"
    assert stats["total"] == 2
    assert isinstance(stats["projects"], list)
    assert isinstance(stats["kinds"], list)

    delete_preview = _run_cli(
        db_path,
        "observation",
        "delete",
        str(scoped_id),
        "--dry-run",
    )
    assert delete_preview["ok"] is True
    assert delete_preview["matched"] == 1
    assert delete_preview["deleted"] == 0
    assert delete_preview["dry_run"] is True

    transition = _run_cli(
        db_path,
        "tool",
        "transition",
        "--phase",
        "team_stage",
        "--action",
        "review:agent",
        "--input",
        '{"task":"review"}',
        "--output",
        '{"summary":"ok"}',
        "--status",
        "success",
        "--project",
        "lsclaw",
    )
    assert transition["ok"] is True
    assert transition["status"] == "success"

    review_file.write_text(
        json.dumps({"items": [{"observation_id": scoped_id, "feedback": "补充验收标准"}]}),
        encoding="utf-8",
    )
    review = _run_cli(
        db_path,
        "review",
        "apply",
        "--file",
        str(review_file),
        "--dry-run",
    )
    assert review["ok"] is True
    assert review["total"] == 1
    assert review["applied"] == 1
    assert review["failed"] == 0
    assert review["dry_run"] is True
