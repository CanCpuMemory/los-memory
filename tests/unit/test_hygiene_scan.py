"""The hygiene ruler must fire on the shapes it claims to see, and only those.

Why the tests look like this: ``scripts/hygiene_scan.py`` is the P0 deliverable of
``docs/design/memory-hygiene-and-consolidation.md`` — it decides *what would be
cleaned* later, so a bucket that silently stops matching is worse than no bucket
(a clean report would then mean "the detector broke"). Every bucket therefore has
one positive case and one negative control that must stay silent.

Two properties are asserted explicitly because they are the ones that would do
real damage:

* the scan never writes to the corpus it reads (content and mtime compared);
* "cannot judge" is not "clean" (missing root and empty root both exit 2).

Fixtures are built under ``tmp_path``; nothing here addresses ``~/.dsh/memories``.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_scan_module():
    """Load ``scripts/hygiene_scan.py`` without importing it as a package.

    The module is registered in ``sys.modules`` before execution because its
    dataclasses resolve string annotations through ``sys.modules[cls.__module__]``
    on Python 3.9, and an unregistered module makes that lookup ``None``.
    """
    path = REPO_ROOT / "scripts" / "hygiene_scan.py"
    spec = importlib.util.spec_from_file_location("hygiene_scan_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scan_module():
    return load_scan_module()


def write_track(path: Path, entries: list[str]) -> None:
    """Write a store file with the DSH entry separator."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n§\n".join(entries) + "\n", encoding="utf-8")


def counts(report: dict) -> dict:
    return {code: item["count"] for code, item in report["findings"].items()}


def test_parses_head_tokens_without_leaking_them_into_the_body(scan_module):
    entry = scan_module.parse_entry(
        "[id:abcd1234] [slot:port] [supersedes:12345678] [deprecated:99999999] "
        "[2026-01-02] [branch:main] 正文在这里"
    )
    assert entry.id == "abcd1234"
    assert entry.slot == "port"
    assert entry.supersedes == "12345678"
    assert entry.deprecated is True
    assert entry.deprecated_by == "99999999"
    assert entry.date == "2026-01-02"
    assert "branch:main" in entry.neutral
    assert entry.body.strip() == "正文在这里"
    assert entry.active is False


def test_identity_ignores_program_metadata_and_whitespace(scan_module):
    """A re-dated / re-identified copy is the same memory, so it must dedupe."""
    left = scan_module.parse_entry("[id:aaaaaaaa] [2026-01-01] 同一件事：端口 8080")
    right = scan_module.parse_entry("[2026-02-02] 同一件事：端口   8080")
    assert left.norm == right.norm


def test_each_bucket_fires_once_on_a_fixture(scan_module, tmp_path):
    shared = [
        "[2026-01-01] 独立的条目",
        "[2026-01-02] 重复的条目：本机 jj 在 /opt/homebrew/bin/jj，且不在默认 PATH",
        "[2026-01-03] 重复的条目：本机 jj 在 /opt/homebrew/bin/jj，且不在默认 PATH",
        "[id:aaaaaaaa] [2026-01-04] 网关端口改为 9090，旧的 8080 作废",
        "[id:bbbbbbbb] [supersedes:aaaaaaaa] [2026-01-05] 声明取代上面那条，但目标仍是 active",
        "[2026-01-06] 修正上一条「独立的条目」的说法，实际由 launchd 托管",
    ]
    write_track(tmp_path / "projects" / "aaaa" / "MEMORY.md", shared)
    write_track(tmp_path / "projects" / "bbbb" / "MEMORY.md", shared)
    write_track(tmp_path / "MEMORY.md", [f"[2026-02-{day:02d}] 全局独立条目 {day}" for day in range(1, 22)])

    report = scan_module.scan(tmp_path)
    found = counts(report)
    assert found["track_duplicate"] == 1
    assert found["exact_duplicate"] == 2  # one per copy of the same track
    assert found["supersedes_unresolved"] == 2  # the edge points at an active entry
    assert found["claim_without_edge"] == 2  # "修正上一条" in prose, no edge
    assert found["lifecycle_blind"] == 1  # only the 21-entry global track is unmarked
    assert report["metrics"]["entries_total"] == 33
    assert report["metrics"]["slots"] == 0


def test_clean_corpus_produces_no_findings(scan_module, tmp_path):
    """Negative control: unrelated facts must not be reported as duplicates."""
    write_track(
        tmp_path / "projects" / "cccc" / "KEY.md",
        [f"[2026-03-0{index}] 互不相关的事实 {index * 7919}" for index in range(1, 5)],
    )
    report = scan_module.scan(tmp_path)
    assert report["findings"] == {}
    assert report["counts_by_safety"] == {"auto_safe": 0, "review_only": 0}


def test_mechanical_edge_is_not_reported_as_a_prose_claim(scan_module, tmp_path):
    """Negative control for D6: having an edge is exactly what D6 excludes."""
    write_track(
        tmp_path / "MEMORY.md",
        [
            "[id:11111111] [2026-01-01] 旧结论：端口 8080",
            "[id:22222222] [supersedes:11111111] [2026-01-02] 修正：端口改成 9090",
        ],
    )
    report = scan_module.scan(tmp_path)
    assert "claim_without_edge" not in report["findings"]


