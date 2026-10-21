#!/usr/bin/env python3
"""Read-only audit of the shadow's contract facets: coverage *and its composition*.

Why this exists (B1 of ``docs/design/nowledge-replacement-batches.md``): a project
coverage number was quoted twice on 2026-10-21 with two different values (0.249 and
0.2089) because two code paths computed it differently. The fix is not a better
number, it is a number that always arrives with the reason each record has the
value it has — otherwise "coverage is low" cannot be acted on.

What it judges (``--strict``):
  * the derived projection is current (no ``unpopulated`` reason rows: that means
    the facets predate the reason column and need ``shadow reindex``) → exit 2,
    because "cannot judge" must never read as "fine";
  * every active record carries a known reason from ``shadow_registry.REASONS``;
  * the reason histogram sums to the active record count;
  * ``claim_source == 'conflict'`` is zero (a conflicting claim field is a human
    decision, not something to resolve by preference).

What it reports but does not fail on by default:
  * ``project_coverage`` vs ``--target`` — the target cannot be met with today's
    upstream labels (measured ceiling is reported next to it), so failing on it
    would train everyone to ignore the gate. Pass ``--fail-under-target`` to make
    it a hard gate once B3 closes the write path.

Exit codes: 0 = judged and consistent, 1 = a real inconsistency, 2 = cannot judge
(missing projection/columns/space).

Usage:
  python3 scripts/shadow_contract_audit.py
  python3 scripts/shadow_contract_audit.py --strict --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_tool.shadow import DEFAULT_DB, FACET_COLUMNS, coverage  # noqa: E402
from memory_tool.shadow_registry import REASONS, known_projects  # noqa: E402

EXIT_OK, EXIT_INCONSISTENT, EXIT_UNJUDGEABLE = 0, 1, 2


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open for reading without creating anything.

    Deliberately not ``sqlite3 -readonly``: that fails on a WAL database whose
    ``-shm`` is absent (measured 2026-10-08), and an audit that cannot open its
    subject is indistinguishable from one that found nothing.
    """
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def facet_columns(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(record_facets)")}


def audit(conn: sqlite3.Connection, space: str, target: float) -> dict:
    missing = sorted(set(FACET_COLUMNS) - facet_columns(conn))
    records = conn.execute(
        "SELECT count(*) FROM records WHERE space=? AND active=1", (space,)).fetchone()[0]
    if not records:
        return {"verdict": "unjudgeable", "why": "no active records in space %r" % space,
                "space": space, "missing_columns": missing}
    if missing:
        return {
            "verdict": "unjudgeable",
            "why": "record_facets lacks %s (run `los-memory shadow reindex`)" % ", ".join(missing),
            "space": space, "records": records, "missing_columns": missing,
        }
    facts = coverage(conn, space)
    checks = []

    unknown_reasons = sorted(set(facts["project_reason"]) - set(REASONS))
    checks.append({
        "name": "reasons-known",
        "ok": not unknown_reasons,
        "detail": "unknown reasons: %s" % unknown_reasons if unknown_reasons else "all reasons are declared",
    })

    total_reasons = sum(facts["project_reason"].values())
    checks.append({
        "name": "reasons-cover-records",
        "ok": total_reasons == facts["records"],
        "detail": "reason histogram sums to %d, active records %d"
                  % (total_reasons, facts["records"]),
    })

    checks.append({
        "name": "projection-current",
        "ok": facts["project_reason_stale"] == 0,
        "detail": "%d facet row(s) predate the reason column" % facts["project_reason_stale"],
    })

    checks.append({
        "name": "claim-no-conflict",
        "ok": facts["claim_conflicts"] == 0,
        "detail": "%d record(s) carry disagreeing claim_status fields" % facts["claim_conflicts"],
    })

    # A shadow with an empty registry cannot assign anything. Reporting 0% coverage
    # as if it were a property of the corpus would hide a configuration error.
    checks.append({
        "name": "registry-non-empty",
        "ok": bool(known_projects()),
        "detail": "registry declares %d project(s)" % len(known_projects()),
    })

    under_target = facts["project_coverage"] < target
    checks.append({
        "name": "coverage-target",
        "ok": not under_target,
        "gating": False,
        "detail": "coverage %.4f vs target %.4f (ceiling with today's labels is %.4f)"
                  % (facts["project_coverage"], target, facts["coverage_ceiling"]),
    })

    gating_failed = [c for c in checks if not c["ok"] and c.get("gating", True)]
    if facts["project_reason_stale"]:
        verdict = "unjudgeable"
    elif gating_failed:
        verdict = "inconsistent"
    else:
        verdict = "ok"
    return {
        "verdict": verdict,
        "space": space,
        "records": facts["records"],
        "target": target,
        "coverage": facts["project_coverage"],
        "ceiling": facts["coverage_ceiling"],
        "project_reason": facts["project_reason"],
        "project_reason_stale": facts["project_reason_stale"],
        "claim_source": facts["claim_source"],
        "claim_conflicts": facts["claim_conflicts"],
        "claim_undeclared": facts["claim_undeclared"],
        "source_thread_refs": facts["source_thread_refs"],
        "source_grounding_refs": facts["source_grounding_refs"],
        "project_multi": facts["project_multi"],
        "unclassified_labels": facts["unclassified_labels"],
        "checks": checks,
    }


def render(report: dict) -> str:
    if report["verdict"] == "unjudgeable":
        return "shadow-contract: UNJUDGEABLE — %s" % report["why"]
    lines = [
        "shadow-contract: %s" % report["verdict"].upper(),
        "  records         : %d" % report["records"],
        "  project coverage: %.4f  (ceiling %d%% with today's labels: %.4f)"
        % (report["coverage"], round(report["ceiling"] * 100), report["ceiling"]),
        "  reasons         : " + ", ".join(
            "%s=%d" % (name, count) for name, count in
            sorted(report["project_reason"].items(), key=lambda item: -item[1])),
        "  multi-project   : %d" % report["project_multi"],
        "  claim sources   : " + ", ".join(
            "%s=%d" % (name, count) for name, count in
            sorted(report["claim_source"].items(), key=lambda item: -item[1])),
        "  refs            : thread=%d grounding=%d"
        % (report["source_thread_refs"], report["source_grounding_refs"]),
    ]
    if report["unclassified_labels"]:
        lines.append("  unclassified labels (top): " + ", ".join(
            "%s×%d" % (label, count) for label, count in report["unclassified_labels"][:8]))
    for check in report["checks"]:
        mark = "ok" if check["ok"] else ("FAIL" if check.get("gating", True) else "info")
        lines.append("  [%-4s] %-22s %s" % (mark, check["name"], check["detail"]))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--space", default="default")
    parser.add_argument("--target", type=float, default=0.80)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--fail-under-target", action="store_true",
                        help="treat coverage < target as a failure (off by default: the "
                             "ceiling with today's labels is below the target)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.db).expanduser()
    if not path.exists():
        print("shadow-contract: UNJUDGEABLE — no database at %s" % path, file=sys.stderr)
        return EXIT_UNJUDGEABLE
    conn = open_readonly(path)
    try:
        report = audit(conn, args.space, args.target)
    finally:
        conn.close()

    if args.fail_under_target and report.get("verdict") != "unjudgeable":
        for check in report["checks"]:
            if check["name"] == "coverage-target" and not check["ok"]:
                check["gating"] = True
        if any(not c["ok"] and c.get("gating", True) for c in report["checks"]):
            report["verdict"] = "inconsistent"

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))

    if not args.strict:
        return EXIT_OK
    return {"ok": EXIT_OK, "inconsistent": EXIT_INCONSISTENT,
            "unjudgeable": EXIT_UNJUDGEABLE}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
