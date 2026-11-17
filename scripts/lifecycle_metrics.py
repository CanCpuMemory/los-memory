#!/usr/bin/env python3
"""Lifecycle metrics for the los-memory ledger (plan §6.2 / §7.10, B2).

Why this exists: the daily job ``com.echerlos.memory-lifecycle-probe`` runs
``dsplugins/dsh-memory-evolve/scripts/lifecycle-probe.mjs``, which reads
``~/.dsh/memories`` **and lives inside the plugin directory**. When evolve is
uninstalled at P5 the job's program path disappears and the job exits 2 — its
ledger of memory health stops exactly when it starts to matter.

So this probe answers the same questions from the ledger instead:

* per track: ``total`` / ``active`` / ``deprecated`` / ``archived`` (evolve has no
  archive concept — the extra key is additive, never a replacement)
* ``slots`` (distinct slots among active) and ``sameSlotMultiActive`` (must be 0)
* ``supersedesEdgeUnresolved`` (an active unit pointing at an id that is gone)
* ``totals`` across the tracks evolve reported (memory / user / key)

Exit codes follow the repo's three-state rule: ``0`` all gates hold,
``1`` a gate really fails, ``2`` **cannot judge** (ledger unreadable / schema
drift). ``2`` is never folded into 1 or 0.

``slots > 0`` is part of the gate: with zero slots the two zeros above are
empty-denominator zeros — they only say the mechanism was never used.

Read-only by construction: the ledger is opened ``mode=ro`` (``immutable=1``
fallback for a WAL without ``-shm``) and the only write is one JSONL line to the
metrics ledger. That write is the reason this script is not part of
``dual_write_observe.py``: different cadence, different owner, different ledger.

Usage::

    python3 scripts/lifecycle_metrics.py                 # judge + append one line
    python3 scripts/lifecycle_metrics.py --json          # machine payload
    python3 scripts/lifecycle_metrics.py --dry-run       # print only, write nothing
    python3 scripts/lifecycle_metrics.py --self-test     # pure-function controls
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sqlite3
import sys
from typing import Any

DEFAULT_DB = "~/.local/share/los-memory/service.sqlite3"
DEFAULT_CWD = "~/syncfolder/project/dsfolder"
DEFAULT_OUT_DIR = "~/.dsh/memory-inject"
DEFAULT_REVIEW_STATE = "~/.dsh/memory-inject/review-state.json"
#: Tracks evolve's probe reported; the ledger has more (project/daily) but a
#: comparison against the old metrics file is only fair on these three.
COMPARE_TRACKS = ("memory", "user", "key")

STATE_PASS = "pass"
STATE_FAIL = "fail"
STATE_NA = "na"


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def project_hash(path_or_hash: str) -> str:
    """Accept a path or an already-computed 12-hex hash (the seam passes hashes).

    ``~`` is expanded before hashing. That is not cosmetic: the default project is
    written as ``~/syncfolder/project/dsfolder``, and hashing the literal string
    silently produces a hash that matches **no** unit — a probe that measures
    nothing while printing ``pass`` (2026-11-18 caught exactly that: entries 350
    instead of 828, key track missing entirely).
    """
    raw = str(path_or_hash or "").strip()
    if not raw:
        raise ValueError("project_required")
    if len(raw) == 12 and all(c in "0123456789abcdef" for c in raw.lower()):
        return raw.lower()
    return hashlib.sha1(os.path.expanduser(raw).encode()).hexdigest()[:12]


def open_ledger(db_path: str) -> sqlite3.Connection:
    """Open the ledger read-only; ``immutable=1`` covers a WAL without ``-shm``."""
    path = os.path.abspath(os.path.expanduser(db_path))
    for uri in (f"file:{path}?mode=ro", f"file:{path}?immutable=1"):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM units LIMIT 1").fetchone()
            return conn
        except sqlite3.Error:
            continue
    raise sqlite3.Error(f"cannot open ledger read-only: {path}")


def _schema_ok(conn: sqlite3.Connection) -> bool:
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(units)")}
    except sqlite3.Error:
        return False
    return {"id", "track", "state", "slot", "superseded_by", "updated_at"} <= cols


def collect_metrics(conn: sqlite3.Connection, project: str | None) -> dict[str, Any]:
    """Per-track lifecycle counters. ``project`` narrows key/project only.

    Same scoping rule as ``mem status --project``: memory/user/daily rows carry no
    project, so narrowing them would report a project's silence as zero entries.
    """
    where = ""
    params: tuple[Any, ...] = ()
    if project:
        where = " AND (track NOT IN ('key','project') OR project=?)"
        params = (project,)
    tracks: dict[str, dict[str, int]] = {}
    for row in conn.execute(
        "SELECT track, state, COUNT(*) AS n FROM units WHERE 1=1" + where + " GROUP BY track, state",
        params,
    ):
        tracks.setdefault(str(row["track"]), {})[str(row["state"])] = int(row["n"])

    active_rows = conn.execute(
        "SELECT track, slot, superseded_by, scope_key FROM units WHERE state='active'" + where,
        params,
    ).fetchall()

    per_track: dict[str, dict[str, Any]] = {}
    for track, states in tracks.items():
        per_track[track] = {
            "total": sum(states.values()),
            "active": states.get("active", 0),
            "deprecated": states.get("deprecated", 0),
            "archived": states.get("archived", 0),
            "slots": 0,
            "sameSlotMultiActive": 0,
            "supersedesEdgeUnresolved": 0,
        }

    slot_seen: dict[str, set[tuple[str, str]]] = {}
    counts: dict[str, dict[tuple[str, str], int]] = {}
    for row in active_rows:
        track = str(row["track"])
        if row["slot"]:
            key = (str(row["scope_key"]), str(row["slot"]))
            slot_seen.setdefault(track, set()).add(key)
            counts.setdefault(track, {})
            counts[track][key] = counts[track].get(key, 0) + 1
        if row["superseded_by"]:
            exists = conn.execute(
                "SELECT 1 FROM units WHERE id=?", (str(row["superseded_by"]),)
            ).fetchone()
            if exists is None:
                per_track[track]["supersedesEdgeUnresolved"] += 1

    for track, groups in counts.items():
        per_track[track]["slots"] = len(slot_seen.get(track, ()))
        per_track[track]["sameSlotMultiActive"] = sum(1 for n in groups.values() if n > 1)

    totals = {
        "entries": sum(per_track[t]["total"] for t in COMPARE_TRACKS if t in per_track),
        "active": sum(per_track[t]["active"] for t in COMPARE_TRACKS if t in per_track),
        "deprecated": sum(per_track[t]["deprecated"] for t in COMPARE_TRACKS if t in per_track),
        "slots": sum(per_track[t]["slots"] for t in COMPARE_TRACKS if t in per_track),
        "multiActive": sum(per_track[t]["sameSlotMultiActive"] for t in COMPARE_TRACKS if t in per_track),
        "unresolved": sum(per_track[t]["supersedesEdgeUnresolved"] for t in COMPARE_TRACKS if t in per_track),
    }
    newest = conn.execute("SELECT MAX(updated_at) AS ts FROM units").fetchone()
    return {
        "tracks": {t: per_track[t] for t in COMPARE_TRACKS if t in per_track},
        "extraTracks": {t: v for t, v in per_track.items() if t not in COMPARE_TRACKS},
        "totals": totals,
        "newestUpdatedAt": newest["ts"] if newest else None,
        "project": project,
    }


def review_due(metrics: dict[str, Any], last_reviewed_at: str | None,
               now: _dt.datetime, interval_days: int = 30) -> dict[str, Any]:
    """Decide the review-due line the injection surface may render (plan §7.9 D2).

    Two triggers, both from facts the ledger already has:
    * **findings** — a slot with two active units, or a supersedes edge pointing at a
      missing unit. Those are wrong states, not "old" ones.
    * **stale** — nothing wrong, but no review recorded for ``interval_days``.

    ``last_reviewed_at`` is read from the state file, never invented: a review that
    nobody recorded did not happen. Returns the exact payload the injector reads.
    """
    findings = {
        "sameSlotMultiActive": int(metrics.get("totals", {}).get("multiActive", 0)),
        "supersedesEdgeUnresolved": int(metrics.get("totals", {}).get("unresolved", 0)),
        "deprecated": int(metrics.get("totals", {}).get("deprecated", 0)),
    }
    actionable = findings["sameSlotMultiActive"] + findings["supersedesEdgeUnresolved"]
    due_at = None
    last_dt = None
    if last_reviewed_at:
        try:
            last_dt = _dt.datetime.fromisoformat(str(last_reviewed_at).replace("Z", "+00:00"))
        except ValueError:
            last_dt = None
    if last_dt is not None:
        due_at = _iso(last_dt + _dt.timedelta(days=int(interval_days)))
    stale = last_dt is not None and (now - last_dt).days >= int(interval_days)
    if actionable > 0:
        due, reason = True, "findings"
    elif stale:
        due, reason = True, "stale"
    else:
        due, reason = False, "not-due"
    return {
        "schema": 1,
        "generatedAt": _iso(now),
        "lastReviewedAt": last_reviewed_at,
        "dueAt": due_at,
        "due": due,
        "reason": reason,
        "findings": findings,
        "reviewIntervalDays": int(interval_days),
    }


def read_last_reviewed(path: str) -> str | None:
    """Preserve the previous ``lastReviewedAt``; a bad/missing file means "no record"."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    value = raw.get("lastReviewedAt") if isinstance(raw, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def write_json_state(path: str, payload: dict[str, Any]) -> None:
    target = os.path.expanduser(path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = f"{target}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    os.replace(tmp, target)


def judge(metrics: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Three-state verdict. Same gates as evolve's probe, plus archive visibility."""
    problems: list[str] = []
    if not metrics.get("tracks"):
        return STATE_NA, "no compared track present in the ledger (schema drift?)", ["no_tracks"]
    totals = metrics["totals"]
    if not totals.get("slots"):
        return STATE_NA, "slots=0 ⇒ sameSlotMultiActive/unresolved are empty-denominator zeros", ["no_slots"]
    if totals.get("multiActive"):
        problems.append(f"sameSlotMultiActive={totals['multiActive']}")
    if totals.get("unresolved"):
        problems.append(f"supersedesEdgeUnresolved={totals['unresolved']}")
    if problems:
        return STATE_FAIL, "; ".join(problems), problems
    return STATE_PASS, (
        f"slots={totals['slots']} multiActive=0 unresolved=0 "
        f"(entries {totals['entries']} / active {totals['active']} / deprecated {totals['deprecated']})"
    ), []


def self_test() -> int:
    cases: list[tuple[str, Any, Any]] = []

    def t(label: str, got: Any, want: Any) -> None:
        cases.append((label, got, want))

    t("project: path → sha1[:12]", project_hash("/Users/x/proj"), hashlib.sha1(b"/Users/x/proj").hexdigest()[:12])
    t("project: 12-hex passed through", project_hash("2D4994D462AF"), "2d4994d462af")
    t("project: ~ expanded before hashing", project_hash("~/proj"),
      hashlib.sha1(os.path.expanduser("~/proj").encode()).hexdigest()[:12])
    t("project: tilde must differ from the literal string (negative control)",
      project_hash("~/proj") == hashlib.sha1(b"~/proj").hexdigest()[:12], False)
    t("project: empty is an error", _raises(lambda: project_hash("")), "ValueError")

    base = {"tracks": {"memory": {}}, "totals": {"entries": 3, "active": 3, "deprecated": 0,
                                                  "slots": 2, "multiActive": 0, "unresolved": 0}}
    t("judge: all gates hold", judge(base)[0], STATE_PASS)
    t("judge: multi-active (negative control)", judge({**base, "totals": {**base["totals"], "multiActive": 1}})[0], STATE_FAIL)
    t("judge: unresolved supersedes (negative control)", judge({**base, "totals": {**base["totals"], "unresolved": 2}})[0], STATE_FAIL)
    t("judge: slots=0 is empty-denominator ⇒ na", judge({**base, "totals": {**base["totals"], "slots": 0}})[0], STATE_NA)
    t("judge: no tracks ⇒ na", judge({"tracks": {}, "totals": {}})[0], STATE_NA)

    now = _dt.datetime(2026, 11, 18, tzinfo=_dt.timezone.utc)
    clean = {"totals": {"multiActive": 0, "unresolved": 0, "deprecated": 3}}
    dirty = {"totals": {"multiActive": 1, "unresolved": 0, "deprecated": 3}}
    t("review: findings ⇒ due", review_due(dirty, "2026-11-17T00:00:00Z", now)["due"], True)
    t("review: clean + reviewed yesterday ⇒ not due",
      review_due(clean, "2026-11-17T00:00:00Z", now)["due"], False)
    t("review: clean + never reviewed ⇒ not due (no invented record)",
      review_due(clean, None, now)["due"], False)
    t("review: clean + reviewed 40 days ago ⇒ stale due",
      review_due(clean, "2026-10-09T00:00:00Z", now)["due"], True)
    t("review: bad timestamp is treated as no record",
      review_due(clean, "not-a-date", now)["due"], False)
    t("review: dueAt = last + interval",
      review_due(clean, "2026-11-01T00:00:00Z", now, 30)["dueAt"], "2026-12-01T00:00:00Z")

    bad = 0
    for label, got, want in cases:
        ok = got == want
        bad += 0 if ok else 1
        print(("ok   " if ok else "FAIL ") + f"{label}: {got!r} (want {want!r})")
    print(f"self-test: {len(cases) - bad}/{len(cases)} pass")
    return 0 if bad == 0 else 1


def _raises(fn) -> str:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - the test asserts the type name
        return type(exc).__name__
    return "no-error"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ledger lifecycle metrics (plan §6.2 B2)")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--project", default=DEFAULT_CWD,
                        help="cwd path or 12-hex hash; pass '' to keep every project")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--review-state", default=DEFAULT_REVIEW_STATE,
                        help="where the injection surface reads the review-due line (plan §7.9 D2)")
    parser.add_argument("--review-interval-days", type=int, default=30)
    parser.add_argument("--mark-reviewed", action="store_true",
                        help="record a review NOW (the agent calls this after a consolidation pass)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    now = _dt.datetime.now(_dt.timezone.utc)
    try:
        project = project_hash(args.project) if args.project else None
        conn = open_ledger(args.db)
    except (sqlite3.Error, ValueError) as exc:
        payload = {"verdict": "unjudgeable", "rc": 2, "reason": str(exc), "generatedAt": _iso(now)}
        print(json.dumps(payload, ensure_ascii=False) if args.json else f"verdict=unjudgeable rc=2 — {exc}")
        return 2
    try:
        if not _schema_ok(conn):
            payload = {"verdict": "unjudgeable", "rc": 2, "reason": "units schema drift", "generatedAt": _iso(now)}
            print(json.dumps(payload, ensure_ascii=False) if args.json else "verdict=unjudgeable rc=2 — units schema drift")
            return 2
        metrics = collect_metrics(conn, project)
    finally:
        conn.close()

    verdict, detail, failures = judge(metrics)
    rc = {"pass": 0, "fail": 1, "na": 2}[verdict]
    payload = {
        "schema": 1,
        "source": "los-memory ledger",
        "generatedAt": _iso(now),
        "verdict": verdict,
        "rc": rc,
        "detail": detail,
        "failures": failures,
        **metrics,
    }

    # ---- review-due line the injector may render (plan §7.9 D2) -------------
    # lastReviewedAt is preserved unless the caller explicitly records a review:
    # "no record" must stay "no record", not silently become today.
    last_reviewed = _iso(now) if args.mark_reviewed else read_last_reviewed(args.review_state)
    review = review_due(metrics, last_reviewed, now, args.review_interval_days)
    payload["review"] = review
    if not args.dry_run:
        try:
            write_json_state(args.review_state, review)
            payload["reviewStatePath"] = os.path.expanduser(args.review_state)
        except OSError as exc:
            payload["reviewStateError"] = str(exc)

    if not args.dry_run:
        try:
            out_dir = os.path.abspath(os.path.expanduser(args.out_dir))
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"lifecycle-{now.strftime('%Y-%m')}.jsonl")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            payload["ledger"] = path
        except OSError as exc:
            # A metrics line nobody can write is not a verdict on the memory system.
            payload["ledgerError"] = str(exc)
            if rc == 0:
                payload["verdict"] = "unjudgeable"
                payload["rc"] = 2

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"verdict={payload['verdict']} rc={payload['rc']} — {detail}")
        for track, row in metrics["tracks"].items():
            print(f"  {track:7} total={row['total']:5} active={row['active']:5} "
                  f"deprecated={row['deprecated']:3} archived={row['archived']:3} "
                  f"slots={row['slots']:3} multi={row['sameSlotMultiActive']} unresolved={row['supersedesEdgeUnresolved']}")
        extra = metrics.get("extraTracks") or {}
        if extra:
            print("  other tracks: " + ", ".join(f"{t}={v['active']} active" for t, v in extra.items()))
    return int(payload["rc"])


if __name__ == "__main__":
    sys.exit(main())
