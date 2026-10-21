#!/usr/bin/env python3
"""Prove what is actually retrievable before scoring a backend, and record the primary's limit.

Why this exists (2026-10-21, B0 of ``docs/design/nowledge-replacement-batches.md``):
the P0 corpus reported ``semantic_paraphrase`` = 0.000 on both backends, and that
number had been read as a capability verdict.  Two questions have to be separated
before any such number means anything:

  1. Does the record the case expects still exist and read back?  (``memories show``)
  2. Can its own distinctive phrase (``support[].anchor``) surface it?  (ranked search)

A case with a missing record is a corpus defect.  A case whose own anchor ranks
low is an honest-hard case and says something about the backend's ranked layer.

Measured on 2026-10-21 against this primary:
  * ``memories search`` never returns an empty set — a nonsense anchor still
    yields five hits, so "empty result" is not a usable control;
  * 3 of the 5 semantic anchors (``SIGKILL``, ``真实运行陷阱``,
    ``固定外部观察输入``) do not surface their record inside top-20 even though
    ``memories show`` reads that record fine.

Constraints: read-only, stdlib only, prints no record bodies, never writes to the
shadow or the primary.  ``--self-test`` proves the rank/verdict logic can report
absence without touching the network.

Usage:
  python3 scripts/eval_human_check.py --category semantic_paraphrase --out <path>
  python3 scripts/eval_human_check.py --category semantic_paraphrase --negative-control
  python3 scripts/eval_human_check.py --self-test
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CASES = Path.home() / ".local/share/los-memory-shadow/eval/cases.jsonl"
DEFAULT_NOWLEDGE = "nmem"

# Verdict vocabulary.  Only ``record_missing`` and ``no_anchor`` fail the gate;
# the rest are findings about ranking that the report must keep visible.
VERDICTS = ("found", "anchor_found_top5", "anchor_ranked_low", "anchor_absent",
            "record_missing", "no_anchor", "no_expectation")


def load_cases(path: Path, categories: list[str]) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        if categories and case.get("category") not in categories:
            continue
        rows.append(case)
    return rows


def anchor_for(case: dict, source_id: str) -> str | None:
    for support in case.get("support", []):
        if support.get("source_id") == source_id and support.get("anchor"):
            return str(support["anchor"]).strip() or None
    return None


def run_json(cmd: list[str], timeout: int) -> dict:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("exit %s: %s" % (proc.returncode, (proc.stderr or proc.stdout)[:200]))
    return json.loads(proc.stdout)


def _readable_from(payload: object) -> bool:
    """Pure shape check, so the self-test can cover both envelopes without network."""
    if not isinstance(payload, dict) or payload.get("error"):
        return False
    memory = payload.get("memory") if isinstance(payload.get("memory"), dict) else payload
    return bool(memory.get("content") or memory.get("title"))


def record_readable(tool: str, source_id: str, timeout: int) -> bool:
    try:
        payload = run_json([tool, "memories", "show", source_id, "-j"], timeout)
    except Exception:
        return False
    if isinstance(payload, dict) and payload.get("error"):
        return False
    # 2026-10-21: `nmem memories show -j` returns the memory object at the top
    # level on this backend (no "memory" wrapper). Requiring the wrapper made
    # every record look missing while the searches still ran.
    return _readable_from(payload)


def query_ids(tool: str, query: str, limit: int, timeout: int) -> list[str]:
    payload = run_json([tool, "memories", "search", query, "-n", str(limit), "-j"], timeout)
    memories = payload.get("memories")
    if memories is None:
        raise RuntimeError("unexpected JSON shape: %s" % list(payload)[:8])
    return [item.get("id") for item in memories]


def find_rank(ids: list[str], target: str) -> int | None:
    for index, value in enumerate(ids, 1):
        if value == target:
            return index
    return None


def verdict(rank: int | None, readable: bool, top: int = 5) -> str:
    if not readable:
        return "record_missing"
    if rank is None:
        return "anchor_absent"
    return "anchor_found_top5" if rank <= top else "anchor_ranked_low"


def check_case(case: dict, tool: str, depth: int, timeout: int, search: bool = True) -> dict:
    checks = []
    for expected in case.get("expected", []):
        source_id = expected.get("source_id")
        if not source_id or int(expected.get("relevance", 0)) < 2:
            continue
        readable = record_readable(tool, source_id, timeout)
        anchor = anchor_for(case, source_id)
        if not anchor:
            checks.append({"source_id": source_id, "anchor": None, "rank": None,
                           "readable": readable, "verdict": "no_anchor"})
            continue
        if not search:
            checks.append({"source_id": source_id, "anchor": anchor, "rank": None,
                           "readable": readable, "verdict": "found" if readable else "record_missing"})
            continue
        ids = query_ids(tool, anchor, depth, timeout)
        rank = find_rank(ids, source_id)
        checks.append({"source_id": source_id, "anchor": anchor, "rank": rank,
                       "readable": readable, "verdict": verdict(rank, readable)})
    return {"id": case["id"], "category": case["category"], "checks": checks}


def summarise(results: list[dict]) -> dict:
    counts = {name: 0 for name in VERDICTS}
    failures = []
    for case in results:
        if not case["checks"]:
            # The `no_answer` category has no expected record by design; that is
            # not a defect of the case, so it must not fail the gate.
            counts["no_expectation"] += 1
            continue
        for check in case["checks"]:
            counts[check["verdict"]] += 1
            if check["verdict"] in ("record_missing", "no_anchor"):
                failures.append({"case": case["id"], "source_id": check["source_id"],
                                 "reason": check["verdict"]})
    counts["failures"] = failures
    counts["expectations"] = sum(
        counts[name] for name in VERDICTS if name != "no_expectation"
    )
    return counts


def self_test() -> int:
    assert find_rank(["a", "b"], "b") == 2
    assert find_rank(["a", "b"], "c") is None
    assert verdict(1, True) == "anchor_found_top5"
    assert verdict(9, True) == "anchor_ranked_low"
    assert verdict(None, True) == "anchor_absent"
    assert verdict(None, False) == "record_missing"
    assert anchor_for({"support": [{"source_id": "x", "anchor": "  "}]}, "x") is None
    assert anchor_for({"support": []}, "y") is None
    # Both envelopes must be accepted: top-level (this backend) and wrapped.
    assert _readable_from({"id": "a", "title": "t", "content": "c"}) is True
    assert _readable_from({"memory": {"id": "a", "title": "t"}}) is True
    assert _readable_from({"memory": {}}) is False
    assert _readable_from({"error": "not found"}) is False
    print("self-test ok: rank, verdict and anchor lookup can all report absence")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--category", action="append", default=None)
    parser.add_argument("--nowledge-cmd", default=DEFAULT_NOWLEDGE)
    parser.add_argument("--depth", type=int, default=20, help="how deep to look for the anchor")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--out", default=None)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--show-only", action="store_true",
                        help="only prove the expected records read back; skip ranked probes")
    parser.add_argument("--negative-control", action="store_true",
                        help="query an anchor that cannot exist; record whether the backend "
                             "returns hits and whether any expected id appears")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    cases = load_cases(Path(args.cases).expanduser(), args.category or [])
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2

    results = [
        check_case(case, args.nowledge_cmd, args.depth, args.timeout, not args.show_only)
        for case in cases
    ]
    summary = summarise(results)

    negative = None
    if args.negative_control:
        bogus = "zzz-anchor-that-cannot-exist-20261021"
        ids = query_ids(args.nowledge_cmd, bogus, 5, args.timeout)
        expected_ids = {e["source_id"] for case in cases for e in case.get("expected", [])}
        leaked = [i for i in ids if i in expected_ids]
        negative = {
            "query": bogus,
            "hit_count": len(ids),
            "expected_id_leaked": leaked,
            "note": ("no_relevance_floor: hits are returned for a nonsense query"
                     if ids else "backend returns an empty set for nonsense"),
            "ok": not leaked,
        }

    report = {
        "harness": "scripts/eval_human_check.py",
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases_path": str(Path(args.cases).expanduser()),
        "categories": args.category or "all",
        "depth": args.depth,
        "summary": summary,
        "negative": negative,
        "cases": results,
    }
    if args.out:
        target = Path(args.out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("human-check: %d expectation(s)" % summary["expectations"])
    for name in VERDICTS:
        if summary[name]:
            print("  %-18s %d" % (name, summary[name]))
    for failure in summary["failures"]:
        print("  FAIL %s: %s" % (failure.get("case"), failure.get("reason")))
    if negative:
        print("  negative-control: hits=%d leaked=%d (%s)"
              % (negative["hit_count"], len(negative["expected_id_leaked"]), negative["note"]))

    failed = bool(summary["failures"]) or summary["expectations"] == 0
    if negative and not negative["ok"]:
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
