from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "memory_tool" / "memory_tool.py"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(CLI_PATH), "--output", "json", *args]
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.mark.contract
def test_doctor_returns_nonzero_when_db_unavailable(tmp_path: Path) -> None:
    db_path = tmp_path / "missing" / "memory.db"
    result = _run_cli("--db", str(db_path), "admin", "doctor")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["data"]["status"] in {"degraded", "unhealthy"}
    assert "capabilities" in payload["data"]


@pytest.mark.contract
def test_search_and_list_contract_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    init_result = _run_cli("--db", str(db_path), "init")
    assert init_result.returncode == 0

    add_result = _run_cli(
        "--db",
        str(db_path),
        "observation",
        "add",
        "--project",
        "lsclaw",
        "--kind",
        "decision",
        "--title",
        "Contract baseline",
        "--summary",
        "contract test",
        "--tags",
        "tenant:t1,user:u1",
    )
    assert add_result.returncode == 0

    search_result = _run_cli(
        "--db",
        str(db_path),
        "memory",
        "search",
        "contract",
        "--require-tags",
        "tenant:t1,user:u1",
    )
    assert search_result.returncode == 0
    search_payload = json.loads(search_result.stdout)
    assert search_payload["ok"] is True
    assert isinstance(search_payload["results"], list)

    list_result = _run_cli(
        "--db",
        str(db_path),
        "memory",
        "list",
        "--require-tags",
        "tenant:t1,user:u1",
    )
    assert list_result.returncode == 0
    list_payload = json.loads(list_result.stdout)
    assert list_payload["ok"] is True
    assert isinstance(list_payload["results"], list)


@pytest.mark.contract
def test_doctor_returns_degraded_when_fts_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    init_result = _run_cli("--db", str(db_path), "init")
    assert init_result.returncode == 0

    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TABLE IF EXISTS observations_fts")
    conn.commit()
    conn.close()

    doctor_result = _run_cli("--db", str(db_path), "admin", "doctor")
    assert doctor_result.returncode == 0
    doctor_payload = json.loads(doctor_result.stdout)
    assert doctor_payload["ok"] is True
    assert doctor_payload["data"]["status"] == "degraded"
    assert doctor_payload["data"]["capabilities"]["can_search"] is False


@pytest.mark.contract
def test_transition_and_review_apply_contract_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    review_path = tmp_path / "review.json"
    init_result = _run_cli("--db", str(db_path), "init")
    assert init_result.returncode == 0

    add_result = _run_cli(
        "--db",
        str(db_path),
        "observation",
        "add",
        "--project",
        "lsclaw",
        "--kind",
        "decision",
        "--title",
        "Review target",
        "--summary",
        "pending feedback",
        "--tags",
        "tenant:t1,user:u1",
    )
    assert add_result.returncode == 0
    observation_id = json.loads(add_result.stdout)["id"]

    transition_result = _run_cli(
        "--db",
        str(db_path),
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
    assert transition_result.returncode == 0
    transition_payload = json.loads(transition_result.stdout)
    assert transition_payload["ok"] is True
    assert set(["id", "phase", "action", "status"]).issubset(transition_payload.keys())

    review_path.write_text(
        json.dumps({"items": [{"observation_id": observation_id, "feedback": "补充契约验证"}]}),
        encoding="utf-8",
    )
    review_result = _run_cli(
        "--db",
        str(db_path),
        "review",
        "apply",
        "--file",
        str(review_path),
        "--dry-run",
    )
    assert review_result.returncode == 0
    review_payload = json.loads(review_result.stdout)
    assert review_payload["ok"] is True
    assert set(["total", "applied", "failed", "errors", "dry_run"]).issubset(review_payload.keys())
