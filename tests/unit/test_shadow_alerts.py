"""Every alert threshold must be proven to fire.

The operation report claimed "7 alert thresholds (each proven to fire)", but that
proof lived only in a one-time manual run — nothing in the repo would catch a
threshold that had silently stopped being reachable (the usual cause is a rename
or a `detail`-only edit that never touches the condition). These tests call the
real `evaluate_alerts` from `scripts/shadow_report.py` with one condition broken
at a time and assert the code appears, plus a healthy baseline that must produce
nothing so a threshold cannot pass by firing on everything.

The `primary_stale_projection` / `recall_probe_*` thresholds were added
2026-10-10 when the probe became a scheduled job; they are the only ones that
look outside the mirror.
"""
from __future__ import annotations

import copy
import datetime
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_report_module():
    path = REPO_ROOT / "scripts" / "shadow_report.py"
    spec = importlib.util.spec_from_file_location("shadow_report_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    """The module under test, with **every production path redirected**.

    On 2026-10-10 a test in this file truncated the operator's real
    `alerts.jsonl` to zero bytes: it loaded the script with `runpy` and tried to
    override `ALERTS` through the returned namespace, but that mapping is not the
    functions' `__globals__`, so the override silently did nothing and
    `cap_ledger` rotated the production file. The content survived only because
    `rotate_log` archives before truncating.

    Rebinding the constants on the loaded module makes that mistake
    unreachable: no test in this file can address the live state directory even
    if it forgets to monkeypatch something.
    """
    module = load_report_module()
    safe = tmp_path_factory.mktemp("shadow-report-state")
    module.STATE_DIR = safe
    module.ALERTS = safe / "alerts.jsonl"
    module.LEDGER = safe / "backup-ledger.jsonl"
    module.ALERT_PUSH_URL = safe / "alert-push-url"
    return module


def test_fixture_redirects_every_production_path(report):
    """Guard: a test must not be able to address the live state directory."""
    live = Path.home() / ".local/share/los-memory-shadow"

    for value in (report.STATE_DIR, report.ALERTS, report.LEDGER, report.ALERT_PUSH_URL):
        assert live not in Path(value).parents, f"{value} still points into {live}"


def healthy():
    """A status/stats/backups/runs/probe set in which no threshold should fire."""
    now = datetime.datetime.now().timestamp()
    status = {
        "total": 2419,
        "oldest_verified_at": now - 3600,
        "metering": {"errors": 0, "window_hours": 24},
        "search_index": {"state": "ready"},
        "contract": {"project_assigned": 615},
    }
    stats = {"seconds_since_last_run": 70}
    backups = {"newest_age_hours": 17.0}
    runs = [{"manifest_count": 2301}, {"manifest_count": 2301}]
    probe = {"state": "ok", "verdict": "ok", "recent_rate": 1.0, "control_rate": 1.0,
             "recent_probes": 5, "control_probes": 3, "errors": [], "age_hours": 2.0}
    return status, stats, backups, runs, probe


def codes_for(report, **breaks):
    status, stats, backups, runs, probe = healthy()
    for key, value in breaks.items():
        if key in ("oldest_verified_at", "metering", "search_index", "contract", "total"):
            status[key] = value
        elif key in ("seconds_since_last_run",):
            stats[key] = value
        elif key in ("newest_age_hours",):
            backups[key] = value
        elif key == "runs":
            runs = value
        elif key == "probe":
            probe = value
        else:  # pragma: no cover - guards against a typo in a test
            raise AssertionError(f"unknown break {key!r}")
    return [alert["code"] for alert in report.evaluate_alerts(status, stats, backups, runs, probe)]


def test_healthy_baseline_fires_nothing(report):
    """A threshold that fires on a healthy host is noise, not a threshold."""
    assert codes_for(report) == []


def test_stale_verification_fires(report):
    old = datetime.datetime.now().timestamp() - (report.MAX_OLDEST_VERIFY_HOURS + 1) * 3600
    assert "stale_verification" in codes_for(report, oldest_verified_at=old)


def test_sync_silent_fires(report):
    assert "sync_silent" in codes_for(
        report, seconds_since_last_run=(report.MAX_SILENCE_MINUTES + 1) * 60)


def test_sync_errors_fires(report):
    assert "sync_errors" in codes_for(report, metering={"errors": 3, "window_hours": 24})


def test_index_not_ready_fires(report):
    assert "index_not_ready" in codes_for(report, search_index={"state": "not_built"})


def test_index_not_ready_stays_silent_on_an_empty_mirror(report):
    """An un-hydrated mirror has no index yet; that is not an alert."""
    assert "index_not_ready" not in codes_for(report, total=0, search_index={"state": "empty"})


def test_contract_empty_fires(report):
    assert "contract_empty" in codes_for(report, contract={"project_assigned": 0})


def test_manifest_shrank_fires(report):
    assert "manifest_shrank" in codes_for(
        report, runs=[{"manifest_count": 2301}, {"manifest_count": 2000}])


def test_manifest_growth_does_not_fire(report):
    assert "manifest_shrank" not in codes_for(
        report, runs=[{"manifest_count": 2000}, {"manifest_count": 2301}])


def test_no_offhost_backup_fires(report):
    assert "no_offhost_backup" in codes_for(report, newest_age_hours=None)


def test_backup_stale_fires(report):
    assert "backup_stale" in codes_for(
        report, newest_age_hours=report.MAX_BACKUP_AGE_HOURS + 1)


def test_primary_stale_projection_fires(report):
    """The condition the probe exists for, and the one every other threshold is blind to.

    Every other code reads the mirror's own health, so a primary that reports
    `search_index available` while serving nothing newer than July produces no
    signal at all — which is how it stayed silent for weeks.
    """
    probe = {"state": "ok", "verdict": "stale_projection_suspected", "recent_rate": 0.0,
             "control_rate": 0.667, "recent_probes": 5, "control_probes": 3,
             "errors": [], "age_hours": 2.0}

    codes = codes_for(report, probe=probe)

    assert "primary_stale_projection" in codes


def test_primary_not_answering_is_not_double_reported(report):
    """A dead primary already trips the mirror's own thresholds.

    Alerting `primary_not_answering` as well would report one outage as two
    codes, which is how a reader learns to ignore both.
    """
    probe = {"state": "ok", "verdict": "primary_not_answering", "recent_rate": 0.0,
             "control_rate": 0.0, "recent_probes": 5, "control_probes": 3,
             "errors": [], "age_hours": 2.0}

    assert codes_for(report, probe=probe) == []


def test_recall_probe_absent_fires(report):
    assert "recall_probe_absent" in codes_for(
        report, probe={"state": "absent", "detail": "no recall-probe output yet"})


def test_recall_probe_unreadable_fires(report):
    assert "recall_probe_unreadable" in codes_for(
        report, probe={"state": "unreadable", "detail": "newest probe line is not JSON"})


def test_recall_probe_stale_fires(report):
    """A scheduled instrument that stops running is itself the finding."""
    probe = {"state": "ok", "verdict": "ok", "recent_rate": 1.0, "control_rate": 1.0,
             "recent_probes": 5, "control_probes": 3, "errors": [],
             "age_hours": report.MAX_RECALL_PROBE_AGE_HOURS + 1}

    assert "recall_probe_stale" in codes_for(report, probe=probe)


def test_probe_state_cannot_be_omitted(report):
    """Calling without the probe state must fail loudly, not skip the threshold.

    A default of `None` (or `{}`) would evaluate every mirror-side threshold and
    quietly drop the only one that can see a silently-stale primary — turning a
    watched blind spot back into an unwatched one.
    """
    status, stats, backups, runs, _ = healthy()

    with pytest.raises(TypeError):
        report.evaluate_alerts(status, stats, backups, runs)

    with pytest.raises(ValueError):
        report.evaluate_alerts(status, stats, backups, runs, None)


def test_every_declared_threshold_has_a_firing_test(report):
    """A guard against adding a threshold and forgetting to prove it fires.

    The list is explicit rather than derived: deriving it would make this test
    pass by construction the moment someone adds a code without a test.
    """
    expected = {
        "stale_verification", "sync_silent", "sync_errors", "index_not_ready",
        "contract_empty", "manifest_shrank", "no_offhost_backup", "backup_stale",
        "recall_probe_absent", "recall_probe_unreadable", "recall_probe_stale",
        "primary_stale_projection",
    }
    source = (REPO_ROOT / "scripts" / "shadow_report.py").read_text()
    declared = {line.split('"code": "')[1].split('"')[0]
                for line in source.splitlines() if '"code": "' in line}

    assert declared == expected, (
        f"thresholds without a test: {sorted(declared - expected)}; "
        f"tested but no longer declared: {sorted(expected - declared)}")


def test_healthy_fixture_is_not_mutated_by_evaluation(report):
    """`evaluate_alerts` must not write back into the payloads it is handed."""
    status, stats, backups, runs, probe = healthy()
    before = copy.deepcopy((status, stats, backups, runs, probe))

    report.evaluate_alerts(status, stats, backups, runs, probe)

    assert (status, stats, backups, runs, probe) == before


# --- delivery -----------------------------------------------------------------
#
# The project already has one notification chain (Uptime Kuma webhook ->
# kuma-webhook-bridge -> feishu-push.sh -> Feishu), so delivery reuses it. These
# tests pin the three properties that matter: unconfigured is a *reported* state
# rather than silent success, a broken channel is recorded instead of raised, and
# a broken channel makes the job exit non-zero.

class FakeResponse:
    def __init__(self, status=200, body=b"ok"):
        self.status = status
        self._body = body

    def read(self, _limit=None):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_push_url_prefers_the_environment(report, monkeypatch):
    monkeypatch.setenv("SHADOW_ALERT_PUSH_URL", "http://kuma/push/abc")
    assert report.alert_push_url() == "http://kuma/push/abc"


def test_push_url_falls_back_to_a_0600_file(report, monkeypatch, tmp_path):
    monkeypatch.delenv("SHADOW_ALERT_PUSH_URL", raising=False)
    path = tmp_path / "alert-push-url"
    path.write_text("http://kuma/push/file\n")
    monkeypatch.setattr(report, "ALERT_PUSH_URL", path)

    assert report.alert_push_url() == "http://kuma/push/file"


def test_push_url_is_none_when_unset_everywhere(report, monkeypatch, tmp_path):
    monkeypatch.delenv("SHADOW_ALERT_PUSH_URL", raising=False)
    monkeypatch.setattr(report, "ALERT_PUSH_URL", tmp_path / "absent")

    assert report.alert_push_url() is None


def test_unconfigured_delivery_is_reported_not_silent(report):
    """The pre-existing behaviour must stay visible rather than look like success."""
    delivery = report.notify(None, [], "2026-10-10T00:00:00+08:00")

    assert delivery["state"] == "unconfigured"
    assert "nothing is notified" in delivery["detail"]


def test_alerts_are_pushed_as_down_with_their_codes(report, monkeypatch):
    seen = {}

    def fake_urlopen(url, timeout=None):
        seen["url"] = url
        return FakeResponse(body=b"OK")

    monkeypatch.setattr(report.urllib.request, "urlopen", fake_urlopen)
    alerts = [{"code": "primary_stale_projection", "severity": "high", "detail": "x"}]

    delivery = report.notify("http://kuma/push/abc", alerts, "t")

    assert delivery["state"] == "pushed" and delivery["status"] == "down"
    assert "status=down" in seen["url"]
    assert "primary_stale_projection" in seen["url"]


def test_a_healthy_host_pushes_up(report, monkeypatch):
    seen = {}
    monkeypatch.setattr(report.urllib.request, "urlopen",
                        lambda url, timeout=None: (seen.setdefault("url", url), FakeResponse())[1])

    delivery = report.notify("http://kuma/push/abc?token=xyz", [], "t")

    assert delivery["state"] == "pushed" and delivery["status"] == "up"
    assert "status=up" in seen["url"]
    assert "?token=xyz&" in seen["url"], "an existing query string must be preserved"


def test_a_broken_channel_is_recorded_not_raised(report, monkeypatch):
    def boom(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(report.urllib.request, "urlopen", boom)

    delivery = report.notify("http://kuma/push/abc", [{"code": "x", "severity": "high"}], "t")

    assert delivery["state"] == "failed"
    assert "connection refused" in delivery["detail"]


def test_ledger_is_capped_by_its_own_writer(report, monkeypatch, tmp_path):
    """`alerts.jsonl` lives on M1, so the M3 rotation job can never reach it.

    Measured 2026-10-10: adding it to the M3 maintenance list only ever reported
    `missing`. The writer caps its own file instead.
    """
    ledger = tmp_path / "alerts.jsonl"
    ledger.write_bytes(b"x" * 500)
    monkeypatch.setattr(report, "ALERTS", ledger)

    result = report.cap_ledger(max_bytes=100, keep=2)

    assert result["rotated"] is True
    assert ledger.stat().st_size == 0, "the live file is emptied in place"
    assert list(tmp_path.glob("alerts.jsonl.*")), "an archive is kept"


def test_ledger_below_the_cap_is_left_alone(report, monkeypatch, tmp_path):
    ledger = tmp_path / "alerts.jsonl"
    ledger.write_bytes(b"x" * 50)
    monkeypatch.setattr(report, "ALERTS", ledger)

    result = report.cap_ledger(max_bytes=100, keep=2)

    assert result["rotated"] is False and result["bytes"] == 50
    assert not list(tmp_path.glob("alerts.jsonl.*"))


def test_a_missing_ledger_is_not_an_error(report, monkeypatch, tmp_path):
    """First run on a fresh host has no ledger yet."""
    monkeypatch.setattr(report, "ALERTS", tmp_path / "absent.jsonl")

    result = report.cap_ledger()

    assert result["rotated"] is False
    assert result.get("reason") == "missing"


def test_ledger_cap_works_the_way_launchd_runs_it(tmp_path):
    """Run the script as launchd does: by path, from an unrelated cwd.

    `pytest` inserts the repository root into `sys.path`, so an in-process test
    cannot see that `python3 scripts/shadow_report.py` puts only `scripts/` on the
    path. The first live run reported `rotate unavailable: ModuleNotFoundError`
    for exactly that reason, with the unit tests green the whole time.

    `SHADOW_STATE_DIR` is what makes this hermetic: the child derives every path
    from it, so its own state directory is outside the repository and outside the
    operator's.
    """
    import os
    import subprocess
    import sys

    state = tmp_path / "state"
    state.mkdir(parents=True)
    ledger = state / "alerts.jsonl"
    ledger.write_bytes(b"x" * 500)
    script = REPO_ROOT / "scripts" / "shadow_report.py"
    program = (
        "import runpy;"
        f"ns = runpy.run_path({str(script)!r}, run_name='not_main');"
        "print('cap', ns['cap_ledger'](max_bytes=100, keep=2))"
    )

    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, cwd="/",
        env={**os.environ, "SHADOW_STATE_DIR": str(state), "PYTHONPATH": ""},
    )

    assert result.returncode == 0, result.stderr
    assert "'rotated': True" in result.stdout or '"rotated": True' in result.stdout, result.stdout
    assert ledger.stat().st_size == 0, "the child capped its own state dir"
    assert list(state.glob("alerts.jsonl.*")), "and archived what it removed"


def test_storing_a_push_url_is_always_0600(report, tmp_path):
    """The URL embeds a token; the file must not depend on the caller's umask.

    This project already had one incident where a webhook token ended up
    world-readable in a 644 plist, so the helper sets the mode itself instead of
    leaving it to shell redirection.
    """
    import os

    target = tmp_path / "state" / "alert-push-url"
    old = os.umask(0o022)
    try:
        result = report.write_push_url("http://kuma/push/secret", path=target)
    finally:
        os.umask(old)

    assert result["state"] == "written"
    assert target.read_text() == "http://kuma/push/secret"
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700


def test_a_non_http_push_url_is_rejected(report, tmp_path):
    with pytest.raises(ValueError):
        report.write_push_url("ftp://kuma/push", path=tmp_path / "u")


def test_clearing_a_push_url_removes_the_file(report, tmp_path):
    target = tmp_path / "u"
    report.write_push_url("http://kuma/push/x", path=target)

    assert report.write_push_url(None, path=target)["state"] == "cleared"
    assert not target.exists()
    assert report.write_push_url(None, path=target)["state"] == "absent"
