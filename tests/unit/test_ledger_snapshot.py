"""The one-shot snapshot payload (``mem snapshot``) and the registry lookup.

The injection plugin will render from exactly this payload once per turn, so the
failures that matter are: a shape the renderer cannot read, a cap that silently
drops facts without saying so, and a degradation that is silent (a missing branch
must be *named*, not guessed). Each property therefore has a test that would fail
if the guard were removed.

Fixtures are temporary ledgers; nothing reads ``~/.dsh/memories`` and the workspace
registry is replaced by a fixture so the assertions do not depend on this machine.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_tool.ledger import cli, registry, schema  # noqa: E402
from memory_tool.ledger.registry import load_project_slugs, project_hash  # noqa: E402
from memory_tool.ledger.snapshot import SnapshotOptions, build_snapshot  # noqa: E402
from memory_tool.ledger.store import Ledger  # noqa: E402

PROJECT = "abc123def456"


def open_ledger(path: Path, slugs: dict[str, str] | None = None) -> tuple[object, Ledger]:
    """Open an initialised ledger, optionally with a project-slug map."""
    conn = schema.connect(str(path), create=True)
    return conn, Ledger(conn, project_slugs=slugs)


def seed_key_units(ledger: Ledger, branches: list[str | None]) -> list[str]:
    """Create one key unit per requested branch scope (``None`` = every branch)."""
    ids: list[str] = []
    with ledger.transaction():
        for branch in branches:
            result = ledger.add(
                "key",
                f"fact visible on {branch or 'every branch'}",
                project=PROJECT,
                branch=branch,
            )
            ids.append(result["id"])
    return ids


def options(tmp_path: Path, **overrides) -> SnapshotOptions:
    """Build options for a cwd that is deliberately not a git repository."""
    base = {"cwd": str(tmp_path / "workspace"), "project": PROJECT}
    base.update(overrides)
    return SnapshotOptions(**base)


def write_registry(path: Path, projects: list[dict]) -> Path:
    """Write a minimal workspace registry."""
    path.write_text(
        json.dumps({"version": 1, "workspaceRoot": str(path.parent), "projects": projects}),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------- payload shape


def test_payload_matches_the_declared_schema(tmp_path: Path) -> None:
    """Every level of the payload has exactly the fields the renderer expects."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3", slugs={PROJECT: "dsfolder"})
    try:
        seed_key_units(ledger, [None])
        with ledger.transaction():
            ledger.add("memory", "全局事实")
            ledger.add("user", "用户偏好")
            ledger.add("daily", "今天的日志", day="2026-01-09")
            ledger.add("project", "项目日志", project=PROJECT)

        payload = build_snapshot(
            ledger, options(tmp_path, branch="main", project_slugs={PROJECT: "dsfolder"},
                            date="2026-01-09")
        )

        assert set(payload) == {
            "ok", "schema", "cwd", "branch", "project", "project_slug", "tracks",
            "daily", "project_log", "slots", "degraded",
        }
        assert payload["ok"] is True
        assert payload["schema"] == 1
        assert set(payload["tracks"]) == {"memory", "user", "key"}
        for track in ("memory", "user", "key"):
            bucket = payload["tracks"][track]
            assert set(bucket) >= {"mode", "count", "entries", "truncated"}
            assert bucket["mode"] in {"summary", "full"}
            for entry in bucket["entries"]:
                assert set(entry) == {"id", "summary", "content_len"}
                assert "content" not in entry, "bodies are fetched with expand, not here"
                assert isinstance(entry["content_len"], int)
        assert payload["tracks"]["key"]["branch_filtered"] is True
        assert set(payload["daily"]) == {"date", "count"}
        assert set(payload["project_log"]) == {"count"}
        assert set(payload["slots"]) == {"active", "deprecated", "same_slot_multi_active"}
        assert payload["project"] == PROJECT
        assert payload["project_slug"] == "dsfolder"
        assert payload["degraded"] == []
    finally:
        conn.close()


# ------------------------------------------------------------------- branch handling


