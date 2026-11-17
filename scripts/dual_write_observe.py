#!/usr/bin/env python3
"""Dual-write observability for the evolve -> los-memory migration.

The operative contract lives in ``docs/design/evolve-replacement-plan.md`` §7.4:
while evolve is still the writer, ``~/.dsh/memories`` is the source of truth and
``service.sqlite3`` is its mirror. This script answers nine questions about that
mirror and nothing else:

1. ``corpus_reachable``    — can the corpus and the ledger be read at all?
2. ``parity_tracks``       — does the ledger hold every entry the importer sees?
3. ``import_lag``          — are pending writes inside the 300 s contract?
4. ``reverse_divergence``  — did anyone write the ledger without the corpus?
5. ``expand_path``         — can a full body actually be fetched by the id the
                             injection surface hands the model?
6. ``alias_cwd``           — does an old (aliased) cwd still resolve to the same
                             project KEY entries as the live path?
7. ``volume``              — is the injection surface still inside its budget and
                             not truncating?
8. ``importer_alive``      — is the 300 s importer itself still running? (a quiet
                             corpus explains a stale ledger, not a dead job)
9. ``render_health``       — did every inject render in the window succeed? (a
                             failed render drops the model's whole memory segment
                             for that turn, which reads downstream exactly like
                             "nothing was remembered")

Design rules this file follows:

* **Three-state exit**: ``0`` all green / ``1`` real drift / ``2`` cannot judge.
  ``2`` is never folded into ``1`` or ``0`` — that distinction is what keeps a
  broken instrument from reading as a clean system.
* **Read-only**. The ledger is opened ``mode=ro``; the importer is invoked with
  ``--dry-run`` (which rolls its own savepoint back). The shell wrapper is the
  only writer (one ledger row per run).
* **The importer is the single source of truth for source counts.** Parity is
  compared against ``mem import-evolve --dry-run``'s own ``tracks[*].entries``
  instead of a second, hand-rolled markdown parser — two parsers would drift and
  the diff would then be uninterpretable.
* **A quiet corpus is not a failure.** ``import_lag`` only fails when the
  importer reports pending work *and* the last import is older than the
  contract; "nothing was written for six hours" is a pass with a named detail.

Usage::

    python3 scripts/dual_write_observe.py            # human table
    python3 scripts/dual_write_observe.py --json     # machine payload
    python3 scripts/dual_write_observe.py --self-test  # negative controls
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import sqlite3
import subprocess
import sys
import time
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_ROOT = "~/.dsh/memories"
DEFAULT_DB = "~/.local/share/los-memory/service.sqlite3"
DEFAULT_RENDERS_DIR = "~/.dsh/memory-inject"
DEFAULT_ALLOWLIST = "~/.dsh/memory-inject/dw-allowlist.json"
DEFAULT_ALIAS_MAP = "~/.dsh/storages/path-alias-map.json"
DEFAULT_PROFILE_PATCH = "~/.dsh/profiles/web/cordis.patch.yml"
DEFAULT_INJECT_TEXT = "~/syncfolder/project/dsplugins/dsh-memory-inject/lib/text.js"
DEFAULT_IMPORT_LOG = "~/.dsh/logs/los-memory-import.out.log"

#: Contract values (plan §7.2). Kept as arguments so a fixture run can tighten them.
MAX_LAG_SECONDS = 300
HARD_LAG_SECONDS = 900
VOLUME_MAX_AGE_HOURS = 12
#: The importer's own heartbeat tolerance: 3 h = 36 x the 300 s interval, which
#: still absorbs a sleep gap (interval jobs do not catch up after sleep).
IMPORTER_MAX_AGE_HOURS = 3
#: How far back ``render_health`` looks for failed inject renders.
RENDER_WINDOW_HOURS = 24

TRACKS = ("memory", "user", "key", "project", "daily")

STATE_PASS = "pass"
STATE_FAIL = "fail"
STATE_NA = "na"


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _check(name: str, state: str, detail: str, **extra: Any) -> dict[str, Any]:
    row = {"name": name, "state": state, "detail": detail}
    row.update(extra)
    return row


# --------------------------------------------------------------------------- #
# Pure verdict helpers (self-testable without touching the real corpus)
# --------------------------------------------------------------------------- #


def verdict_parity(source: dict[str, int], ledger: dict[str, int], pending: dict[str, int]) -> tuple[str, str]:
    """Compare the importer's per-track entry counts with the ledger.

    A diff is only acceptable when it is *exactly* the pending work the importer
    reports: the ledger lagging by the entries that have not been imported yet is
    the normal state between two runs, not drift. Anything else means either a
    dropped entry or a unit nobody can trace back to a file.
    """
    problems: list[str] = []
    explained = 0
    for track in TRACKS:
        src = int(source.get(track, 0))
        led = int(ledger.get(track, 0))
        pend = int(pending.get(track, 0))
        diff = src - led
        if diff == pend:
            explained += 1
            continue
        problems.append(f"{track}: source={src} ledger={led} pending={pend} (diff {diff:+d} != pending)")
    if problems:
        return STATE_FAIL, "per-track mismatch: " + "; ".join(problems)
    return STATE_PASS, f"all {len(TRACKS)} tracks match the importer (pending explains every diff)"


def verdict_lag(age_seconds: float, pending: int, max_lag: float = MAX_LAG_SECONDS) -> tuple[str, str]:
    """Pending work older than the contract is drift; a quiet source is not."""
    age = max(0.0, float(age_seconds))
    if pending <= 0:
        return STATE_PASS, f"nothing pending; last import {age / 60:.1f} min ago"
    if age <= max_lag:
        return STATE_PASS, f"{pending} pending within contract ({age:.0f}s <= {max_lag:.0f}s)"
    return STATE_FAIL, f"{pending} pending and last import {age / 60:.1f} min ago (> {max_lag:.0f}s contract)"


def verdict_divergence(rows: list[dict[str, Any]], allowed: set[str]) -> tuple[str, str]:
    """Ledger-only units (origin=cli) are silent divergence: the corpus lacks them."""
    leaked = [row for row in rows if row.get("id") not in allowed]
    if not leaked:
        return STATE_PASS, f"no ledger-only units outside the allowlist ({len(allowed)} allowed)"
    detail = ", ".join(f"{row.get('id')}({row.get('slot') or '-'})" for row in leaked[:6])
    return STATE_FAIL, f"{len(leaked)} ledger-only active unit(s): {detail}"


def verdict_volume(row: dict[str, Any] | None, max_chars: int, now: _dt.datetime) -> tuple[str, str]:
    """The injection surface must stay inside its budget and never truncate."""
    if not row:
        return STATE_NA, "no inject render on record"
    raw_truncated = row.get("truncated")
    if isinstance(raw_truncated, dict):
        # The renderer reports per-track drops (``{"key": 0, ...}``), not a scalar.
        truncated = sum(int(value or 0) for value in raw_truncated.values())
    else:
        truncated = int(raw_truncated or 0)
    total = int((row.get("chars") or {}).get("total") or 0)
    try:
        ts = _dt.datetime.fromisoformat(str(row.get("ts", "")).replace("Z", "+00:00"))
    except ValueError:
        return STATE_NA, "render row has no parsable ts"
    age_h = (now - ts).total_seconds() / 3600.0
    if truncated > 0:
        return STATE_FAIL, f"{truncated} entries dropped by the budget (total {total})"
    if total > max_chars:
        return STATE_FAIL, f"total {total} > maxChars {max_chars}"
    if age_h > VOLUME_MAX_AGE_HOURS:
        return STATE_NA, f"last render {age_h:.1f} h ago (no web session since); total {total}"
    return STATE_PASS, f"total {total} / {max_chars} ({total / max_chars:.0%}), truncated=0, {age_h:.1f} h ago"


def verdict_expand_hint(text: str) -> tuple[str, str]:
    """The hint must point at a path that can actually resolve a ``unit_<12hex>`` id."""
    if "mem expand" in text:
        return STATE_PASS, "injection hint points at `mem expand --id`"
    return STATE_FAIL, "injection hint does not mention `mem expand`; the registered `memory` tool cannot resolve unit ids"


def verdict_importer_alive(age_seconds: float | None,
                           max_age_h: float = IMPORTER_MAX_AGE_HOURS) -> tuple[str, str]:
    """Liveness of the importer job itself — a quiet corpus must not hide a dead job.

    ``import_lag`` deliberately passes when nothing is pending, so it cannot see a
    stopped importer. This check answers the other half: the job that fills the
    mirror wrote its heartbeat recently.
    """
    if age_seconds is None:
        return STATE_NA, "import job log not readable; liveness cannot be judged"
    age_h = max(0.0, float(age_seconds)) / 3600.0
    if age_h <= max_age_h:
        return STATE_PASS, f"import job wrote {age_h:.1f} h ago (<= {max_age_h:.1f} h)"
    return STATE_FAIL, (
        f"import job silent for {age_h:.1f} h (> {max_age_h:.1f} h): the 300 s importer is not running "
        f"(a quiet corpus explains a stale ledger, not a dead job)"
    )


def verdict_render_health(rows: list[dict[str, Any]],
                          hours: float = RENDER_WINDOW_HOURS) -> tuple[str, str]:
    """Every inject render in the window must have succeeded.

    ``volume`` only reads the newest row, so a single failed render (snapshot
    timeout) is invisible there. A failed render means the model's whole memory
    segment was dropped for that turn — downstream that is indistinguishable from
    "nothing was remembered", which is why it is a failure and not a warning.
    """
    if not rows:
        return STATE_NA, f"no inject render in the last {hours:.0f} h (no session asked for a prompt)"
    bad = [row for row in rows if row.get("ok") is not True]
    if not bad:
        return STATE_PASS, f"all {len(rows)} inject renders ok in the last {hours:.0f} h"
    detail = ", ".join(f"{str(row.get('ts'))[:19]}:{row.get('error') or 'unknown'}" for row in bad[:4])
    return STATE_FAIL, f"{len(bad)} of {len(rows)} inject render(s) failed in the last {hours:.0f} h ({detail})"


# --------------------------------------------------------------------------- #
# IO adapters
# --------------------------------------------------------------------------- #


def _read_jsonl_tail(path: str, limit: int = 400) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-limit:]


def newest_inject_render(renders_dir: str) -> dict[str, Any] | None:
    files = sorted(glob.glob(os.path.join(_expand(renders_dir), "renders-*.jsonl")))
    for path in reversed(files):
        rows = [row for row in _read_jsonl_tail(path) if row.get("mode") == "inject"]
        if rows:
            return rows[-1]
    return None


def inject_renders_since(renders_dir: str, hours: float, now: _dt.datetime) -> list[dict[str, Any]]:
    """Every ``mode=inject`` render row recorded within ``hours`` of ``now``."""
    cutoff = now - _dt.timedelta(hours=float(hours))
    window: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(_expand(renders_dir), "renders-*.jsonl"))):
        for row in _read_jsonl_tail(path, limit=1_000_000):
            if row.get("mode") != "inject":
                continue
            try:
                ts = _dt.datetime.fromisoformat(str(row.get("ts", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts >= cutoff:
                window.append(row)
    return window


def file_age_seconds(path: str) -> float | None:
    """Seconds since ``path`` was last written; ``None`` when it cannot be read."""
    try:
        return max(0.0, time.time() - os.stat(_expand(path)).st_mtime)
    except OSError:
        return None


def open_ledger(db_path: str) -> sqlite3.Connection:
    """Open the ledger read-only; fall back to ``immutable=1`` for a WAL without -shm."""
    path = _expand(db_path)
    for uri in (f"file:{path}?mode=ro", f"file:{path}?immutable=1"):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM units LIMIT 1").fetchone()
            return conn
        except sqlite3.Error:
            continue
    raise sqlite3.Error(f"cannot open ledger read-only: {path}")


def importer_dry_run(repo: str, root: str, timeout: int = 180) -> dict[str, Any]:
    """Ask the importer itself what the corpus contains and what is still pending."""
    cmd = [sys.executable, "-m", "memory_tool", "mem", "import-evolve", "--dry-run", "--format", "json",
           "--root", _expand(root)]
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"import-evolve --dry-run rc={proc.returncode}: {(proc.stderr or proc.stdout)[:300]}")
    payload = json.loads(proc.stdout)
    data = payload.get("data") or payload
    if "tracks" not in data:
        raise RuntimeError("import-evolve --dry-run returned no tracks")
    return data


def mem_json(repo: str, args: list[str], timeout: int = 120) -> tuple[int, Any]:
    cmd = [sys.executable, "-m", "memory_tool", "mem", *args, "--format", "json"]
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=timeout)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"raw": (proc.stdout or proc.stderr)[:400]}
    return proc.returncode, payload


def load_json(path: str) -> Any:
    try:
        with open(_expand(path), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def max_chars_from_patch(path: str, default: int = 100_000) -> int:
    try:
        with open(_expand(path), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return default
    marker = "# ── dsh-memory-inject"
    segment = text.split(marker, 1)[1] if marker in text else text
    for line in segment.splitlines()[:40]:
        stripped = line.strip()
        if stripped.startswith("maxChars:"):
            try:
                return int(stripped.split(":", 1)[1].strip())
            except ValueError:
                return default
    return default


def alias_pairs(path: str) -> list[dict[str, str]]:
    raw = load_json(path)
    if not isinstance(raw, dict):
        return []
    declared = raw.get("declared")
    if not isinstance(declared, list):
        return []
    pairs: list[dict[str, str]] = []
    for item in declared:
        if not isinstance(item, dict):
            continue
        src = item.get("from")
        dst = item.get("to")
        if isinstance(src, str) and isinstance(dst, str) and src != dst:
            pairs.append({"from": src, "to": dst})
    return pairs


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def collect(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    now = _now()

    root = _expand(args.root)
    db_path = _expand(args.db)

    # 1. corpus_reachable ---------------------------------------------------
    corpus_ok = os.path.isdir(root) and os.access(root, os.R_OK)
    db_error: str | None = None
    conn: sqlite3.Connection | None = None
    try:
        conn = open_ledger(db_path)
    except sqlite3.Error as exc:
        db_error = str(exc)
    if not corpus_ok or conn is None:
        reason = []
        if not corpus_ok:
            reason.append(f"corpus not readable: {root}")
        if db_error:
            reason.append(f"ledger not readable: {db_error}")
        checks.append(_check("corpus_reachable", STATE_NA, "; ".join(reason)))
        return {
            "verdict": "unjudgeable",
            "rc": 2,
            "reason": "; ".join(reason),
            "checks": checks,
            "failures": [],
            "unjudgeable": ["corpus_reachable"],
            "metrics": metrics,
        }
    checks.append(_check("corpus_reachable", STATE_PASS, f"corpus {root} and ledger read-only"))

    # Shared inputs: the importer's own view of the corpus.
    try:
        importer = importer_dry_run(REPO_ROOT, root)
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        importer = None
        importer_error = str(exc)[:300]
    else:
        importer_error = ""

    ledger_counts: dict[str, int] = {}
    for row in conn.execute(
        "SELECT track, COUNT(*) AS n FROM units WHERE origin LIKE 'import:evolve%' GROUP BY track"
    ):
        ledger_counts[row["track"]] = int(row["n"])
    metrics["ledger_import_units"] = ledger_counts
    metrics["ledger_total_units"] = int(conn.execute("SELECT COUNT(*) FROM units").fetchone()[0])

    # 2. parity_tracks -------------------------------------------------------
    if importer is None:
        checks.append(_check("parity_tracks", STATE_NA, f"importer unavailable: {importer_error}"))
        checks.append(_check("import_lag", STATE_NA, f"importer unavailable: {importer_error}"))
    else:
        source = {track: int((importer["tracks"].get(track) or {}).get("entries", 0)) for track in TRACKS}
        # ``created`` adds a unit; ``updated`` only appends a revision to one that is
        # already counted, so only ``created`` can explain a parity gap.
        pending_new = {track: int((importer["tracks"].get(track) or {}).get("created", 0)) for track in TRACKS}
        pending_rev = {track: int((importer["tracks"].get(track) or {}).get("updated", 0)) for track in TRACKS}
        metrics["source_entries"] = source
        metrics["pending_created"] = pending_new
        metrics["pending_updated"] = pending_rev
        state, detail = verdict_parity(source, ledger_counts, pending_new)
        checks.append(_check("parity_tracks", state, detail, source=source, ledger=ledger_counts,
                             pending_created=pending_new, pending_updated=pending_rev))

        # 3. import_lag ------------------------------------------------------
        newest_row = conn.execute(
            "SELECT MAX(updated_at) AS ts FROM units WHERE origin LIKE 'import:evolve%'"
        ).fetchone()
        newest_import = newest_row["ts"] if newest_row else None
        age = float("inf")
        if newest_import:
            try:
                ts = _dt.datetime.fromisoformat(str(newest_import).replace("Z", "+00:00"))
                age = (now - ts).total_seconds()
            except ValueError:
                age = float("inf")
        total_pending = sum(pending_new.values()) + sum(pending_rev.values())
        state, detail = verdict_lag(age, total_pending, args.max_lag_seconds)
        metrics["last_import"] = newest_import
        metrics["last_import_age_s"] = None if age == float("inf") else round(age)
        checks.append(_check("import_lag", state, detail, pending=total_pending,
                             max_lag_seconds=args.max_lag_seconds))

    # 4. reverse_divergence --------------------------------------------------
    allow_raw = load_json(args.allowlist)
    allowed: set[str] = set()
    if isinstance(allow_raw, dict):
        for item in allow_raw.get("ids") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                allowed.add(item["id"])
            elif isinstance(item, str):
                allowed.add(item)
    leaked_rows = [
        {"id": row["id"], "slot": row["slot"], "created_at": row["created_at"]}
        for row in conn.execute(
            "SELECT id, slot, created_at FROM units "
            "WHERE origin = 'cli' AND state = 'active' ORDER BY created_at"
        )
    ]
    state, detail = verdict_divergence(leaked_rows, allowed)
    checks.append(_check("reverse_divergence", state, detail,
                         leaked=[row["id"] for row in leaked_rows if row["id"] not in allowed][:10]))

    # 5. expand_path ---------------------------------------------------------
    hint_text = ""
    try:
        with open(_expand(args.inject_text), encoding="utf-8") as fh:
            hint_text = fh.read()
    except OSError:
        hint_text = ""
    hint_state, hint_detail = (verdict_expand_hint(hint_text) if hint_text
                               else (STATE_NA, f"hint source unreadable: {args.inject_text}"))
    target = conn.execute(
        "SELECT id FROM units WHERE track = 'memory' AND state = 'active' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if target is None:
        expand_state, expand_detail = STATE_NA, "no active memory unit to expand"
    else:
        unit_id = target["id"]
        try:
            rc, payload = mem_json(REPO_ROOT, ["expand", "--id", unit_id])
        except (subprocess.TimeoutExpired, OSError) as exc:
            rc, payload = 99, {"raw": str(exc)}
        data = payload.get("data") if isinstance(payload, dict) else None
        body = ""
        if isinstance(data, dict):
            for key in ("text", "body", "content", "unit", "entry"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    body = value
                    break
        if rc != 0:
            expand_state = STATE_FAIL
            expand_detail = f"`mem expand --id {unit_id}` rc={rc}"
        elif len(body) < 20:
            expand_state = STATE_FAIL
            expand_detail = f"`mem expand --id {unit_id}` returned no body ({len(body)} chars)"
        else:
            expand_state = STATE_PASS
            expand_detail = f"`mem expand --id {unit_id}` returned {len(body)} chars"
    combined_state = STATE_FAIL if STATE_FAIL in (expand_state, hint_state) else (
        STATE_PASS if STATE_PASS in (expand_state, hint_state) else STATE_NA)
    checks.append(_check("expand_path", combined_state, f"cli: {expand_detail}; hint: {hint_detail}",
                         cli_state=expand_state, hint_state=hint_state))

    # 6. alias_cwd -----------------------------------------------------------
    pairs = alias_pairs(args.alias_map)
    if not pairs:
        checks.append(_check("alias_cwd", STATE_NA, f"no declared alias pairs in {args.alias_map}"))
    else:
        def key_count(cwd: str) -> int | None:
            try:
                rc, payload = mem_json(REPO_ROOT, ["snapshot", "--json", "--cwd", cwd,
                                                  "--max-entries", "600"], timeout=180)
            except (subprocess.TimeoutExpired, OSError):
                return None
            if rc != 0 or not isinstance(payload, dict):
                return None
            # ``mem snapshot`` returns its payload at the top level; ``import-evolve``
            # wraps it in ``data``. Handle both rather than assuming one envelope.
            data = payload.get("data") or payload
            tracks = data.get("tracks") or {}
            key = tracks.get("key") or {}
            entries = key.get("entries") if isinstance(key, dict) else None
            if isinstance(entries, list):
                return len(entries)
            if isinstance(key, dict) and isinstance(key.get("count"), int):
                return int(key["count"])
            return 0

        usable = None
        for pair in pairs:
            live = key_count(pair["to"])
            if live:
                usable = (pair, live)
                break
        if usable is None:
            checks.append(_check("alias_cwd", STATE_NA,
                                 "no declared alias pair whose live path carries project KEY entries"))
        else:
            pair, live = usable
            aliased = key_count(pair["from"])
            if aliased is None or live is None:
                checks.append(_check("alias_cwd", STATE_NA,
                                     f"snapshot failed for {pair['from']} -> {pair['to']}"))
            elif aliased == live:
                checks.append(_check("alias_cwd", STATE_PASS,
                                     f"{pair['from']} resolves to the live project ({live} key entries)"))
            else:
                checks.append(_check("alias_cwd", STATE_FAIL,
                                     f"{pair['from']} key={aliased} vs {pair['to']} key={live}",
                                     pair=pair, counts={"from": aliased, "to": live}))

    # 7. volume --------------------------------------------------------------
    render = newest_inject_render(args.renders_dir)
    max_chars = max_chars_from_patch(args.profile_patch)
    state, detail = verdict_volume(render, max_chars, now)
    checks.append(_check("volume", state, detail, max_chars=max_chars))

    # 8. importer_alive ------------------------------------------------------
    log_age = file_age_seconds(args.import_log)
    state, detail = verdict_importer_alive(log_age, args.importer_max_age_hours)
    checks.append(_check("importer_alive", state, detail, log=args.import_log,
                         age_seconds=None if log_age is None else round(log_age, 1)))

    # 9. render_health -------------------------------------------------------
    window = inject_renders_since(args.renders_dir, args.render_window_hours, now)
    state, detail = verdict_render_health(window, args.render_window_hours)
    checks.append(_check("render_health", state, detail, renders=len(window),
                         window_hours=args.render_window_hours))

    failures = [row["name"] for row in checks if row["state"] == STATE_FAIL]
    unjudgeable = [row["name"] for row in checks if row["state"] == STATE_NA]
    verdict = "fail" if failures else "pass"
    return {
        "verdict": verdict,
        "rc": 1 if failures else 0,
        "reason": "; ".join(failures) or None,
        "checks": checks,
        "failures": failures,
        "unjudgeable": unjudgeable,
        "metrics": metrics,
    }


# --------------------------------------------------------------------------- #
# Self-test (negative controls included)
# --------------------------------------------------------------------------- #


def self_test() -> int:
    cases: list[tuple[str, tuple[str, str], str]] = []

    def t(label: str, got: tuple[str, str], want: str) -> None:
        cases.append((label, got, want))

    base = {"memory": 340, "user": 5, "key": 817, "project": 2888, "daily": 2927}
    t("parity: exact", verdict_parity(base, dict(base), {k: 0 for k in base}), STATE_PASS)
    t("parity: diff equals pending",
      verdict_parity(base, {**base, "key": 816}, {**{k: 0 for k in base}, "key": 1}), STATE_PASS)
    t("parity: dropped entry (negative control)",
      verdict_parity(base, {**base, "key": 800}, {k: 0 for k in base}), STATE_FAIL)
    t("parity: ledger ahead (negative control)",
      verdict_parity(base, {**base, "key": 900}, {k: 0 for k in base}), STATE_FAIL)

    t("lag: quiet source", verdict_lag(3600 * 6, 0), STATE_PASS)
    t("lag: pending inside contract", verdict_lag(120, 3), STATE_PASS)
    t("lag: pending outside contract (negative control)", verdict_lag(1200, 3), STATE_FAIL)
    t("lag: pending exactly at boundary", verdict_lag(300, 1), STATE_PASS)

    rows = [{"id": "unit_a", "slot": "s"}, {"id": "unit_b", "slot": None}]
    t("divergence: allowlisted", verdict_divergence([rows[0]], {"unit_a"}), STATE_PASS)
    t("divergence: leaked (negative control)", verdict_divergence(rows, {"unit_a"}), STATE_FAIL)
    t("divergence: no rows", verdict_divergence([], set()), STATE_PASS)

    now = _dt.datetime(2026, 11, 16, tzinfo=_dt.timezone.utc)
    fresh = {"ts": "2026-11-16T00:00:00Z", "chars": {"total": 82_207}, "truncated": 0}
    t("volume: fresh", verdict_volume(fresh, 100_000, now), STATE_PASS)
    t("volume: per-track truncated dict (renderer shape)",
      verdict_volume({**fresh, "truncated": {"memory": 0, "user": 0, "key": 2}}, 100_000, now), STATE_FAIL)
    t("volume: per-track dict all zero",
      verdict_volume({**fresh, "truncated": {"memory": 0, "user": 0, "key": 0}}, 100_000, now), STATE_PASS)
    t("volume: truncated (negative control)",
      verdict_volume({**fresh, "truncated": 3}, 100_000, now), STATE_FAIL)
    t("volume: over budget (negative control)",
      verdict_volume({**fresh, "chars": {"total": 120_000}}, 100_000, now), STATE_FAIL)
    t("volume: stale window", verdict_volume({**fresh, "ts": "2026-11-10T00:00:00Z"}, 100_000, now), STATE_NA)
    t("volume: missing", verdict_volume(None, 100_000, now), STATE_NA)

    t("expand hint: points at cli", verdict_expand_hint("用 `mem expand --id X` 取全文"), STATE_PASS)
    t("expand hint: tool-only (negative control)",
      verdict_expand_hint("用 memory action=expand+id 取全文"), STATE_FAIL)

    t("importer alive: fresh", verdict_importer_alive(600), STATE_PASS)
    t("importer alive: exactly at tolerance", verdict_importer_alive(IMPORTER_MAX_AGE_HOURS * 3600), STATE_PASS)
    t("importer alive: silent past tolerance (negative control)",
      verdict_importer_alive(IMPORTER_MAX_AGE_HOURS * 3600 + 60), STATE_FAIL)
    t("importer alive: log unreadable", verdict_importer_alive(None), STATE_NA)

    ok_render = {"ts": "2026-11-16T00:00:00Z", "mode": "inject", "ok": True}
    t("render health: all ok", verdict_render_health([ok_render, ok_render]), STATE_PASS)
    t("render health: one timeout (negative control)",
      verdict_render_health([ok_render, {**ok_render, "ok": False, "error": "timeout"}]), STATE_FAIL)
    t("render health: empty window", verdict_render_health([]), STATE_NA)

    bad = 0
    for label, (state, detail), want in cases:
        ok = state == want
        bad += 0 if ok else 1
        print(("ok   " if ok else "FAIL ") + f"{label}: {state} (want {want}) — {detail}")
    print(f"self-test: {len(cases) - bad}/{len(cases)} pass")
    return 0 if bad == 0 else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def render_human(payload: dict[str, Any]) -> str:
    lines = [f"verdict={payload['verdict']} rc={payload['rc']}"]
    for row in payload["checks"]:
        lines.append(f"  [{row['state']:4}] {row['name']}: {row['detail']}")
    if payload.get("unjudgeable"):
        lines.append("na: " + ", ".join(payload["unjudgeable"]))
    if payload.get("failures"):
        lines.append("failures: " + ", ".join(payload["failures"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dual-write observability (plan §7.4)")
    parser.add_argument("--json", action="store_true", help="emit the machine payload only")
    parser.add_argument("--self-test", action="store_true", help="run the pure-function controls")
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--renders-dir", default=DEFAULT_RENDERS_DIR)
    parser.add_argument("--allowlist", default=DEFAULT_ALLOWLIST)
    parser.add_argument("--alias-map", default=DEFAULT_ALIAS_MAP)
    parser.add_argument("--profile-patch", default=DEFAULT_PROFILE_PATCH)
    parser.add_argument("--inject-text", default=DEFAULT_INJECT_TEXT)
    parser.add_argument("--import-log", default=DEFAULT_IMPORT_LOG)
    parser.add_argument("--importer-max-age-hours", type=float, default=IMPORTER_MAX_AGE_HOURS)
    parser.add_argument("--render-window-hours", type=float, default=RENDER_WINDOW_HOURS)
    parser.add_argument("--max-lag-seconds", type=float, default=MAX_LAG_SECONDS)
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    payload = collect(args)
    payload["generatedAt"] = _iso(_now())
    payload["contract"] = {"max_lag_seconds": args.max_lag_seconds, "hard_lag_seconds": HARD_LAG_SECONDS}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_human(payload))
    return int(payload["rc"])


if __name__ == "__main__":
    sys.exit(main())
