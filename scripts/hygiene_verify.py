#!/usr/bin/env python3
"""Accuracy by scenario: verify what a machine can prove, sample what it cannot.

The user's rule (2026-11-04): *facts a machine can check are handled as facts;
subjective claims go to cross-review or a human; and a periodic report must list
what is settled, what is not, and a sample to inspect.* This script is that rule
made mechanical:

  **T1 可验证** — an entry names a filesystem path (optionally ``path:line``).
      ``confirmed`` (exists), ``refuted`` (does not exist on this host),
      ``unknown`` (outside this host's reach, or unreadable).
  **T2 需交叉评审** — no checkable assertion, but the entry asserts a
      modification/decision ⇒ listed for two independent reviewers.
  **T3 主观** — no checkable assertion and no claim ⇒ human, by sample.
  **report** — the weekly split of the three buckets plus a deterministic
      sample (same week ⇒ same sample, so the sample can be audited).

Why ``unknown`` is a first-class verdict: a path on another host (``/root/…``,
``/volume1/…``, ``C:\\…``) is *not* evidence of a wrong memory, and a missing
tool is not evidence of anything. Treating either as ``refuted`` would retire
correct memories — the exact failure mode the design forbids.

Read-only by construction: filesystem ``stat`` only. No commands, no network,
no writes to the corpus. Exit codes: 0 = verified (refuted may exist; inspect
the report), 1 = contradictions found (``--fail-on refuted``), 2 = cannot judge.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
EXIT_OK, EXIT_REFUTED, EXIT_UNJUDGEABLE = 0, 1, 2

#: Roots this host can actually answer for. Everything else is out of scope:
#: a memory may legitimately describe another machine.
LOCAL_ROOTS = (
    "~/",
    "/Users/",
    "/opt/",
    "/usr/local/",
    "/etc/",
    "/var/",
    "/tmp/",
    "/private/tmp/",
    "/private/var/",
)

#: Path-looking strings that belong to another host or another OS.
FOREIGN_ROOTS = ("/root/", "/volume1/", "/home/", "C:\\", "D:\\", "/mnt/")

#: Paths that are transient by nature. A memory recording one is not "wrong"
#: when the file is gone — it is a durable memory holding a momentary path,
#: which is a *quality* signal, not a contradiction.
EPHEMERAL_ROOTS = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")

#: `~/x`, `/Users/x/y`, `/opt/homebrew/bin/jj`, `/etc/hosts:12`
PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.\-/~])"
    r"(?:~/|/(?:Users|opt|usr|etc|var|tmp|private|root|volume1|home|mnt)/)"
    r"[A-Za-z0-9._@%+\-/]*[A-Za-z0-9._@%+\-]"
    r"(?::\d{1,6})?"
)
TRAILING = ".,;:，。；：、）)]}」』\"'"

#: Words that make an entry a *claim about a change*, i.e. T2 material.
CLAIM_RE = re.compile(r"(?:修正|更正|取代|已过时|已废弃|supersedes|deprecated)")


def load_scan_module():
    """Reuse the scan's discovery/parsing so there is one definition of a track."""
    path = Path(__file__).resolve().parent / "hygiene_scan.py"
    name = "hygiene_scan_for_verify"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if spec.name not in sys.modules:
        sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def extract_assertions(body: str) -> list[dict[str, str]]:
    """Pull filesystem assertions out of one entry, de-duplicated and in order."""
    seen: set[str] = set()
    found: list[dict[str, str]] = []
    for match in PATH_RE.finditer(body):
        raw = match.group(0).rstrip(TRAILING)
        line: str | None = None
        if re.search(r":\d{1,6}$", raw):
            raw, line = raw.rsplit(":", 1)
        if raw in seen:
            continue
        seen.add(raw)
        found.append({"path": raw, "line": line or ""})
    return found


def classify_scope(path: str) -> str:
    """``local`` / ``foreign`` / ``unknown`` — never guess that a foreign path is wrong."""
    if any(path.startswith(prefix) for prefix in FOREIGN_ROOTS):
        return "foreign"
    if any(path.startswith(prefix) for prefix in LOCAL_ROOTS):
        return "local"
    return "unknown"