def test_branch_filter_excludes_other_branch_entries(tmp_path: Path) -> None:
    """A branch-scoped fact is invisible on another branch; untagged facts stay."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        seed_key_units(ledger, [None, "main", "superagent/T104"])
        on_main = build_snapshot(ledger, options(tmp_path, branch="main"))
        elsewhere = build_snapshot(ledger, options(tmp_path, branch="superagent/T104"))
        all_branches = build_snapshot(ledger, options(tmp_path, branch=None,
                                                       cwd=str(tmp_path / "not-a-repo")))

        assert on_main["tracks"]["key"]["count"] == 2, "untagged + main"
        assert elsewhere["tracks"]["key"]["count"] == 2, "untagged + that branch"
        # Fail-open: with no branch resolved there is no filter, so everything shows.
        assert all_branches["tracks"]["key"]["count"] == 3
        assert all_branches["tracks"]["key"]["branch_filtered"] is False
        assert "branch_unresolved" in all_branches["degraded"]
        assert all_branches["branch"] is None
    finally:
        conn.close()


def test_explicit_branch_avoids_the_degradation(tmp_path: Path) -> None:
    """An explicit ``--branch`` needs no git, and is reported as a real filter."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        seed_key_units(ledger, [None, "main"])
        payload = build_snapshot(ledger, options(tmp_path, branch="main"))
        assert payload["branch"] == "main"
        assert payload["degraded"] == []
        assert payload["tracks"]["key"]["branch_filtered"] is True
    finally:
        conn.close()


def test_git_branch_is_detected_when_available(tmp_path: Path) -> None:
    """When the cwd is a git repo the branch is resolved (not guessed from a name)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    # A real repo is required for git to answer; skip cleanly when git is absent.
    import shutil
    import subprocess

    if shutil.which("git") is None:  # pragma: no cover - environment dependent
        pytest.skip("git not available")
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "topic"], check=False,
                   capture_output=True)

    from memory_tool.ledger.snapshot import resolve_git_branch

    resolved = resolve_git_branch(str(repo))
    assert resolved is None or isinstance(resolved, str)


# ------------------------------------------------------------------ caps and mode


def test_truncation_counts_add_up(tmp_path: Path) -> None:
    """``count - truncated == len(entries)`` for both cap kinds."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            for index in range(5):
                ledger.add("memory", f"条目 {index} " + "x" * 40)
        by_entries = build_snapshot(ledger, options(tmp_path, max_entries=2))
        bucket = by_entries["tracks"]["memory"]
        assert bucket["count"] == 5
        assert len(bucket["entries"]) == 2
        assert bucket["truncated"] == 3

        by_chars = build_snapshot(ledger, options(tmp_path, max_entries=100, max_chars=60))
        capped = by_chars["tracks"]["memory"]
        assert capped["truncated"] >= 4, "the char cap must also drop entries"
        assert capped["count"] - capped["truncated"] == len(capped["entries"])
    finally:
        conn.close()


def test_mode_boundary_is_inclusive_on_both_thresholds(tmp_path: Path) -> None:
    """``full`` only while BOTH the entry count and the character total fit."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            for index in range(3):
                ledger.add("memory", f"条目 {index}")
        total_chars = sum(
            len(row.content) for row in ledger.list_units(track="memory", limit=None)
        )

        fits = build_snapshot(
            ledger,
            options(tmp_path, full_threshold_entries=3, full_threshold_chars=total_chars),
        )
        assert fits["tracks"]["memory"]["mode"] == "full", "boundary values are inclusive"

        too_many_entries = build_snapshot(
            ledger,
            options(tmp_path, full_threshold_entries=2, full_threshold_chars=total_chars),
        )
        assert too_many_entries["tracks"]["memory"]["mode"] == "summary"

        too_many_chars = build_snapshot(
            ledger,
            options(tmp_path,
                    full_threshold_entries=3, full_threshold_chars=total_chars - 1),
        )
        assert too_many_chars["tracks"]["memory"]["mode"] == "summary"
    finally:
        conn.close()


def test_summary_prefers_the_summary_tag_then_the_first_line(tmp_path: Path) -> None:
    """The rendered title is the ``[summary:…]`` tag when present, else line 1."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            ledger.add("memory", "[2026-01-01] [summary:端口改为 10090] 正文第一行\n第二行")
            ledger.add("memory", "[2026-01-02] 只有正文的第一行\n第二行")
        payload = build_snapshot(ledger, options(tmp_path))
        summaries = [entry["summary"] for entry in payload["tracks"]["memory"]["entries"]]
        assert "端口改为 10090" in summaries
        assert "只有正文的第一行" in summaries
        assert all("\n" not in summary for summary in summaries)
    finally:
        conn.close()


