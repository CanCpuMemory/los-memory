#!/usr/bin/env python3
"""Turn the hygiene scan into a **decision-ready plan** — and nothing else.

Why this exists (P1 of ``docs/design/memory-hygiene-and-consolidation.md``): the
scan (P0) says *what* looks wrong; a plan says *what would be done, to which
entry, keeping which copy, and why*. It is deliberately still **read-only**:

* the corpus belongs to another writer (``dsh-memory-evolve`` holds a directory
  lock and applies its own ``[id:…]/[deprecated:…]`` marks). Adding a second
  writer from Python is exactly what the design forbids, so this tool emits the
  plan and the writer applies it;
* two classes in the measured corpus need a human decision before any action —
  symbolically-aliased tracks (one file, two names: reported as ``aliases`` and
  never "deduped") and prose claims without a mechanical edge (**only 4 of 136**
  resolve to a target at all). Both are emitted under ``proposals``, never under
  ``actions``.

``actions`` therefore contains only entry-scoped, mechanically decidable work
(exact duplicate bodies, unresolved supersede edges, multi-active slots — the
last two are empty today but the shape is stable).

Exit codes: 0 = plan written and judged, 2 = cannot judge (no corpus/track).

Usage:
  python3 scripts/hygiene_cleanup.py plan
  python3 scripts/hygiene_cleanup.py plan --corpus dsh-md:~/.dsh/memories --json
  python3 scripts/hygiene_cleanup.py status
  python3 scripts/hygiene_cleanup.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
RULES_VERSION = "hygiene-1"
EXIT_OK, EXIT_REFUSED, EXIT_UNJUDGEABLE = 0, 1, 2
PLAN_TTL_DAYS = 7
#: Only these finding codes become executable actions. Everything else is a
#: proposal or a review item; keeping the list explicit means a new detector
#: cannot silently become auto-executable.
AUTO_CODES = ("exact_duplicate", "supersedes_unresolved", "slot_multi_active")
REVIEW_CODES = ("claim_without_edge", "near_duplicate", "lifecycle_blind")
PROPOSAL_CODES = ("track_duplicate",)
#: `daily/*.md` is an append-only evidence surface (a day's log), so a duplicate
#: line there is reported, never "cleaned": removing it would edit history.
EVIDENCE_TRACK_PREFIXES = ("daily/",)

#: Positional self-reference: "修正上一条 …" points at the previous entry in the
#: same track. Measured 2026-11-04: 12/12 such claims have a live predecessor, so
#: this is the highest-confidence resolver for a claim's target.
POSITIONAL_RE = re.compile(r"(上一条|上条|前一条|上面那条|此前那条|上一条目)")

#: A quoted span is the second-strongest pointer (43 of 112 quoted claims resolve
#: to exactly one entry in the same track). Identifier overlap is deliberately
#: NOT used: it "matched" 135/136 claims in a 5k-entry corpus, i.e. no signal.
QUOTED_RE = re.compile(r"[「“\"]([^」”\"]{4,60})[」”\"]")

#: Card generation wants **recall** (a human filters cheaply), while the scan's
#: `CLAIM_RE` is a stable bucket definition. So this one is broader on purpose:
#: "更正「X」" is a claim even though it lacks the "上一条" phrasing.
CLAIM_ANY_RE = re.compile(r"(supersedes|取代|修正|更正|已过时|已废弃|过期)")

#: A quoted span is only a *pointer* when it looks like a name, not like data.
#: Measured 2026-11-04: without this filter, `o9cq807d…@im.wechat` and
#: `{{DSH_SCHED_DELIVERY_KEY}}` were "resolved" to unrelated entries — text
#: matching again failing to discriminate. Spans carrying these shapes are dropped.
POINTER_REJECT_RE = re.compile(r"[@{}<>|/\\`]")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
#: Distance (chars) from the nearest claim word within which a quote is treated as
#: the object of the claim rather than incidental quoting elsewhere in the entry.
QUOTE_PROXIMITY = 40

#: The four choices a human makes per card. Written into the card so the review
#: surface and the plan cannot drift apart.
CARD_CHOICES = ("supersede_entry", "supersede_clause", "independent", "unclear")


def load_scan_module():
    """Load ``scripts/hygiene_scan.py`` as a module (single source of the ruler)."""
    path = Path(__file__).resolve().parent / "hygiene_scan.py"
    name = "hygiene_scan_for_cleanup"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if spec.name not in sys.modules:
        sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def state_dir() -> Path:
    """Where plans live; ``LOS_MEMORY_HYGIENE_DIR`` keeps tests hermetic."""
    override = os.environ.get("LOS_MEMORY_HYGIENE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "los-memory-hygiene"


def corpus_snapshot_hash(root: Path, scan_module) -> str:
    """Hash of every track's bytes — drift detection that does not trust mtime."""
    digest = hashlib.sha256()
    for path in sorted(scan_module.discover_tracks(root), key=lambda item: str(item)):
        try:
            payload = path.read_bytes()
        except OSError:
            payload = b""
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def _canonical_actions(actions: list[dict[str, Any]]) -> str:
    """Stable serialisation, so the same plan on the same corpus keeps its id."""
    return json.dumps(actions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plan_id_for(snapshot_hash: str, actions: list[dict[str, Any]]) -> str:
    """Content-addressed plan id: same corpus + same actions ⇒ same id (replay-safe)."""
    digest = hashlib.sha256()
    digest.update(RULES_VERSION.encode("utf-8"))
    digest.update(b"\0")
    digest.update(snapshot_hash.encode("utf-8"))
    digest.update(b"\0")
    digest.update(_canonical_actions(actions).encode("utf-8"))
    return digest.hexdigest()[:32]


def _action_from_sample(code: str, sample: dict[str, Any]) -> dict[str, Any]:
    """Map one scan sample onto one reversible, entry-scoped action."""
    action = {
        "code": code,
        "op": "deprecate",
        "target": {
            "track": sample["track"],
            "id8": sample.get("id"),
            "label": sample.get("label"),
        },
        "reason": code,
        "safety": "auto_safe",
    }
    if code == "exact_duplicate":
        action["keep"] = {"track": sample["track"], "id8": sample.get("first_id")}
        action["why"] = "归一化正文完全相同，保留首次出现的那条，另一条打失效标记"
    elif code == "supersedes_unresolved":
        action["target"]["id8"] = sample.get("target")
        action["why"] = "该条目已被后续条目声明取代，但自身仍为 active"
    elif code == "slot_multi_active":
        action["slot"] = sample.get("slot")
        action["why"] = "同一轨道同一槽位有多个 active，保留最新一条"
    return action


def _pointer_like(span: str) -> bool:
    """Whether a quoted span reads like a name a claim would point at."""
    if POINTER_REJECT_RE.search(span):
        return False
    if len(span) < 4 or len(span) > 60:
        return False
    if not CJK_RE.search(span) and len(span) < 8:
        return False  # bare short ASCII tokens are identifiers, not titles
    return not re.fullmatch(r"[0-9a-fA-F\-.]+", span)


def _claim_logo_spans(body: str) -> list[int]:
    """Positions of claim words, used for the proximity test."""
    return [match.start() for match in CLAIM_ANY_RE.finditer(body)]


def _best_quoted_pointer(body: str, entries: list[Any], self_entry: Any, df_lookup) -> dict[str, Any] | None:
    """Pick the quoted span that most likely names the entry being superseded.

    Scoring is deliberately about *shape and position*, not similarity:
    spans near a claim word score higher; a target whose body starts with the span
    (i.e. the span is its title) scores higher. No span ⇒ no card.
    """
    logo = _claim_logo_spans(body)
    best: dict[str, Any] | None = None
    best_score = 0.0
    for match in QUOTED_RE.finditer(body):
        span = match.group(1).strip()
        if not _pointer_like(span):
            continue
        distance = min((abs(match.start() - position) for position in logo), default=10_000)
        if distance > QUOTE_PROXIMITY:
            continue
        hits = [other for other in entries if other is not self_entry and other.active and span in other.body]
        if not hits:
            continue
        # **Rarity gate** (measured 2026-11-04): a common phrase like "有真实调用"
        # matches an unrelated entry, so a span present in more than two entries is
        # not a pointer. This is the discriminator that plain containment lacks.
        if df_lookup(span) > 2:
            continue
        score = 3.0
        score += 2.0 if any(other.body.lstrip().startswith(span) for other in hits) else 0.0
        score += 1.0 if len(span) >= 8 else 0.0
        score += max(0.0, 1.0 - distance / QUOTE_PROXIMITY)
        if score <= best_score:
            continue
        best_score = score
        signal = "quoted" if len(hits) == 1 else "quoted_multi"
        best = {
            "signal": signal,
            "target": hits[0],
            "evidence": {
                "quoted": span,
                "candidates": len(hits),
                "distance_to_claim_word": distance,
                "document_frequency": df_lookup(span),
                "score": round(score, 2),
                "rule": "引号短语（名称形状 + 靠近声明词）在同轨命中",
            },
        }
    return best



def resolve_claims(root: Path) -> dict[str, Any]:
    """Turn prose claims into a short list of reviewable cards (read-only).

    The funnel measured on 2026-11-04: 278 T2 entries → drop `daily/` (113) and
    already-edged entries → 136 → 47 cards (12 positional + 35 quoted). The
    remaining claims have no referent at all; they are counted, not judged.
    """
    scan = load_scan_module()
    canonical, _aliases = scan.resolve_aliases(root, scan.discover_tracks(root))
    all_entries: list[Any] = []
    for path in canonical:
        all_entries.extend(scan.load_track(path, root).entries)
    df_cache: dict[str, int] = {}

    def df_lookup(span: str) -> int:
        """How many corpus entries contain the span — the rarity discriminator."""
        if span not in df_cache:
            df_cache[span] = sum(1 for candidate in all_entries if span in candidate.body)
        return df_cache[span]

    cards: list[dict[str, Any]] = []
    counts = {"claim_entries": 0, "excluded_evidence_track": 0, "excluded_has_edge": 0,
              "excluded_deprecated": 0, "unresolved_no_referent": 0, "cards": 0}
    for path in canonical:
        track = scan.load_track(path, root)
        if track.path.startswith(EVIDENCE_TRACK_PREFIXES):
            counts["excluded_evidence_track"] += sum(
                1 for entry in track.entries if entry.active and CLAIM_ANY_RE.search(entry.body)
            )
            continue
        entries = track.entries
        for index, entry in enumerate(entries):
            if not entry.active or not CLAIM_ANY_RE.search(entry.body):
                continue
            counts["claim_entries"] += 1
            if entry.supersedes is not None:
                counts["excluded_has_edge"] += 1
                continue
            if entry.deprecated:
                counts["excluded_deprecated"] += 1
                continue
            signal, target, evidence = "", None, {}
            previous = entries[index - 1] if index > 0 else None
            if POSITIONAL_RE.search(entry.body) and previous is not None and previous.active:
                signal, target = "positional", previous
                evidence = {"distance": 1, "rule": "上一条 ⇒ 同轨前一条"}
            if target is None:
                chosen = _best_quoted_pointer(entry.body, entries, entry, df_lookup)
                if chosen is not None:
                    signal, target = chosen["signal"], chosen["target"]
                    evidence = chosen["evidence"]
            if target is None:
                counts["unresolved_no_referent"] += 1
                continue
            cards.append(
                {
                    "card_id": f"c{len(cards) + 1}",
                    "signal": signal,
                    "track": track.path,
                    "claim": {
                        "id8": entry.id,
                        "date": entry.date,
                        "label": re.sub(r"\s+", " ", entry.body)[:160],
                        "excerpt": re.sub(r"\s+", " ", entry.body)[:400],
                    },
                    "target": {
                        "id8": target.id,
                        "date": target.date,
                        "label": re.sub(r"\s+", " ", target.body)[:160],
                        "excerpt": re.sub(r"\s+", " ", target.body)[:400],
                    },
                    "evidence": evidence,
                    "choices": list(CARD_CHOICES),
                    "default": "unclear",
                }
            )
    order = {"positional": 0, "quoted": 1, "quoted_multi": 2}
    cards.sort(key=lambda card: (order[card["signal"]], card["track"]))
    for index, card in enumerate(cards, start=1):
        card["card_id"] = f"c{index}"
    counts["cards"] = len(cards)
    counts["by_signal"] = {
        name: sum(1 for card in cards if card["signal"] == name) for name in order
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
        "cards": cards,
        "how_to_answer": "每张卡选 1=整条取代 2=仅这一句 3=无关 4=看不出；结果写回后由写者落 [supersedes:id]",
    }


def render_cards(claims: dict[str, Any]) -> str:
    """Human review list: one block per card, choices at the bottom."""
    counts = claims["counts"]
    lines = [
        f"待判卡片 {counts['cards']} 张（位置 {counts['by_signal']['positional']} / 引号 "
        f"{counts['by_signal']['quoted']} / 多候选 {counts['by_signal']['quoted_multi']}）",
        f"已排除：daily 证据面 {counts['excluded_evidence_track']} · 已有边 {counts['excluded_has_edge']}"
        f" · 已退役 {counts['excluded_deprecated']}；无指代（不判）{counts['unresolved_no_referent']}",
        "",
    ]
    for card in claims["cards"]:
        evidence = card["evidence"]
        hint = evidence.get("quoted") or evidence.get("rule", "")
        lines.append(f"=== {card['card_id']} · {card['signal']} · {card['track']} · 线索：{hint}")
        lines.append(f"旧（{card['target']['date'] or '?'}·{card['target']['id8'] or '无id'}）：{card['target']['label']}")
        lines.append(f"新（{card['claim']['date'] or '?'}·{card['claim']['id8'] or '无id'}）：{card['claim']['label']}")
        lines.append("判定：1=整条取代 2=仅这一句 3=无关 4=看不出")
        lines.append("")
    return "\n".join(lines)


def build_plan(root: Path, include_review: bool = False) -> dict[str, Any]:
    """Scan ``root`` and render a plan document (no writes to the corpus)."""
    scan_module = load_scan_module()
    report = scan_module.scan(root, max_samples=500)
    findings = report["findings"]

    raw_actions = [_action_from_sample(code, sample) for code in AUTO_CODES
                   for sample in findings.get(code, {}).get("samples", [])]
    actions = [action for action in raw_actions
               if not action["target"]["track"].startswith(EVIDENCE_TRACK_PREFIXES)]
    evidence_held = [action for action in raw_actions if action not in actions]
    for index, action in enumerate(actions, start=1):
        action["id"] = f"a{index}"

    proposals: dict[str, Any] = {}
    for code in PROPOSAL_CODES:
        item = findings.get(code)
        if not item:
            continue
        groups = []
        for sample in item["samples"]:
            tracks = sample.get("tracks", [])
            survivor, stranded = tracks[0], tracks[1:]
            groups.append(
                {
                    "tracks": tracks,
                    "entries": sample.get("entries"),
                    "entry_set_hash": sample.get("entry_set_hash"),
                    "proposed_survivor": survivor,
                    "proposed_removal": stranded,
                    "why": "条目集合 hash 相同（同一项目经两个 cwd 别名各一份）；"
                    "保留 survivor，移除其余路径即可，内容由 survivor 持有",
                    "requires_decision": True,
                }
            )
        if groups:
            proposals["dedupe_track"] = {
                "count": item["count"],
                "extra_entries": sum(group["entries"] for group in groups),
                "groups": groups,
            }

    review: dict[str, Any] = {}
    for code in REVIEW_CODES:
        item = findings.get(code)
        if not item:
            continue
        review[code] = {
            "count": item["count"],
            "samples": item["samples"][: (10 if include_review else 3)],
            "handling": "必须人工确认（文本相似只提名，不是判据）",
        }
    if evidence_held:
        review.setdefault("exact_duplicate_evidence_track", {})["count"] = len(evidence_held)
        review["exact_duplicate_evidence_track"]["handling"] = (
            "daily/ 是证据面：重复行只报告，不自动清理（清理会改写当天日志）"
        )
        review["exact_duplicate_evidence_track"]["samples"] = [
            {"track": action["target"]["track"], "label": action["target"]["label"]}
            for action in evidence_held[:3]
        ]
    if review:
        proposals["review"] = review

    created = datetime.now(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id_for(corpus_snapshot_hash(root, scan_module), actions),
        "created_at": created.isoformat(timespec="seconds"),
        "expires_at": (created + timedelta(days=PLAN_TTL_DAYS)).isoformat(timespec="seconds"),
        "corpus": {
            "kind": "dsh-md",
            "root": str(root),
            "snapshot_hash": corpus_snapshot_hash(root, scan_module),
            "tracks": report["metrics"]["tracks"],
            "entries": report["metrics"]["entries_total"],
        },
        "rules_version": RULES_VERSION,
        "actions": actions,
        "proposals": proposals,
        "counts": {
            "actions": len(actions),
            "proposal_groups": len(proposals.get("dedupe_track", {}).get("groups", [])),
            "review": sum(item["count"] for item in review.values()),
        },
        "writer": {
            "owner": "dsh-memory-evolve",
            "lock": "<dir>/.memory.lock (openSync 'wx')",
            "apply_surface": "lib/lifecycle.js deprecateEntry(entry, byId) + lib/store.js withLock(dir, fn)",
            "state": "not_connected",
        },
    }


def render_human(plan: dict[str, Any]) -> str:
    """Small human view; the JSON stays the contract the writer consumes."""
    corpus = plan["corpus"]
    lines = [
        f"plan {plan['plan_id']} · {corpus['kind']} {corpus['root']}",
        f"tracks {corpus['tracks']} · entries {corpus['entries']} · expires {plan['expires_at']}",
        f"actions {plan['counts']['actions']}（可自动执行，仍需写者落盘）",
    ]
    for action in plan["actions"]:
        lines.append(f"  {action['id']} {action['code']} → {action['target']['track']} {action['target']['id8'] or '(无 id)'}")
    dedupe = plan["proposals"].get("dedupe_track")
    if dedupe:
        lines.append(
            f"proposals.dedupe_track {dedupe['count']} 组 · 多出 {dedupe['extra_entries']} 条（需决策：保留哪个目录）"
        )
    review = plan["proposals"].get("review")
    if review:
        lines.append("proposals.review " + " · ".join(f"{code} {item['count']}" for code, item in review.items()))
    lines.append(f"writer: {plan['writer']['owner']}（{plan['writer']['state']}）")
    return "\n".join(lines)


def cmd_plan(args: argparse.Namespace) -> int:
    """Build the plan, write it under the state directory, and print a summary."""
    kind, _, raw = args.corpus.partition(":")
    if kind != "dsh-md" or not raw:
        print(f"cannot judge: unknown corpus spec {args.corpus!r}", file=sys.stderr)
        return EXIT_UNJUDGEABLE
    root = Path(raw).expanduser()
    try:
        plan = build_plan(root, include_review=args.include_review)
    except (FileNotFoundError, LookupError) as error:
        print(f"cannot judge: {error}", file=sys.stderr)
        return EXIT_UNJUDGEABLE

    if args.claims:
        plan["claims"] = resolve_claims(root)

    target = args.out or (state_dir() / "plans" / f"{plan['plan_id']}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.claims and args.cards is not None:
        args.cards.parent.mkdir(parents=True, exist_ok=True)
        args.cards.write_text(render_cards(plan["claims"]) + "\n", encoding="utf-8")
    print(json.dumps(plan, ensure_ascii=False, indent=2) if args.json else render_human(plan))
    if args.claims:
        cards = plan["claims"]["counts"]
        print(
            f"claims: {cards['cards']} 张卡（位置 {cards['by_signal']['positional']} / 引号 "
            f"{cards['by_signal']['quoted']} / 多候选 {cards['by_signal']['quoted_multi']}）· "
            f"无指代 {cards['unresolved_no_referent']} 条不判",
            file=sys.stderr,
        )
    print(f"plan written: {target}", file=sys.stderr)
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    """List plans already produced, newest first."""
    directory = state_dir() / "plans"
    items = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            items.append({"path": str(path), "state": "unreadable"})
            continue
        items.append(
            {
                "plan_id": plan.get("plan_id"),
                "created_at": plan.get("created_at"),
                "expires_at": plan.get("expires_at"),
                "actions": plan.get("counts", {}).get("actions"),
                "path": str(path),
            }
        )
    payload = {"state_dir": str(state_dir()), "plans": items}
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else
          f"state_dir {payload['state_dir']}\n" + "\n".join(
              f"{item.get('created_at', '?')} {item.get('plan_id', item.get('path'))} actions={item.get('actions', '?')}"
              for item in items
          ))
    return EXIT_OK


def self_test() -> int:
    """Assert the plan's shape, stability and the read-only boundary."""
    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "corpus"
        (root / "projects" / "aaaa").mkdir(parents=True)
        (root / "projects" / "bbbb").mkdir(parents=True)
        shared = "\n§\n".join(
            [
                "[2026-01-01] 独立的条目：端口 8080 由 gateway 监听",
                "[2026-01-02] 重复的条目：本机 jj 在 /opt/homebrew/bin/jj，且不在默认 PATH",
                "[2026-01-03] 重复的条目：本机 jj 在 /opt/homebrew/bin/jj，且不在默认 PATH",
                "[2026-01-04] 修正上一条「独立的条目」的说法，实际由 launchd 托管",
            ]
        )
        for name in ("aaaa", "bbbb"):
            (root / "projects" / name / "MEMORY.md").write_text(shared + "\n", encoding="utf-8")

        before = {path: path.read_bytes() for path in root.rglob("*.md")}
        first = build_plan(root)
        second = build_plan(root)
        after = {path: path.read_bytes() for path in root.rglob("*.md")}

        checks.append(("plan has actions", first["counts"]["actions"] >= 1))
        checks.append(("only auto codes in actions", all(a["code"] in AUTO_CODES for a in first["actions"])))
        checks.append(("track duplicate is a proposal", first["proposals"]["dedupe_track"]["count"] == 1))
        checks.append(("proposal needs a decision", first["proposals"]["dedupe_track"]["groups"][0]["requires_decision"]))
        checks.append(("plan_id is content-addressed", first["plan_id"] == second["plan_id"]))
        checks.append(("review kept out of actions", "claim_without_edge" not in
                       {a["code"] for a in first["actions"]}))
        checks.append(("corpus untouched", before == after))
        checks.append(("writer is named", first["writer"]["owner"] == "dsh-memory-evolve"))

        (root / "projects" / "bbbb" / "MEMORY.md").write_text(shared + "\n§\n[2026-01-05] 新增一条\n", encoding="utf-8")
        third = build_plan(root)
        checks.append(("snapshot change changes plan_id", third["plan_id"] != first["plan_id"]))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'ok' if ok else 'FAIL'}  {name}")
    print(f"self-test: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)})")
    return EXIT_OK if not failed else EXIT_REFUSED


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Build a read-only hygiene plan (P1).")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("plan", "status", "self-test"),
        default="plan",
        help="plan (default) | status | self-test",
    )
    parser.add_argument("--corpus", default="dsh-md:~/.dsh/memories", help="kind:path")
    parser.add_argument("--out", type=Path, help="plan file path (default: state dir)")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument("--include-review", action="store_true", help="carry more review samples")
    parser.add_argument("--claims", action="store_true", help="also emit the T2 review cards (read-only)")
    parser.add_argument("--cards", type=Path, help="write the human card list here")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "self-test":
        return self_test()
    if args.command == "status":
        return cmd_status(args)
    return cmd_plan(args)


if __name__ == "__main__":
    raise SystemExit(main())
