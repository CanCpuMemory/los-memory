#!/usr/bin/env python3
"""Read-only hygiene scan of a memory corpus: the ruler before the cleanup.

Why this exists (P0 of ``docs/design/memory-hygiene-and-consolidation.md``):
the repository already has three levers that *measure* memory — the shadow's
``compact``/``status.storage`` for space, ``scripts/lifecycle-probe.mjs`` for
lifecycle counters, ``scripts/memory_console.py`` for system health — and none
of them produces an actionable list. Meanwhile the corpus this tool reads
(``~/.dsh/memories``) carries 1,474-entry project tracks duplicated verbatim
under two directory hashes and a global track with **zero** lifecycle marks
(measured 2026-11-03). "Nothing to clean" and "no way to see what to clean" look
identical from outside, so the first deliverable is a scan, not a deletion.

What it reads (never writes):

  ``dsh-md:<root>``
      The DeepSeek Harness memory store: ``MEMORY.md``, ``USER.md``,
      ``daily/*.md`` and ``projects/*/MEMORY.md|KEY.md``. Entries are separated
      by a line containing only ``§``; head tokens are
      ``[id:xxxxxxxx] [slot:slug] [supersedes:xxxxxxxx] [deprecated(:xxxxxxxx)?]``
      followed by a program-stamped date. That vocabulary is owned by
      ``dsh-memory-evolve/lib/lifecycle.js`` and is deliberately reused here
      instead of invented again — one vocabulary across the layers.

What it judges (one bucket per structural criterion, never text similarity
alone; see the design doc §6):

  ``track_duplicate``          two tracks with the identical entry set (a project
                               reachable through two cwd aliases)
  ``exact_duplicate``          the same normalized entry twice inside one track
  ``near_duplicate``           Jaccard(5-gram) >= threshold, review only
  ``claim_without_edge``       the entry says "supersedes/corrected/obsolete" but
                               carries no mechanical ``[supersedes:id]`` edge
  ``supersedes_unresolved``    a mechanical edge whose target is still active
  ``slot_multi_active``        more than one active entry claims the same slot
  ``lifecycle_blind``          a track with entries but no marks at all

Exit codes (the repository's three-state contract, and ``2`` is not a pass):
  0 = judged (findings may exist; use ``--fail-on`` to make them fatal)
  1 = a finding crossed the ``--fail-on`` line
  2 = cannot judge (root missing/unreadable, or no track found)

Usage:
  python3 scripts/hygiene_scan.py --corpus dsh-md:~/.dsh/memories
  python3 scripts/hygiene_scan.py --corpus dsh-md:~/.dsh/memories --json --out /tmp/h.json
  python3 scripts/hygiene_scan.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterable
from typing import Any

SCHEMA_VERSION = 1
EXIT_OK, EXIT_FINDING, EXIT_UNJUDGEABLE = 0, 1, 2

#: Entry separator used by the DSH store (a line containing only ``§``).
ENTRY_SEPARATOR = "\n§\n"

#: One leading bracketed head token. Head tokens are program metadata, not body.
HEAD_TOKEN_RE = re.compile(r"^\[([^\[\]]{1,120})\]\s*")

#: Claim words that assert a supersession in prose. Kept small and literal: this
#: bucket is a review queue, so recall matters more than precision, and the
#: false positives cost a human ten seconds.
CLAIM_RE = re.compile(r"(supersedes|取代|修正上一条|更正上一条|已过时|已废弃)")

#: Tracks worth scanning. ``TODOS*.md`` uses a different schema (tag line with
#: ``[id: xxxx]``), so it is excluded rather than mis-parsed.
TRACK_NAMES = ("MEMORY.md", "USER.md", "KEY.md")

#: Minimum entry count before an unmarked track is reported as blind. A track
#: with one entry and no marks is normal (nothing has needed a correction yet).
BLIND_TRACK_MIN_ENTRIES = 20

#: Cap on samples carried in the report, so a 1,474-entry track cannot turn the
#: report into a second copy of the corpus.
MAX_SAMPLES = 5

#: A 5-gram appearing in more entries than this carries no discriminating power
#: (shared boilerplate), so it must not be used to propose candidate pairs.
MAX_POSTING = 64


@dataclass
class Entry:
    """One memory entry, with the head tokens parsed out."""

    text: str
    body: str
    id: str | None = None
    slot: str | None = None
    supersedes: str | None = None
    deprecated_by: str | None = None
    deprecated: bool = False
    date: str | None = None
    neutral: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        """Whether the entry is still in the default recall set."""
        return not self.deprecated

    @property
    def norm(self) -> str:
        """Identity for duplicate detection: body with whitespace removed."""
        return re.sub(r"\s+", "", self.body)

    @property
    def label(self) -> str:
        """Short human-readable label for samples."""
        clean = re.sub(r"\s+", " ", self.body).strip()
        return clean[:80]


@dataclass
class Track:
    """One memory file: the unit that owns its own entry list."""

    path: str
    entries: list[Entry]

    @property
    def entry_set_hash(self) -> str:
        """Hash of the *set* of normalized entries, so order and marks do not matter."""
        joined = "\x00".join(sorted(entry.norm for entry in self.entries))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def split_entries(text: str) -> list[str]:
    """Split a store file into raw entries."""
    parts = text.split(ENTRY_SEPARATOR)
    return [part.strip() for part in parts if part.strip()]


def parse_entry(text: str) -> Entry:
    """Parse the leading program-metadata tokens of one entry."""
    rest = text
    entry = Entry(text=text, body=text)
    while True:
        match = HEAD_TOKEN_RE.match(rest)
        if match is None:
            break
        token = match.group(1).strip()
        rest = rest[match.end() :]
        if token.startswith("id:"):
            entry.id = token[3:].strip()
        elif token.startswith("slot:"):
            entry.slot = token[5:].strip()
        elif token.startswith("supersedes:"):
            entry.supersedes = token[11:].strip()
        elif token.startswith("deprecated"):
            entry.deprecated = True
            entry.deprecated_by = token.split(":", 1)[1].strip() if ":" in token else None
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}(?: \d{1,2}:\d{2}(?::\d{2})?)?", token) or re.fullmatch(
            r"\d{1,2}:\d{2}(?::\d{2})?", token
        ):
            entry.date = token
        else:
            # [git …] / [branch:…] / [dsh-only] / [summary:…] are order-preserving
            # metadata that must not become body text but must not change identity.
            entry.neutral.append(token)
    entry.body = rest
    return entry


def discover_tracks(root: Path) -> list[Path]:
    """List the track files under ``root`` without following model conventions."""
    found: list[Path] = []
    for name in ("MEMORY.md", "USER.md"):
        candidate = root / name
        if candidate.is_file():
            found.append(candidate)
    daily = root / "daily"
    if daily.is_dir():
        found.extend(sorted(p for p in daily.glob("*.md") if p.is_file()))
    projects = root / "projects"
    if projects.is_dir():
        for project in sorted(p for p in projects.iterdir() if p.is_dir()):
            for name in TRACK_NAMES:
                candidate = project / name
                if candidate.is_file():
                    found.append(candidate)
    return found


def load_track(path: Path, root: Path) -> Track:
    """Read one track; an unreadable file is reported as empty rather than raising."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    entries = [parse_entry(raw) for raw in split_entries(text)]
    return Track(path=str(path.relative_to(root)), entries=entries)


