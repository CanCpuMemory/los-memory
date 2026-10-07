"""Retention policy for the off-host backup set.

A bug here deletes backups, so the policy is pinned by test rather than by
reading the code: `retention_keep` decides what survives on the NAS.
"""

import datetime
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "shadow_backup", Path(__file__).resolve().parents[2] / "scripts" / "shadow_backup.py")
shadow_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shadow_backup)


def name_for(days_ago, now):
    stamp = datetime.datetime.fromtimestamp(now - days_ago * 86400, datetime.timezone.utc)
    return f"/nas/shadow-{stamp.strftime('%Y%m%dT%H%M%SZ')}.sqlite3.enc"


@pytest.fixture
def now():
    # Fixed clock: retention must not depend on when the test runs.
    return datetime.datetime(2026, 10, 7, 12, 0, tzinfo=datetime.timezone.utc).timestamp()


def test_newest_backup_is_never_dropped(now):
    names = [name_for(day, now) for day in range(0, 400)]
    keep = shadow_backup.retention_keep(names, now)
    assert names[0] in keep, "the most recent backup is the one that matters most"


def test_every_backup_inside_the_daily_window_survives(now):
    names = [name_for(day, now) for day in range(0, 7)]
    keep = shadow_backup.retention_keep(names, now)
    assert len(keep) == 7, f"all of the last 7 days must survive, kept {len(keep)}"


def test_old_backups_are_pruned_and_the_set_is_bounded(now):
    names = [name_for(day, now) for day in range(0, 400)]
    keep = shadow_backup.retention_keep(names, now)
    assert names[-1] not in keep, "a 400-day-old daily file should not be kept"
    # 7 daily + weekly/monthly tiers. Bound it generously but far below "keep all".
    # Measured: 18 of 400.
    assert 7 <= len(keep) <= 30, f"retention is not bounded: kept {len(keep)} of {len(names)}"
    # Tiering is real: several distinct months beyond the daily window survive.
    # Assert on months rather than on a specific age band, so the test does not
    # depend on which exact day lands in a given ISO week.
    months = {datetime.datetime.fromtimestamp(shadow_backup.stamp_to_epoch(n), datetime.timezone.utc)
              .strftime("%Y-%m") for n in keep}
    assert len(months) >= 4, f"monthly tier collapsed: only {sorted(months)} survived"
    assert not any(n in keep for n in names if (now - shadow_backup.stamp_to_epoch(n)) / 86400 > 200)


def test_empty_and_single_inputs_do_not_crash(now):
    assert shadow_backup.retention_keep([], now) == set()
    one = name_for(1, now)
    assert shadow_backup.retention_keep([one], now) == {one}