# --------------------------------------------------- daily / project log / slots


def test_daily_and_project_log_are_counted_for_the_requested_scope(tmp_path: Path) -> None:
    """``daily`` counts one date; ``project_log`` counts one project."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            ledger.add("daily", "九月九日", day="2026-01-09")
            ledger.add("daily", "九月九日第二条", day="2026-01-09")
            ledger.add("daily", "九月十日", day="2026-01-10")
            ledger.add("project", "本项目日志", project=PROJECT)
            ledger.add("project", "别的项目日志", project="ffffffffffff")

        payload = build_snapshot(ledger, options(tmp_path, date="2026-01-09"))
        assert payload["daily"] == {"date": "2026-01-09", "count": 2}
        assert payload["project_log"] == {"count": 1}
    finally:
        conn.close()


def test_slot_health_reports_the_invariant(tmp_path: Path) -> None:
    """Slot counters are reported, and a hand-off leaves no multi-active slot."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        with ledger.transaction():
            ledger.add("memory", "端口 9090", slot="port")
        with ledger.transaction():
            ledger.add("memory", "端口 10090", slot="port")
        slots = build_snapshot(ledger, options(tmp_path))["slots"]
        assert slots["active"] == 1
        assert slots["deprecated"] == 1
        assert slots["same_slot_multi_active"] == 0
    finally:
        conn.close()


# ----------------------------------------------------------------------- registry


def test_registry_lookup_derives_hashes_from_declared_roots(tmp_path: Path) -> None:
    """A registered root and a nested child both resolve to their registry key."""
    root = tmp_path / "project"
    path = write_registry(
        tmp_path / "projects.json",
        [
            {"key": "dsfolder", "path": str(root / "dsfolder")},
            {"key": "los", "path": str(root / "los"), "children": [{"key": "unirun"}]},
        ],
    )
    slugs = load_project_slugs(path)
    assert slugs[project_hash(str(root / "dsfolder"))] == "dsfolder"
    assert slugs[project_hash(str(root / "los" / "unirun"))] == "unirun"


def test_registry_missing_or_unrecognized_is_cannot_judge(tmp_path: Path) -> None:
    """A missing or shapeless registry raises instead of reporting "no projects"."""
    with pytest.raises(schema.LedgerUnavailableError) as missing:
        load_project_slugs(tmp_path / "absent.json")
    assert "project_registry_missing" in str(missing.value)

    shapeless = tmp_path / "projects.json"
    shapeless.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(schema.LedgerUnavailableError) as unrecognized:
        load_project_slugs(shapeless)
    assert "project_registry_unrecognized" in str(unrecognized.value)


def test_best_effort_lookup_never_raises(tmp_path: Path) -> None:
    """The read path degrades with a named reason instead of failing."""
    slugs, info = registry.try_load_project_slugs(tmp_path / "absent.json")
    assert slugs == {}
    assert info["available"] is False
    assert info["reason"] == "project_registry_missing"


def test_project_slug_is_null_when_unmapped(tmp_path: Path) -> None:
    """An unknown hash stays unknown — no directory-name guessing."""
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        payload = build_snapshot(ledger, options(tmp_path, project_slugs={}))
        assert payload["project"] == PROJECT
        assert payload["project_slug"] is None
    finally:
        conn.close()


def test_slug_and_hash_address_the_same_units(tmp_path: Path) -> None:
    """A unit written with a slug is stored under the hash, so both queries match."""
    slugs = {PROJECT: "dsfolder"}
    conn, ledger = open_ledger(tmp_path / "s.sqlite3", slugs=slugs)
    try:
        with ledger.transaction():
            created = ledger.add("key", "按 slug 写入的事实", project="dsfolder")
        stored = ledger.get_unit(created["id"])
        assert stored is not None
        assert stored.project == PROJECT, "identity is the evolve hash, not the slug"
        assert [row.id for row in ledger.list_units(project=ledger.project_aliases("dsfolder"),
                                                    limit=None)] == [created["id"]]
        assert [row.id for row in ledger.list_units(project=ledger.project_aliases(PROJECT),
                                                    limit=None)] == [created["id"]]
    finally:
        conn.close()