def verify_assertion(assertion: dict[str, str]) -> dict[str, Any]:
    """Check one path assertion. Verdicts: confirmed / refuted / unknown."""
    path = assertion["path"]
    scope = classify_scope(path)
    result: dict[str, Any] = {"path": path, "line": assertion["line"], "scope": scope}
    if scope != "local":
        result.update(verdict="unknown", reason=f"{scope}_scope")
        return result
    if any(path.startswith(prefix) for prefix in EPHEMERAL_ROOTS):
        result.update(verdict="unknown", reason="ephemeral_path")
        return result
    target = Path(path).expanduser()
    try:
        exists = target.exists()
    except PermissionError:
        result.update(verdict="unknown", reason="permission_denied")
        return result
    except OSError as error:
        result.update(verdict="unknown", reason=f"os_error:{error.errno}")
        return result
    if not exists:
        # Refuting requires evidence that we are looking at the same tree: if the
        # parent directory is absent too, the path may simply live on another
        # host (the store mixes memories written on M1 and M3, same user name).
        if not target.parent.exists():
            result.update(verdict="unknown", reason="ancestor_missing")
            return result
        result.update(verdict="refuted", reason="stale_path")
        return result
    if assertion["line"]:
        try:
            with target.open("rb") as handle:
                total = sum(1 for _ in handle)
        except OSError:
            result.update(verdict="confirmed", reason="exists_line_unreadable")
            return result
        if int(assertion["line"]) > total:
            result.update(verdict="refuted", reason=f"line_beyond_eof:{total}")
            return result
        result.update(verdict="confirmed", reason="exists_with_line")
        return result
    result.update(verdict="confirmed", reason="exists")
    return result


def judge_entry(entry, verifier) -> dict[str, Any]:
    """Bucket one entry into T1 (with a verdict) / T2 / T3."""
    assertions = verifier(entry.body)
    results = [verify_assertion(item) for item in assertions]
    payload: dict[str, Any] = {
        "track": entry.track,
        "label": entry.body.strip().replace("\n", " ")[:120],
        "assertions": results,
    }
    if not results:
        payload["tier"] = "T2" if CLAIM_RE.search(entry.body) else "T3"
        payload["verdict"] = "unverifiable"
        return payload
    verdicts = {item["verdict"] for item in results}
    if "refuted" in verdicts:
        payload.update(tier="T1", verdict="refuted")
    elif verdicts == {"confirmed"}:
        payload.update(tier="T1", verdict="confirmed")
    else:
        payload.update(tier="T1", verdict="unknown", reason="partial_or_out_of_scope")
    return payload


def collect_entries(root: Path):
    """Yield ``(track_path, entry)`` for every entry, aliasing collapsed."""
    scan = load_scan_module()
    canonical, _aliases = scan.resolve_aliases(root, scan.discover_tracks(root))
    for path in canonical:
        track = scan.load_track(path, root)
        for entry in track.entries:
            entry.track = track.path  # type: ignore[attr-defined]
            yield track.path, entry


