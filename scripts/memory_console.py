#!/usr/bin/env python3
"""One place to see the whole memory system — and the only one that checks all of it.

Why this exists (2026-10-14). Memory had instruments but no single view, and the
instruments covered only part of the system:

    surface              before this script
    -------------------  ---------------------------------------------------
    shadow mirror (M3)   hourly thresholds -> Uptime Kuma -> Feishu  (good)
    primary (Nowledge)   nothing scheduled; `nmem status/doctor` was manual only
    write path (DSH ->    nothing; mem-sync-evolve.py ran hourly into a log
      Nowledge)          nobody read
    local profiles        nothing at all — which is where a test suite appended
                         records to production memory for months unnoticed

It aggregates the four surfaces into one report, records a per-profile snapshot
so *growth* is visible, and (in `alert` mode) evaluates the thresholds the
existing shadow report structurally cannot see. The shadow's own health stays
where it is (`scripts/shadow_report.py`): this script *reuses* its readers and
its delivery channel rather than re-implementing either, because a second
implementation of "how to read the shadow" is a second thing to drift.

Read-only with respect to memory. The only writes are its own state directory:
the snapshot file and its alert ledger.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# Two entries, for the reason `shadow_report.cap_ledger` documents: running this
# as `python3 scripts/memory_console.py` (which is how launchd runs it, and how it
# is meant to be used) puts `scripts/` on sys.path but *not* the repository root,
# so `import shadow_report` works while `import memory_tool` does not. Pytest
# inserts the rootdir and hides exactly this, so it is not a test-detectable
# problem: it fails on the first real run.
for entry in (str(HERE), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import shadow_report as shadow  # noqa: E402  - shares the M3 readers and delivery

STATE_DIR = Path(os.environ.get("MEMORY_CONSOLE_STATE_DIR", shadow.STATE_DIR))
SNAPSHOT = STATE_DIR / "memory-console-snapshot.json"
LEDGER = STATE_DIR / "memory-console-alerts.jsonl"

MEM_SYNC_LOG = Path(os.environ.get(
    "MEMORY_CONSOLE_MEM_SYNC_LOG", Path.home() / ".dsh/logs/mem-sync.out.log"))
DSH_MEMORIES = Path(os.environ.get(
    "MEMORY_CONSOLE_DSH_MEMORIES", Path.home() / ".dsh/memories"))
NMEM = os.environ.get("MEMORY_CONSOLE_NMEM", "nmem")
# The direct Feishu path used by the other scheduled jobs (`disk-cleanup.sh
# --push`, the headless scheduler jobs). Preferred over the Uptime Kuma monitor
# for this watcher, for two reasons measured on 2026-10-14: Kuma's *notification
# text* carries only the monitor name, so console findings would arrive as
# "不可用：los-memory-shadow-alerts" about a healthy shadow; and fixing that name
# needs a container restart, which would blip six 60-second monitors and produce
# exactly the false-noise this exercise exists to remove. This script's own
# message names itself.
FEISHU_PUSH = Path(os.environ.get(
    "MEMORY_CONSOLE_FEISHU_PUSH", Path.home() / ".dsh/scripts/feishu-push.sh"))

# Thresholds. Each is a stated budget with a reason, not a tuned guess.
MAX_WRITE_PATH_AGE_HOURS = 6     # mem-sync-evolve is hourly; two missed runs is silence
REVIEW_BACKLOG_BUDGET = 500     # the primary's own maintenance backlog, its health field
REVIEW_EXPIRY_WARN_DAYS = 7     # a pending review that expires gets no second notice
PROFILE_GROWTH_ALERT_ROWS = 5   # local profiles are legacy: any real growth is unexpected
MAX_SHADOW_ALERT_SILENCE_HOURS = 2.5  # the shadow watcher is hourly; 2 misses is silence
NMEM_TIMEOUT_SECONDS = 30

PROFILES = ("codex", "claude", "shared")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def run_nmem(args, timeout=NMEM_TIMEOUT_SECONDS):
    """`nmem -j` returns JSON on stdout; a failure is a fact, not an exception.

    `cli_missing` is separated from every other failure on purpose. The first run
    of this script under launchd reported `primary_unreachable` because launchd's
    PATH does not include `/usr/local/bin`, where `nmem` lives — a healthy primary
    reported as down. A watcher that cries wolf about its own configuration is
    the failure this whole exercise is trying to avoid, so "I cannot look" is a
    different code from "it is not there".
    """
    try:
        result = subprocess.run([NMEM, *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, f"cli_missing: {NMEM!r} is not on PATH ({os.environ.get('PATH', '')})"
    except (OSError, subprocess.SubprocessError) as error:
        return None, f"{type(error).__name__}: {error}"[:200]
    if result.returncode != 0:
        return None, (result.stderr or result.stdout).strip()[:200]
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as error:
        return None, f"unparsable JSON: {error}"[:200]


def primary_state():
    """Primary health: reachability, versions, and the review inbox.

    The inbox matters as much as reachability: a reachable primary with a pending
    contradiction review is a decision waiting on a human, and its own
    `automatic_resolution` will discard it silently when it expires.
    """
    status, error = run_nmem(["status", "-j"])
    if status is None:
        return {"reachable": False, "error": error}
    state = {
        "reachable": True,
        "status": status.get("status"),
        "server_version": status.get("server_version") or status.get("version"),
        "cli_version": status.get("cli_version"),
        "api_url": status.get("api_url"),
        "database": status.get("database"),
        "embedding_degraded": (status.get("embedding") or {}).get("degraded"),
        "update_available": (status.get("update") or {}).get("update_available"),
    }
    stats, stats_error = run_nmem(["stats", "-j"])
    if stats is not None:
        state["stats"] = stats
    else:
        state["stats_error"] = stats_error

    inbox, inbox_error = run_nmem(["feed", "inbox", "-j"])
    if inbox is None:
        state["inbox_error"] = inbox_error
        return state
    health = (inbox.get("health") or {}).get("health") or {}
    state["review_count"] = inbox.get("review_count") or len(inbox.get("reviews") or [])
    state["attention_count"] = inbox.get("attention_count")
    state["health"] = {
        "state": health.get("state"),
        "pressure": health.get("pressure"),
        "review_pressure": health.get("review_pressure"),
        "review_ratio": health.get("review_ratio"),
        "cleanup_backlog": health.get("cleanup_review_backlog_count"),
        "relation_backlog": health.get("relation_review_backlog_count"),
        "total_backlog": health.get("total_maintenance_review_backlog_count"),
        "growth_per_day": health.get("active_latest_growth_per_day"),
        "daily_review_limit": health.get("maintenance_daily_review_limit"),
    }
    state["reviews"] = [
        {
            "id": review.get("id"),
            "type": review.get("event_type"),
            "severity": review.get("severity"),
            "title": review.get("title"),
            "created_at": review.get("created_at"),
            "expires_at": ((review.get("review") or {}).get("expires_at")),
            "recommended_action": ((review.get("review") or {}).get("recommended_action")),
        }
        for review in (inbox.get("reviews") or [])
    ]
    return state


def write_path_state():
    """The DSH -> Nowledge write path (`mem-sync-evolve.py`, hourly on M1).

    Its stdout is one JSON object per run; nothing has ever read it, so a run
    that starts failing (or stops happening) is invisible. Failures are counted
    in-band (`failed`), and staleness is the log's own mtime.
    """
    state = {"path": str(MEM_SYNC_LOG), "exists": MEM_SYNC_LOG.exists()}
    if not MEM_SYNC_LOG.exists():
        return state
    mtime = MEM_SYNC_LOG.stat().st_mtime
    state["age_hours"] = round((datetime.datetime.now().timestamp() - mtime) / 3600, 2)
    state["last_run_at"] = datetime.datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
    last = None
    with MEM_SYNC_LOG.open(errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith("{"):
                last = line
    if last:
        try:
            state["last"] = json.loads(last)
        except json.JSONDecodeError:
            state["last_error"] = "last line is not JSON"
    if DSH_MEMORIES.exists():
        tracks = list((DSH_MEMORIES / "projects").glob("*/MEMORY.md"))
        daily = sorted((DSH_MEMORIES / "daily").glob("*.md"))
        state["dsh_tracks"] = len(tracks)
        state["dsh_daily_files"] = len(daily)
        state["dsh_latest_daily"] = daily[-1].name if daily else None
    return state


def profile_rows(db_path):
    """Row count and newest record for one profile, or why it could not be read."""
    if not Path(db_path).exists():
        return {"exists": False}
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            row = connection.execute(
                "SELECT COUNT(*), MAX(timestamp) FROM observations").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return {"exists": True, "error": f"{type(error).__name__}: {error}"[:120]}
    return {"exists": True, "rows": row[0], "newest": row[1],
            "bytes": Path(db_path).stat().st_size}


def local_profiles_state():
    """The three local profiles, as data rather than as a warning in a docstring.

    These are legacy stores now that Nowledge is the write target, so *growth* is
    the signal: the test suite that wrote 18 rows into `shared` on every run was
    invisible precisely because nobody ever looked at a row count over time.
    """
    from memory_tool.utils import resolve_db_path

    state = {}
    for profile in PROFILES:
        path = resolve_db_path(profile, None)
        state[profile] = {"path": path, **profile_rows(path)}
    return state


def read_snapshot(path=SNAPSHOT):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_snapshot(state, taken_at, path=SNAPSHOT):
    Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(
        {"taken_at": taken_at,
         "profiles": {name: data.get("rows") for name, data in state.items()}},
        ensure_ascii=False, indent=2))


def profile_growth(current, snapshot) -> dict:
    """Rows added per profile since the snapshot, when one exists to compare."""
    if not snapshot:
        return {}
    growth = {}
    for name, data in current.items():
        before = (snapshot.get("profiles") or {}).get(name)
        after = data.get("rows")
        if isinstance(before, int) and isinstance(after, int):
            growth[name] = after - before
    return growth


def shadow_state():
    """The shadow side, delegated: its health rules stay in one place."""
    status = shadow.fetch_status()
    probe = shadow.recall_probe_state()
    backups = shadow.backup_state() or {}
    last_backup = backups.get("last") or {}
    alerts = []
    alert_age = None
    if shadow.ALERTS.exists():
        alert_age = round((datetime.datetime.now().timestamp()
                           - shadow.ALERTS.stat().st_mtime) / 3600, 2)
        with shadow.ALERTS.open(errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    alerts = json.loads(line).get("alerts", [])
                except json.JSONDecodeError:
                    continue
    return {
        "total": status.get("total"),
        "active": status.get("active"),
        "sync": {key: (status.get("sync") or {}).get(key)
                 for key in ("missing", "errors", "tombstones_deferred")},
        "index_state": (status.get("search_index") or {}).get("state"),
        "storage": status.get("storage"),
        "metering": status.get("metering"),
        "contract": status.get("contract"),
        "probe_verdict": (probe or {}).get("verdict"),
        "probe_recent_rate": (probe or {}).get("recent_rate"),
        "backup_age_hours": backups.get("newest_age_hours"),
        "backup_readback_ok": last_backup.get("readback_ok"),
        "backup_records": last_backup.get("records"),
        "alert_ledger_age_hours": alert_age,
        "active_alerts": alerts,
    }


def collect(snapshot=None):
    """Gather every surface. The shadow is the only remote read."""
    state = {"generated_at": now_iso()}
    state["shadow"] = shadow_state()
    state["primary"] = primary_state()
    state["write_path"] = write_path_state()
    profiles = local_profiles_state()
    state["local_profiles"] = profiles
    state["profile_growth"] = profile_growth(profiles, snapshot if snapshot is not None
                                             else read_snapshot())
    return state


def evaluate_alerts(state, now=None):
    """Thresholds for the surfaces the shadow report cannot see.

    One code per condition, and each names a budget that a reader can disagree
    with — the same contract `shadow_report.evaluate_alerts` follows.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    alerts = []

    # Watch the watcher. The hourly shadow report cannot detect its own silence
    # (it is the instrument that would report it), and it has already gone quiet
    # once: on 2026-10-10 it missed four hourly runs while M1 slept. This script
    # runs on a different schedule, so its own ledger age is evidence about that
    # job from outside it.
    alert_ledger_age = (state.get("shadow") or {}).get("alert_ledger_age_hours")
    if not isinstance(alert_ledger_age, (int, float)):
        alerts.append({"code": "shadow_alert_stale", "severity": "high",
                       "detail": "no shadow alert ledger to date: the hourly shadow watcher "
                                 "has never written one"})
    elif alert_ledger_age > MAX_SHADOW_ALERT_SILENCE_HOURS:
        alerts.append({"code": "shadow_alert_stale", "severity": "high",
                       "detail": f"the hourly shadow alert ledger has not been written for "
                                 f"{alert_ledger_age} h (budget "
                                 f"{MAX_SHADOW_ALERT_SILENCE_HOURS} h): the shadow watcher "
                                 f"itself may be silent"})

    primary = state.get("primary") or {}
    if not primary.get("reachable"):
        error = str(primary.get("error") or "")
        if error.startswith("cli_missing"):
            alerts.append({"code": "primary_cli_missing", "severity": "high",
                           "detail": f"cannot inspect the primary at all: {error}"})
        else:
            alerts.append({"code": "primary_unreachable", "severity": "high",
                           "detail": f"nmem status failed: {error}"})
    else:
        if primary.get("database") is False or primary.get("status") not in (None, "ok"):
            alerts.append({"code": "primary_unhealthy", "severity": "high",
                           "detail": f"status={primary.get('status')} "
                                     f"database={primary.get('database')}"})
        if primary.get("embedding_degraded"):
            alerts.append({"code": "primary_embedding_degraded", "severity": "high",
                           "detail": "the primary reports its embedding mode as degraded"})

        reviews = primary.get("reviews") or []
        if reviews:
            expiries = [review["expires_at"] for review in reviews if review.get("expires_at")]
            nearest, days = None, None
            for expiry in expiries:
                try:
                    moment = datetime.datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                except ValueError:
                    continue
                remaining = (moment - now).days
                if days is None or remaining < days:
                    nearest, days = expiry, remaining
            severity = "high" if (days is not None and days < REVIEW_EXPIRY_WARN_DAYS) else "medium"
            detail = f"{len(reviews)} pending review(s)"
            if nearest:
                try:
                    local_day = datetime.datetime.fromisoformat(nearest.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
                except ValueError:
                    local_day = "unknown"
                detail += f"; nearest auto-discard {local_day} ({days} day(s))"
            alerts.append({"code": "primary_reviews_pending", "severity": severity,
                           "detail": detail})

        health = primary.get("health") or {}
        backlog = health.get("total_backlog")
        if isinstance(backlog, int) and backlog > REVIEW_BACKLOG_BUDGET:
            alerts.append({"code": "primary_review_backlog", "severity": "medium",
                           "detail": f"maintenance backlog {backlog} "
                                     f"(cleanup {health.get('cleanup_backlog')}, "
                                     f"relation {health.get('relation_backlog')}), "
                                     f"pressure={health.get('pressure')}, "
                                     f"review limit {health.get('daily_review_limit')}/day vs "
                                     f"growth {health.get('growth_per_day')}/day"})

    write_path = state.get("write_path") or {}
    if not write_path.get("exists"):
        alerts.append({"code": "memory_write_path_missing", "severity": "high",
                       "detail": f"{write_path.get('path')} does not exist"})
    else:
        age = write_path.get("age_hours")
        if isinstance(age, (int, float)) and age > MAX_WRITE_PATH_AGE_HOURS:
            alerts.append({"code": "memory_write_path_stale", "severity": "high",
                           "detail": f"last run {age} h ago "
                                     f"(budget {MAX_WRITE_PATH_AGE_HOURS} h)"})
        failed = (write_path.get("last") or {}).get("failed")
        if failed:
            alerts.append({"code": "memory_write_path_failed", "severity": "high",
                           "detail": f"last run reported failed={failed}"})

    for name, delta in (state.get("profile_growth") or {}).items():
        if delta >= PROFILE_GROWTH_ALERT_ROWS:
            alerts.append({"code": "local_profile_growth", "severity": "medium",
                           "detail": f"{name} gained {delta} rows since the last snapshot; "
                                     f"these profiles are legacy, so unexpected writes show "
                                     f"up here"})
    return alerts


def render(state, alerts=None):
    """Human report. Numbers first, then the one line that says what to do."""
    shadow_state_ = state["shadow"]
    primary = state["primary"]
    write_path = state["write_path"]
    lines = ["", "=" * 72, f"记忆总览  {state['generated_at']}", "=" * 72, ""]

    lines.append("[影子镜像 / M3]")
    storage = shadow_state_.get("storage") or {}
    lines.append(f"  记录        total {shadow_state_.get('total')} / "
                 f"active {shadow_state_.get('active')}   索引 {shadow_state_.get('index_state')}")
    lines.append(f"  同步        missing {shadow_state_['sync'].get('missing')} / "
                 f"errors {shadow_state_['sync'].get('errors')} / "
                 f"墓碑延迟 {shadow_state_['sync'].get('tombstones_deferred')}")
    lines.append(f"  存储        {_mb(storage.get('file_bytes'))} "
                 f"(freelist {_mb(storage.get('freelist_bytes'))}, "
                 f"FTS {_mb(storage.get('fts_index_bytes'))})")
    lines.append(f"  召回探针    {shadow_state_.get('probe_verdict')} "
                 f"(recent_rate {shadow_state_.get('probe_recent_rate')})")
    lines.append(f"  异机备份    {shadow_state_.get('backup_age_hours')} h 前, "
                 f"readback {shadow_state_.get('backup_readback_ok')}")
    active = shadow_state_.get("active_alerts") or []
    lines.append(f"  活动告警    {len(active)}" +
                 ("" if not active else "  " + "; ".join(
                     f"{a.get('code')}({a.get('severity')})" for a in active)))

    lines.append("")
    lines.append("[主库 / Nowledge]")
    if not primary.get("reachable"):
        lines.append(f"  不可达      {primary.get('error')}")
    else:
        stats = primary.get("stats") or {}
        lines.append(f"  状态        {primary.get('status')}  server "
                     f"{primary.get('server_version')} / cli {primary.get('cli_version')}"
                     f"  db {primary.get('database')}")
        lines.append(f"  规模        memories {stats.get('memories')} / "
                     f"threads {stats.get('threads')} / entities {stats.get('entities')} / "
                     f"labels {stats.get('labels')}")
        health = primary.get("health") or {}
        lines.append(f"  健康        {health.get('state')} "
                     f"(pressure {health.get('pressure')}, ratio {health.get('review_ratio')})")
        lines.append(f"  维护 backlog 总 {health.get('total_backlog')} "
                     f"(清理 {health.get('cleanup_backlog')} / 关系 {health.get('relation_backlog')})"
                     f"   产能 {health.get('daily_review_limit')}/日 vs 增长 "
                     f"{health.get('growth_per_day')}/日")
        reviews = primary.get("reviews") or []
        lines.append(f"  待裁决      {len(reviews)} 条")
        for review in reviews:
            lines.append(f"      - [{review.get('type')}] {str(review.get('title'))[:60]}")
            lines.append(f"        创建 {review.get('created_at')}  建议 "
                         f"{review.get('recommended_action')}  自动处置 {review.get('expires_at')}")

    lines.append("")
    lines.append("[写入路径 / DSH -> 主库]")
    last = write_path.get("last") or {}
    lines.append(f"  mem-sync    {write_path.get('age_hours')} h 前  "
                 f"created {last.get('created')} / updated {last.get('updated')} / "
                 f"skipped {last.get('skipped')} / failed {last.get('failed')}")
    lines.append(f"  DSH 记忆轨  {write_path.get('dsh_tracks')} 个项目轨, "
                 f"{write_path.get('dsh_daily_files')} 个日志（最新 "
                 f"{write_path.get('dsh_latest_daily')}）")

    lines.append("")
    lines.append("[本地 profile（遗留存储，增长即异常）]")
    for name, data in (state.get("local_profiles") or {}).items():
        delta = (state.get("profile_growth") or {}).get(name)
        mark = "" if delta in (None, 0) else f"   Δ{delta:+d} 自上次快照"
        if data.get("exists"):
            lines.append(f"  {name:8s} {data.get('rows')} 行  最新 {data.get('newest')}"
                         f"  {_mb(data.get('bytes'))}{mark}")
        else:
            lines.append(f"  {name:8s} 不存在")

    if alerts is not None:
        lines.append("")
        lines.append("[阈值判定]")
        if not alerts:
            lines.append("  全部满足")
        for alert in alerts:
            lines.append(f"  {alert['severity']:6s} {alert['code']}: {alert['detail']}")

    lines.append("")
    return "\n".join(lines)


def _mb(value):
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value / 1048576:.1f} MiB"


