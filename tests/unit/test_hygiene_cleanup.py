"""The plan must be decision-ready, content-addressed, and still read-only.

Why these assertions (P1 of ``docs/design/memory-hygiene-and-consolidation.md``):

* ``actions`` is the only part a writer may execute without a human, so it must
  contain **only** mechanically decidable codes — a new detector must not leak
  into it silently;
* ``daily/`` is an evidence surface (a day's log); a duplicate line there is
  reported, never cleaned;
* ``plan_id`` is content-addressed: the same corpus and the same actions yield
  the same id, so a replayed plan is idempotent, while any corpus edit yields a
  different id (that is the drift check the writer relies on);
* the tool must not touch the corpus it reads.

Fixtures live under ``tmp_path``; the plan state directory is redirected through
``LOS_MEMORY_HYGIENE_DIR`` so nothing here addresses the operator's store.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str):
    """Load a ``scripts/*.py`` file as a module, registered for dataclass lookups."""
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cleanup():
    return load_module("hygiene_cleanup")


def write(path: Path, entries: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n§\n".join(entries) + "\n", encoding="utf-8")


@pytest.fixture()
def corpus(tmp_path):
    """Two identical project tracks plus a daily log holding one duplicate pair."""
    root = tmp_path / "corpus"
    shared = [
        "[2026-01-01] 独立的条目：端口 8080 由 gateway 监听",
        "[2026-01-02] 重复的条目：本机 jj 在 /opt/homebrew/bin/jj，且不在默认 PATH",
        "[2026-01-03] 重复的条目：本机 jj 在 /opt/homebrew/bin/jj，且不在默认 PATH",
        "[2026-01-04] 修正上一条「独立的条目」的说法，实际由 launchd 托管",
    ]
    write(root / "projects" / "aaaa" / "MEMORY.md", shared)
    write(root / "projects" / "bbbb" / "MEMORY.md", shared)
    write(
        root / "daily" / "2026-01-05.md",
        ["[2026-01-05] 日刊里的一次巡检结论，足够长的一条正文用于重复检测",
         "[2026-01-05] 日刊里的一次巡检结论，足够长的一条正文用于重复检测"],
    )
    return root


def test_actions_hold_only_mechanically_decidable_codes(cleanup, corpus):
    plan = cleanup.build_plan(corpus)
    assert all(action["code"] in cleanup.AUTO_CODES for action in plan["actions"])
    assert all(action["op"] == "deprecate" for action in plan["actions"])
    assert all("id" in action for action in plan["actions"])


def test_daily_duplicates_are_review_items_not_actions(cleanup, corpus):
    plan = cleanup.build_plan(corpus)
    # The two *project* tracks still yield entry actions; the daily pair must not.
    assert {action["target"]["track"] for action in plan["actions"]} == {
        "projects/aaaa/MEMORY.md",
        "projects/bbbb/MEMORY.md",
    }
    review = plan["proposals"]["review"]
    assert review["exact_duplicate_evidence_track"]["count"] == 1
    assert "daily/" in review["exact_duplicate_evidence_track"]["samples"][0]["track"]


def test_track_duplicate_becomes_a_decision_not_an_action(cleanup, corpus):
    plan = cleanup.build_plan(corpus)
    dedupe = plan["proposals"]["dedupe_track"]
    assert dedupe["count"] == 1
    group = dedupe["groups"][0]
    assert group["requires_decision"] is True
    assert group["proposed_survivor"] == "projects/aaaa/MEMORY.md"
    assert group["proposed_removal"] == ["projects/bbbb/MEMORY.md"]
    assert dedupe["extra_entries"] == group["entries"] * (len(group["tracks"]) - 1)


def test_plan_id_is_content_addressed(cleanup, corpus):
    first = cleanup.build_plan(corpus)
    second = cleanup.build_plan(corpus)
    assert first["plan_id"] == second["plan_id"]
    assert first["corpus"]["snapshot_hash"] == second["corpus"]["snapshot_hash"]

    write(corpus / "projects" / "bbbb" / "MEMORY.md", ["[2026-02-01] 完全不同的内容"])
    third = cleanup.build_plan(corpus)
    assert third["corpus"]["snapshot_hash"] != first["corpus"]["snapshot_hash"]


def test_plan_expires_within_the_declared_ttl(cleanup, corpus):
    plan = cleanup.build_plan(corpus)
    created = cleanup.datetime.fromisoformat(plan["created_at"])
    expires = cleanup.datetime.fromisoformat(plan["expires_at"])
    assert (expires - created).days == cleanup.PLAN_TTL_DAYS


def test_plan_never_writes_the_corpus(cleanup, corpus):
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in corpus.rglob("*.md")}
    cleanup.build_plan(corpus)
    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in corpus.rglob("*.md")}
    assert before == after


def test_writer_is_named_so_the_handoff_is_not_implicit(cleanup, corpus):
    plan = cleanup.build_plan(corpus)
    assert plan["writer"]["owner"] == "dsh-memory-evolve"
    assert plan["writer"]["state"] != "connected"


def test_cli_writes_the_plan_and_reports_cannot_judge(cleanup, corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("LOS_MEMORY_HYGIENE_DIR", str(tmp_path / "state"))
    out = tmp_path / "plan.json"
    assert cleanup.main(["plan", "--corpus", f"dsh-md:{corpus}", "--out", str(out)]) == cleanup.EXIT_OK
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == cleanup.SCHEMA_VERSION

    assert cleanup.main(["plan", "--corpus", "unknown:/tmp"]) == cleanup.EXIT_UNJUDGEABLE
    assert cleanup.main(["plan", "--corpus", f"dsh-md:{tmp_path / 'missing'}"]) == cleanup.EXIT_UNJUDGEABLE


def test_status_lists_plans_from_the_state_dir(cleanup, corpus, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOS_MEMORY_HYGIENE_DIR", str(tmp_path / "state"))
    assert cleanup.main(["plan", "--corpus", f"dsh-md:{corpus}"]) == cleanup.EXIT_OK
    capsys.readouterr()
    assert cleanup.main(["status", "--json"]) == cleanup.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["plans"]) == 1
    assert payload["plans"][0]["actions"] == 2


def test_self_test_passes(cleanup):
    assert cleanup.self_test() == 0