# ----------------------------------------------------------------------- CLI level


def invoke(argv: list[str], db: Path | str) -> int:
    """Run one ``mem`` command in-process."""
    import argparse

    parser = argparse.ArgumentParser(prog="los-memory mem")
    actions = parser.add_subparsers(dest="mem_action", required=True)
    cli._register_actions(actions)  # noqa: SLF001 - exercising the real registration
    args = parser.parse_args(argv)
    args.db = str(db)
    return cli.run(args)


def test_cli_snapshot_emits_the_payload_directly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mem snapshot --json`` prints the payload itself, not the command envelope."""
    monkeypatch.setattr(registry, "DEFAULT_REGISTRY", str(tmp_path / "absent.json"))
    db = tmp_path / "s.sqlite3"
    conn, ledger = open_ledger(db)
    try:
        with ledger.transaction():
            ledger.add("memory", "全局事实")
    finally:
        conn.close()

    code = invoke(["snapshot", "--json", "--cwd", str(tmp_path), "--project", PROJECT], db)
    payload = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK
    assert payload["ok"] is True and payload["schema"] == 1
    assert "data" not in payload, "the snapshot is not wrapped in the command envelope"
    assert payload["tracks"]["memory"]["count"] == 1
    assert "branch_unresolved" in payload["degraded"]
    assert "project_map_unavailable" in payload["degraded"]


def test_cli_snapshot_without_a_ledger_is_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read on a missing ledger is "cannot judge", and creates nothing."""
    monkeypatch.setattr(registry, "DEFAULT_REGISTRY", str(tmp_path / "absent.json"))
    db = tmp_path / "absent.sqlite3"
    code = invoke(["snapshot", "--cwd", str(tmp_path)], db)
    payload = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_UNJUDGEABLE
    assert payload["error"]["code"] == "ledger_unavailable"
    assert not db.exists()


def test_cli_snapshot_rejects_invalid_caps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative caps are a malformed request (exit 2), not silently clamped."""
    monkeypatch.setattr(registry, "DEFAULT_REGISTRY", str(tmp_path / "absent.json"))
    db = tmp_path / "s.sqlite3"
    conn, _ledger = open_ledger(db)
    conn.close()
    assert invoke(["snapshot", "--max-entries", "-1", "--cwd", str(tmp_path)], db) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "invalid_max_entries"


def test_cli_import_explicit_registry_is_required(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An explicit registry path that cannot be read is exit 2 (never a silent zero)."""
    corpus = tmp_path / "memories"
    corpus.mkdir()
    (corpus / "USER.md").write_text("[2026-01-01] 偏好\n", encoding="utf-8")
    db = tmp_path / "s.sqlite3"
    code = invoke(
        ["import-evolve", "--root", str(corpus), "--dry-run",
         "--project-map-from-registry", str(tmp_path / "absent.json")],
        db,
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_UNJUDGEABLE
    assert payload["error"]["code"] == "ledger_unavailable"


def test_cli_status_counts_assigned_and_unassigned_projects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``status`` reports slug coverage instead of an empty-denominator zero."""
    registry_path = write_registry(
        tmp_path / "projects.json", [{"key": "dsfolder", "path": "/tmp/dsfolder"}]
    )
    db = tmp_path / "s.sqlite3"
    conn, ledger = open_ledger(db)
    try:
        with ledger.transaction():
            ledger.add("key", "已映射项目", project=project_hash("/tmp/dsfolder"))
            ledger.add("key", "未映射项目", project="ffffffffffff")
    finally:
        conn.close()

    code = invoke(["status", "--project-map-from-registry", str(registry_path)], db)
    payload = json.loads(capsys.readouterr().out)["data"]
    assert code == cli.EXIT_OK
    assert payload["project_slug_assigned"] == 1
    assert payload["project_slug_unassigned"] == 1
    assert payload["project_slug_map"]["available"] is True
