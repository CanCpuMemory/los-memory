"""Contract-facet tests for the shadow (B1).

The properties being pinned, each with the failure it prevents:

* every active record gets exactly one known *reason*, and the histogram sums to
  the record count — otherwise a coverage number cannot be attributed;
* `multi` is a distinct state, never counted as assigned — resolving it by label
  order is guessing;
* an inactive record never enters the denominator;
* claim status prefers the record field, falls back to metadata, and a
  disagreement is recorded as a conflict instead of being resolved silently;
* removing the registry lowers coverage (proved by reindexing the same rows) and
  makes the audit fail — this is the negative control for "coverage is computed
  from a declared registry, not from the data's shape";
* a record whose text *mentions* a project directory but carries no registered
  label stays unassigned — the negative control for "no inference from paths".
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_tool import shadow  # noqa: E402
from memory_tool import shadow_registry  # noqa: E402


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = _load("shadow_contract_audit", "scripts/shadow_contract_audit.py")


def record(rid: str, labels=None, *, claim=None, meta_claim=None, active=True,
           thread=None, grounding=None) -> dict:
    metadata = {}
    if meta_claim is not None:
        metadata["claim_status"] = meta_claim
    if grounding is not None:
        metadata["source_grounding"] = grounding
    if thread is not None:
        metadata["source_thread_id"] = thread
    if labels is not None:
        metadata["label_ids"] = labels
    return {
        "id": rid,
        "space_id": "default",
        "title": "t-" + rid,
        "content": "c-" + rid,
        "lifecycle_state": "active" if active else "deleted",
        "is_latest": True,
        "unit_type": "learning",
        "claim_status": claim,
        "label_ids": labels or [],
        "metadata": metadata,
        "source_thread": {"id": "thread-1"} if thread else None,
        "source_grounding": grounding,
    }


def build(tmp_path, rows) -> "sqlite3.Connection":  # noqa: F821
    conn = shadow.connect(tmp_path / "shadow.sqlite3")
    for row in rows:
        shadow.put(conn, "default", row)
    return conn


def test_every_state_gets_one_reason(tmp_path):
    conn = build(tmp_path, [
        record("a", ["label_cantool"]),
        record("b", ["label_cantool", "label_lot2extension"]),
        record("c", ["label_daily"]),
        record("d", ["label_key-0123456789ab"]),
        record("e", []),
        record("f", ["label_popup"]),
    ])
    facts = shadow.coverage(conn, "default")
    assert facts["records"] == 6
    assert facts["project_reason"] == {
        "assigned": 1, "multi": 1, "policy_excluded": 1,
        "key_label_unmapped": 1, "no_labels": 1, "unclassified": 1,
    }
    assert sum(facts["project_reason"].values()) == facts["records"]
    assert facts["project_assigned"] == 1
    assert facts["project_multi"] == 1
    assert facts["project_unassigned"] == 4
    assert facts["project_coverage"] == round(1 / 6, 4)


def test_multi_is_not_counted_as_assigned_and_sets_the_ceiling(tmp_path):
    conn = build(tmp_path, [
        record("a", ["label_cantool"]),
        record("b", ["label_cantool", "label_lot2extension"]),
        record("c", ["label_key-0123456789ab"]),
    ])
    facts = shadow.coverage(conn, "default")
    assert facts["project_assigned"] == 1          # not 2
    # nameable today = assigned + multi + key_label_unmapped
    assert facts["coverage_ceiling"] == 1.0
    assert facts["project_coverage"] == round(1 / 3, 4)


def test_inactive_records_leave_the_denominator(tmp_path):
    conn = build(tmp_path, [record("a", ["label_cantool"]), record("b", ["label_cantool"])])
    shadow.mark_missing(conn, "default", "b")
    facts = shadow.coverage(conn, "default")
    assert facts["records"] == 1
    assert facts["project_coverage"] == 1.0


def test_claim_source_precedence_and_conflict(tmp_path):
    conn = build(tmp_path, [
        record("a", claim="asserted"),
        record("b", meta_claim="proposed"),
        record("c", claim="asserted", meta_claim="proposed"),
        record("d"),
    ])
    facts = shadow.coverage(conn, "default")
    assert facts["claim_source"] == {"record": 1, "metadata": 1, "conflict": 1, "undeclared": 1}
    assert facts["claim_conflicts"] == 1
    assert facts["claim_undeclared"] == 1


def test_reference_passthrough_counts(tmp_path):
    conn = build(tmp_path, [
        record("a", thread="thread-1", grounding="quoted evidence"),
        record("b"),
    ])
    facts = shadow.coverage(conn, "default")
    assert facts["source_thread_refs"] == 1
    assert facts["source_grounding_refs"] == 1


def test_mentioning_a_project_does_not_assign_it(tmp_path):
    """Negative control: text that names a project must not become a project."""
    tricky = record("x", ["label_daily"])
    tricky["content"] = "cwd=/Users/example/projects/cantool and repo=cankey"
    tricky["title"] = "cantool release"
    conn = build(tmp_path, [tricky])
    facets = conn.execute("SELECT project, reason FROM record_facets WHERE source_id='x'").fetchone()
    assert facets["project"] == "unassigned"
    assert facets["reason"] == "policy_excluded"


def test_removing_the_registry_lowers_coverage_and_fails_the_audit(tmp_path, monkeypatch):
    conn = build(tmp_path, [record("a", ["label_cantool"])])
    assert shadow.coverage(conn, "default")["project_coverage"] == 1.0

    monkeypatch.setattr(shadow_registry, "PROJECT_LABELS", {})
    monkeypatch.setattr(shadow_registry, "PROJECT_LABEL_ALIASES", {})
    monkeypatch.setattr(shadow_registry, "KEY_LABEL_PROJECTS", {})
    shadow.reindex(conn, "default")

    facts = shadow.coverage(conn, "default")
    assert facts["project_coverage"] == 0.0
    report = AUDIT.audit(conn, "default", target=0.8)
    assert report["verdict"] == "inconsistent"
    failed = {check["name"] for check in report["checks"]
              if not check["ok"] and check.get("gating", True)}
    assert "registry-non-empty" in failed


def test_audit_is_unjudgeable_on_a_pre_b1_projection(tmp_path):
    conn = build(tmp_path, [record("a", ["label_cantool"])])
    conn.execute("ALTER TABLE record_facets DROP COLUMN reason")
    report = AUDIT.audit(conn, "default", target=0.8)
    assert report["verdict"] == "unjudgeable"
    assert "reason" in report["missing_columns"]


def test_coverage_has_one_definition(tmp_path):
    """`shadow_eval` must report the same number as `shadow.coverage`."""
    conn = build(tmp_path, [
        record("a", ["label_cantool"]),
        record("b", ["label_daily"]),
        record("c", ["label_key-0123456789ab"]),
    ])
    shadow.mark_missing(conn, "default", "b")  # inactive rows must not shift the ratio
    eval_module = _load("shadow_eval", "scripts/shadow_eval.py")
    facts = eval_module.shadow_facts(conn, tmp_path / "shadow.sqlite3")
    assert facts["project_coverage"] == shadow.coverage(conn, "default")["project_coverage"]
    assert facts["coverage_ceiling"] == shadow.coverage(conn, "default")["coverage_ceiling"]
