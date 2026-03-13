#!/usr/bin/env python3
"""Runtime smoke verifier for los-memory adapter integration.

This script validates real grouped-command runtime paths used by upstream
adapter checks. It intentionally runs read/write commands against a temporary
database to exercise parser + execution + output contracts end to end.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _run_cli(db_path: Path, *args: str) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "memory_tool",
        "--db",
        str(db_path),
        "--output",
        "json",
        *args,
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


def _assert_admin_manage_smoke(db_path: Path) -> None:
    stats = _run_cli(db_path, "admin", "manage", "stats")
    assert stats["ok"] is True
    assert stats["action"] == "stats"
    assert "total" in stats
    assert "projects" in stats
    assert "kinds" in stats


def _assert_observation_delete_smoke(db_path: Path, observation_id: int) -> None:
    delete_preview = _run_cli(
        db_path,
        "observation",
        "delete",
        str(observation_id),
        "--dry-run",
    )
    assert delete_preview["ok"] is True
    assert delete_preview["matched"] == 1
    assert delete_preview["deleted"] == 0
    assert delete_preview["dry_run"] is True


def _assert_review_apply_smoke(db_path: Path, observation_id: int, review_file: Path) -> None:
    review_file.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "observation_id": observation_id,
                        "feedback": "supplement smoke verification",
                    }
                ]
            }
        ),
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


def verify_los_memory_adapter() -> dict[str, Any]:
    """Run runtime smoke checks for grouped adapter commands."""
    with tempfile.TemporaryDirectory(prefix="los-memory-adapter-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "adapter-smoke.db"
        review_file = tmp_path / "review.json"

        init = _run_cli(db_path, "init")
        assert init["ok"] is True

        created = _run_cli(
            db_path,
            "observation",
            "add",
            "--project",
            "lsclaw",
            "--kind",
            "decision",
            "--title",
            "Adapter smoke target",
            "--summary",
            "grouped command runtime verification",
            "--tags",
            "tenant:t1,user:u1,session:s1,taskType:review",
        )
        assert created["ok"] is True
        observation_id = int(created["id"])

        _assert_admin_manage_smoke(db_path)
        _assert_observation_delete_smoke(db_path, observation_id)
        _assert_review_apply_smoke(db_path, observation_id, review_file)

        return {
            "ok": True,
            "checks": [
                "admin manage stats",
                "observation delete --dry-run",
                "review apply --file ... --dry-run",
            ],
            "observation_id": observation_id,
        }


def main() -> int:
    try:
        result = verify_los_memory_adapter()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
