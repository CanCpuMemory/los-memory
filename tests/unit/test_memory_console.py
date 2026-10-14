"""Every `memory_console` threshold must be proven to fire.

Same contract as `tests/unit/test_shadow_alerts.py`, and for the same reason: a
threshold whose condition is renamed or edited away is otherwise invisible until
the day it was needed. One condition is broken at a time and the code is
asserted; a healthy baseline must produce **nothing**, so no threshold can pass
by firing on everything.

`tests/unit/test_shadow_alerts.py` documents the 2026-10-10 incident where a test
truncated the operator's real `alerts.jsonl`. The fixture here rebinds every
production path on the loaded module *and* on the `shadow_report` module it
imports, then a guard test asserts none of them can address the live state
directory.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every code `evaluate_alerts` can emit. Kept explicit so adding a threshold
#: without a firing test fails the suite below.
DECLARED_CODES = {
    "shadow_alert_stale",
    "primary_unreachable",
    "primary_cli_missing",
    "primary_unhealthy",
    "primary_embedding_degraded",
    "primary_reviews_pending",
    "primary_review_backlog",
    "memory_write_path_missing",
    "memory_write_path_stale",
    "memory_write_path_failed",
    "local_profile_growth",
}


def load_console_module():
    path = REPO_ROOT / "scripts" / "memory_console.py"
    spec = importlib.util.spec_from_file_location("memory_console_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def console(tmp_path_factory):
    """The module under test, with every production path redirected."""
    module = load_console_module()
    safe = tmp_path_factory.mktemp("memory-console-state")
    module.STATE_DIR = safe
    module.SNAPSHOT = safe / "memory-console-snapshot.json"
    module.LEDGER = safe / "memory-console-alerts.jsonl"
    module.MEM_SYNC_LOG = safe / "mem-sync.out.log"
    module.DSH_MEMORIES = safe / "memories"
    # The imported report module owns the delivery URL and the shadow ledger.
    module.shadow.STATE_DIR = safe
    module.shadow.ALERTS = safe / "alerts.jsonl"
    module.shadow.LEDGER = safe / "backup-ledger.jsonl"
    module.shadow.ALERT_PUSH_URL = safe / "alert-push-url"
    return module


def test_fixture_redirects_every_production_path(console):
    """Guard: no test in this file may address the live state directory."""
    live = Path.home() / ".local/share/los-memory-shadow"
    for path in (console.STATE_DIR, console.SNAPSHOT, console.LEDGER, console.MEM_SYNC_LOG,
                 console.DSH_MEMORIES, console.shadow.ALERTS, console.shadow.LEDGER,
                 console.shadow.ALERT_PUSH_URL):
        assert live not in Path(path).parents, f"{path} still points into {live}"


def healthy_state(console, **overrides):
    """A state in which nothing should fire, so each test breaks exactly one thing."""
    state = {
        "generated_at": "2026-10-14T00:00:00+00:00",
        "primary": {
            "reachable": True,
            "status": "ok",
            "database": True,
            "embedding_degraded": False,
            "reviews": [],
            "health": {"pressure": "low", "total_backlog": 10,
                       "cleanup_backlog": 4, "relation_backlog": 6,
                       "daily_review_limit": 42, "growth_per_day": 1.0},
        },
        "write_path": {"exists": True, "age_hours": 1.0, "last": {"created": 0, "failed": 0}},
        "shadow": {"alert_ledger_age_hours": 0.5},
        "local_profiles": {"shared": {"exists": True, "rows": 10}},
        "profile_growth": {"shared": 0},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            state[key] = {**state[key], **value}
        else:
            state[key] = value
    return state


def codes_for(console, state):
    return {alert["code"] for alert in console.evaluate_alerts(state)}


def test_healthy_state_produces_no_alerts(console):
    assert console.evaluate_alerts(healthy_state(console)) == []


def test_stale_shadow_watcher_fires(console):
    """The hourly shadow report cannot see its own silence; this script can."""
    stale = healthy_state(console, shadow={"alert_ledger_age_hours": 9.0})
    absent = healthy_state(console, shadow={"alert_ledger_age_hours": None})

    stale_alerts = console.evaluate_alerts(stale)
    absent_alerts = console.evaluate_alerts(absent)

    assert [a["code"] for a in stale_alerts] == ["shadow_alert_stale"]
    assert stale_alerts[0]["severity"] == "high"
    assert "9.0 h" in stale_alerts[0]["detail"]
    assert [a["code"] for a in absent_alerts] == ["shadow_alert_stale"]


def test_fresh_shadow_watcher_does_not_fire(console):
    assert console.evaluate_alerts(healthy_state(console)) == []


def test_unreachable_primary_fires(console):
    state = healthy_state(console, primary={"reachable": False, "error": "ConnectTimeout"})

    alerts = console.evaluate_alerts(state)

    assert [alert["code"] for alert in alerts] == ["primary_unreachable"]
    assert "ConnectTimeout" in alerts[0]["detail"]


def test_missing_cli_is_its_own_code_not_a_primary_outage(console):
    """A watcher that cannot find its own tool must not report the primary as down.

    Measured 2026-10-14 on the first launchd run of this script: launchd's PATH
    excludes /usr/local/bin, so `nmem` was not found and the alert said
    `primary_unreachable` about a healthy primary.
    """
    state = healthy_state(console, primary={
        "reachable": False, "error": "cli_missing: 'nmem' is not on PATH (/usr/bin:/bin)"})

    alerts = console.evaluate_alerts(state)

    assert [alert["code"] for alert in alerts] == ["primary_cli_missing"]
    assert "not on PATH" in alerts[0]["detail"]


def test_run_nmem_classifies_a_missing_binary(console, monkeypatch):
    monkeypatch.setattr(console, "NMEM", "/nonexistent/nmem")

    payload, error = console.run_nmem(["status", "-j"])

    assert payload is None and error.startswith("cli_missing")


def test_unhealthy_primary_fires(console):
    state = healthy_state(console, primary={"status": "degraded", "database": False})

    assert "primary_unhealthy" in codes_for(console, state)


def test_degraded_embedding_fires(console):
    state = healthy_state(console, primary={"embedding_degraded": True})

    assert "primary_embedding_degraded" in codes_for(console, state)


def test_pending_review_fires_and_escalates_when_it_is_about_to_expire(console):
    now = datetime.datetime(2026, 10, 14, tzinfo=datetime.timezone.utc)
    far = healthy_state(console, primary={"reviews": [
        {"id": "evt-1", "type": "flag_contradiction", "expires_at": "2026-11-09T04:33:37Z"}]})
    near = healthy_state(console, primary={"reviews": [
        {"id": "evt-2", "type": "flag_contradiction", "expires_at": "2026-10-16T04:33:37Z"}]})

    medium = console.evaluate_alerts(far, now=now)
    high = console.evaluate_alerts(near, now=now)

    assert [alert["code"] for alert in medium] == ["primary_reviews_pending"]
    assert medium[0]["severity"] == "medium"
    assert high[0]["severity"] == "high", (
        "a review whose auto-discard is inside the warning window must escalate: "
        "the primary discards it silently with no second notice")
    assert "2026-10-16" in high[0]["detail"]


def test_review_backlog_over_budget_fires(console):
    state = healthy_state(console, primary={"health": {
        "total_backlog": 772, "cleanup_backlog": 162, "relation_backlog": 610,
        "pressure": "high", "daily_review_limit": 42, "growth_per_day": 54.03}})

    alerts = console.evaluate_alerts(state)

    assert [alert["code"] for alert in alerts] == ["primary_review_backlog"]
    assert "772" in alerts[0]["detail"] and "54.03" in alerts[0]["detail"]


def test_review_backlog_at_the_budget_does_not_fire(console):
    state = healthy_state(console, primary={
        "health": {"total_backlog": console.REVIEW_BACKLOG_BUDGET}})

    assert console.evaluate_alerts(state) == []


def test_missing_write_path_fires(console):
    state = healthy_state(console, write_path={"exists": False, "path": "/tmp/nope.log"})

    alerts = console.evaluate_alerts(state)

    assert [alert["code"] for alert in alerts] == ["memory_write_path_missing"]


def test_stale_write_path_fires(console):
    state = healthy_state(console, write_path={"exists": True, "age_hours": 7.0, "last": {}})

    assert "memory_write_path_stale" in codes_for(console, state)


def test_write_path_failure_fires(console):
    state = healthy_state(console, write_path={
        "exists": True, "age_hours": 1.0, "last": {"failed": 3}})

    alerts = console.evaluate_alerts(state)

    assert [alert["code"] for alert in alerts] == ["memory_write_path_failed"]
    assert "failed=3" in alerts[0]["detail"]


def test_local_profile_growth_fires(console):
    state = healthy_state(console, profile_growth={"shared": 18, "codex": 0})

    alerts = console.evaluate_alerts(state)

    assert [alert["code"] for alert in alerts] == ["local_profile_growth"]
    assert "shared gained 18 rows" in alerts[0]["detail"]


def test_growth_below_the_alert_threshold_does_not_fire(console):
    state = healthy_state(console, profile_growth={"shared": console.PROFILE_GROWTH_ALERT_ROWS - 1})

    assert console.evaluate_alerts(state) == []


def test_every_declared_code_has_a_firing_test(console):
    """Adding a threshold without proving it fires must fail this suite."""
    fired = set()
    fired |= codes_for(console, healthy_state(console, shadow={"alert_ledger_age_hours": 9.0}))
    fired |= codes_for(console, healthy_state(console, primary={"reachable": False}))
    fired |= codes_for(console, healthy_state(console, primary={
        "reachable": False, "error": "cli_missing: 'nmem' is not on PATH"}))
    fired |= codes_for(console, healthy_state(console, primary={"status": "bad"}))
    fired |= codes_for(console, healthy_state(console, primary={"embedding_degraded": True}))
    fired |= codes_for(console, healthy_state(console, primary={
        "reviews": [{"expires_at": "2026-11-09T04:33:37Z"}]}))
    fired |= codes_for(console, healthy_state(console, primary={
        "health": {"total_backlog": 9999}}))
    fired |= codes_for(console, healthy_state(console, write_path={"exists": False}))
    fired |= codes_for(console, healthy_state(console, write_path={"exists": True,
                                                                   "age_hours": 99.0}))
    fired |= codes_for(console, healthy_state(console, write_path={
        "exists": True, "age_hours": 1.0, "last": {"failed": 1}}))
    fired |= codes_for(console, healthy_state(console, profile_growth={"shared": 18}))

    assert fired == DECLARED_CODES, f"untested or undeclared codes: {fired ^ DECLARED_CODES}"


def test_snapshot_round_trip_and_growth_math(console, tmp_path):
    path = tmp_path / "snap.json"
    current = {"shared": {"rows": 20}, "codex": {"rows": 7}}

    console.write_snapshot(current, "2026-10-14T00:00:00+00:00", path=path)
    snapshot = console.read_snapshot(path)
    growth = console.profile_growth({"shared": {"rows": 38}, "codex": {"rows": 7}}, snapshot)

    assert snapshot["profiles"] == {"shared": 20, "codex": 7}
    assert growth == {"shared": 18, "codex": 0}


def test_read_snapshot_tolerates_missing_and_broken_files(console, tmp_path):
    assert console.read_snapshot(tmp_path / "absent.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert console.read_snapshot(broken) is None


def test_profile_growth_is_empty_without_a_snapshot(console):
    assert console.profile_growth({"shared": {"rows": 5}}, None) == {}


def test_write_path_state_reads_the_last_json_line(console, tmp_path):
    log = tmp_path / "mem-sync.out.log"
    log.write_text('{"entries": 10, "failed": 0}\nnoise\n{"entries": 11, "failed": 2}\n')
    console.MEM_SYNC_LOG = log

    state = console.write_path_state()

    assert state["last"] == {"entries": 11, "failed": 2}, (
        "the newest run is the last JSON line, not the first")
    assert state["age_hours"] >= 0


def test_write_path_state_reports_a_missing_log(console, tmp_path):
    console.MEM_SYNC_LOG = tmp_path / "absent.log"

    state = console.write_path_state()

    assert state["exists"] is False


def test_primary_state_reports_unreachable_without_raising(console, monkeypatch):
    monkeypatch.setattr(console, "run_nmem", lambda *a, **k: (None, "ConnectTimeout"))

    state = console.primary_state()

    assert state["reachable"] is False and "ConnectTimeout" in state["error"]


def test_primary_state_summarizes_inbox_and_health(console, monkeypatch):
    status = {"status": "ok", "server_version": "0.10.86", "cli_version": "0.10.98",
              "database": True, "embedding": {"degraded": False}}
    stats = {"memories": 2341, "threads": 417}
    inbox = {"review_count": 1, "attention_count": 4,
             "health": {"health": {"state": "needs_review", "pressure": "high",
                                   "total_maintenance_review_backlog_count": 772}},
             "reviews": [{"id": "evt-1", "event_type": "flag_contradiction",
                          "severity": "action_required", "title": "Possible contradiction",
                          "review": {"expires_at": "2026-11-09T04:33:37Z",
                                     "recommended_action": "keep_both_linked"}}]}
    responses = {("status", "-j"): status, ("stats", "-j"): stats,
                 ("feed", "inbox", "-j"): inbox}
    monkeypatch.setattr(console, "run_nmem", lambda args, **k: (responses.get(tuple(args)), None))

    state = console.primary_state()

    assert state["stats"]["memories"] == 2341
    assert state["health"]["total_backlog"] == 772
    assert state["reviews"][0]["recommended_action"] == "keep_both_linked"
    assert state["reviews"][0]["expires_at"] == "2026-11-09T04:33:37Z"


def test_render_includes_the_sections_and_the_alerts(console):
    state = healthy_state(console)
    state["shadow"] = {"total": 1, "active": 1, "sync": {"missing": 0, "errors": [],
                                                         "tombstones_deferred": 0},
                       "index_state": "ready", "storage": {}, "probe_verdict": "ok",
                       "probe_recent_rate": 1.0, "backup_age_hours": 1.0,
                       "backup_readback_ok": True, "alert_ledger_age_hours": 0.5,
                       "active_alerts": []}
    state["write_path"] = {**state["write_path"], "dsh_tracks": 16, "dsh_daily_files": 46}

    text = console.render(state, console.evaluate_alerts(state))

    for marker in ("[影子镜像 / M3]", "[主库 / Nowledge]", "[写入路径 / DSH -> 主库]",
                   "[本地 profile", "[阈值判定]", "全部满足"):
        assert marker in text


def test_alert_mode_records_an_empty_ledger_row_and_exits_zero(console, monkeypatch, tmp_path):
    console.LEDGER = tmp_path / "console-alerts.jsonl"
    monkeypatch.setattr(console, "collect", lambda snapshot=None: healthy_state(console))
    monkeypatch.setattr(console, "read_snapshot", lambda: None)
    monkeypatch.setattr(console, "write_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(console.shadow, "alert_push_url", lambda: None)

    code = console.main(["alert", "--no-notify"])

    assert code == 0, "a healthy state with no channel configured is not a failure"
    rows = [json.loads(line) for line in console.LEDGER.read_text().splitlines() if line]
    assert len(rows) == 1
    assert rows[0]["alerts"] == []
    assert rows[0]["delivery"]["state"] == "suppressed"


def test_alert_mode_exits_nonzero_and_records_the_alert(console, monkeypatch, tmp_path):
    console.LEDGER = tmp_path / "console-alerts.jsonl"
    broken = healthy_state(console, write_path={"exists": False, "path": "/tmp/nope"})
    monkeypatch.setattr(console, "collect", lambda snapshot=None: broken)
    monkeypatch.setattr(console, "read_snapshot", lambda: None)
    monkeypatch.setattr(console, "write_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(console.shadow, "alert_push_url", lambda: None)

    code = console.main(["alert", "--no-notify"])

    assert code == 1
    rows = [json.loads(line) for line in console.LEDGER.read_text().splitlines() if line]
    assert [alert["code"] for alert in rows[0]["alerts"]] == ["memory_write_path_missing"]


def test_alert_ledger_cap_addresses_this_scripts_ledger(console, tmp_path):
    """`alert` owns a second ledger, so it must cap that one, not the shadow's."""
    console.LEDGER = tmp_path / "console-alerts.jsonl"
    console.LEDGER.write_text('{"alerts": []}\n')

    cap = console.shadow.cap_ledger(path=console.LEDGER)

    assert cap.get("path") == str(console.LEDGER)
    assert cap.get("rotated") is False, "a tiny ledger has nothing to rotate"


