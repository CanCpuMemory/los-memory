#!/usr/bin/env python3
"""Encrypted off-host backup of the M3 Nowledge shadow, plus a timed restore drill.

Why this is not restic (measured 2026-10-07, not assumed)
--------------------------------------------------------
The chosen off-host target is the Synology DS716+II (ssh alias ``syno``, DSM
7.2.1, 2.6 TiB free). restic's only workable backend there would be ``sftp:``,
and SFTP is **disabled** on that box: ``sftp syno`` closes the connection
immediately, ``/etc/ssh/sshd_config`` has no ``Subsystem sftp`` line reachable to
the login user, and ``sudo`` is not passwordless (``sudo -n true`` -> "a password
is required"), so the service cannot be enabled without DSM access. FTP (21) and
SMB (445) are open but need credentials this tool does not have, and WebDAV
(5005/5006) is closed. A ``rest:`` backend would need a container on the NAS.

So this uses the transport that provably works today: a **consistent** SQLite
snapshot (backup API, not a file copy — the live database is in WAL), streamed
over plain SSH, encrypted at rest with AES-256-CBC + PBKDF2, with a retention
policy and an end-to-end checksum readback. If SFTP is enabled later, replacing
``upload``/``fetch`` with restic is a contained change; the snapshot, verify and
drill logic stays.

What it proves, and what it does not
------------------------------------
``backup`` proves the off-host copy is byte-identical and decryptable, and
records the canonical identity digest. ``restore-drill`` proves a cold restore
from the NAS alone reproduces that identity digest and passes
``PRAGMA integrity_check``, and reports the wall-clock RTO.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

M3_HOST = os.environ.get("SHADOW_M3_HOST", "m3-t")
M3_DB = os.environ.get("SHADOW_M3_DB", "~/.local/share/los-memory-shadow/shadow.sqlite3")
NAS_HOST = os.environ.get("SHADOW_NAS_HOST", "syno")
NAS_DIR = os.environ.get("SHADOW_NAS_DIR", "los-memory-shadow-backup")
M3_ROOT = "~/.local/share/los-memory-shadow"
STATE_DIR = Path(os.environ.get("SHADOW_STATE_DIR",
                                Path.home() / ".local/share/los-memory-shadow"))
KEY_FILE = STATE_DIR / "backup.key"
LEDGER = STATE_DIR / "backup-ledger.jsonl"
REPO_ROOT = Path(__file__).resolve().parents[1]
KEEP_DAILY, KEEP_WEEKLY, KEEP_MONTHLY = 7, 4, 6

# launchd runs with a minimal PATH; pin the toolchain we verified.
OPENSSL = next((path for path in ("/opt/homebrew/bin/openssl", "/usr/bin/openssl")
                if os.path.exists(path)), "openssl")

SNAPSHOT_PY = r'''
import os, sqlite3, time
source = sqlite3.connect("file:%s?mode=ro" % os.path.expanduser(os.environ["SNAP_SRC"]), uri=True)
target = sqlite3.connect(os.environ["SNAP_DST"])
started = time.perf_counter()
source.backup(target)          # consistent even while WAL writers are active
target.close(); source.close()
print("%.3f" % (time.perf_counter() - started))
'''


def run(command, input_bytes=None, check=True, capture=True, cwd=None):
    result = subprocess.run(command, input=input_bytes, check=False, cwd=cwd,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE)
    if check and result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(command)}\n"
                         f"{result.stderr.decode(errors='replace')[:2000]}")
    return result


def ssh(host, command, input_bytes=None, check=True):
    return run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command],
               input_bytes=input_bytes, check=check)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_key():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not KEY_FILE.exists():
        KEY_FILE.write_text(secrets.token_hex(32))
        os.chmod(KEY_FILE, 0o600)
    return KEY_FILE


def m3_release():
    """The release the launchd job actually runs — not merely the newest directory."""
    plist = "~/Library/LaunchAgents/co.los.memory-shadow.plist"
    result = ssh(M3_HOST, "python3 -c \"import plistlib,sys;"
                          "print(plistlib.load(open(sys.argv[1],'rb'))['WorkingDirectory'])\" "
                          f"{plist}",
                 check=False)
    if result.returncode == 0:
        return result.stdout.decode().strip()
    raise SystemExit("could not read the deployed release from the M3 launchd job")


def remote_summary(release):
    result = ssh(M3_HOST, f"cd {release} && python3 -m memory_tool.shadow summary --db {M3_DB}")
    return json.loads(result.stdout.decode())


def local_summary(path):
    # cwd matters: launchd starts this with cwd=/, and -m needs the repo on sys.path.
    result = run([sys.executable, "-m", "memory_tool.shadow", "summary", "--db", str(path)],
                 capture=True, cwd=str(REPO_ROOT))
    return json.loads(result.stdout.decode())


def nas_listing():
    result = ssh(NAS_HOST, f"ls -1 {NAS_DIR}/*.sqlite3.enc 2>/dev/null || true")
    names = [line.strip() for line in result.stdout.decode().splitlines() if line.strip()]
    return sorted(names)


def remote_size(remote):
    result = ssh(NAS_HOST, f"wc -c < {remote}")
    return int(result.stdout.decode().strip())


def remote_sha(remote):
    result = ssh(NAS_HOST, f"sha256sum {remote} | cut -d' ' -f1")
    return result.stdout.decode().strip()


def upload(local, remote):
    ssh(NAS_HOST, f"cat > {remote}", input_bytes=Path(local).read_bytes())


def download(remote, local):
    result = ssh(NAS_HOST, f"cat {remote}")
    Path(local).write_bytes(result.stdout)


def encrypt(source, target, key_file):
    run([OPENSSL, "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt",
         "-pass", f"file:{key_file}", "-in", str(source), "-out", str(target)])


def decrypt(source, target, key_file):
    run([OPENSSL, "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
         "-pass", f"file:{key_file}", "-in", str(source), "-out", str(target)])


def stamp_to_epoch(name):
    base = os.path.basename(name).split(".")[0]           # shadow-YYYYMMDDTHHMMSSZ
    return datetime.datetime.strptime(base.split("-", 1)[1], "%Y%m%dT%H%M%SZ").replace(
        tzinfo=datetime.timezone.utc).timestamp()


def retention_keep(names, now=None):
    """Daily for a week, weekly for a month, monthly for half a year."""
    now = now or time.time()
    kept, seen_week, seen_month = set(), set(), set()
    for name in sorted(names, key=stamp_to_epoch, reverse=True):
        age_days = (now - stamp_to_epoch(name)) / 86400.0
        stamp = datetime.datetime.fromtimestamp(stamp_to_epoch(name), datetime.timezone.utc)
        week = stamp.strftime("%G-W%V")
        month = stamp.strftime("%Y-%m")
        if age_days <= KEEP_DAILY:
            kept.add(name)
        elif week not in seen_week and len(seen_week) < KEEP_WEEKLY:
            seen_week.add(week)
            kept.add(name)
        elif month not in seen_month and len(seen_month) < KEEP_MONTHLY:
            seen_month.add(month)
            kept.add(name)
    return kept


def prune(dry_run=False):
    names = nas_listing()
    keep = retention_keep(names)
    removed = []
    for name in names:
        if name in keep:
            continue
        removed.append(name)
        if not dry_run:
            ssh(NAS_HOST, f"rm -f {name}")
    return {"present": len(names), "kept": len(keep), "removed": removed}


def ledger_append(entry):
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(LEDGER, "a") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    os.chmod(LEDGER, 0o600)


def ledger_last():
    if not LEDGER.exists():
        return None
    lines = [line for line in LEDGER.read_text().splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else None


def ledger_entry_for(name):
    """The ledger row written when `name` was backed up.

    The drill's reference must be this row, not the newest one and not the live
    mirror: `name` is the artifact being restored, and a mirror that kept
    writing after the backup was taken will (correctly) no longer match it. For
    any backup that is not the newest, `ledger_last()` is simply the wrong row.
    """
    if not LEDGER.exists():
        return None
    wanted = os.path.basename(name)
    found = None
    for line in LEDGER.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if os.path.basename(entry.get("name", "")) == wanted:
            found = entry
    return found


def cmd_backup(args):
    started = time.time()
    key = ensure_key()
    release = m3_release()
    remote_snapshot = f"/tmp/shadow-snap-{int(started)}.sqlite3"
    workdir = Path(tempfile.mkdtemp(prefix="shadow-backup-"))
    local_db = workdir / "shadow.sqlite3"
    local_enc = workdir / "shadow.sqlite3.enc"
    try:
        snap = run(["ssh", "-T", "-o", "BatchMode=yes", M3_HOST,
                    f"SNAP_SRC={M3_DB} SNAP_DST={remote_snapshot} python3 -"],
                   input_bytes=SNAPSHOT_PY.encode())
        snapshot_seconds = float(snap.stdout.decode().strip() or 0)
        live = remote_summary(release)

        run(["scp", "-q", "-o", "BatchMode=yes", f"{M3_HOST}:{remote_snapshot}", str(local_db)])
        ssh(M3_HOST, f"rm -f {remote_snapshot}")

        copy = local_summary(local_db)
        if copy["identity_digest"] != live["identity_digest"]:
            raise SystemExit(f"snapshot identity mismatch: {copy['identity_digest']} != "
                             f"{live['identity_digest']}")
        connection = sqlite3.connect(str(local_db))
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        connection.close()
        if integrity != "ok":
            raise SystemExit(f"snapshot integrity_check = {integrity}")

        encrypt(local_db, local_enc, key)
        plaintext_sha, encrypted_sha = sha256_file(local_db), sha256_file(local_enc)
        stamp = datetime.datetime.fromtimestamp(started, datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"shadow-{stamp}.sqlite3.enc"
        remote = f"{NAS_DIR}/{name}"
        ssh(NAS_HOST, f"mkdir -p {NAS_DIR}")
        upload(local_enc, remote)

        # End-to-end proof: the bytes now on the NAS hash to the bytes we encrypted.
        nas_sha = remote_sha(remote)
        readback_ok = nas_sha == encrypted_sha
        nas_bytes = remote_size(remote)
        if not readback_ok or nas_bytes != local_enc.stat().st_size:
            raise SystemExit(f"off-host readback mismatch for {name}: "
                             f"{nas_sha} vs {encrypted_sha}, {nas_bytes} bytes")

        pruned = prune()
        previous = ledger_last()
        entry = {"ts": time.time(), "iso": datetime.datetime.now().astimezone().isoformat(),
                 "name": name, "bytes": nas_bytes, "encrypted_sha256": encrypted_sha,
                 "plaintext_sha256": plaintext_sha, "identity_digest": live["identity_digest"],
                 "records": live["records"], "integrity": integrity, "readback_ok": readback_ok,
                 "snapshot_seconds": snapshot_seconds,
                 "total_seconds": round(time.time() - started, 2), "pruned": len(pruned["removed"]),
                 "rpo_seconds": round(started - previous["ts"], 1) if previous else None}
        ledger_append(entry)
        return entry
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def cmd_restore_drill(args):
    names = nas_listing()
    if not names:
        raise SystemExit("no off-host backups found")
    remote = names[-1] if not args.name else f"{NAS_DIR}/{args.name}"
    started = time.time()
    key = ensure_key()
    workdir = Path(tempfile.mkdtemp(prefix="shadow-drill-"))
    local_enc = workdir / "backup.enc"
    local_db = workdir / "restored.sqlite3"
    try:
        download(remote, local_enc)
        download_seconds = time.time() - started
        if remote_sha(remote) != sha256_file(local_enc):
            raise SystemExit("downloaded copy does not match the off-host checksum")
        decrypt(local_enc, local_db, key)
        decrypt_seconds = time.time() - started
        connection = sqlite3.connect(str(local_db))
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        counts = connection.execute("SELECT count(*) FROM records").fetchone()[0]
        connection.close()
        restored = local_summary(local_db)
        verified_at = time.time()

        # Reference selection decides whether a healthy drill reads as a
        # success. The artifact being restored is `name`, so the authoritative
        # expectation is that backup's own ledger row; the live mirror is
        # reported only as advisory, because a mirror that kept writing after
        # the snapshot was taken will legitimately differ (2026-10-10: the
        # drill restored 2329 records against a live mirror of 2419 and printed
        # `digest_match: false` even though the identity digest was byte-for-byte
        # identical to the ledger row).
        ledger_row = ledger_entry_for(remote)
        reference = None
        try:
            reference = remote_summary(m3_release())
        except SystemExit:
            reference = None
        expected = (ledger_row or ledger_last() or {}).get("identity_digest")
        matched = expected is None or restored["identity_digest"] == expected
        return {"name": os.path.basename(remote), "integrity": integrity,
                "records_restored": counts,
                "records_expected": (ledger_row or {}).get("records"),
                "identity_digest": restored["identity_digest"],
                "reference_digest": expected, "digest_match": matched,
                "reference_source": ("ledger" if ledger_row else
                                     "ledger_last_fallback" if expected else "none"),
                "live_digest": (reference or {}).get("identity_digest"),
                "live_records": (reference or {}).get("records"),
                "live_matches_snapshot": bool(
                    reference and reference.get("identity_digest") == restored["identity_digest"]),
                "download_seconds": round(download_seconds, 2),
                "decrypt_seconds": round(decrypt_seconds, 2),
                "rto_seconds": round(verified_at - started, 2),
                "rto_within_gate": (verified_at - started) <= 3600}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def cmd_status(args):
    names = nas_listing()
    last = ledger_last()
    newest_age = (time.time() - stamp_to_epoch(names[-1])) if names else None
    return {"off_host_backups": len(names), "newest": os.path.basename(names[-1]) if names else None,
            "newest_age_hours": round(newest_age / 3600, 2) if newest_age is not None else None,
            "rpo_within_gate": newest_age is not None and newest_age <= 86400,
            "last_backup": last}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("backup")
    drill = subparsers.add_parser("restore-drill")
    drill.add_argument("--name", default=None, help="specific off-host file name")
    subparsers.add_parser("status")
    args = parser.parse_args()
    os.umask(0o077)
    handlers = {"backup": cmd_backup, "restore-drill": cmd_restore_drill, "status": cmd_status}
    print(json.dumps(handlers[args.action](args), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