def previous_alerts():
    """The alert set recorded by the last run, so recovery can be announced."""
    if not LEDGER.exists():
        return None
    last = None
    with LEDGER.open(errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line:
                last = line
    if not last:
        return None
    try:
        return json.loads(last).get("alerts")
    except json.JSONDecodeError:
        return None


def deliver(alerts, generated_at, before=None, runner=subprocess.run):
    """Send the alert set (or its clearing) to Feishu through the shared script.

    `--key` makes the send idempotent per (day, state): the same findings do not
    repeat every three hours, while a changed set is a new message. Losing the
    channel is recorded, never raised, and makes the job exit non-zero — a
    watcher that cannot speak must not look like a watcher with nothing to say.
    """
    if not FEISHU_PUSH.exists():
        return {"state": "unconfigured", "detail": f"{FEISHU_PUSH} not found"}
    if not alerts and not before:
        return {"state": "silent", "detail": "no alerts, and none to clear"}
    codes = ",".join(sorted(alert["code"] for alert in alerts)) or "clear"
    day = generated_at[:10]
    key = f"memory-console-{day}-{codes}"[:120]
    if alerts:
        body = "记忆总览：" + "; ".join(
            f"{alert['code']}({alert['severity']}) {alert['detail']}" for alert in alerts)
    else:
        body = "记忆总览：已恢复正常（上次告警 " + ", ".join(
            sorted({alert.get("code", "?") for alert in before})) + "）"
    try:
        result = runner([str(FEISHU_PUSH), "--key", key, "--max-chars", "900", body],
                        capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        return {"state": "failed", "detail": f"{type(error).__name__}: {error}"[:200]}
    detail = (result.stdout or result.stderr or "").strip()[:200]
    if result.returncode != 0:
        return {"state": "failed", "detail": f"exit {result.returncode}: {detail}"}
    return {"state": "pushed", "key": key, "detail": detail}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="report", choices=("report", "alert"))
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--no-notify", action="store_true",
                        help="alert mode: record and exit non-zero, but do not push")
    args = parser.parse_args(argv)

    snapshot = read_snapshot()
    state = collect(snapshot=snapshot)
    generated_at = state["generated_at"]

    if args.action == "report":
        alerts = evaluate_alerts(state)
        if args.json:
            print(json.dumps({**state, "alerts": alerts}, ensure_ascii=False, indent=2,
                             default=str))
        else:
            print(render(state, alerts))
        write_snapshot(state["local_profiles"], generated_at)
        return 0

    alerts = evaluate_alerts(state)
    write_snapshot(state["local_profiles"], generated_at)

    before = previous_alerts()
    delivery = ({"state": "suppressed", "detail": "--no-notify"}
                if args.no_notify else deliver(alerts, generated_at, before=before))
    row = {"ts": datetime.datetime.now().timestamp(), "generated_at": generated_at,
           "alerts": alerts, "delivery": delivery}
    LEDGER.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LEDGER.open("a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    # This script has its own ledger, so it must cap its own ledger: a second
    # writer with an unbounded file is exactly the failure the first one fixed.
    row["ledger_cap"] = shadow.cap_ledger(path=LEDGER)

    print(json.dumps(row, ensure_ascii=False))
    return 1 if (alerts or delivery["state"] == "failed") else 0

if __name__ == "__main__":
    raise SystemExit(main())