class _FakeRun:
    """Captures the argv the delivery path would execute."""

    def __init__(self, returncode=0, stdout='{"ok":true}'):
        self.calls = []
        self.returncode = returncode
        self.stdout = stdout

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_deliver_names_the_sender_and_keys_on_the_state(console, tmp_path):
    script = tmp_path / "feishu-push.sh"
    script.write_text("#!/bin/sh\n")
    console.FEISHU_PUSH = script
    runner = _FakeRun()

    result = console.deliver(
        [{"code": "primary_review_backlog", "severity": "medium", "detail": "backlog 772"}],
        "2026-10-14T00:00:00+00:00", before=None, runner=runner)

    argv = runner.calls[0]
    assert result["state"] == "pushed"
    assert "--key" in argv and "memory-console-2026-10-14-primary_review_backlog" in argv
    body = argv[-1]
    assert body.startswith("记忆总览："), "the message must name the sender, not the monitor"
    assert "backlog 772" in body


def test_deliver_announces_recovery_once_the_alerts_clear(console, tmp_path):
    script = tmp_path / "feishu-push.sh"
    script.write_text("#!/bin/sh\n")
    console.FEISHU_PUSH = script
    runner = _FakeRun()

    result = console.deliver([], "2026-10-14T00:00:00+00:00",
                             before=[{"code": "primary_review_backlog"}], runner=runner)

    assert result["state"] == "pushed"
    assert "已恢复正常" in runner.calls[0][-1]
    assert "primary_review_backlog" in runner.calls[0][-1]


def test_deliver_stays_silent_with_nothing_to_say(console, tmp_path):
    script = tmp_path / "feishu-push.sh"
    script.write_text("#!/bin/sh\n")
    console.FEISHU_PUSH = script
    runner = _FakeRun()

    result = console.deliver([], "2026-10-14T00:00:00+00:00", before=[], runner=runner)

    assert result["state"] == "silent"
    assert runner.calls == [], "an all-clear with no prior alerts must not send"


def test_deliver_reports_a_missing_channel(console, tmp_path):
    console.FEISHU_PUSH = tmp_path / "absent.sh"

    result = console.deliver([{"code": "x", "severity": "high", "detail": "d"}],
                             "2026-10-14T00:00:00+00:00", runner=_FakeRun())

    assert result["state"] == "unconfigured"


def test_deliver_reports_a_failed_send(console, tmp_path):
    script = tmp_path / "feishu-push.sh"
    script.write_text("#!/bin/sh\n")
    console.FEISHU_PUSH = script

    result = console.deliver([{"code": "x", "severity": "high", "detail": "d"}],
                             "2026-10-14T00:00:00+00:00", runner=_FakeRun(returncode=3))

    assert result["state"] == "failed" and "exit 3" in result["detail"]
