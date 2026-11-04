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
#: A card asks about **one clause on one subject**, so the answers are about that
#: clause: which statement is current. "Old still valid" is not "nothing" — it
#: means the *new* entry may be wrong and is worth flagging as a contradiction.
CARD_CHOICES = ("supersede_clause", "old_still_valid", "different_things", "unclear")
#: Keys accepted in an answers file: digits (fastest to type) and the names.
#: `supersede_entry` stays accepted but is not offered: retiring a whole entry
#: from one clause verdict is exactly the mistake this redesign removes.
ANSWER_KEYS = {
    "1": "supersede_clause", "2": "old_still_valid", "3": "different_things", "4": "unclear",
    "supersede_clause": "supersede_clause", "old_still_valid": "old_still_valid",
    "different_things": "different_things", "unclear": "unclear",
    "supersede_entry": "supersede_entry",
}
#: Only a supersede verdict produces an edge; a contradiction produces a review flag.
EDGE_CHOICES = ("supersede_entry", "supersede_clause")


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



def render_compact(claims: dict[str, Any], width: int = 64) -> str:
    """One line per card: the review surface for a chat or a terminal."""
    lines = [f"# {claims['counts']['cards']} 张卡；答案格式 c1=1（1 新的成立 / 2 旧的仍有效 / 3 两件事 / 4 看不出）"]
    for card in claims["cards"]:
        old = card["old_clause"][:width]
        new = card["new_clause"][:width]
        lines.append(f"{card['card_id']:>4} [{card['subject'][:18]}] 旧: {old}")
        lines.append(f"{'':>4} {'':>20} 新: {new}")
    return "\n".join(lines)


def parse_answers(path: Path, claims: dict[str, Any]) -> dict[str, str]:
    """Read ``c1=1`` lines. Unknown card id or value is fatal, never silently dropped."""
    known = {card["card_id"]: card for card in claims["cards"]}
    answers: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"line {number}: expected c1=1, got {raw!r}")
        card_id, _, value = line.partition("=")
        card_id = card_id.strip()
        value = value.strip()
        if card_id not in known:
            raise ValueError(f"line {number}: unknown card {card_id!r}")
        if value not in ANSWER_KEYS:
            raise ValueError(f"line {number}: unknown choice {value!r} for {card_id}")
        answers[card_id] = ANSWER_KEYS[value]
    return answers


def build_edge_proposals(claims: dict[str, Any], answers: dict[str, str]) -> list[dict[str, Any]]:
    """Turn answers into *edge proposals* — nothing here writes the corpus."""
    proposals: list[dict[str, Any]] = []
    for card in claims["cards"]:
        choice = answers.get(card["card_id"])
        if choice not in EDGE_CHOICES:
            if choice == "old_still_valid":
                # The reviewer says the older statement is still current, i.e. the
                # newer entry is wrong: that is a contradiction, not a no-op.
                proposals.append(
                    {
                        "op": "flag_contradiction",
                        "card_id": card["card_id"],
                        "subject": card.get("subject"),
                        "old_clause": card.get("old_clause"),
                        "new_clause": card.get("new_clause"),
                        "track": card["track"],
                        "requires_human": True,
                    }
                )
            continue
        proposals.append(
            {
                "op": "add_supersedes_edge",
                "card_id": card["card_id"],
                "from": {"track": card["track"], "id8": card["claim"]["id8"], "label": card["claim"]["label"]},
                "to": {"track": card["track"], "id8": card["target"]["id8"], "label": card["target"]["label"]},
                "granularity": "entry" if choice == "supersede_entry" else "clause",
                "subject": card.get("subject"),
                "old_clause": card.get("old_clause"),
                "new_clause": card.get("new_clause"),
                # A target without `[id:…]` needs the writer to backfill the
                # deterministic id (`legacyIdFor = sha1(normalized content)`)
                # before the edge can be written; naming it here keeps that
                # implicit step from being lost.
                "id_backfill_required": card["target"]["id8"] is None,
                "signal": card["signal"],
                "requires_human": True,
            }
        )
    return proposals



HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>记忆待判卡片 __PLAN_ID__</title>
<style>
:root{--bg:#f7f7f5;--fg:#16181a;--muted:#5b6167;--line:#dfe1de;--card:#fff;--accent:#0f766e;--warn:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e8eaed;--muted:#9aa3ab;--line:#2b3036;--card:#1b1f24;--accent:#2dd4bf;--warn:#fbbf24}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,"Helvetica Neue","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 20px;z-index:2}
h1{font-size:15px;margin:0 0 6px;font-weight:600;letter-spacing:.02em}
.bar{height:4px;background:var(--line);border-radius:2px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--accent);width:0;transition:width .18s ease}
.meta{color:var(--muted);font-size:13px;display:flex;gap:14px;flex-wrap:wrap;margin-top:6px}
main{max-width:880px;margin:0 auto;padding:20px 20px 120px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px}
p.q{font-size:16px;font-weight:600;margin:0 0 14px;line-height:1.5}
.tagrow{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-bottom:12px}
.tag{border:1px solid var(--line);border-radius:999px;padding:2px 9px}
.tag.acc{border-color:var(--accent);color:var(--accent)}
section.part{border-top:1px dashed var(--line);padding-top:12px;margin-top:12px}
section.part:first-of-type{border-top:0;padding-top:0;margin-top:0}
h2{font-size:12px;margin:0 0 6px;color:var(--muted);font-weight:600;letter-spacing:.04em;text-transform:uppercase}
p.label{margin:0;font-size:15px}
details{margin-top:8px}summary{cursor:pointer;color:var(--muted);font-size:13px}
pre{white-space:pre-wrap;word-break:break-word;background:transparent;border-left:3px solid var(--line);padding:6px 10px;margin:8px 0 0;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted)}
.choices{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:18px}
@media (max-width:640px){.choices{grid-template-columns:repeat(2,1fr)}}
button{font:inherit;color:inherit;background:transparent;border:1px solid var(--line);border-radius:10px;padding:10px 12px;cursor:pointer;text-align:left}
button:hover{border-color:var(--accent)}
button[aria-pressed="true"]{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
button b{display:block;font-size:14px}
button span{display:block;font-size:12px;color:var(--muted);margin-top:2px}
footer{position:fixed;bottom:0;left:0;right:0;background:var(--bg);border-top:1px solid var(--line);padding:10px 20px;display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap}
.nav{display:flex;gap:8px}
.pill{padding:7px 12px;border-radius:999px}
.primary{background:var(--accent);color:#04110f;border-color:transparent;font-weight:600}
.ghost{border-color:var(--line)}
textarea{width:100%;height:120px;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:10px;padding:10px;font:13px/1.5 ui-monospace,Menlo,monospace}
dialog{border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--fg);max-width:720px;width:92%}
dialog::backdrop{background:rgba(0,0,0,.45)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
.hint{color:var(--muted);font-size:12px}
</style></head><body>
<header>
  <h1>记忆待判卡片 · 逐张确认</h1>
  <div class="bar"><i id="progress"></i></div>
  <div class="meta"><span id="counter"></span><span id="tally"></span><span class="hint">键 1–4 选择，←/→ 翻页，u 撤销，s 跳过</span></div>
</header>
<main>
  <div class="card" id="card">
    <div class="tagrow" id="tags"></div>
    <p class="q" id="question"></p>
    <section class="part"><h2>旧说法（该主题）</h2><p class="label" id="oldClause"></p>
      <p class="label" id="oldLabel" style="display:none"></p>
      <details><summary>看全文</summary><pre id="oldText"></pre></details></section>
    <section class="part"><h2>新说法（该主题）</h2><p class="label" id="newClause"></p>
      <p class="label" id="newLabel" style="display:none"></p>
      <details><summary>看全文</summary><pre id="newText"></pre></details></section>
    <div class="choices" id="choices"></div>
  </div>
</main>
<footer>
  <div class="nav"><button class="pill ghost" id="prev">← 上一张</button><button class="pill ghost" id="next">下一张 →</button></div>
  <div class="nav"><button class="pill ghost" id="skip">跳过</button><button class="pill ghost" id="summaryBtn">查看进度</button><button class="pill primary" id="exportBtn">导出答案</button></div>
</footer>
<dialog id="summaryDlg"><h3 style="margin:0 0 10px;font-size:15px">进度</h3><table id="summaryTable"></table>
<p class="hint">点任意行可跳到该卡。</p><div style="text-align:right;margin-top:10px"><button class="pill ghost" id="closeSummary">关闭</button></div></dialog>
<dialog id="exportDlg"><h3 style="margin:0 0 10px;font-size:15px">导出</h3>
<p class="hint" id="exportHint"></p><textarea id="exportText" readonly></textarea>
<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px">
<button class="pill ghost" id="copyBtn">复制</button><button class="pill ghost" id="downloadBtn">下载 answers.txt</button><button class="pill primary" id="closeExport">关闭</button></div></dialog>
<script>
const PLAN_ID = "__PLAN_ID__";
const WEEK = "__WEEK__";
const CARDS = __CARDS_JSON__;
const KEY = "hygiene-answers-" + PLAN_ID;
const LABELS = {1:["新的是当前","旧的这句作废"],2:["旧的仍有效","新条目可能写错了"],3:["两件不同的事","不构成取代"],4:["看不出","需要更多上下文"]};
let state = {index:0, answers:{}};
try { const raw = localStorage.getItem(KEY); if (raw) state = JSON.parse(raw); } catch (e) {}
if (!state.answers) state.answers = {};
if (typeof state.index !== "number" || state.index < 0 || state.index >= CARDS.length) state.index = 0;
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} };
const card = () => CARDS[state.index];
const esc = (s) => String(s == null ? "" : s);
// Labels are shown as plain prose: markdown emphasis and the [summary:…] marker
// are storage syntax, not reading material. The full excerpt stays raw.
const plain = (s) => esc(s).replace(/\[summary:[^\]]*\]\s*/g, "").replace(/\*\*/g, "").replace(/`{1,3}/g, "").replace(/\s+/g, " ").trim();
function render(){
  const c = card();
  document.getElementById("tags").innerHTML =
    '<span class="tag acc">' + (c.signal === "positional" ? "位置指代（上一条）" : "引号指代") + "</span>" +
    '<span class="tag">' + esc(c.track) + "</span>" +
    '<span class="tag">' + esc(c.card_id) + "</span>" +
    (c.subject ? '<span class="tag">主题：' + esc(c.subject) + "</span>" : "") +
    (c.evidence && c.evidence.quoted ? '<span class="tag">引号：' + esc(c.evidence.quoted) + "</span>" : "");
  document.getElementById("question").textContent = c.question || "";
  document.getElementById("oldClause").textContent = plain(c.old_clause || c.target.label);
  document.getElementById("newClause").textContent = plain(c.new_clause || c.claim.label);
  document.getElementById("oldLabel").textContent = plain(c.target.label);
  document.getElementById("newLabel").textContent = plain(c.claim.label);
  document.getElementById("oldText").textContent = esc(c.target.excerpt);
  document.getElementById("newText").textContent = esc(c.claim.excerpt);
  const box = document.getElementById("choices"); box.innerHTML = "";
  [1,2,3,4].forEach((k) => {
    const b = document.createElement("button");
    b.type = "button"; b.dataset.key = k;
    b.setAttribute("aria-pressed", String(state.answers[c.card_id] === k));
    b.innerHTML = "<b>" + k + " · " + LABELS[k][0] + "</b><span>" + LABELS[k][1] + "</span>";
    b.addEventListener("click", () => choose(k));
    box.appendChild(b);
  });
  const answered = Object.keys(state.answers).length;
  document.getElementById("counter").textContent = "第 " + (state.index+1) + " / " + CARDS.length + " 张";
  const tally = [1,2,3,4].map((k) => k + ":" + Object.values(state.answers).filter((v)=>v===k).length).join("  ");
  document.getElementById("tally").textContent = "已答 " + answered + " · " + tally;
  document.getElementById("progress").style.width = (answered / CARDS.length * 100) + "%";
}
function choose(k){
  const c = card(); state.answers[c.card_id] = k; save();
  if (state.index < CARDS.length - 1) state.index += 1;
  save(); render();
}
function go(delta){ state.index = Math.min(CARDS.length-1, Math.max(0, state.index+delta)); save(); render(); }
document.getElementById("prev").onclick = () => go(-1);
document.getElementById("next").onclick = () => go(1);
document.getElementById("skip").onclick = () => go(1);
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "TEXTAREA") return;
  if (["1","2","3","4"].includes(e.key)) { e.preventDefault(); choose(Number(e.key)); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
  else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
  else if (e.key === "u" || e.key === "U") { delete state.answers[card().card_id]; save(); render(); }
  else if (e.key === "s" || e.key === "S") { go(1); }
});
function answersText(){
  return CARDS.filter((c) => state.answers[c.card_id]).map((c) => c.card_id + "=" + state.answers[c.card_id]).join("\n") + "\n";
}
const summaryDlg = document.getElementById("summaryDlg");
document.getElementById("summaryBtn").onclick = () => {
  const rows = CARDS.map((c, i) => "<tr data-i='" + i + "'><td>" + esc(c.card_id) + "</td><td>" + esc(c.track) + "</td><td>" +
    (state.answers[c.card_id] ? state.answers[c.card_id] + " · " + LABELS[state.answers[c.card_id]][0] : "—") + "</td></tr>").join("");
  document.getElementById("summaryTable").innerHTML = "<tr><th>卡</th><th>轨道</th><th>答案</th></tr>" + rows;
  summaryDlg.showModal();
};
document.getElementById("summaryTable").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-i]"); if (!tr) return;
  state.index = Number(tr.dataset.i); save(); render(); summaryDlg.close();
});
document.getElementById("closeSummary").onclick = () => summaryDlg.close();
const exportDlg = document.getElementById("exportDlg");
document.getElementById("exportBtn").onclick = () => {
  const text = answersText();
  document.getElementById("exportText").value = text;
  const missing = CARDS.length - Object.keys(state.answers).length;
  document.getElementById("exportHint").textContent = missing
    ? "还有 " + missing + " 张未答；导出只包含已答的 " + (CARDS.length - missing) + " 条。"
    : "全部 " + CARDS.length + " 张已答。";
  exportDlg.showModal();
};
document.getElementById("closeExport").onclick = () => exportDlg.close();
document.getElementById("copyBtn").onclick = async () => {
  const ta = document.getElementById("exportText"); ta.select();
  try { await navigator.clipboard.writeText(ta.value); } catch (e) { document.execCommand("copy"); }
};
document.getElementById("downloadBtn").onclick = () => {
  const blob = new Blob([document.getElementById("exportText").value], {type:"text/plain"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "answers-" + WEEK + ".txt"; a.click();
  URL.revokeObjectURL(a.href);
};
render();
</script></body></html>
"""


def render_html(claims: dict[str, Any], plan_id: str, week: str) -> str:
    """Single-file, offline questionnaire over the cards (no external assets).

    The cards are embedded so the page never fetches anything: opening it from
    ``file://`` still works, and the answers it exports are exactly the
    ``c1=1`` lines that ``parse_answers`` accepts.
    """
    cards = [
        {
            "card_id": card["card_id"],
            "signal": card["signal"],
            "track": card["track"],
            "subject": card.get("subject", ""),
            "subject_kind": card.get("subject_kind", ""),
            "question": card.get("question", ""),
            "old_clause": card.get("old_clause", ""),
            "new_clause": card.get("new_clause", ""),
            "claim": card["claim"],
            "target": card["target"],
            "evidence": card.get("evidence", {}),
        }
        for card in claims["cards"]
    ]
    payload = json.dumps(cards, ensure_ascii=False).replace("</", "<\\/")
    return (
        HTML_TEMPLATE.replace("__CARDS_JSON__", payload)
        .replace("__PLAN_ID__", plan_id)
        .replace("__WEEK__", week)
    )



#: Clause markers used by this store: circled numbers, semicolons, line breaks.
CLAUSE_SPLIT_RE = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]|[；;]|\n|(?<=[。！？])\s")
#: Subject keys are *structural*: a path, an endpoint, a version, a backticked
#: identifier, a CONSTANT_LIKE token, or a 「quoted name」. Order = specificity.
KEY_PATTERNS = (
    ("path", re.compile(r"(?:~|/Users|/opt|/usr|/etc|/var|/private)/[A-Za-z0-9._@%+\-/]{2,}")),
    ("endpoint", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b|\b[A-Za-z0-9._-]+:\d{2,5}\b")),
    ("version", re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")),
    ("token", re.compile(r"`([^`]{3,60})`")),
    ("const", re.compile(r"\b[A-Z][A-Z0-9_\-]{5,}\b")),
    ("name", re.compile(r"[「“\"]([^」”\"]{4,60})[」”\"]")),
)
CLAUDE_MIN_CLAUSE = 12


def split_clauses(body: str) -> list[str]:
    """Split one entry into clause-sized segments (storage syntax out of the way)."""
    text = re.sub(r"\[summary:[^\]]*\]", "", body)
    text = re.sub(r"\*\*|`{1,3}", "", text)
    parts = [re.sub(r"\s+", " ", part).strip(" ：:、，,") for part in CLAUSE_SPLIT_RE.split(text)]
    return [part for part in parts if len(part) >= CLAUDE_MIN_CLAUSE]


def subject_keys(clause: str) -> list[tuple[str, str]]:
    """Ordered ``(kind, key)`` pairs a clause is about."""
    found: list[tuple[str, str]] = []
    for kind, pattern in KEY_PATTERNS:
        for match in pattern.finditer(clause):
            key = (match.group(1) if pattern.groups else match.group(0)).strip()
            if len(key) >= 3 and (kind, key) not in found:
                found.append((kind, key))
    return found


def trim_around(text: str, key: str, width: int = 200) -> str:
    """Show a window around the subject so a card stays one readable line-block."""
    position = text.find(key)
    if position < 0:
        return text[:width]
    half = max(40, (width - len(key)) // 2)
    start = max(0, position - half)
    end = min(len(text), position + len(key) + half)
    snippet = text[start:end].strip()
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")


def best_clause_pair(claim_body: str, target_body: str, df_lookup) -> dict[str, Any] | None:
    """Pick the clause pair that shares the most specific subject key.

    This is the answer to "条目我看不懂": an entry is often a bundle of many
    statements, so the judgement unit must be one clause on one subject. Rarity
    decides which shared key is the subject (a path/port/version beats a common
    phrase), and only one pair is shown — the shortest readable one.
    """
    claim_clauses = split_clauses(claim_body)
    target_clauses = split_clauses(target_body)
    if not claim_clauses or not target_clauses:
        return None
    candidates: list[dict[str, Any]] = []
    for claim_clause in claim_clauses:
        for kind, key in subject_keys(claim_clause):
            hits = [clause for clause in target_clauses if key in clause]
            if not hits:
                continue
            target_clause = hits[0]
            specificity = {"path": 0, "endpoint": 1, "version": 2, "token": 3, "const": 4, "name": 5}[kind]
            candidates.append(
                {
                    "subject_kind": kind,
                    "subject": key,
                    "old_clause": trim_around(target_clause, key),
                    "new_clause": trim_around(claim_clause, key),
                    "document_frequency": df_lookup(key),
                    "score": (10 - specificity) + max(0.0, 3.0 - df_lookup(key) / 2.0),
                }
            )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item["score"], len(item["new_clause"])))
    best = candidates[0]
    best["alternatives"] = len(candidates)
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
              "excluded_deprecated": 0, "unresolved_no_referent": 0,
              "unresolved_no_shared_subject": 0, "cards": 0}
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
            pair = best_clause_pair(entry.body, target.body, df_lookup)
            if pair is None:
                # No shared structural subject ⇒ the pair is not judgeable clause
                # by clause. Counted, never dumped on the reviewer as a whole-entry
                # question ("条目我看不懂" is exactly this case).
                counts["unresolved_no_shared_subject"] += 1
                continue
            cards.append(
                {
                    "card_id": f"c{len(cards) + 1}",
                    "signal": signal,
                    "track": track.path,
                    "subject_kind": pair["subject_kind"],
                    "subject": pair["subject"],
                    "old_clause": pair["old_clause"],
                    "new_clause": pair["new_clause"],
                    "alternatives": pair["alternatives"],
                    "question": f"「{pair['subject']}」这件事，哪个说法是当前的？",
                    "claim": {
                        "id8": entry.id,
                        "date": entry.date,
                        "label": re.sub(r"\s+", " ", entry.body)[:160],
                        "excerpt": re.sub(r"\s+", " ", entry.body),
                    },
                    "target": {
                        "id8": target.id,
                        "date": target.date,
                        "label": re.sub(r"\s+", " ", target.body)[:160],
                        "excerpt": re.sub(r"\s+", " ", target.body),
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
        lines.append(f"=== {card['card_id']} · {card['track']} · 主题 {card['subject_kind']}:{card['subject']}")
        lines.append(f"问：{card['question']}")
        lines.append(f"旧（{card['target']['date'] or '?'}）：{card['old_clause']}")
        lines.append(f"新（{card['claim']['date'] or '?'}）：{card['new_clause']}")
        lines.append("判定：1=新的取代旧的 2=旧的仍有效（新条目可能错） 3=两件不同的事 4=看不出")
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

    if args.claims and args.html is not None:
        week = args.week or f"{datetime.now().isocalendar().year}-W{datetime.now().isocalendar().week:02d}"
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html(plan["claims"], plan["plan_id"], week), encoding="utf-8")
        print(f"questionnaire written: {args.html}", file=sys.stderr)

    if args.claims and args.cards is not None:
        args.cards.parent.mkdir(parents=True, exist_ok=True)
        args.cards.write_text(render_cards(plan["claims"]) + "\n", encoding="utf-8")
    print(json.dumps(plan, ensure_ascii=False, indent=2) if args.json else render_human(plan))
    if args.claims and args.answers is not None:
        try:
            answers = parse_answers(args.answers, plan["claims"])
        except ValueError as error:
            print(f"cannot judge answers: {error}", file=sys.stderr)
            return EXIT_UNJUDGEABLE
        proposals = build_edge_proposals(plan["claims"], answers)
        answered_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        week = args.week or f"{datetime.now().isocalendar().year}-W{datetime.now().isocalendar().week:02d}"
        rows = [
            {
                "answered_at": answered_at, "card_id": card_id, "choice": choice,
                "plan_id": plan["plan_id"], "week": week,
            }
            for card_id, choice in sorted(answers.items())
        ]
        answers_out = args.answers_out or (state_dir() / "claims" / f"answers-{plan['plan_id']}.jsonl")
        answers_out.parent.mkdir(parents=True, exist_ok=True)
        with answers_out.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        proposals_out = args.proposals_out or (state_dir() / "claims" / f"proposals-{plan['plan_id']}.json")
        proposals_out.parent.mkdir(parents=True, exist_ok=True)
        proposals_out.write_text(json.dumps(proposals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        plan["answers"] = {"count": len(answers), "ledger": str(answers_out), "proposals": len(proposals)}
        print(f"answers {len(answers)} 条 → 边提案 {len(proposals)} 条（仍需写者落盘）", file=sys.stderr)
        print(f"answers: {answers_out}\nproposals: {proposals_out}", file=sys.stderr)

    if args.claims and args.compact:
        print(render_compact(plan["claims"]))

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
    parser.add_argument("--cards", type=Path, help="write the human card list here (markdown)")
    parser.add_argument("--compact", action="store_true", help="print one line per card (chat/terminal review)")
    parser.add_argument("--week", default=None, help="ISO week for the answer ledger (default: current)")
    parser.add_argument("--html", type=Path, help="write the interactive questionnaire (single file)")
    parser.add_argument("--answers", type=Path, help="read c1=1 answers from this file")
    parser.add_argument("--answers-out", type=Path, help="append accepted answers here")
    parser.add_argument("--proposals-out", type=Path, help="write edge proposals here")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "self-test":
        return self_test()
    if args.command == "status":
        return cmd_status(args)
    return cmd_plan(args)


if __name__ == "__main__":
    raise SystemExit(main())
