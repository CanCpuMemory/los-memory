#!/usr/bin/env python3
"""Measure los-memory's OWN retrieval quality.

Why this exists: `memory search` had no quality gate anywhere — CI ran Ruff, docs
lint, CLI contract, unit and integration tests, none of which can tell a ranking
change from a regression. `scripts/shadow_eval.py` measures the *shadow against
Nowledge*; nothing measured the core ledger. That is how `--semantic` shipped
with Hit@1 = 0.000 against the default path's 0.650 and nobody noticed.

Method
------
Sample real observations, cut a short CJK window out of each record's own
`summary`, and ask whether the source record comes back and at what rank. Two
guards keep it honest:

* **Rarity control.** A query like `勾选条目` appears in many records, so a low
  rank measures the corpus, not the ranker. Windows are kept only when the whole
  table contains at most `--max-corpus-hits` of them.
* **Self-check.** `--self-check` builds a throwaway database with known answers
  and asserts the harness scores a good ranker high and a broken one low, so the
  instrument is validated before its numbers are believed.

Read-only: the database is opened with `mode=ro`. Writes nothing anywhere.

Typical use::

    # record a baseline
    python3 scripts/measure_core_search.py --db ~/.local/share/llm-memory/memory.db
    # gate a change (non-zero exit if the default path regresses)
    python3 scripts/measure_core_search.py --min-hit1 0.60 --min-hit5 0.95
    # validate the instrument itself (CI-safe, needs no private corpus)
    python3 scripts/measure_core_search.py --self-check
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_tool.operations import run_search, run_semantic_search  # noqa: E402
from memory_tool.utils import like_pattern  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fa5]")
# Identifier shape: a leading letter plus at least one separator-joined part.
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[_./:-][A-Za-z0-9]+)+")
# Timestamps match the identifier shape but are not identifiers.
TIMESTAMP_LIKE = re.compile(r"^T?\d{2}:\d{2}:\d{2}")
DEFAULT_DB = Path.home() / ".local/share/llm-memory/memory.db"
MODES = ("auto", "semantic")


def connect_read_only(path: str) -> sqlite3.Connection:
    """Open the database read-only, so a measurement run can never mutate it."""
    if not os.path.exists(path):
        raise SystemExit(f"database not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def ranker(mode: str):
    return run_search if mode == "auto" else run_semantic_search


def build_cases(conn, samples: int, max_corpus_hits: int, seed: int):
    """Return [(row, query, corpus_hits)] with rare CJK windows as queries."""
    random.seed(seed)
    rows = conn.execute("SELECT id, title, summary FROM observations").fetchall()
    rich = [r for r in rows if len(CJK.findall(r["summary"] or "")) >= 12]
    cases = []
    for row in random.sample(rich, min(samples * 3, len(rich))):
        if len(cases) >= samples:
            break
        text = row["summary"]
        starts = [i for i in range(len(text) - 4)
                  if all(CJK.match(ch) for ch in text[i:i + 4])]
        random.shuffle(starts)
        for i in starts[:12]:
            window = text[i:i + 4]
            hits = conn.execute(
                "SELECT count(*) FROM observations WHERE summary LIKE ? ESCAPE '\\'",
                (like_pattern(window),)).fetchone()[0]
            if hits <= max_corpus_hits:
                cases.append((row, window, hits))
                break
    return len(rows), len(rich), cases


def build_identifier_cases(conn, samples: int, seed: int):
    """Cases whose query is an identifier occurring in exactly one record.

    A second family, because the CJK-window family cannot see this one: identifier
    queries are single terms carrying `_`, `-`, `/`, `.` or `:`, and those characters
    change how FTS5 parses the query — `-`/`/`/`.`/`:` are operators and raised a
    syntax error, while `_` is a token separator. A change to FTS handling is
    invisible to the CJK family and must be measured here.

    Uniqueness is checked against the whole table rather than sampled: a query sharing
    its answer with another record measures the corpus, not the ranker.
    """
    random.seed(seed)
    rows = conn.execute("SELECT id, title, summary, raw FROM observations").fetchall()
    counts = {}
    per_record = {}
    for row in rows:
        text = " ".join(filter(None, [row["title"], row["summary"], row["raw"]]))
        found = {token for token in IDENTIFIER.findall(text)
                 if not TIMESTAMP_LIKE.match(token) and 6 <= len(token) <= 60}
        per_record[row["id"]] = found
        for token in found:
            counts[token] = counts.get(token, 0) + 1
    unique = [(row, token) for row in rows
              for token in per_record.get(row["id"], ()) if counts[token] == 1]
    if not unique:
        return []
    random.shuffle(unique)
    return [(row, token, 1) for row, token in unique[:samples]]


def measure(conn, cases, limit: int) -> dict:
    report = {}
    for mode in MODES:
        ranks, latencies = [], []
        for row, query, _hits in cases:
            started = time_now()
            try:
                results = ranker(mode)(conn, query, limit=limit)
            except Exception as error:  # noqa: BLE001 - surface, never hide
                results = []
                print(f"  !! {mode} raised on {query!r}: {type(error).__name__}: {error}",
                      file=sys.stderr)
            latencies.append((time_now() - started) * 1000)
            ids = [r.get("id") for r in results]
            ranks.append(ids.index(row["id"]) + 1 if row["id"] in ids else None)
        total = len(ranks) or 1
        report[mode] = {
            "hit@1": round(sum(1 for r in ranks if r == 1) / total, 4),
            "hit@5": round(sum(1 for r in ranks if r and r <= 5) / total, 4),
            "hit@k": round(sum(1 for r in ranks if r and r <= limit) / total, 4),
            "p50_ms": round(statistics.median(latencies), 1) if latencies else None,
            "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1) if latencies else None,
            "misses": [{"query": q, "expected_id": row["id"], "rank": rank}
                       for (row, q, _h), rank in zip(cases, ranks) if rank != 1][:10],
        }
    return report


def time_now() -> float:
    import time
    return time.perf_counter()


def render(cases_total: int, report: dict, limit: int, family: str = "cjk") -> str:
    lines = [f"# core search measurement — {family} family "
             f"({cases_total} rarity-controlled cases, limit={limit})", "",
             f"| {'path':<10} | {'Hit@1':>6} | {'Hit@5':>6} | {'Hit@' + str(limit):>6} "
             f"| {'p50 ms':>8} | {'p95 ms':>8} |",
             f"|{'-' * 12}|{'-' * 8}|{'-' * 8}|{'-' * 8}|{'-' * 10}|{'-' * 10}|"]
    for mode, data in report.items():
        lines.append(f"| {mode:<10} | {data['hit@1']:>6.3f} | {data['hit@5']:>6.3f} "
                     f"| {data['hit@k']:>6.3f} | {data['p50_ms']:>8} | {data['p95_ms']:>8} |")
    return "\n".join(lines)


def self_check() -> int:
    """Validate the instrument against a fixture with known answers."""
    from memory_tool.database import ensure_schema

    print("self-check: validating the instrument before trusting its numbers")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixture.db"
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        expected = {}
        for index in range(12):
            summary = f"第{index}号记录的独特内容{index}标记片段，用于验证排序仪器的区分能力。"
            cur = conn.execute(
                "INSERT INTO observations (timestamp, project, kind, title, summary,"
                " tags, tags_text, raw) VALUES ('2026-01-01T00:00:00Z','p','note',?,?,'[]','','')",
                (f"记录{index}", summary))
            expected[cur.lastrowid] = summary
        conn.commit()

        cases = []
        for obs_id, summary in expected.items():
            row = conn.execute("SELECT id, title, summary FROM observations WHERE id=?",
                               (obs_id,)).fetchone()
            window = summary[:4]
            hits = conn.execute(
                "SELECT count(*) FROM observations WHERE summary LIKE ? ESCAPE '\\'",
                (like_pattern(window),)).fetchone()[0]
            assert hits == 1, f"fixture window {window!r} is not unique (hits={hits})"
            cases.append((row, window, hits))

        good = measure(conn, cases, limit=5)["auto"]
        if good["hit@1"] != 1.0:
            print(f"FAIL: the default path scored Hit@1={good['hit@1']} on answers it must find")
            return 1

        class BrokenRanker:
            """Least-relevant-first: the instrument must notice this."""

            @staticmethod
            def __call__(conn, query, limit=10):
                return []

        original = run_search
        try:
            globals()["run_search"] = BrokenRanker()
            broken = measure(conn, cases, limit=5)["auto"]
        finally:
            globals()["run_search"] = original
        if broken["hit@1"] != 0.0:
            print(f"FAIL: the instrument did not detect a broken ranker (Hit@1={broken['hit@1']})")
            return 1
        conn.close()

    print(f"self-check OK: good ranker Hit@1={good['hit@1']}, broken ranker Hit@1={broken['hit@1']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        help=f"SQLite database to measure (opened read-only). Default: {DEFAULT_DB}")
    parser.add_argument("--samples", type=int, default=30, help="cases to build per family (default 30)")
    parser.add_argument("--family", choices=["cjk", "identifier", "both"], default="cjk",
                        help="cjk: 4-character windows of a record's own summary; "
                             "identifier: a separator-bearing token unique to one record "
                             "(these exercise FTS query parsing, which the cjk family cannot see)")
    parser.add_argument("--limit", type=int, default=10, help="results per query (default 10)")
    parser.add_argument("--max-corpus-hits", type=int, default=2,
                        help="keep only windows appearing in at most this many records (default 2)")
    parser.add_argument("--seed", type=int, default=20261008, help="sampling seed")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--min-hit1", type=float, default=None,
                        help="exit non-zero if the default path's Hit@1 is below this")
    parser.add_argument("--min-hit5", type=float, default=None,
                        help="exit non-zero if the default path's Hit@5 is below this")
    parser.add_argument("--self-check", action="store_true",
                        help="validate the instrument on a fixture; needs no corpus")
    args = parser.parse_args()

    if args.self_check:
        return self_check()

    conn = connect_read_only(args.db)
    try:
        families = {}
        if args.family in ("cjk", "both"):
            total, rich, cases = build_cases(conn, args.samples, args.max_corpus_hits, args.seed)
            if cases:
                families["cjk"] = {"cases": cases, "report": measure(conn, cases, args.limit),
                                   "candidates": total, "rich": rich}
        if args.family in ("identifier", "both"):
            cases = build_identifier_cases(conn, args.samples, args.seed)
            if cases:
                families["identifier"] = {"cases": cases,
                                          "report": measure(conn, cases, args.limit)}
        if not families:
            raise SystemExit("no rarity-controlled cases could be built; "
                             "raise --max-corpus-hits or point --db at a populated ledger")
    finally:
        conn.close()

    if args.json:
        print(json.dumps({"db": args.db, "limit": args.limit,
                          "families": {name: {"cases": len(data["cases"]),
                                              **{k: v for k, v in data.items()
                                                 if k in ("candidates", "rich")},
                                              "report": data["report"]}
                                       for name, data in families.items()}},
                         ensure_ascii=False, indent=2))
    else:
        for name, data in families.items():
            extra = ""
            if "rich" in data:
                extra = f" (observations {data['candidates']}, CJK-rich {data['rich']})"
            print(f"cases: {len(data['cases'])}{extra}")
            print()
            print(render(len(data["cases"]), data["report"], args.limit, name))
            print()

    failures = []
    # The gate applies to the `auto` path of each family: it is the default and the
    # one a regression would hit.
    for name, data in families.items():
        auto = data["report"]["auto"]
        if args.min_hit1 is not None and auto["hit@1"] < args.min_hit1:
            failures.append(f"[{name}] Hit@1 {auto['hit@1']} < --min-hit1 {args.min_hit1}")
        if args.min_hit5 is not None and auto["hit@5"] < args.min_hit5:
            failures.append(f"[{name}] Hit@5 {auto['hit@5']} < --min-hit5 {args.min_hit5}")
    for failure in failures:
        print(f"GATE FAILED: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