def test_near_duplicate_respects_its_threshold(scan_module, tmp_path):
    base = "本机磁盘口径必须先量后定，因为 APFS 快照会让可用空间双向移动十几 GiB，单点 df 相减不成立"
    tweaked = base.replace("十几", "数十") + "，还要按 inode 记账"
    write_track(tmp_path / "MEMORY.md", [f"[2026-01-01] {base}", f"[2026-01-02] {tweaked}"])

    strict = scan_module.scan(tmp_path, near_dup_threshold=0.99)
    assert "near_duplicate" not in strict["findings"]

    loose = scan_module.scan(tmp_path, near_dup_threshold=0.5)
    assert counts(loose)["near_duplicate"] == 1
    sample = loose["findings"]["near_duplicate"]["samples"][0]
    assert 0.0 < sample["jaccard"] < 1.0


def test_scan_never_writes_to_the_corpus(scan_module, tmp_path):
    track = tmp_path / "MEMORY.md"
    write_track(track, ["[2026-01-01] 同样的一条正文用于写保护检测", "[2026-01-02] 同样的一条正文用于写保护检测"])
    before = (track.read_bytes(), track.stat().st_mtime_ns)
    scan_module.scan(tmp_path)
    after = (track.read_bytes(), track.stat().st_mtime_ns)
    assert before == after


def test_missing_and_empty_roots_are_cannot_judge(scan_module, tmp_path):
    with pytest.raises(FileNotFoundError):
        scan_module.scan(tmp_path / "nope")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(LookupError):
        scan_module.scan(empty)


def test_exit_codes_follow_the_three_state_contract(scan_module, tmp_path, capsys):
    write_track(tmp_path / "MEMORY.md", ["[2026-01-01] 足够长的一条正文用于重复检测", "[2026-01-02] 足够长的一条正文用于重复检测"])
    spec = f"dsh-md:{tmp_path}"

    assert scan_module.main(["--corpus", spec]) == scan_module.EXIT_OK  # informational by default
    assert scan_module.main(["--corpus", spec, "--fail-on", "auto-safe"]) == scan_module.EXIT_FINDING
    assert scan_module.main(["--corpus", f"dsh-md:{tmp_path / 'nope'}"]) == scan_module.EXIT_UNJUDGEABLE
    assert scan_module.main(["--corpus", "unknown-kind:/tmp"]) == scan_module.EXIT_UNJUDGEABLE
    capsys.readouterr()


def test_json_report_is_the_machine_contract(scan_module, tmp_path, capsys):
    write_track(tmp_path / "MEMORY.md", ["[2026-01-01] 足够长的一条正文用于重复检测", "[2026-01-02] 足够长的一条正文用于重复检测"])
    code = scan_module.main(["--corpus", f"dsh-md:{tmp_path}", "--json"])
    assert code == scan_module.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["corpus"]["kind"] == "dsh-md"
    assert set(payload["metrics"]) >= {"tracks", "entries_total", "entries_deprecated", "slots"}
    assert payload["findings"]["exact_duplicate"]["safety"] == "auto_safe"


def test_self_test_passes(scan_module):
    """The script's own ruler check, which CI can run without a corpus."""
    assert scan_module.self_test() == 0


def test_symlinked_project_is_an_alias_not_a_duplicate(scan_module, tmp_path):
    """A path alias addresses one file; counting it twice inflated a baseline 31%.

    `dsh-memory-evolve` resolves path aliases by symlinking the project directory,
    so the same track is reachable through two names. Identity is (st_dev, st_ino):
    the alias must be reported, not "deduped".
    """
    write_track(tmp_path / "projects" / "aaaa" / "MEMORY.md", ["[2026-01-01] 一条足够长的正文用于别名检测"])
    (tmp_path / "projects" / "bbbb").symlink_to(tmp_path / "projects" / "aaaa")

    report = scan_module.scan(tmp_path)
    assert report["metrics"]["tracks"] == 1
    assert report["metrics"]["alias_tracks"] == 1
    assert report["aliases"] == [
        {"canonical": "projects/aaaa/MEMORY.md", "aliases": ["projects/bbbb/MEMORY.md"]}
    ]
    assert "track_duplicate" not in report["findings"]


def test_a_real_copy_is_still_a_duplicate(scan_module, tmp_path):
    """Negative control: two inodes with identical content are real duplication."""
    import shutil

    write_track(tmp_path / "projects" / "aaaa" / "MEMORY.md", ["[2026-01-01] 一条足够长的正文用于复制检测"])
    (tmp_path / "projects" / "bbbb").mkdir(parents=True)
    shutil.copyfile(tmp_path / "projects" / "aaaa" / "MEMORY.md", tmp_path / "projects" / "bbbb" / "MEMORY.md")

    report = scan_module.scan(tmp_path)
    assert report["metrics"]["tracks"] == 2
    assert report["metrics"]["alias_tracks"] == 0
    assert counts(report)["track_duplicate"] == 1


def test_coverage_ratios_are_reported(scan_module, tmp_path):
    """The two objectives need ratios, not raw counts, to be comparable over time."""
    write_track(
        tmp_path / "MEMORY.md",
        [
            "[id:11111111] [2026-01-01] 有身份证的一条足够长的正文",
            "[2026-01-02] 另一条足够长的正文",
            "[id:22222222] [supersedes:11111111] [2026-01-03] 取代上一条的足够长正文",
        ],
    )
    report = scan_module.scan(tmp_path)
    coverage = report["metrics"]["coverage"]
    assert coverage["addressability"] == pytest.approx(2 / 3, abs=1e-4)
    assert coverage["validity"] == pytest.approx(1 / 3, abs=1e-4)
    assert "context_face" in report["metrics"]
