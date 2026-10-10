"""The restore drill's comparison baseline must be the restored backup's own row.

Regression guard for the 2026-10-10 drill: it compared the restored database
against the *live* M3 mirror, so a healthy restore printed `digest_match: false`
whenever the mirror had grown since the snapshot (`records_restored=2329` vs
`records_expected=2419`) even though the restored identity digest was
byte-for-byte identical to the ledger row for that backup. The fallback then
used `ledger_last()`, which is the wrong row for any backup that is not newest.
"""
import importlib.util
import json
from pathlib import Path

import pytest


def load_backup_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "shadow_backup.py"
    spec = importlib.util.spec_from_file_location("shadow_backup_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    module = load_backup_module()
    path = tmp_path / "backup-ledger.jsonl"
    monkeypatch.setattr(module, "LEDGER", path)

    rows = [
        {"name": "shadow-20261007T203006Z.sqlite3.enc", "identity_digest": "aaa", "records": 2075},
        {"name": "shadow-20261008T203005Z.sqlite3.enc", "identity_digest": "bbb", "records": 2140},
        {"name": "shadow-20261009T203010Z.sqlite3.enc", "identity_digest": "ccc", "records": 2329},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return module, path, rows


def test_entry_for_returns_that_backups_row_not_the_newest(ledger):
    module, path, rows = ledger

    entry = module.ledger_entry_for(str(path.parent / rows[0]["name"]))

    assert entry is not None
    assert entry["identity_digest"] == "aaa", "must not fall through to ledger_last()"
    assert entry["records"] == 2075


def test_entry_for_matches_on_basename(ledger):
    """The drill passes a remote path; the ledger stores a bare filename."""
    module, _, rows = ledger

    entry = module.ledger_entry_for("/volume1/los-memory-shadow-backup/" + rows[2]["name"])

    assert entry["identity_digest"] == "ccc"


def test_entry_for_unknown_backup_is_none(ledger):
    module, _, _ = ledger

    assert module.ledger_entry_for("shadow-19700101T000000Z.sqlite3.enc") is None


def test_entry_for_survives_a_truncated_ledger_line(ledger):
    module, path, rows = ledger
    path.write_text(path.read_text() + '{"name": "shadow-cut.sqlite3.enc", "ident')

    assert module.ledger_entry_for(rows[0]["name"])["identity_digest"] == "aaa"


def test_entry_for_missing_ledger_is_none(tmp_path, monkeypatch):
    module = load_backup_module()
    monkeypatch.setattr(module, "LEDGER", tmp_path / "absent.jsonl")

    assert module.ledger_entry_for("anything.enc") is None
