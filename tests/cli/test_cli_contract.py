from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "memory_tool", "--output", "json", *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _run_cli_with_input(
    *args: str,
    stdin: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "memory_tool", "--output", "json", *args]
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env)


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
def test_doctor_does_not_create_database_side_effect(tmp_path: Path) -> None:
    db_path = tmp_path / "doctor-probe.db"
    assert not db_path.exists()

    result = _run_cli("--db", str(db_path), "admin", "doctor")
    assert result.returncode == 1
    assert not db_path.exists()


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
def test_legacy_flat_commands_preserve_tag_scoped_contract(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    init_result = _run_cli("--db", str(db_path), "init")
    assert init_result.returncode == 0

    add_result = _run_cli(
        "--db",
        str(db_path),
        "add",
        "--project",
        "lsclaw",
        "--kind",
        "decision",
        "--title",
        "Scoped contract baseline",
        "--summary",
        "legacy adapter path",
        "--tags",
        "tenant:t1,user:u1",
    )
    assert add_result.returncode == 0
    add_payload = json.loads(add_result.stdout)
    assert add_payload["ok"] is True

    other_add_result = _run_cli(
        "--db",
        str(db_path),
        "add",
        "--project",
        "lsclaw",
        "--kind",
        "decision",
        "--title",
        "Other tenant baseline",
        "--summary",
        "legacy adapter path",
        "--tags",
        "tenant:t2,user:u2",
    )
    assert other_add_result.returncode == 0

    search_result = _run_cli(
        "--db",
        str(db_path),
        "search",
        "legacy",
        "--require-tags",
        "tenant:t1,user:u1",
    )
    assert search_result.returncode == 0
    search_payload = json.loads(search_result.stdout)
    assert search_payload["ok"] is True
    assert len(search_payload["results"]) == 1
    assert search_payload["results"][0]["title"] == "Scoped contract baseline"

    list_result = _run_cli(
        "--db",
        str(db_path),
        "list",
        "--require-tags",
        "tenant:t1,user:u1",
    )
    assert list_result.returncode == 0
    list_payload = json.loads(list_result.stdout)
    assert list_payload["ok"] is True
    assert len(list_payload["results"]) == 1
    assert list_payload["results"][0]["title"] == "Scoped contract baseline"


@pytest.mark.contract
def test_observation_metadata_round_trip_and_edit_contract(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    assert _run_cli("--db", str(db_path), "init").returncode == 0

    metadata = {
        "tenant_id": "tenant-a",
        "project_id": "proj-42",
        "actor_id": "agent-7",
        "user_id": "user-9",
        "role": "reviewer",
        "trace_id": "trace-123",
        "request_id": "req-456",
        "session_id": "external-session-1",
        "idempotency_key": "idem-1",
        "job_id": "job-88",
        "approval_id": "approval-3",
        "event_type": "memory.write",
        "source": "vps-agent-web",
    }
    add_result = _run_cli(
        "--db",
        str(db_path),
        "--profile",
        "shared",
        "observation",
        "add",
        "--project",
        "lsclaw",
        "--kind",
        "decision",
        "--title",
        "Metadata contract baseline",
        "--summary",
        "round trip metadata",
        "--metadata",
        json.dumps(metadata, ensure_ascii=False),
    )
    assert add_result.returncode == 0
    add_payload = json.loads(add_result.stdout)
    assert add_payload["ok"] is True
    assert add_payload["profile"] == "shared"
    assert add_payload["metadata"] == metadata
    obs_id = add_payload["id"]

    get_result = _run_cli("--db", str(db_path), "memory", "get", str(obs_id))
    assert get_result.returncode == 0
    get_payload = json.loads(get_result.stdout)
    assert get_payload["results"][0]["metadata"] == metadata

    edited_metadata = {
        **metadata,
        "event_type": "memory.corrected",
        "trace_id": "trace-999",
    }
    edit_result = _run_cli(
        "--db",
        str(db_path),
        "observation",
        "edit",
        "--id",
        str(obs_id),
        "--metadata",
        json.dumps(edited_metadata, ensure_ascii=False),
    )
    assert edit_result.returncode == 0
    edit_payload = json.loads(edit_result.stdout)
    assert edit_payload["updated"]["metadata"] == edited_metadata

    get_after_edit = _run_cli("--db", str(db_path), "memory", "get", str(obs_id))
    assert get_after_edit.returncode == 0
    get_after_edit_payload = json.loads(get_after_edit.stdout)
    assert get_after_edit_payload["results"][0]["metadata"] == edited_metadata


@pytest.mark.contract
def test_observation_add_accepts_metadata_from_stdin(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    assert _run_cli("--db", str(db_path), "init").returncode == 0

    stdin_metadata = {
        "tenant_id": "tenant-stdin",
        "trace_id": "trace-stdin",
        "source": "vps-agent-web",
    }
    add_result = _run_cli_with_input(
        "--db",
        str(db_path),
        "observation",
        "add",
        "--title",
        "Metadata from stdin",
        "--summary",
        "stdin path",
        "--metadata",
        "@-",
        stdin=json.dumps(stdin_metadata, ensure_ascii=False),
    )
    assert add_result.returncode == 0
    obs_id = json.loads(add_result.stdout)["id"]

    get_result = _run_cli("--db", str(db_path), "memory", "get", str(obs_id))
    assert get_result.returncode == 0
    get_payload = json.loads(get_result.stdout)
    assert get_payload["results"][0]["metadata"] == stdin_metadata


@pytest.mark.contract
def test_observation_feedback_metadata_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    assert _run_cli("--db", str(db_path), "init").returncode == 0

    add_result = _run_cli(
        "--db",
        str(db_path),
        "observation",
        "add",
        "--title",
        "Feedback target",
        "--summary",
        "Original value",
    )
    obs_id = json.loads(add_result.stdout)["id"]

    feedback_metadata = {
        "trace_id": "trace-feedback-1",
        "request_id": "req-feedback-1",
        "approval_id": "approval-1",
        "source": "vps-agent-web",
    }
    feedback_result = _run_cli(
        "--db",
        str(db_path),
        "observation",
        "feedback",
        "--id",
        str(obs_id),
        "--metadata",
        json.dumps(feedback_metadata, ensure_ascii=False),
        "修正: Updated value",
    )
    assert feedback_result.returncode == 0
    feedback_payload = json.loads(feedback_result.stdout)
    assert feedback_payload["metadata"] == feedback_metadata

    history_result = _run_cli(
        "--db",
        str(db_path),
        "observation",
        "feedback",
        "--id",
        str(obs_id),
        "--history",
        "history",
    )
    assert history_result.returncode == 0
    history_payload = json.loads(history_result.stdout)
    assert history_payload["history"][0]["metadata"] == feedback_metadata


@pytest.mark.contract
def test_review_apply_propagates_feedback_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    review_path = tmp_path / "review.json"
    assert _run_cli("--db", str(db_path), "init").returncode == 0

    add_result = _run_cli(
        "--db",
        str(db_path),
        "observation",
        "add",
        "--title",
        "Review feedback target",
        "--summary",
        "Pending review",
    )
    obs_id = json.loads(add_result.stdout)["id"]

    review_metadata = {
        "trace_id": "trace-review-1",
        "job_id": "job-review-1",
        "source": "vps-agent-web",
    }
    review_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "observation_id": obs_id,
                        "feedback": "补充: Reviewed note",
                        "metadata": review_metadata,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    review_result = _run_cli(
        "--db",
        str(db_path),
        "review",
        "apply",
        "--file",
        str(review_path),
    )
    assert review_result.returncode == 0
    review_payload = json.loads(review_result.stdout)
    assert review_payload["results"][0]["metadata"] == review_metadata

    history_result = _run_cli(
        "--db",
        str(db_path),
        "observation",
        "feedback",
        "--id",
        str(obs_id),
        "--history",
        "history",
    )
    assert history_result.returncode == 0
    history_payload = json.loads(history_result.stdout)
    assert history_payload["history"][0]["metadata"] == review_metadata


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
def test_session_show_returns_standardized_not_found_error(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    init_result = _run_cli("--db", str(db_path), "init")
    assert init_result.returncode == 0

    missing_result = _run_cli("--db", str(db_path), "session", "show", "999")
    assert missing_result.returncode == 5

    payload = json.loads(missing_result.stdout)
    assert payload["ok"] is False
    assert payload["error_code"] == "NF_SESSION"
    assert payload["help_command"] == "los-memory session list --help"


@pytest.mark.contract
def test_session_stop_without_active_session_returns_standardized_error(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    init_result = _run_cli("--db", str(db_path), "init")
    assert init_result.returncode == 0

    stop_result = _run_cli("--db", str(db_path), "session", "stop")
    assert stop_result.returncode == 5

    payload = json.loads(stop_result.stdout)
    assert payload["ok"] is False
    assert payload["error_code"] == "NF_ACTIVE_SESSION"
    assert payload["help_command"] == "los-memory session start --help"


@pytest.mark.contract
def test_session_stop_ignores_active_session_from_different_db(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home_dir)

    db_one = tmp_path / "one.db"
    db_two = tmp_path / "two.db"

    assert _run_cli("--db", str(db_one), "init", env=env).returncode == 0
    assert _run_cli("--db", str(db_one), "session", "start", "--project", "alpha", env=env).returncode == 0
    assert _run_cli("--db", str(db_two), "init", env=env).returncode == 0

    stop_result = _run_cli("--db", str(db_two), "session", "stop", env=env)
    assert stop_result.returncode == 5

    payload = json.loads(stop_result.stdout)
    assert payload["ok"] is False
    assert payload["error_code"] == "NF_ACTIVE_SESSION"


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


@pytest.mark.contract
def test_review_apply_invalid_shape_returns_validation_error(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    review_path = tmp_path / "review.json"
    init_result = _run_cli("--db", str(db_path), "init")
    assert init_result.returncode == 0

    review_path.write_text('"invalid"', encoding="utf-8")

    review_result = _run_cli(
        "--db",
        str(db_path),
        "review",
        "apply",
        "--file",
        str(review_path),
    )
    assert review_result.returncode == 4

    payload = json.loads(review_result.stdout)
    assert payload["ok"] is False
    assert payload["error_code"] == "VAL_INVALID_FORMAT"


@pytest.mark.contract
def test_admin_manage_stats_exposes_stable_smoke_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    init_result = _run_cli("--db", str(db_path), "init")
    assert init_result.returncode == 0

    add_result = _run_cli(
        "--db",
        str(db_path),
        "observation",
        "add",
        "--project",
        "ops",
        "--kind",
        "decision",
        "--title",
        "Stats target",
        "--summary",
        "smoke baseline",
        "--tags",
        "tenant:t1,user:u1",
    )
    assert add_result.returncode == 0

    stats_result = _run_cli(
        "--db",
        str(db_path),
        "admin",
        "manage",
        "stats",
    )
    assert stats_result.returncode == 0

    payload = json.loads(stats_result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "stats"
    assert payload["total"] == 1
    assert isinstance(payload["earliest"], str)
    assert isinstance(payload["latest"], str)
    assert payload["projects"] == [{"project": "ops", "count": 1}]
    assert payload["kinds"] == [{"kind": "decision", "count": 1}]
