#!/usr/bin/env python3
"""Period operation report and alert evaluation for the M3 Nowledge shadow.

Reads only: the M3 sync log, the M3 shadow database (via `shadow status` /
`shadow summary`), and the local off-host backup ledger. Nothing here writes to
M3, so it is safe to run on a schedule.

Why the log *and* the database: the log holds per-run history (and survives a
database rebuild), while the database holds point-in-time coverage, freshness and
the metering ledger. A report that reads only one of them cannot tell "the job
stopped running" apart from "the job runs but nothing changes".
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

M3_HOST = os.environ.get("SHADOW_M3_HOST", "m3-t")
M3_LOG = os.environ.get("SHADOW_M3_LOG", "~/.local/share/los-memory-shadow/sync.out.log")
M3_DB = os.environ.get("SHADOW_M3_DB", "~/.local/share/los-memory-shadow/shadow.sqlite3")
STATE_DIR = Path(os.environ.get("SHADOW_STATE_DIR",
                                Path.home() / ".local/share/los-memory-shadow"))
LEDGER = STATE_DIR / "backup-ledger.jsonl"
ALERTS = STATE_DIR / "alerts.jsonl"

# Thresholds. Each is a stated budget from the design docs, not a tuned guess.
MAX_OLDEST_VERIFY_HOURS = 24      # migration gate 1
MAX_SILENCE_MINUTES = 30          # 300 s cadence + slack before "job died"
MAX_BACKUP_AGE_HOURS = 24         # RPO gate
MANIFEST_DROP_RATIO = 0.95        # a listing that suddenly shrinks is suspicious


def ssh(host, command):
    return subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                           host, command], capture_output=True, check=False)


def fetch_log():
    result = ssh(M3_HOST, f"cat {M3_LOG}")
    if result.returncode != 0:
        raise SystemExit("cannot read the M3 sync log: " + result.stderr.decode()[:500])
    runs = []
    for line in result.stdout.decode(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return runs


def fetch_status():
    result = ssh(M3_HOST, f"cd ~ && python3 -m memory_tool.shadow status --db {M3_DB}")
    if result.returncode != 0:
        # fall back to the deployed release, which has the module on its path
        result = ssh(M3_HOST, "cd ~/.local/share/los-memory-shadow && "
                              f"ls -td releases/*/ | head -1 | xargs -I{{}} sh -c "
                              f"'cd {{}} && python3 -m memory_tool.shadow status --db {M3_DB}'")
    if result.returncode != 0:
        raise SystemExit("cannot read shadow status: " + result.stderr.decode()[:500])
    return json.loads(result.stdout.decode())


def disk_state():
    """Data-directory growth, and the WAL that a reader must never copy raw."""
    result = ssh(M3_HOST, "cd ~/.local/share/los-memory-shadow && "
                          "du -sk . 2>/dev/null | cut -f1 && "
                          "stat -f '%z' shadow.sqlite3 2>/dev/null && "
                          "stat -f '%z' shadow.sqlite3-wal 2>/dev/null || true")
    lines = [line.strip() for line in result.stdout.decode().splitlines() if line.strip()]
    if result.returncode != 0 or not lines:
        return {"error": "unavailable"}
    values = [int(line) for line in lines if line.isdigit()]
    return {"total_bytes": values[0] * 1024 if len(values) > 0 else None,
            "db_bytes": values[1] if len(values) > 1 else None,
            "wal_bytes": values[2] if len(values) > 2 else None}


def handshake_state():
    """Probe the *same* path clients use, so the report answers 'can clients reach it'.

    A config file existing is not reachability; this performs a real MCP
    initialize + tools/list over the launcher the clients point at.
    """
    script = ('printf \'%s\\n\' \'{"jsonrpc":"2.0","id":1,"method":"initialize",'
              '"params":{"protocolVersion":"2025-06-18"}}\' '
              '\'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\' | '
              '~/.local/share/los-memory-shadow/serve')
    result = ssh(M3_HOST, script)
    tools, server = None, None
    for line in result.stdout.decode(errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("id") == 2 and "result" in payload:
            tools = [entry["name"] for entry in payload["result"].get("tools", [])]
        if payload.get("id") == 1 and "result" in payload:
            server = payload["result"].get("serverInfo", {}).get("name")
    return {"ok": tools is not None, "server": server, "tools": tools}


def backup_state():
    if not LEDGER.exists():
        return {"last": None, "newest_age_hours": None}
    entries = [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
    if not entries:
        return {"last": None, "newest_age_hours": None}
    last = entries[-1]
    return {"last": last, "newest_age_hours": round((datetime.datetime.now().timestamp()
                                                     - last["ts"]) / 3600, 2)}


def analyse(runs, now=None):
    now = now or datetime.datetime.now().timestamp()
    starts = [run.get("started_at") for run in runs if run.get("started_at")]
    if not starts:
        return {"runs": 0}
    gaps = [starts[index + 1] - starts[index] for index in range(len(starts) - 1)]
    ideal = (starts[-1] - starts[0]) / 300 if starts[-1] > starts[0] else 0
    with_errors = [run for run in runs if run.get("errors")]
    daily = {}
    for run in runs:
        day = datetime.datetime.fromtimestamp(run["started_at"]).strftime("%Y-%m-%d")
        bucket = daily.setdefault(day, {"runs": 0, "errors": 0, "changed": 0, "checked": 0,
                                        "missing": 0})
        bucket["runs"] += 1
        bucket["checked"] += run.get("checked", 0)
        bucket["changed"] += run.get("changed", 0)
        bucket["missing"] += run.get("missing", 0)
        bucket["errors"] += 1 if run.get("errors") else 0
    metered = [run for run in runs if run.get("bytes")]
    return {
        "runs": len(runs),
        "first": datetime.datetime.fromtimestamp(starts[0]).isoformat(timespec="seconds"),
        "last": datetime.datetime.fromtimestamp(starts[-1]).isoformat(timespec="seconds"),
        "span_days": round((starts[-1] - starts[0]) / 86400, 2),
        "expected_runs": int(ideal),
        "cadence_pct": round(len(runs) / ideal * 100, 1) if ideal else None,
        "median_gap_seconds": round(sorted(gaps)[len(gaps) // 2], 1) if gaps else None,
        "max_gap_minutes": round(max(gaps) / 60, 1) if gaps else None,
        "runs_with_errors": len(with_errors),
        "total_checked": sum(run.get("checked", 0) for run in runs),
        "total_changed": sum(run.get("changed", 0) for run in runs),
        "seconds_since_last_run": round(now - starts[-1], 1),
        "metered_runs": len(metered),
        "metered_bytes": sum(run.get("bytes", 0) for run in metered),
        "metered_requests": sum(run.get("requests", 0) for run in metered),
        "daily": daily,
    }


def evaluate_alerts(status, stats, backups, runs):
    alerts = []
    oldest = status.get("oldest_verified_at")
    if oldest:
        age_hours = (datetime.datetime.now().timestamp() - oldest) / 3600
        if age_hours > MAX_OLDEST_VERIFY_HOURS:
            alerts.append({"code": "stale_verification", "severity": "high",
                           "detail": f"oldest verified record is {age_hours:.1f}h old "
                                     f"(budget {MAX_OLDEST_VERIFY_HOURS}h)"})
    silence = stats.get("seconds_since_last_run")
    if silence is not None and silence > MAX_SILENCE_MINUTES * 60:
        alerts.append({"code": "sync_silent", "severity": "high",
                       "detail": f"no sync run for {silence / 60:.1f} min "
                                 f"(budget {MAX_SILENCE_MINUTES} min) — is the launchd job alive?"})
    metering = status.get("metering", {})
    if metering.get("errors"):
        alerts.append({"code": "sync_errors", "severity": "high",
                       "detail": f"{metering['errors']} failed run(s) in the last "
                                 f"{metering.get('window_hours')}h"})
    index = status.get("search_index", {})
    if status.get("total") and index.get("state") != "ready":
        alerts.append({"code": "index_not_ready", "severity": "high",
                       "detail": f"search_index.state = {index.get('state')}"})
    coverage = status.get("contract", {})
    if status.get("total") and not coverage.get("project_assigned"):
        alerts.append({"code": "contract_empty", "severity": "medium",
                       "detail": "no record carries a contract project — the facet rebuild "
                                 "may not have run"})
    manifests = [run.get("manifest_count") for run in runs if run.get("manifest_count")]
    if len(manifests) >= 2 and manifests[-1] < manifests[-2] * MANIFEST_DROP_RATIO:
        alerts.append({"code": "manifest_shrank", "severity": "high",
                       "detail": f"manifest {manifests[-2]} -> {manifests[-1]}"})
    age = backups.get("newest_age_hours")
    if age is None:
        alerts.append({"code": "no_offhost_backup", "severity": "high",
                       "detail": "no off-host backup ledger entry"})
    elif age > MAX_BACKUP_AGE_HOURS:
        alerts.append({"code": "backup_stale", "severity": "high",
                       "detail": f"newest off-host backup is {age}h old "
                                 f"(budget {MAX_BACKUP_AGE_HOURS}h)"})
    return alerts


def _mb(value):
    return "n/a" if not value else f"{value / 1048576:.1f} MiB"


def render_markdown(stats, status, backups, alerts, generated_at, disk=None, handshake=None):
    lines = ["# 影子服务运行报告", "",
             f"生成时间：{generated_at}。数据来源：M3 `sync.out.log`、M3 影子库 `shadow status`、"
             "本机异机备份台账。只读采集。", ""]
    lines += ["## 1. 可用性与调度", "",
              "| 指标 | 实测 |", "| --- | --- |",
              f"| 成功/失败轮数 | {stats.get('runs')} / {stats.get('runs_with_errors')} |",
              f"| 观察窗口 | {stats.get('first')} → {stats.get('last')}"
              f"（{stats.get('span_days')} 天） |",
              f"| 到位率（对 300 s 理想轮数） | {stats.get('cadence_pct')}% |",
              f"| 中位间隔 / 最大间隔 | {stats.get('median_gap_seconds')} s / "
              f"{stats.get('max_gap_minutes')} min |",
              f"| 距最近一轮 | {stats.get('seconds_since_last_run')} s |",
              f"| 累计 checked / changed | {stats.get('total_checked')} / "
              f"{stats.get('total_changed')} |", ""]
    lines += ["## 2. 覆盖与新鲜度", "",
              "| 指标 | 实测 |", "| --- | --- |",
              f"| 记录 total / active | {status.get('total')} / {status.get('active')} |",
              f"| unhydrated | {(status.get('sync') or {}).get('unhydrated')} |",
              f"| 最旧验证时间 | {datetime.datetime.fromtimestamp(status['oldest_verified_at']).isoformat(timespec='seconds') if status.get('oldest_verified_at') else None} |",
              f"| 检索索引 | {status.get('search_index', {}).get('state')}"
              f"（indexed {status.get('search_index', {}).get('indexed')}） |",
              f"| 契约覆盖 | project {status.get('contract', {}).get('project_assigned')}"
              f"/{status.get('contract', {}).get('records')}"
              f"（{status.get('contract', {}).get('project_coverage')}），"
              f"claim_undeclared {status.get('contract', {}).get('claim_undeclared')} |",
              f"| 失败台账 | {status.get('error_ledger', {}).get('size')} 条 |", ""]
    metering = status.get("metering", {})
    lines += ["## 3. 流量计量", "",
              "| 指标 | 实测 |", "| --- | --- |",
              f"| 计量轮数（滚动 {metering.get('window_hours')}h） | {metering.get('runs')} |",
              f"| 响应字节 | {metering.get('bytes')} |",
              f"| 请求数 | {metering.get('requests')} |",
              f"| 预计日流量 | {metering.get('projected_bytes_per_day')} |",
              f"| 日志内可计量轮数 | {stats.get('metered_runs')}"
              f"（累计 {stats.get('metered_bytes')} 字节 / {stats.get('metered_requests')} 请求） |", ""]
    last = backups.get("last") or {}
    lines += ["## 4. 异机备份", "",
              "| 指标 | 实测 |", "| --- | --- |",
              f"| 最近备份 | {last.get('name')} |",
              f"| 大小 / 记录数 | {last.get('bytes')} / {last.get('records')} |",
              f"| 回读校验 | {last.get('readback_ok')} |",
              f"| 距今天数（小时） | {backups.get('newest_age_hours')} |",
              f"| 上次 RTO | {last.get('total_seconds')} s（备份） |", ""]
    lines += ["## 5. 每日明细", "",
              "| 日期 | 轮数 | 失败 | checked | changed | missing |", "| --- | --- | --- | --- | --- | --- |"]
    for day in sorted(stats.get("daily", {})):
        bucket = stats["daily"][day]
        lines.append(f"| {day} | {bucket['runs']} | {bucket['errors']} | {bucket['checked']} | "
                     f"{bucket['changed']} | {bucket['missing']} |")
    lines += ["", "## 6. 告警", ""]
    if alerts:
        lines += ["| 代码 | 级别 | 详情 |", "| --- | --- | --- |"]
        lines += [f"| {item['code']} | {item['severity']} | {item['detail']} |" for item in alerts]
    else:
        lines.append("无告警：所有门槛（最旧验证 ≤24h、轮询未中断、无失败轮、索引 ready、"
                     "契约非空、清单未骤降、异机备份 ≤24h）均满足。")
    if disk:
        lines += ["", "## 6b. 磁盘与容量", "",
                  "| 指标 | 实测 |", "| --- | --- |",
                  f"| 数据目录 | {_mb(disk.get('total_bytes'))} |",
                  f"| shadow.sqlite3 | {_mb(disk.get('db_bytes'))} |",
                  f"| 预写日志 WAL | {_mb(disk.get('wal_bytes'))} |"]
    if handshake is not None:
        lines += ["", "## 6c. 客户端可达性（走客户端同一条路径实测）", "",
                  "| 指标 | 实测 |", "| --- | --- |",
                  f"| MCP 握手 | {'OK' if handshake.get('ok') else 'FAILED'} |",
                  f"| serverInfo.name | {handshake.get('server')} |",
                  f"| 工具面 | {handshake.get('tools')} |"]
    lines += ["", "## 7. 未达标项与口径说明", "",
              "- 14 天窗口的**正式**判定必须在窗口满 14 天后重跑本脚本；本报告若早于该时点，"
              "只作为滚动观察，不能当作门槛已通过。",
              "- 到位率不是 SLA：缺口可能来自休眠/重启，需与 `max_gap_minutes` 一起读。",
              "- 流量只有本轮计量上线后的数据；上线前的历史轮次不计入 `metered_*`。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="action", required=True)
    report = subparsers.add_parser("report")
    report.add_argument("--out", default=None, help="write markdown here as well as stdout")
    subparsers.add_parser("alert")
    args = parser.parse_args()
    os.umask(0o077)

    runs = fetch_log()
    status = fetch_status()
    backups = backup_state()
    stats = analyse(runs)
    alerts = evaluate_alerts(status, stats, backups, runs)
    generated_at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    if args.action == "alert":
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = {"ts": datetime.datetime.now().timestamp(), "generated_at": generated_at,
                  "alerts": alerts}
        with open(ALERTS, "a") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.chmod(ALERTS, 0o600)
        print(json.dumps(record, ensure_ascii=False))
        raise SystemExit(1 if alerts else 0)

    disk, handshake = disk_state(), handshake_state()
    markdown = render_markdown(stats, status, backups, alerts, generated_at, disk, handshake)
    if args.out:
        Path(args.out).write_text(markdown)
    print(markdown)
    print(json.dumps({"stats": stats, "alerts": alerts,
                      "metering": status.get("metering"),
                      "contract": status.get("contract"),
                      "disk": disk, "handshake": handshake},
                     ensure_ascii=False, default=str),
          file=sys.stderr)


if __name__ == "__main__":
    main()
