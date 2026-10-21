"""Regression tests for the eval human-retrievability check.

The instrument exists because the P0 corpus read ``semantic_paraphrase = 0.000``
on both backends and that number was mistaken for a capability verdict.  Two
properties have to hold or the instrument is worse than no instrument:

* a record that does not read back must be reported ``record_missing`` (and fail);
* a case with no expected record (the ``no_answer`` category) must not fail.

Everything here is offline: the primary is never contacted.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "eval_human_check.py"

spec = importlib.util.spec_from_file_location("eval_human_check", SCRIPT_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_rank_lookup_reports_absence() -> None:
    assert module.find_rank(["a", "b"], "b") == 2
    assert module.find_rank(["a", "b"], "c") is None
    assert module.find_rank([], "a") is None


def test_verdict_separates_missing_record_from_low_rank() -> None:
    assert module.verdict(1, readable=True) == "anchor_found_top5"
    assert module.verdict(9, readable=True) == "anchor_ranked_low"
    assert module.verdict(None, readable=True) == "anchor_absent"
    # A record that cannot be read is a corpus defect, whatever its rank says.
    assert module.verdict(1, readable=False) == "record_missing"
    assert module.verdict(None, readable=False) == "record_missing"


def test_readable_accepts_both_envelopes() -> None:
    """`nmem memories show -j` returns the memory at top level; other callers wrap it."""
    assert module._readable_from({"id": "a", "title": "t", "content": "c"}) is True
    assert module._readable_from({"id": "a", "title": "t"}) is True
    assert module._readable_from({"memory": {"id": "a", "content": "c"}}) is True
    assert module._readable_from({"memory": {}}) is False
    assert module._readable_from({"error": "not found"}) is False
    assert module._readable_from(None) is False


def test_anchor_lookup_requires_a_non_empty_anchor_for_that_record() -> None:
    case = {"support": [{"source_id": "x", "anchor": "  "}, {"source_id": "y", "anchor": "ABC"}]}
    assert module.anchor_for(case, "y") == "ABC"
    assert module.anchor_for(case, "x") is None
    assert module.anchor_for(case, "z") is None


def test_summary_does_not_fail_a_case_without_expectations() -> None:
    """The `no_answer` category has no expected record by design."""
    results = [
        {"id": "c01", "category": "exact_identifier",
         "checks": [{"source_id": "s", "anchor": "a", "rank": 1, "readable": True,
                     "verdict": "anchor_found_top5"}]},
        {"id": "c36", "category": "no_answer", "checks": []},
    ]
    summary = module.summarise(results)
    assert summary["no_expectation"] == 1
    assert summary["failures"] == []
    assert summary["expectations"] == 1


def test_summary_fails_on_missing_record() -> None:
    results = [
        {"id": "nc01", "category": "semantic_paraphrase",
         "checks": [{"source_id": "s", "anchor": "a", "rank": None, "readable": False,
                     "verdict": "record_missing"}]},
    ]
    summary = module.summarise(results)
    assert summary["record_missing"] == 1
    assert summary["failures"], "a missing record must fail the gate"


def test_self_test_runs_offline() -> None:
    assert module.self_test() == 0