def build_report(root: Path, week: str) -> dict[str, Any]:
    """Judge the whole corpus and split it into settled / unsettled / sample."""
    scan = load_scan_module()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(str(root))
    if not scan.discover_tracks(root):
        raise LookupError(f"no memory track found under {root}")
    items = [judge_entry(entry, extract_assertions) for _track, entry in collect_entries(root)]
    buckets: dict[str, list[dict[str, Any]]] = {"confirmed": [], "refuted": [], "unknown": [], "unverifiable": []}
    for item in items:
        buckets[item["verdict"]].append(item)

    seed_material = f"{week}|los-memory-hygiene|{len(items)}".encode()
    seed = int(hashlib.sha256(seed_material).hexdigest()[:12], 16)
    rng = random.Random(seed)

    def sample(name: str, population: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
        if not population:
            return []
        picked = rng.sample(population, min(size, len(population)))
        return [{"bucket": name, **item} for item in picked]

    return {
        "schema_version": SCHEMA_VERSION,
        "week": week,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus": {"kind": "dsh-md", "root": str(root)},
        "counts": {
            "entries": len(items),
            "unknown_reasons": dict(
                sorted(
                    {
                        reason: sum(
                            1
                            for item in buckets["unknown"]
                            for a in item["assertions"]
                            if a["verdict"] == "unknown" and a["reason"] == reason
                        )
                        for reason in {a["reason"] for item in buckets["unknown"] for a in item["assertions"] if a["verdict"] == "unknown"}
                    }.items()
                )
            ),
            "confirmed": len(buckets["confirmed"]),
            "refuted": len(buckets["refuted"]),
            "unknown": len(buckets["unknown"]),
            "unverifiable": len(buckets["unverifiable"]),
            "t2_claim": sum(1 for item in buckets["unverifiable"] if item["tier"] == "T2"),
            "t3_subjective": sum(1 for item in buckets["unverifiable"] if item["tier"] == "T3"),
        },
        "refuted": buckets["refuted"],
        "unknown": buckets["unknown"][:50],
        "sample": {
            "seed": seed,
            "rule": "同一周同一语料 ⇒ 同一抽样（可复核）；refuted 全量纳入，其余按桶抽样",
            "refuted": buckets["refuted"],
            "unverifiable": sample("unverifiable", buckets["unverifiable"], 20),
            "confirmed_control": sample("confirmed_control", buckets["confirmed"], 5),
        },
    }


def render(report: dict[str, Any]) -> str:
    """Human view: settled / unsettled / what to inspect this week."""
    counts = report["counts"]
    lines = [
        f"week {report['week']} · entries {counts['entries']}",
        f"T1 confirmed {counts['confirmed']} · refuted {counts['refuted']} · unknown {counts['unknown']}",
        f"T2 claim (需交叉评审) {counts['t2_claim']} · T3 subjective (人/抽样) {counts['t3_subjective']}",
    ]
    if report["refuted"]:
        lines.append("refuted（机器已反证，按事实处理）:")
        for item in report["refuted"][:10]:
            bad = next(a for a in item["assertions"] if a["verdict"] == "refuted")
            lines.append(f"  [{item['track']}] {bad['path']} — {bad['reason']} | {item['label'][:70]}")
    lines.append(f"抽检（seed {report['sample']['seed']}）:")
    for item in report["sample"]["unverifiable"][:8]:
        lines.append(f"  {item['tier']} [{item['track']}] {item['label'][:80]}")
    for item in report["sample"]["confirmed_control"][:3]:
        lines.append(f"  control(confirmed) [{item['track']}] {item['label'][:70]}")
    return "\n".join(lines)


def iso_week(now: datetime | None = None) -> str:
    """Current ISO week, so a report is reproducible per week."""
    stamp = now or datetime.now()
    year, week, _ = stamp.isocalendar()
    return f"{year}-W{week:02d}"


def self_test() -> int:
    """Assert the three tiers and the two 'unknown is not refuted' controls."""
    checks: list[tuple[str, bool]] = []
    with open(os.devnull, "w") as _:
        pass

    checks.append(("extracts ~ path", [a["path"] for a in extract_assertions("见 ~/.dsh/memories/MEMORY.md 这条")] == ["~/.dsh/memories/MEMORY.md"]))
    checks.append(("extracts path:line", extract_assertions("lib/store.js 之外，还有 /Users/echerlos/x.py:12 这样")[0]["line"] == "12"))
    checks.append(("ignores prose", extract_assertions("没有任何路径的一句话") == []))
    checks.append(("foreign path is unknown", verify_assertion({"path": "/root/foo", "line": ""})["verdict"] == "unknown"))
    checks.append(("ephemeral path is unknown", verify_assertion({"path": "/tmp/whatever", "line": ""})["reason"] == "ephemeral_path"))
    checks.append(("missing ancestor is unknown", verify_assertion({"path": "~/no-such-tree-xyz/file.md", "line": ""})["reason"] == "ancestor_missing"))
    checks.append(("missing sibling under a real tree is refuted", verify_assertion({"path": "/etc/definitely-absent-hygiene-probe", "line": ""})["verdict"] == "refuted"))
    checks.append(("existing local path is confirmed", verify_assertion({"path": "/etc/hosts", "line": ""})["verdict"] == "confirmed"))
    checks.append(("line beyond eof is refuted", verify_assertion({"path": "/etc/hosts", "line": "999999"})["verdict"] == "refuted"))
    checks.append(("week is stable", iso_week(datetime(2026, 11, 4)) == "2026-W45"))

    class _Entry:
        def __init__(self, body: str) -> None:
            self.body = body
            self.track = "MEMORY.md"

    checks.append(("claim without path is T2", judge_entry(_Entry("修正上一条关于端口 8080 的说法"), extract_assertions)["tier"] == "T2"))
    checks.append(("plain prose is T3", judge_entry(_Entry("一个没有路径也没有声明的主观判断"), extract_assertions)["tier"] == "T3"))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'ok' if ok else 'FAIL'}  {name}")
    print(f"self-test: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)})")
    return EXIT_OK if not failed else EXIT_REFUTED


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Verify what is verifiable; sample what is not.")
    parser.add_argument("command", nargs="?", choices=("report", "self-test"), default="report")
    parser.add_argument("--corpus", default="dsh-md:~/.dsh/memories")
    parser.add_argument("--week", default=None, help="ISO week, e.g. 2026-W45 (default: current)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--fail-on", choices=("none", "refuted"), default="none")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "self-test":
        return self_test()

    kind, _, raw = args.corpus.partition(":")
    if kind != "dsh-md" or not raw:
        print(f"cannot judge: unknown corpus spec {args.corpus!r}", file=sys.stderr)
        return EXIT_UNJUDGEABLE
    root = Path(raw).expanduser()
    try:
        report = build_report(root, args.week or iso_week())
    except (FileNotFoundError, LookupError) as error:
        print(f"cannot judge: {error}", file=sys.stderr)
        return EXIT_UNJUDGEABLE

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload if args.json else render(report))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    if args.fail_on == "refuted" and report["counts"]["refuted"] > 0:
        return EXIT_REFUTED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
