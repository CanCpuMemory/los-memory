"""CLI contract of ``los-memory mem``: exit codes, dry-run, profile isolation.

Exit codes are the three-state convention used elsewhere in this repo for
instruments (``0`` judged / ``1`` real negative / ``2`` cannot judge). The two
failures that would be read as success are asserted directly:

* an **empty ledger** and an **uninitialised ledger** must both be exit ``2``, never
  ``0`` (a missing database is not "no memories");
* ``--dry-run`` must write nothing at all — including not creating the file it is
  supposed to inspect.
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_tool.ledger import cli, schema  # noqa: E402
from memory_tool.ledger.store import Ledger  # noqa: E402


def invoke(argv: list[str], db: Path | str) -> int:
    """Run one ``mem`` command in-process and return its exit code."""
    parser = argparse.ArgumentParser(prog="los-memory mem")
    actions = parser.add_subparsers(dest="mem_action", required=True)
    cli._register_actions(actions)  # noqa: SLF001 - exercising the real registration
    args = parser.parse_args(argv)
    args.db = str(db)
    return cli.run(args)


def payload(capsys: pytest.CaptureFixture[str]) -> dict:
    """Parse the JSON payload the command printed."""
    return json.loads(capsys.readouterr().out)


def seed(db: Path, content: str = "网关端口 10090") -> str:
    """Write one unit and return its id."""
    conn = schema.connect(str(db), create=True)
    try:
        ledger = Ledger(conn)
        with ledger.transaction():
            result = ledger.add("key", content, project="dsfolder", branch="main")
        return result["id"]
    finally:
        conn.close()


def test_add_prints_a_machine_readable_payload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A successful add is exit 0 with the new unit in the payload."""
    db = tmp_path / "s.sqlite3"
    code = invoke([
        "add", "--track", "key", "--project", "dsfolder", "--branch", "main",
        "--content", "网关端口 10090",
    ], db)
    out = payload(capsys)
    assert code == cli.EXIT_OK
    assert out["ok"] is True and out["data"]["created"] is True
    assert out["data"]["track"] == "key"


def test_empty_content_exits_two_not_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty stdin is "cannot judge" (exit 2), never a silent success."""
    db = tmp_path / "s.sqlite3"
    monkeypatch.setattr(sys, "stdin", io.StringIO("   \n\n"))
    code = invoke(["add", "--track", "memory", "--content-file", "-"], db)
    out = payload(capsys)
    assert code == cli.EXIT_UNJUDGEABLE == 2
    assert out["ok"] is False
    assert out["error"]["code"] == "empty_content"


def test_missing_ledger_is_exit_two_and_creates_no_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A read on an uninitialised ledger reports the fault and creates nothing."""
    db = tmp_path / "absent.sqlite3"
    code = invoke(["list"], db)
    out = payload(capsys)
    assert code == cli.EXIT_UNJUDGEABLE
    assert out["error"]["code"] == "ledger_unavailable"
    assert out["details"]["db"] == str(db)
    assert not db.exists(), "a read must not fabricate the ledger"