def _ngrams(body: str, size: int = 5) -> set[str]:
    """Character n-grams; for CJK text a char window is the honest unit."""
    if len(body) <= size:
        return {body} if body else set()
    return {body[i : i + size] for i in range(len(body) - size + 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    shared = len(left & right)
    return shared / len(left | right)


def _sample(track: Track, entry: Entry | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"track": track.path}
    if entry is not None:
        payload.update({"id": entry.id, "date": entry.date, "label": entry.label})
    if extra:
        payload.update(extra)
    return payload


def scan(root: Path, near_dup_threshold: float = 0.8, max_samples: int = MAX_SAMPLES) -> dict[str, Any]:
    """Scan ``root`` and return the report body (no printing, no writes).

    ``max_samples`` exists because the plan writer needs *every* track-duplicate
    group to build an actionable plan, while the human report must stay small.
    """
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(str(root))
    paths = discover_tracks(root)
    if not paths:
        raise LookupError(f"no memory track found under {root}")
    tracks = [load_track(path, root) for path in paths]

    metrics = {
        "tracks": len(tracks),
        "entries_total": sum(len(track.entries) for track in tracks),
        "entries_active": sum(1 for track in tracks for entry in track.entries if entry.active),
        "entries_deprecated": sum(1 for track in tracks for entry in track.entries if entry.deprecated),
        "entries_with_id": sum(1 for track in tracks for entry in track.entries if entry.id),
        "slots": len({entry.slot for track in tracks for entry in track.entries if entry.slot}),
        "claim_entries": sum(
            1 for track in tracks for entry in track.entries if CLAIM_RE.search(entry.body)
        ),
    }

    findings: dict[str, dict[str, Any]] = {}

    def bucket(code: str, safety: str, description: str) -> dict[str, Any]:
        return findings.setdefault(
            code, {"code": code, "safety": safety, "description": description, "count": 0, "samples": []}
        )

    def hit(code: str, safety: str, description: str, sample: dict[str, Any]) -> None:
        current = bucket(code, safety, description)
        current["count"] += 1
        if len(current["samples"]) < max_samples:
            current["samples"].append(sample)

    # D1: the same entry set under two tracks (cwd-alias duplication).
    by_hash: dict[str, list[Track]] = {}
    for track in tracks:
        if track.entries:
            by_hash.setdefault(track.entry_set_hash, []).append(track)
    for group in by_hash.values():
        if len(group) < 2:
            continue
        hit(
            "track_duplicate",
            "auto_safe",
            "两个轨道的条目集合完全相同（通常是同一项目经 cwd 别名建了两份）",
            {
                "tracks": [track.path for track in group],
                "entries": len(group[0].entries),
                "entry_set_hash": group[0].entry_set_hash[:16],
            },
        )

    # D2/D3: within-track exact duplicates, and the mechanical lifecycle edges.
    for track in tracks:
        seen: dict[str, Entry] = {}
        for entry in track.entries:
            if len(entry.norm) < 12:
                continue
            digest = hashlib.sha1(entry.norm.encode("utf-8")).hexdigest()
            if digest in seen:
                hit(
                    "exact_duplicate",
                    "auto_safe",
                    "同一轨道内归一化后正文完全相同",
                    _sample(track, entry, {"first_id": seen[digest].id}),
                )
            else:
                seen[digest] = entry

        by_id = {entry.id: entry for entry in track.entries if entry.id}
        for entry in track.entries:
            if entry.supersedes is not None:
                target = by_id.get(entry.supersedes)
                if target is not None and target.active:
                    hit(
                        "supersedes_unresolved",
                        "auto_safe",
                        "条目声明取代了 X，但 X 仍为 active（声明了却没有落实）",
                        _sample(track, entry, {"target": entry.supersedes}),
                    )
            if entry.active and entry.slot is not None:
                continue

        slot_counts: dict[str, int] = {}
        for entry in track.entries:
            if entry.active and entry.slot is not None:
                slot_counts[entry.slot] = slot_counts.get(entry.slot, 0) + 1
        for slot, count in slot_counts.items():
            if count > 1:
                hit(
                    "slot_multi_active",
                    "auto_safe",
                    "同一槽位有多个 active 条目（槽位语义是每槽只留一条）",
                    _sample(track, None, {"slot": slot, "active": count}),
                )

        # D6: prose claims without a mechanical edge. Review only: prose lies.
        for entry in track.entries:
            if not entry.active or entry.supersedes is not None:
                continue
            if CLAIM_RE.search(entry.body):
                hit(
                    "claim_without_edge",
                    "review_only",
                    "正文自称修正/取代/已过时，但没有机械 supersedes 边",
                    _sample(track, entry),
                )

        # D7: a track that never used the vocabulary at all.
        unmarked = not any(entry.id or entry.slot or entry.deprecated for entry in track.entries)
        if len(track.entries) >= BLIND_TRACK_MIN_ENTRIES and unmarked:
            hit(
                    "lifecycle_blind",
                    "review_only",
                    "该轨道条目较多但完全没有生命周期标记（id/slot/deprecated 皆 0）",
                    _sample(track, None, {"entries": len(track.entries)}),
                )

        # D5: near duplicates, review only — text similarity is never a verdict.
        # Blocked by a rare 5-gram inverted index: an all-pairs comparison over a
        # 1,474-entry track is ~1.1M intersections and does not finish promptly,
        # which would make the ruler unusable exactly where it is needed.
        grams = [(index, entry, _ngrams(entry.norm)) for index, entry in enumerate(track.entries) if len(entry.norm) >= 24]
        postings: dict[str, list[int]] = {}
        for position, (_index, _entry, gram_set) in enumerate(grams):
            for gram in gram_set:
                postings.setdefault(gram, []).append(position)
        candidates: set[tuple[int, int]] = set()
        for posting in postings.values():
            if len(posting) < 2 or len(posting) > MAX_POSTING:
                continue
            for left_pos in range(len(posting)):
                for right_pos in range(left_pos + 1, len(posting)):
                    candidates.add((posting[left_pos], posting[right_pos]))
        for left_pos, right_pos in sorted(candidates):
            left_entry, left_grams = grams[left_pos][1], grams[left_pos][2]
            right_entry, right_grams = grams[right_pos][1], grams[right_pos][2]
            score = _jaccard(left_grams, right_grams)
            if score < near_dup_threshold:
                continue
            if left_entry.norm == right_entry.norm:
                continue  # already counted as exact
            hit(
                "near_duplicate",
                "review_only",
                "两条正文高度重叠（仅作为合并候选，必须人工确认）",
                _sample(track, left_entry, {"other_id": right_entry.id, "jaccard": round(score, 3)}),
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus": {"kind": "dsh-md", "root": str(root)},
        "rules": {
            "near_dup_threshold": near_dup_threshold,
            "blind_track_min_entries": BLIND_TRACK_MIN_ENTRIES,
            "max_samples": max_samples,
        },
        "metrics": metrics,
        "findings": {code: findings[code] for code in sorted(findings)},
        "counts_by_safety": {
            "auto_safe": sum(item["count"] for item in findings.values() if item["safety"] == "auto_safe"),
            "review_only": sum(item["count"] for item in findings.values() if item["safety"] == "review_only"),
        },
    }


def render_human(report: dict[str, Any]) -> str:
    """Render a compact human view; the JSON stays the machine contract."""
    lines = []
    corpus = report["corpus"]
    lines.append(f"corpus: {corpus['kind']} {corpus['root']}")
    metrics = report["metrics"]
    lines.append(
        "tracks {tracks} · entries {entries_total} (active {entries_active} / deprecated "
        "{entries_deprecated}) · ids {entries_with_id} · slots {slots} · claim-entries {claim_entries}".format(
            **metrics
        )
    )
    if not report["findings"]:
        lines.append("findings: none (judged)")
        return "\n".join(lines)
    for code, item in report["findings"].items():
        lines.append(f"[{item['safety']}] {code}: {item['count']} — {item['description']}")
        for sample in item["samples"]:
            where = sample.get("track") or ", ".join(sample.get("tracks", []))
            detail = sample.get("label") or sample.get("slot") or sample.get("entries")
            lines.append(f"    {where}: {detail}")
    counts = report["counts_by_safety"]
    lines.append(f"auto_safe: {counts['auto_safe']} · review_only: {counts['review_only']}")
    return "\n".join(lines)


def build_fixture(root: Path) -> None:
    """Write a small corpus that exercises every bucket once (self-test only)."""
    (root / "daily").mkdir(parents=True)
    (root / "projects" / "aaaa").mkdir(parents=True)
    (root / "projects" / "bbbb").mkdir(parents=True)

    shared = "\n§\n".join(
        [
            "[2026-01-01] 干净的条目：端口 8080 由 los gateway 监听",
            "[2026-01-02] 重复的条目：本机 jj 在 /opt/homebrew/bin/jj，且不在默认 PATH",
            "[2026-01-03] 重复的条目：本机 jj 在 /opt/homebrew/bin/jj，且不在默认 PATH",
            "[id:aaaaaaaa] [2026-01-04] 新的条目：网关端口已改为 9090，旧的 8080 作废",
            "[id:bbbbbbbb] [supersedes:aaaaaaaa] [2026-01-05] 声明取代上面那条，但目标仍是 active",
            "[2026-01-06] 修正上一条「干净的条目」的说法，实际由 launchd 托管",
        ]
    )
    (root / "projects" / "aaaa" / "MEMORY.md").write_text(shared + "\n", encoding="utf-8")
    (root / "projects" / "bbbb" / "MEMORY.md").write_text(shared + "\n", encoding="utf-8")

    (root / "MEMORY.md").write_text(
        "\n§\n".join(
            [
                f"[2026-02-{day:02d}] 全局条目 {day}：这一条与其它条目没有重叠，用于反向控制"
                for day in range(1, 22)
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def self_test() -> int:
    """Run the bucket assertions on a fixture and a clean corpus (negative control)."""
    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_fixture(root)
        report = scan(root)
        counts = {code: item["count"] for code, item in report["findings"].items()}
        checks.append(("track_duplicate == 1", counts.get("track_duplicate") == 1))
        checks.append(("exact_duplicate == 2 (one per duplicated track)", counts.get("exact_duplicate") == 2))
        checks.append(("claim_without_edge == 2", counts.get("claim_without_edge") == 2))
        checks.append(("lifecycle_blind == 1", counts.get("lifecycle_blind") == 1))
        checks.append(("near_duplicate off at 0.99", counts.get("near_duplicate", 0) == 0))
        checks.append(("supersedes_unresolved == 2", counts.get("supersedes_unresolved") == 2))
        checks.append(("metrics entries_total == 33", report["metrics"]["entries_total"] == 33))

        # Negative control 1: a corpus with no relationship must produce nothing.
        clean = Path(tmp) / "clean"
        (clean / "projects" / "cccc").mkdir(parents=True)
        (clean / "projects" / "cccc" / "KEY.md").write_text(
            "\n§\n".join([f"[2026-03-0{i}] 互不相同的独立事实 {i * 7919}，用于反向控制" for i in range(1, 5)]),
            encoding="utf-8",
        )
        clean_report = scan(clean)
        clean_counts = {code: item["count"] for code, item in clean_report["findings"].items()}
        checks.append(("clean corpus has no duplicates", not clean_counts.get("exact_duplicate")))
        checks.append(("clean corpus has no near duplicates", not clean_counts.get("near_duplicate")))
        checks.append(("clean corpus is not track_duplicate", not clean_counts.get("track_duplicate")))

        # Negative control 2: a missing root must be "cannot judge", not "clean".
        try:
            scan(Path(tmp) / "does-not-exist")
            checks.append(("missing root raises", False))
        except FileNotFoundError:
            checks.append(("missing root raises", True))

        # Negative control 3: an empty root must be "cannot judge", not "clean".
        empty = Path(tmp) / "empty"
        empty.mkdir()
        try:
            scan(empty)
            checks.append(("empty root raises", False))
        except LookupError:
            checks.append(("empty root raises", True))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'ok' if ok else 'FAIL'}  {name}")
    print(f"self-test: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)})")
    return EXIT_OK if not failed else EXIT_FINDING


def parse_corpus(spec: str) -> tuple[str, Path]:
    """Parse ``kind:path`` (``dsh-md`` is the only kind today)."""
    if ":" not in spec:
        raise ValueError(f"corpus spec must be kind:path, got {spec!r}")
    kind, _, raw = spec.partition(":")
    if kind != "dsh-md":
        raise ValueError(f"unknown corpus kind {kind!r} (supported: dsh-md)")
    return kind, Path(raw).expanduser()


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Read-only hygiene scan of a memory corpus.")
    parser.add_argument("--corpus", default="dsh-md:~/.dsh/memories", help="kind:path (default dsh-md:~/.dsh/memories)")
    parser.add_argument("--near-dup", type=float, default=0.8, help="Jaccard threshold for near-duplicate candidates")
    parser.add_argument("--json", action="store_true", help="print the JSON report")
    parser.add_argument("--out", type=Path, help="also write the JSON report here (the corpus is never written)")
    parser.add_argument(
        "--fail-on",
        choices=("none", "auto-safe", "any"),
        default="none",
        help="which finding class makes exit 1 (default none: scan is informational)",
    )
    parser.add_argument("--self-test", action="store_true", help="run bucket assertions on a fixture and exit")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        return self_test()

    try:
        _, root = parse_corpus(args.corpus)
        report = scan(root, near_dup_threshold=args.near_dup)
    except (ValueError, FileNotFoundError, LookupError) as error:
        print(f"cannot judge: {error}", file=sys.stderr)
        return EXIT_UNJUDGEABLE

    text = json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_human(report)
    print(text)
    if args.out is not None:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = report["counts_by_safety"]
    if args.fail_on == "auto-safe" and counts["auto_safe"] > 0:
        return EXIT_FINDING
    if args.fail_on == "any" and (counts["auto_safe"] + counts["review_only"]) > 0:
        return EXIT_FINDING
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
