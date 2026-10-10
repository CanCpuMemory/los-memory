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
def report():
    return load_report_module()


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
