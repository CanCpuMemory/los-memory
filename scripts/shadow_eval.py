#!/usr/bin/env python3
"""P0 retrieval eval harness: Nowledge shadow vs. the Nowledge primary store.

This is the measurement instrument for the "shadow replaces Nowledge" roadmap.
It answers one narrow question with the same query set against both backends:

    given 40 hand-authored retrieval questions over the real mirrored corpus,
    what does each backend put in its top-K, and is that the right thing?

Design constraints it deliberately honours:

* Read-only.  The shadow database is opened ``mode=ro``; the M3 copy is only
  ever reached through ``--refresh-shadow``, which uses SQLite's online backup
  API to produce a torn-free snapshot and deletes the remote temp file.
* No invented numbers.  ``version_correctness`` is reported as
  ``N/A (no revision in shadow API)`` because the shadow record envelope has no
  revision concept.  Nothing here fabricates a metric the data cannot support.
* Two backends, one query set.  Each case is issued verbatim to both backends;
  ``--scope native`` additionally applies each backend's own scoping filter so
  the difference between "no scoping" and "native scoping" is measurable.
* Frozen baselines.  ``--freeze`` writes the Nowledge rankings once; later runs
  read them and never silently rewrite them.

Stdlib only.  See ``docs/reports/2026-10-07-eval-baseline.md`` for the run this
produced and the honest limits of the numbers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_tool.shadow import search_detailed  # noqa: E402  (needs sys.path first)

# Private corpus lives outside the repository on purpose: the case set quotes
# real internal records, so it must never be committed.
DEFAULT_EVAL_DIR = Path.home() / ".local/share/los-memory-shadow/eval"
DEFAULT_CASES = DEFAULT_EVAL_DIR / "cases.jsonl"
DEFAULT_SHADOW_DB = DEFAULT_EVAL_DIR / "snap.sqlite3"
DEFAULT_M3_HOST = "m3-t"
DEFAULT_M3_SHADOW = "~/.local/share/los-memory-shadow/shadow.sqlite3"
DEFAULT_NOWLEDGE = "nmem"

CATEGORIES = [
    "exact_identifier",
    "chinese_short",
    "semantic_paraphrase",
    "project_isolation",
    "fact_revision",
    "relational_multihop",
    "cross_tool_continuation",
    "no_answer",
]

# The shadow API is a mirror of records, not of a versioned edit history, so
# there is nothing to score here.  Reported verbatim instead of guesswork.
VERSION_CORRECTNESS = "N/A (no revision in shadow API)"

HIT_DEPTH = 5
NDCG_DEPTH = 10

# Depth at which forbidden ids are counted.  Deliberately the same K the caller
# asked for, so "violations" means "surfaced in the working set".
FORBIDDEN_DEPTH = None  # resolved to --limit at runtime


class CaseError(ValueError):
    """A case file that cannot be scored honestly."""


# --------------------------------------------------------------------------
# cases
# --------------------------------------------------------------------------


def load_cases(path, limit=None):
    """Read JSON Lines cases, validate the schema, return a list of dicts."""
    path = Path(path).expanduser()
    if not path.exists():
        raise CaseError(
            "case file not found: %s\n"
            "The case corpus is private and is not in the repository. "
            "Author it at %s (see the report's method section)."
            % (path, DEFAULT_CASES)
        )
    cases = []
    seen = set()
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CaseError("%s:%d is not valid JSON: %s" % (path, lineno, exc))
            for field in ("id", "category", "query", "expected", "forbidden"):
                if field not in case:
                    raise CaseError("%s:%d missing %r" % (path, lineno, field))
            if case["category"] not in CATEGORIES:
                raise CaseError(
                    "%s:%d unknown category %r (expected one of %s)"
                    % (path, lineno, case["category"], ", ".join(CATEGORIES))
                )
            if case["id"] in seen:
                raise CaseError("%s:%d duplicate case id %r" % (path, lineno, case["id"]))
            seen.add(case["id"])
            for item in case["expected"]:
                if int(item.get("relevance", 0)) not in (1, 2):
                    raise CaseError(
                        "%s:%d expected relevance must be 1 or 2, got %r"
                        % (path, lineno, item.get("relevance"))
                    )
            if not isinstance(case["forbidden"], list):
                raise CaseError("%s:%d forbidden must be a list" % (path, lineno))
            case.setdefault("scope", {})
            case.setdefault("support", [])
            case.setdefault("notes", "")
            case.setdefault("provenance", "corpus-derived")
            cases.append(case)
    if limit is not None:
        cases = cases[:limit]
    return cases


def verify_case_grounding(cases, conn):
    """Every referenced source_id must really exist in the shadow snapshot.

    This is the guard against a fabricated expectation: a case that names an id
    the corpus does not contain is a broken case, not a finding, so it fails
    loudly before any metric is computed.
    """
    missing = []
    for case in cases:
        refs = [item["source_id"] for item in case["expected"]]
        refs += [item["source_id"] for item in case["forbidden"]]
        for source_id in refs:
            row = conn.execute(
                "SELECT 1 FROM records WHERE space=? AND source_id=? AND active=1",
                (case["scope"].get("space", "default"), source_id),
            ).fetchone()
            if row is None:
                missing.append((case["id"], source_id))
    return missing


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------


def open_shadow(path):
    """Open the shadow snapshot read-only.  Never opens the live M3 database."""
    path = Path(path).expanduser()
    if not path.exists():
        raise CaseError("shadow database not found: %s (try --refresh-shadow)" % path)
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def shadow_search(conn, case, limit, scope_mode):
    """Return [(rank, id, score)] from the shadow substring+filter search."""
    space = case["scope"].get("space", "default")
    project = case["scope"].get("project") if scope_mode == "native" else None
    kind = case["scope"].get("kind") if scope_mode == "native" else None
    results, meta = search_detailed(
        conn,
        case["query"],
        limit=min(limit, 50),
        project=project,
        kind=kind,
        space=space,
    )
    ranked = []
    for rank, item in enumerate(results, 1):
        # The shadow ranks by (title-term hits, verified_at); it has no
        # relevance score, so score stays null rather than being invented.
        ranked.append({"rank": rank, "id": item["source_id"], "score": None})
    return ranked, meta


class NowledgeError(RuntimeError):
    pass


def nowledge_search(case, limit, scope_mode, tool, timeout, extra=()):
    """Return [(rank, id, score)] from `nmem memories search -j`."""
    query = case["query"]
    project = case["scope"].get("project") if scope_mode == "native" else None
    cmd = [tool, "memories", "search", query, "-n", str(limit), "-j"]
    if project:
        # Nowledge has no project concept; its nearest equivalent is the
        # project label, which the shadow registry maps 1:1.
        cmd += ["-l", project]
    cmd += list(extra)
    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env
        )
    except FileNotFoundError as exc:
        raise NowledgeError("%s not found on PATH" % tool) from exc
    except subprocess.TimeoutExpired as exc:
        raise NowledgeError("timeout after %ss" % timeout) from exc
    if proc.returncode != 0:
        raise NowledgeError(
            "exit %s: %s" % (proc.returncode, (proc.stderr or proc.stdout).strip()[:300])
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise NowledgeError("non-JSON output: %s" % proc.stdout[:200]) from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise NowledgeError("%s: %s" % (payload.get("error"), payload.get("message")))
    memories = payload.get("memories") if isinstance(payload, dict) else None
    if memories is None:
        raise NowledgeError("unexpected JSON shape: %s" % list(payload)[:8])
    ranked = []
    for rank, item in enumerate(memories, 1):
        ranked.append(
            {
                "rank": rank,
                "id": item.get("id"),
                "score": item.get("score"),
                "title": item.get("title"),
                "unit_type": item.get("unit_type"),
                "source": item.get("source"),
            }
        )
    return ranked


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def dcg(gains):
    return sum(gain / math.log2(idx + 1) for idx, gain in enumerate(gains, 1))


def score_case(case, ranked, depth):
    """Per-case retrieval metrics.  Returns a dict; None means 'not applicable'."""
    expected = {item["source_id"]: int(item["relevance"]) for item in case["expected"]}
    forbidden = {item["source_id"]: item.get("reason", "") for item in case["forbidden"]}
    ids = [entry["id"] for entry in ranked]

    top5 = ids[:HIT_DEPTH]
    top_ndcg = ids[: min(NDCG_DEPTH, depth)]
    top_forbidden = ids[: (FORBIDDEN_DEPTH or depth)]

    relevant = {sid for sid, rel in expected.items() if rel >= 1}
    hits_top5 = [sid for sid in top5 if sid in relevant]
    forbidden_hits = [
        {"rank": ids.index(sid) + 1, "id": sid, "reason": forbidden.get(sid, "")}
        for sid in top_forbidden
        if sid in forbidden
    ]
    expected_ranks = {
        sid: (ids.index(sid) + 1 if sid in ids else None) for sid in expected
    }

    if expected:
        gains = [expected.get(sid, 0) and (2 ** expected[sid] - 1) for sid in top_ndcg]
        gains = [g or 0 for g in gains]
        # The ideal ranking is a property of the case, not of what came back, so
        # an empty result set scores 0.0 instead of becoming undefined.
        ideal = sorted((2 ** rel - 1 for rel in expected.values()), reverse=True)
        idcg = dcg(ideal[:NDCG_DEPTH])
        ndcg = (dcg(gains) / idcg) if idcg else None
        hit5 = 1 if hits_top5 else 0
        recall5 = len(hits_top5) / len(relevant) if relevant else None
    else:
        # Category 8 is deliberately unanswerable: there is no winning record to
        # find, so Hit/Recall/nDCG are undefined rather than zero.
        ndcg = hit5 = recall5 = None

    return {
        "hit@5": hit5,
        "recall@5": recall5,
        "ndcg@10": ndcg,
        "ndcg_depth": len(top_ndcg),
        "expected_ranks": expected_ranks,
        "relevant_found@5": hits_top5,
        "forbidden_hits": forbidden_hits,
        "forbidden_hits@5": [h for h in forbidden_hits if h["rank"] <= HIT_DEPTH],
        "returned": len(ids),
    }


def aggregate(per_case):
    """Macro-average the defined metrics and count failures."""
    out = {"cases": len(per_case), "scored": 0, "errors": 0}
    for key in ("hit@5", "recall@5", "ndcg@10"):
        values = [
            c["metrics"][key]
            for c in per_case
            if c["metrics"].get(key) is not None and not c.get("error")
        ]
        out[key] = (sum(values) / len(values)) if values else None
        out[key + "_n"] = len(values)
    out["scored"] = out["hit@5_n"]
    out["errors"] = sum(1 for c in per_case if c.get("error"))
    out["forbidden_hits"] = sum(len(c["metrics"]["forbidden_hits"]) for c in per_case)
    out["forbidden_hits@5"] = sum(
        len(c["metrics"]["forbidden_hits@5"]) for c in per_case
    )
    out["isolation_violations"] = sum(
        len(c["metrics"]["forbidden_hits"])
        for c in per_case
        if c["category"] == "project_isolation"
    )
    out["isolation_violations@5"] = sum(
        len(c["metrics"]["forbidden_hits@5"])
        for c in per_case
        if c["category"] == "project_isolation"
    )
    out["isolation_violation_cases"] = [
        c["id"]
        for c in per_case
        if c["category"] == "project_isolation" and c["metrics"]["forbidden_hits"]
    ]
    return out


def group_by_category(per_case):
    groups = {}
    for category in CATEGORIES:
        subset = [c for c in per_case if c["category"] == category]
        if subset:
            groups[category] = aggregate(subset)
    return groups


# --------------------------------------------------------------------------
# snapshot refresh
# --------------------------------------------------------------------------

SNAPSHOT_PY = """\
import os, sqlite3, time
t = time.time()
s = sqlite3.connect("file:%s?mode=ro" % os.path.expanduser(os.environ["SNAP_SRC"]), uri=True)
d = sqlite3.connect(os.environ["SNAP_DST"]); s.backup(d); d.close(); s.close()
print("snapshot_seconds %.2f" % (time.time() - t))
"""


def refresh_shadow(host, source, dest, remote_tmp="/tmp/los-memory-shadow-snap.sqlite3"):
    """Take a torn-free snapshot on the remote host and copy it back.

    The live shadow database runs in WAL mode, so copying the main file would
    tear.  SQLite's online backup API gives a consistent single file; the remote
    temp file is removed on every exit path.
    """
    dest = Path(dest).expanduser()
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    script = SNAPSHOT_PY % source
    env_prefix = 'SNAP_SRC=%s SNAP_DST=%s ' % (source, remote_tmp)
    try:
        proc = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", host, env_prefix + "python3 -"],
            input=script,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise CaseError(
                "remote snapshot failed: %s" % (proc.stderr.strip()[:400] or proc.returncode)
            )
        with tempfile.NamedTemporaryFile(
            dir=str(dest.parent), prefix=".snap-", suffix=".sqlite3", delete=False
        ) as fh:
            tmp = Path(fh.name)
        try:
            copy = subprocess.run(
                ["scp", "-q", "%s:%s" % (host, remote_tmp), str(tmp)],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if copy.returncode != 0:
                raise CaseError("scp failed: %s" % copy.stderr.strip()[:400])
            os.chmod(tmp, 0o600)
            os.replace(tmp, dest)  # atomic: readers never see a partial file
        finally:
            if tmp.exists():
                tmp.unlink()
    finally:
        subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", host, "rm -f %s" % remote_tmp],
            capture_output=True,
            text=True,
            timeout=120,
        )
    return proc.stdout.strip()


# --------------------------------------------------------------------------
# corpus facts and baselines
# --------------------------------------------------------------------------


def shadow_facts(conn, path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    active = conn.execute(
        "SELECT COUNT(*) FROM records WHERE space='default' AND active=1"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    facets = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT project, COUNT(*) FROM record_facets GROUP BY project ORDER BY 2 DESC"
        )
    }
    kinds = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT kind, COUNT(*) FROM record_facets GROUP BY kind ORDER BY 2 DESC"
        )
    }
    state = {}
    row = conn.execute("SELECT value FROM state WHERE key='sync:default'").fetchone()
    if row:
        try:
            state = json.loads(row[0])
        except json.JSONDecodeError:
            state = {"raw": row[0][:200]}
    return {
        "path": str(path),
        "sha256": digest.hexdigest(),
        "size_bytes": path.stat().st_size,
        "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "records_active": active,
        "records_total": total,
        "projects": facets,
        "kinds": kinds,
        "project_coverage": (
            round(1 - facets.get("unassigned", 0) / active, 4) if active else None
        ),
        "last_sync": state,
    }


def nowledge_facts(tool, timeout=60):
    """Record how the Nowledge backend was reached, from its own CLI."""
    facts = {"tool": shutil.which(tool) or tool}
    try:
        proc = subprocess.run(
            [tool, "status", "-j"], capture_output=True, text=True, timeout=timeout
        )
        facts["status"] = json.loads(proc.stdout) if proc.stdout.strip() else None
    except Exception as exc:  # status is evidence, never a hard requirement
        facts["status_error"] = "%s: %s" % (type(exc).__name__, exc)
    config = Path.home() / ".nowledge-mem/config.json"
    if config.exists():
        try:
            cfg = json.loads(config.read_text())
            facts["api_url"] = cfg.get("apiUrl")
            facts["api_key_present"] = bool(cfg.get("apiKey"))
        except json.JSONDecodeError:
            facts["config_error"] = "unreadable config.json"
    return facts


def baseline_path_for(date, directory=DEFAULT_EVAL_DIR):
    return Path(directory) / ("baseline-%s.json" % date)


def newest_baseline(directory=DEFAULT_EVAL_DIR):
    files = sorted(Path(directory).glob("baseline-*.json"))
    return files[-1] if files else None


def write_baseline(payload, date, directory=DEFAULT_EVAL_DIR):
    """Never rewrite a frozen baseline.  A conflicting freeze gets a new file."""
    target = baseline_path_for(date, directory)
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing.get("cases") == payload.get("cases"):
            return target, False
        index = 2
        while True:
            candidate = Path(directory) / ("baseline-%s-%d.json" % (date, index))
            if not candidate.exists():
                target = candidate
                break
            index += 1
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(target, 0o600)
    return target, True


def baseline_metrics(baseline, cases, depth):
    """Recompute a frozen baseline's metrics without touching the network."""
    per_case = []
    by_id = {c["id"]: c for c in cases}
    for case_id, entry in baseline.get("cases", {}).items():
        case = by_id.get(case_id)
        if case is None:
            continue
        ranked = entry.get("results", [])
        per_case.append(
            {
                "id": case_id,
                "category": case["category"],
                "query": case["query"],
                "metrics": score_case(case, ranked, depth),
            }
        )
    return {
        "source": baseline.get("path"),
        "frozen_at": baseline.get("frozen_at"),
        "limit": baseline.get("limit"),
        "overall": aggregate(per_case),
        "by_category": group_by_category(per_case),
        "cases": per_case,
    }


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def run(args):
    started = datetime.now(timezone.utc)
    cases = load_cases(args.cases, limit=args.case_limit)
    if args.category:
        cases = [c for c in cases if c["category"] in set(args.category)]
    if not cases:
        raise CaseError("no cases loaded from %s" % args.cases)

    conn = open_shadow(args.shadow_db)

    missing = verify_case_grounding(cases, conn)
    if missing:
        detail = ", ".join("%s->%s" % (c, i) for c, i in missing[:10])
        raise CaseError(
            "%d expected/forbidden source_id values are not active records in the "
            "shadow snapshot: %s" % (len(missing), detail)
        )

    global FORBIDDEN_DEPTH
    FORBIDDEN_DEPTH = args.limit

    backends = args.backend
    per_backend = {}

    if backends in ("both", "shadow"):
        per_case = []
        for case in cases:
            entry = {
                "id": case["id"],
                "category": case["category"],
                "query": case["query"],
                "scope": case["scope"],
                "expected": case["expected"],
                "forbidden": case["forbidden"],
            }
            try:
                ranked, meta = shadow_search(conn, case, args.limit, args.scope_mode)
                entry["ranked"] = ranked
                entry["meta"] = {
                    "mode": meta.get("mode"),
                    "candidates": meta.get("candidates"),
                    "paths": meta.get("paths"),
                    "scan_terms": meta.get("scan_terms"),
                }
            except Exception as exc:  # a backend error is a result, not a crash
                entry["error"] = "%s: %s" % (type(exc).__name__, exc)
                ranked = []
                entry["ranked"] = []
            entry["metrics"] = score_case(case, ranked, args.limit)
            per_case.append(entry)
        per_backend["shadow"] = {
            "overall": aggregate(per_case),
            "by_category": group_by_category(per_case),
            "cases": per_case,
        }

    if backends in ("both", "nowledge"):
        per_case = []
        for case in cases:
            entry = {
                "id": case["id"],
                "category": case["category"],
                "query": case["query"],
                "scope": case["scope"],
            }
            try:
                ranked = nowledge_search(
                    case, args.limit, args.scope_mode, args.nowledge_cmd,
                    args.timeout, args.nowledge_extra,
                )
                entry["ranked"] = ranked
            except Exception as exc:
                entry["error"] = "%s: %s" % (type(exc).__name__, exc)
                ranked = []
                entry["ranked"] = []
            entry["metrics"] = score_case(case, ranked, args.limit)
            per_case.append(entry)
        per_backend["nowledge"] = {
            "overall": aggregate(per_case),
            "by_category": group_by_category(per_case),
            "cases": per_case,
        }

    report = {
        "harness": "scripts/shadow_eval.py",
        "schema_version": 1,
        "generated_at": started.isoformat(),
        "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 2),
        "cases_path": str(Path(args.cases).expanduser()),
        "cases_total": len(cases),
        "limit": args.limit,
        "scope_mode": args.scope_mode,
        "version_correctness": VERSION_CORRECTNESS,
        "shadow": shadow_facts(conn, args.shadow_db),
        "nowledge": nowledge_facts(args.nowledge_cmd) if backends != "shadow" else None,
        "backends": per_backend,
    }

    # A shadow-only run is the interesting one to compare against a frozen
    # Nowledge baseline, but the baseline's own numbers are reported whenever a
    # baseline exists so "did we win?" is never asserted without its absolute.
    baseline_file = Path(args.baseline).expanduser() if args.baseline else None
    if baseline_file is None and args.use_baseline:
        candidate = newest_baseline(Path(args.cases).expanduser().parent)
        baseline_file = candidate
    if baseline_file and baseline_file.exists():
        raw = json.loads(baseline_file.read_text(encoding="utf-8"))
        raw["path"] = str(baseline_file)
        report["baseline"] = baseline_metrics(raw, cases, args.limit)
        report["baseline"]["frozen_command"] = raw.get("command")
    elif baseline_file:
        report["baseline"] = {"error": "baseline not found: %s" % baseline_file}

    if args.freeze:
        if "nowledge" not in per_backend:
            raise CaseError("--freeze requires a run that queries the Nowledge backend")
        date = args.freeze_date or started.strftime("%Y%m%d")
        payload = {
            "frozen_at": started.isoformat(),
            "backend": "nowledge",
            "command": "%s memories search <query> -n %d -j"
            % (args.nowledge_cmd, args.limit),
            "query_set": str(Path(args.cases).expanduser()),
            "query_set_sha256": hashlib.sha256(
                Path(args.cases).expanduser().read_bytes()
            ).hexdigest(),
            "limit": args.limit,
            "scope_mode": args.scope_mode,
            "cases": {
                entry["id"]: {
                    "query": entry["query"],
                    "results": entry["ranked"],
                    "error": entry.get("error"),
                }
                for entry in per_backend["nowledge"]["cases"]
            },
        }
        target, created = write_baseline(payload, date, Path(args.cases).expanduser().parent)
        report["frozen_baseline"] = {
            "path": str(target),
            "created": created,
            "note": (
                "frozen" if created else "identical baseline already existed; left untouched"
            ),
        }

    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["_out"] = str(out)
    return report


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def fmt(value, digits=3):
    return "n/a" if value is None else ("%.*f" % (digits, value))


