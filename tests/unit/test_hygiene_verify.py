"""Accuracy by scenario: only a machine-provable claim may be judged as fact.

The user's rule (2026-11-04): facts that data/code can confirm are handled as
facts; subjective claims go to cross-review or a human; a periodic report lists
what is settled, what is not, and a sample to inspect.

These tests pin the three things that make that rule safe:

* **"cannot tell" never becomes "wrong"** — a foreign tree, an ephemeral `/tmp`
  path, or a missing ancestor is ``unknown``, because the store mixes memories
  written on M1 and M3 under the same user name;
* **refuted requires evidence of the same tree** — a missing file whose parent
  exists is a stale path; a missing file under a missing directory is not;
* **the sample is reproducible** — the same week and corpus yield the same seed
  and the same sample, so a human inspection can be audited afterwards.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str):
    """Load a ``scripts/*.py`` file, registering it for dataclass/typing lookups."""
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verify():
    return load_module("hygiene_verify")


def write_track(path: Path, entries: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n§\n".join(entries) + "\n", encoding="utf-8")


def test_extracts_paths_and_ignores_prose(verify):
    assert [a["path"] for a in verify.extract_assertions("见 ~/.dsh/MEMORY.md")] == ["~/.dsh/MEMORY.md"]
    assert verify.extract_assertions("版本 1.2.3 与 2026-11-04 都不是路径") == []
    line = verify.extract_assertions("代码在 /Users/x/y.py:12 这里")[0]
    assert (line["path"], line["line"]) == ("/Users/x/y.py", "12")


def test_trailing_punctuation_is_not_part_of_the_path(verify):
    assertion = verify.extract_assertions("路径是 /etc/hosts，后面是正文")[0]
    assert assertion["path"] == "/etc/hosts"


def test_foreign_and_ephemeral_are_unknown_not_refuted(verify):
    """The two controls that keep a correct memory from being retired."""
    assert verify.verify_assertion({"path": "/root/nas-only", "line": ""})["reason"] == "foreign_scope"
    assert verify.verify_assertion({"path": "/tmp/gone", "line": ""})["reason"] == "ephemeral_path"


def test_missing_ancestor_is_unknown_but_missing_sibling_is_refuted(verify, tmp_path, monkeypatch):
    # `tmp_path` lives under /var/folders, which production treats as ephemeral.
    # The rule under test is the ancestor check, so the ephemeral rule is lifted.
    monkeypatch.setattr(verify, "EPHEMERAL_ROOTS", ())
    existing_tree = tmp_path / "real-tree"
    existing_tree.mkdir()
    absent_here = verify.verify_assertion({"path": str(existing_tree / "gone.md"), "line": ""})
    assert (absent_here["verdict"], absent_here["reason"]) == ("refuted", "stale_path")

    absent_tree = verify.verify_assertion({"path": str(tmp_path / "no-such-tree" / "x.md"), "line": ""})
    assert (absent_tree["verdict"], absent_tree["reason"]) == ("unknown", "ancestor_missing")


def test_line_beyond_eof_is_refuted(verify, tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "EPHEMERAL_ROOTS", ())
    target = tmp_path / "f.md"
    target.write_text("one line\n", encoding="utf-8")
    verdict = verify.verify_assertion({"path": str(target), "line": "99"})
    assert verdict["verdict"] == "refuted"
    assert verdict["reason"].startswith("line_beyond_eof")


def test_entry_tiers(verify):
    class Entry:
        def __init__(self, body: str) -> None:
            self.body = body
            self.track = "MEMORY.md"

    assert verify.judge_entry(Entry("修正上一条关于端口的说法"), verify.extract_assertions)["tier"] == "T2"
    assert verify.judge_entry(Entry("一个没有路径也没有声明的判断"), verify.extract_assertions)["tier"] == "T3"


def test_report_counts_and_sampling_are_reproducible(verify, tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "EPHEMERAL_ROOTS", ())
    real = tmp_path / "present.md"
    real.write_text("x\n", encoding="utf-8")
    corpus = tmp_path / "corpus"
    write_track(
        corpus / "MEMORY.md",
        [
            f"[2026-01-01] 存在的文件在 {real} 这里，说一条足够长的正文",
            f"[2026-01-02] 已消失的文件在 {real.parent}/gone.md 这里，也要足够长",
            "[2026-01-03] 一个没有路径的主观判断，正文需要足够长以免被忽略",
            "[2026-01-04] 修正上一条关于端口 8080 的说法，同样需要足够长",
        ],
    )
    first = verify.build_report(corpus, "2026-W45")
    second = verify.build_report(corpus, "2026-W45")
    assert first["counts"]["confirmed"] == 1
    assert first["counts"]["refuted"] == 1
    assert first["counts"]["t2_claim"] == 1
    assert first["counts"]["t3_subjective"] == 1
    assert first["sample"]["seed"] == second["sample"]["seed"]
    assert [item["label"] for item in first["sample"]["unverifiable"]] == [
        item["label"] for item in second["sample"]["unverifiable"]
    ]
    assert first["sample"]["refuted"] == first["refuted"]  # refuted is not sampled

    other = verify.build_report(corpus, "2026-W46")
    assert other["sample"]["seed"] != first["sample"]["seed"]


def test_report_does_not_write_the_corpus(verify, tmp_path):
    corpus = tmp_path / "corpus"
    write_track(corpus / "MEMORY.md", ["[2026-01-01] 一条足够长的正文，没有路径"])
    before = (corpus / "MEMORY.md").read_bytes(), (corpus / "MEMORY.md").stat().st_mtime_ns
    verify.build_report(corpus, "2026-W45")
    after = (corpus / "MEMORY.md").read_bytes(), (corpus / "MEMORY.md").stat().st_mtime_ns
    assert before == after


def test_cli_exit_codes(verify, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(verify, "EPHEMERAL_ROOTS", ())
    corpus = tmp_path / "corpus"
    tree = tmp_path / "tree"
    tree.mkdir()
    write_track(corpus / "MEMORY.md", [f"[2026-01-01] 一条足够长的正文，提到 {tree}/gone.md 这个路径"])
    spec = f"dsh-md:{corpus}"

    assert verify.main(["report", "--corpus", spec, "--json"]) == verify.EXIT_OK
    capsys.readouterr()
    assert verify.main(["report", "--corpus", spec, "--fail-on", "refuted"]) == verify.EXIT_REFUTED
    assert verify.main(["report", "--corpus", "unknown:/tmp"]) == verify.EXIT_UNJUDGEABLE
    assert verify.main(["report", "--corpus", f"dsh-md:{tmp_path / 'missing'}"]) == verify.EXIT_UNJUDGEABLE


def test_json_report_is_the_contract(verify, tmp_path, capsys):
    corpus = tmp_path / "corpus"
    write_track(corpus / "MEMORY.md", ["[2026-01-01] 一条足够长的正文，没有路径"])
    assert verify.main(["report", "--corpus", f"dsh-md:{corpus}", "--json", "--week", "2026-W45"]) == verify.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["week"] == "2026-W45"
    assert set(payload["counts"]) >= {"entries", "confirmed", "refuted", "unknown", "unverifiable"}
    assert "seed" in payload["sample"]


def test_self_test_passes(verify):
    assert verify.self_test() == 0