def test_unknown_id_is_exit_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A well-formed request for a missing unit is a real negative, not "cannot judge"."""
    db = tmp_path / "s.sqlite3"
    seed(db)
    code = invoke(["expand", "--id", "unit_nope"], db)
    out = payload(capsys)
    assert code == cli.EXIT_REFUSED == 1
    assert out["error"]["code"] == "ledger_refused"


def test_dry_run_writes_nothing_to_an_existing_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dry run on a real ledger reports the effect and rolls everything back."""
    db = tmp_path / "s.sqlite3"
    seed(db)
    conn = schema.connect(str(db))
    try:
        before = tuple(conn.execute("SELECT COUNT(*) FROM events").fetchone())
        revision_count = conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
    finally:
        conn.close()

    code = invoke([
        "add", "--track", "memory", "--content", "should not persist", "--dry-run",
    ], db)
    out = payload(capsys)
    assert code == cli.EXIT_OK
    assert out["dry_run"] is True
    assert out["data"]["created"] is True, "the dry run predicts the create"

    conn = schema.connect(str(db))
    try:
        assert tuple(conn.execute("SELECT COUNT(*) FROM events").fetchone()) == before
        assert conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == revision_count
        assert conn.execute(
            "SELECT COUNT(*) FROM revisions WHERE content='should not persist'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_dry_run_on_a_missing_ledger_does_not_create_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--dry-run`` simulates on a scratch database instead of creating the real one."""
    db = tmp_path / "absent.sqlite3"
    code = invoke(["add", "--track", "memory", "--content", "only a prediction", "--dry-run"], db)
    out = payload(capsys)
    assert code == cli.EXIT_OK
    assert out["simulated_fresh"] is True
    assert out["data"]["created"] is True
    assert not db.exists()


def test_import_dry_run_reports_counts_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The importer's dry run prints per-track counts and leaves no database."""
    corpus = tmp_path / "memories"
    corpus.mkdir()
    (corpus / "USER.md").write_text("[2026-01-01] 用户偏好\n", encoding="utf-8")
    db = tmp_path / "absent.sqlite3"

    code = invoke(["import-evolve", "--root", str(corpus), "--dry-run"], db)
    out = payload(capsys)
    assert code == cli.EXIT_OK
    assert out["data"]["dry_run"] is True
    assert out["data"]["tracks"]["user"]["created"] == 1
    assert not db.exists()


def test_import_missing_root_is_cannot_judge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An absent corpus root must not read as "nothing to import" (exit 0)."""
    db = tmp_path / "s.sqlite3"
    code = invoke(["import-evolve", "--root", str(tmp_path / "nope")], db)
    out = payload(capsys)
    assert code == cli.EXIT_UNJUDGEABLE
    assert out["error"]["code"] == "ledger_unavailable"


def test_search_without_matches_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty result is a judged answer: exit 0 with an empty list and honest meta."""
    db = tmp_path / "s.sqlite3"
    seed(db)
    code = invoke(["search", "完全不存在的词"], db)
    out = payload(capsys)
    assert code == cli.EXIT_OK
    assert out["data"]["results"] == []
    assert out["data"]["meta"]["hits"] == 0


def test_search_finds_two_character_cjk_terms(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bigram path is what makes 2-character CJK queries work at all."""
    db = tmp_path / "s.sqlite3"
    seed(db, "网关端口 10090")
    code = invoke(["search", "网关"], db)
    out = payload(capsys)
    assert code == cli.EXIT_OK
    assert len(out["data"]["results"]) == 1
    assert out["data"]["meta"]["paths"]["网关"] == "bigram"


def test_archive_then_list_and_expand(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Archive hides the unit from ``list`` but ``expand`` still reads it."""
    db = tmp_path / "s.sqlite3"
    unit_id = seed(db)
    assert invoke(["archive", "--id", unit_id], db) == cli.EXIT_OK
    payload(capsys)

    assert invoke(["list", "--track", "key", "--project", "dsfolder", "--state", "active"], db) == 0
    assert payload(capsys)["data"]["count"] == 0

    assert invoke(["expand", "--id", unit_id], db) == 0
    expanded = payload(capsys)["data"]
    assert expanded["unit"]["state"] == "archived"
    assert expanded["content"] == "网关端口 10090"


def test_text_format_is_human_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--format text`` prints a line per unit; JSON stays the default."""
    db = tmp_path / "s.sqlite3"
    seed(db)
    assert invoke(["list", "--track", "key", "--format", "text"], db) == cli.EXIT_OK
    printed = capsys.readouterr().out
    assert "key/dsfolder" in printed
    assert printed.lstrip().startswith("unit_")


def test_cli_is_registered_on_the_main_entrypoint() -> None:
    """The command group is reachable through the real ``los-memory`` entrypoint."""
    result = subprocess.run(
        [sys.executable, "-m", "memory_tool", "mem", "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT)},
    )
    assert result.returncode == 0, result.stderr
    for action in ("add", "list", "search", "import-evolve", "archive", "expand"):
        assert action in result.stdout


def test_mem_commands_do_not_touch_a_profile_database(tmp_path: Path) -> None:
    """Negative control for profile isolation: no codex/claude/shared ledger appears.

    The ``mem`` group is dispatched before config resolution on purpose; this test
    runs the real entrypoint with a redirected ``HOME`` so a profile database would
    land somewhere visible if the isolation ever regressed.
    """
    home = tmp_path / "home"
    home.mkdir()
    db = tmp_path / "s.sqlite3"
    result = subprocess.run(
        [
            sys.executable, "-m", "memory_tool", "--profile", "codex", "mem", "add",
            "--track", "memory", "--content", "isolated write", "--db", str(db),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(REPO_ROOT),
            "HOME": str(home),
        },
    )
    assert result.returncode == 0, result.stderr
    assert db.exists()
    assert not (home / ".codex_memory").exists(), "profile isolation regressed"
    assert not (home / ".claude_memory").exists()