def render(report, verbose=False):
    lines = []
    shadow = report["shadow"]
    lines.append("shadow snapshot : %s" % shadow["path"])
    lines.append("  sha256        : %s" % shadow["sha256"][:16] + "…")
    lines.append(
        "  records       : %s active / %s total, project coverage %s"
        % (shadow["records_active"], shadow["records_total"], shadow["project_coverage"])
    )
    lines.append("cases           : %s   limit=%s   scope=%s"
                 % (report["cases_total"], report["limit"], report["scope_mode"]))
    lines.append("version_correctness: %s" % report["version_correctness"])
    lines.append("")

    header = "%-24s %8s %8s %9s" % ("category", "Hit@5", "Recall@5", "nDCG@10")
    for name in ("shadow", "nowledge"):
        block = report["backends"].get(name)
        if not block:
            continue
        lines.append("== %s ==" % name)
        lines.append(header)
        for label, stats in [("(overall)", block["overall"])] + sorted(
            block["by_category"].items()
        ):
            lines.append(
                "%-24s %8s %8s %9s"
                % (
                    label,
                    fmt(stats["hit@5"]),
                    fmt(stats["recall@5"]),
                    fmt(stats["ndcg@10"]),
                )
            )
        lines.append(
            "forbidden hits@%s: %d (project_isolation only: %d)  errors: %d"
            % (
                report["limit"],
                block["overall"]["forbidden_hits"],
                block["overall"]["isolation_violations"],
                block["overall"]["errors"],
            )
        )
        lines.append("")

    baseline = report.get("baseline")
    if baseline and "overall" in baseline:
        lines.append("== frozen baseline: %s ==" % baseline.get("source"))
        stats = baseline["overall"]
        lines.append(
            "%s  Hit@5=%s Recall@5=%s nDCG@10=%s  forbidden=%d"
            % (
                "nowledge",
                fmt(stats["hit@5"]),
                fmt(stats["recall@5"]),
                fmt(stats["ndcg@10"]),
                stats["forbidden_hits"],
            )
        )
        lines.append("")

    if verbose:
        for name in ("shadow", "nowledge"):
            block = report["backends"].get(name)
            if not block:
                continue
            lines.append("-- per-case failures (%s) --" % name)
            for case in block["cases"]:
                if case.get("error"):
                    lines.append("  %s ERROR %s" % (case["id"], case["error"]))
                    continue
                m = case["metrics"]
                missed = [
                    sid for sid, rank in m["expected_ranks"].items() if rank is None
                ]
                if m["hit@5"] == 1 and not m["forbidden_hits"]:
                    continue
                lines.append(
                    "  %s [%s] hit@5=%s missed=%s forbidden=%s"
                    % (case["id"], case["category"], m["hit@5"], missed[:3],
                       [h["id"] for h in m["forbidden_hits"]][:3])
                )
    return "\n".join(lines)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="P0 retrieval eval: Nowledge shadow vs. Nowledge primary."
    )
    ap.add_argument("--cases", default=str(DEFAULT_CASES),
                    help="JSON Lines case corpus (default: %(default)s, private)")
    ap.add_argument("--shadow-db", default=str(DEFAULT_SHADOW_DB),
                    help="local shadow snapshot, opened read-only (default: %(default)s)")
    ap.add_argument("--refresh-shadow", action="store_true",
                    help="pull a torn-free snapshot from M3 before evaluating")
    ap.add_argument("--m3-host", default=DEFAULT_M3_HOST,
                    help="SSH host holding the live shadow database (default: %(default)s)")
    ap.add_argument("--m3-shadow-path", default=DEFAULT_M3_SHADOW,
                    help="path of the live shadow database on the M3 host")
    ap.add_argument("--backend", choices=("both", "shadow", "nowledge"), default="both")
    ap.add_argument("--limit", type=int, default=20,
                    help="top-K retrieved from each backend (default: %(default)s)")
    ap.add_argument("--scope", dest="scope_mode", choices=("none", "native"), default="none",
                    help="'none' = identical query for both backends (default); "
                         "'native' = also apply each backend's own project filter")
    ap.add_argument("--freeze", action="store_true",
                    help="write the Nowledge top-K to the private baseline file")
    ap.add_argument("--freeze-date", default=None,
                    help="override the baseline date stamp (YYYYMMDD)")
    ap.add_argument("--baseline", default=None,
                    help="explicit baseline file to compare against")
    ap.add_argument("--no-baseline", dest="use_baseline", action="store_false",
                    help="do not auto-load the newest baseline")
    ap.add_argument("--nowledge-cmd", default=os.environ.get("NMEM_BIN", DEFAULT_NOWLEDGE),
                    help="Nowledge CLI (default: %(default)s)")
    ap.add_argument("--timeout", type=int, default=90,
                    help="per-query timeout for the Nowledge CLI (default: %(default)s)")
    ap.add_argument("--case-limit", type=int, default=None,
                    help="only run the first N cases (debugging)")
    ap.add_argument("--category", action="append", choices=CATEGORIES, default=None,
                    help="restrict to one or more categories (repeatable)")
    ap.add_argument("--nowledge-extra", action="append", default=None, metavar="ARG",
                    help="extra argv appended to the nmem command (repeatable); "
                         "used for sensitivity runs such as --nowledge-extra --mode "
                         "--nowledge-extra deep")
    ap.add_argument("--out", default=None, help="write the full JSON report here")
    ap.add_argument("--quiet", action="store_true", help="suppress the human summary")
    ap.add_argument("-v", "--verbose", action="store_true", help="list per-case failures")
    args = ap.parse_args(argv)
    args.nowledge_extra = args.nowledge_extra or []

    if args.limit < HIT_DEPTH:
        ap.error("--limit must be >= %d to compute Hit@5" % HIT_DEPTH)
    if args.limit > 50:
        ap.error("--limit must be <= 50 (shadow search hard limit)")
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.refresh_shadow:
        try:
            out = refresh_shadow(args.m3_host, args.m3_shadow_path, args.shadow_db)
        except CaseError as exc:
            print("refresh-shadow failed: %s" % exc, file=sys.stderr)
            return 2
        print("refreshed %s%s" % (args.shadow_db, (" (%s)" % out) if out else ""))
    try:
        report = run(args)
    except CaseError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    if not args.quiet:
        print(render(report, verbose=args.verbose))
    if args.out:
        print("wrote %s" % report["_out"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
