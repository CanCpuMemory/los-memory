"""The plan must be decision-ready, content-addressed, and still read-only.

Why these assertions (P1 of ``docs/design/memory-hygiene-and-consolidation.md``):

* ``actions`` is the only part a writer may execute without a human, so it must
  contain **only** mechanically decidable codes — a new detector must not leak
  into it silently;
* ``daily/`` is an evidence surface (a day's log); a duplicate line there is
  reported, never cleaned;
* ``plan_id`` is content-addressed: the same corpus and the same actions yield
  the same id, so a replayed plan is idempotent, while any corpus edit yields a
  different id (that is the drift check the writer relies on);
* a **review card is one clause on one subject**, not a whole entry: entries here
  bundle many statements (process logs, multi-item deliveries), so an entry-level
  "supersede?" question is unanswerable — the 2026-11-04 redesign;
* the tool must not touch the corpus it reads.

Fixtures live under ``tmp_path``; the plan state directory is redirected through
``LOS_MEMORY_HYGIENE_DIR`` so nothing here addresses the operator's store.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str):
    """Load a ``scripts/*.py`` file as a module, registered for dataclass lookups."""
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cleanup():
    return load_module("hygiene_cleanup")


def write(path: Path, entries: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n§\n".join(entries) + "\n", encoding="utf-8")


def pair_corpus(root: Path) -> Path:
    """A claim and its target that share one structural subject (`/opt/gw/conf.yml`)."""
    write(
        root / "MEMORY.md",
        [
            "[2026-01-01] `/opt/gw/conf.yml` 里的 listen 端口是 8080，这条正文足够长",
            "[2026-01-02] 修正上一条：`/opt/gw/conf.yml` 的 listen 端口改为 9090，正文足够长",
        ],
    )
    return root


@pytest.fixture()
def corpus(tmp_path):
    """Two identical project tracks plus a daily log holding one duplicate pair."""
    root = tmp_path / "corpus"
    shared = [
        "[2026-01-01] 独立的条目：端口 8080 由 gateway 监听",
        "[2026-01-02] 重复的条目：本机 jj 在 /opt/homebrew/bin/jj，且不在默认 PATH",
        "[2026-01-03] 重复的条目：本机 jj 在 /opt/homebrew/bin/jj，且不在默认 PATH",
        "[2026-01-04] 修正上一条「独立的条目」的说法，实际由 launchd 托管",
    ]
    write(root / "projects" / "aaaa" / "MEMORY.md", shared)
    write(root / "projects" / "bbbb" / "MEMORY.md", shared)
    write(
        root / "daily" / "2026-01-05.md",
        ["[2026-01-05] 日刊里的一次巡检结论，足够长的一条正文用于重复检测",
         "[2026-01-05] 日刊里的一次巡检结论，足够长的一条正文用于重复检测"],
    )
    return root


# --------------------------------------------------------------------------- plan

def test_actions_hold_only_mechanically_decidable_codes(cleanup, corpus):
    plan = cleanup.build_plan(corpus)
    assert all(action["code"] in cleanup.AUTO_CODES for action in plan["actions"])
    assert all(action["op"] == "deprecate" for action in plan["actions"])


def test_daily_duplicates_are_review_items_not_actions(cleanup, corpus):
    plan = cleanup.build_plan(corpus)
    assert {action["target"]["track"] for action in plan["actions"]} == {
        "projects/aaaa/MEMORY.md",
        "projects/bbbb/MEMORY.md",
    }
    review = plan["proposals"]["review"]
    assert review["exact_duplicate_evidence_track"]["count"] == 1
    assert "daily/" in review["exact_duplicate_evidence_track"]["samples"][0]["track"]


def test_track_duplicate_becomes_a_decision_not_an_action(cleanup, corpus):
    plan = cleanup.build_plan(corpus)
    dedupe = plan["proposals"]["dedupe_track"]
    assert dedupe["count"] == 1
    group = dedupe["groups"][0]
    assert group["requires_decision"] is True
    assert group["proposed_survivor"] == "projects/aaaa/MEMORY.md"
    assert group["proposed_removal"] == ["projects/bbbb/MEMORY.md"]
    assert dedupe["extra_entries"] == group["entries"] * (len(group["tracks"]) - 1)


def test_plan_id_is_content_addressed(cleanup, corpus):
    first = cleanup.build_plan(corpus)
    second = cleanup.build_plan(corpus)
    assert first["plan_id"] == second["plan_id"]
    assert first["corpus"]["snapshot_hash"] == second["corpus"]["snapshot_hash"]

    write(corpus / "projects" / "bbbb" / "MEMORY.md", ["[2026-02-01] 完全不同的内容"])
    third = cleanup.build_plan(corpus)
    assert third["corpus"]["snapshot_hash"] != first["corpus"]["snapshot_hash"]


def test_plan_expires_within_the_declared_ttl(cleanup, corpus):
    plan = cleanup.build_plan(corpus)
    created = cleanup.datetime.fromisoformat(plan["created_at"])
    expires = cleanup.datetime.fromisoformat(plan["expires_at"])
    assert (expires - created).days == cleanup.PLAN_TTL_DAYS


def test_plan_never_writes_the_corpus(cleanup, corpus):
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in corpus.rglob("*.md")}
    cleanup.build_plan(corpus)
    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in corpus.rglob("*.md")}
    assert before == after


def test_writer_is_named_so_the_handoff_is_not_implicit(cleanup, corpus):
    plan = cleanup.build_plan(corpus)
    assert plan["writer"]["owner"] == "dsh-memory-evolve"
    assert plan["writer"]["state"] != "connected"


def test_cli_writes_the_plan_and_reports_cannot_judge(cleanup, corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("LOS_MEMORY_HYGIENE_DIR", str(tmp_path / "state"))
    out = tmp_path / "plan.json"
    assert cleanup.main(["plan", "--corpus", f"dsh-md:{corpus}", "--out", str(out)]) == cleanup.EXIT_OK
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == cleanup.SCHEMA_VERSION

    assert cleanup.main(["plan", "--corpus", "unknown:/tmp"]) == cleanup.EXIT_UNJUDGEABLE
    assert cleanup.main(["plan", "--corpus", f"dsh-md:{tmp_path / 'missing'}"]) == cleanup.EXIT_UNJUDGEABLE


def test_status_lists_plans_from_the_state_dir(cleanup, corpus, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOS_MEMORY_HYGIENE_DIR", str(tmp_path / "state"))
    assert cleanup.main(["plan", "--corpus", f"dsh-md:{corpus}"]) == cleanup.EXIT_OK
    capsys.readouterr()
    assert cleanup.main(["status", "--json"]) == cleanup.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["plans"]) == 1
    assert payload["plans"][0]["actions"] == 2


# ---------------------------------------------------------------------- clauses

def test_split_clauses_breaks_on_markers_and_semicolons(cleanup):
    clauses = cleanup.split_clauses(
        "第一件事的正文长度必须够长；第二件事的正文长度也必须够长 ①第三件事的正文长度同样够长"
    )
    assert len(clauses) == 3
    assert clauses[0].startswith("第一件事")


def test_subject_keys_prefer_structure_over_phrases(cleanup):
    keys = cleanup.subject_keys("把 `/opt/gw/conf.yml` 的 listen 改成 9090，见 100.68.106.96:3022")
    kinds = [kind for kind, _ in keys]
    assert "path" in kinds and "token" in kinds and "endpoint" in kinds


def test_card_is_one_clause_on_one_subject(cleanup, tmp_path):
    claims = cleanup.resolve_claims(pair_corpus(tmp_path / "corpus"))
    card = claims["cards"][0]
    assert card["subject"] == "/opt/gw/conf.yml"
    assert "8080" in card["old_clause"] and "9090" in card["new_clause"]
    assert card["question"].startswith("「/opt/gw/conf.yml」")
    assert card["choices"] == list(cleanup.CARD_CHOICES)


def test_positional_claim_resolves_to_previous_entry(cleanup, tmp_path):
    claims = cleanup.resolve_claims(pair_corpus(tmp_path / "corpus"))
    assert claims["cards"][0]["signal"] == "positional"


def test_quoted_claim_resolves_to_a_unique_entry(cleanup, tmp_path):
    write(
        tmp_path / "corpus" / "MEMORY.md",
        [
            "[2026-01-01] 关于「网关超时」的旧结论：/etc/hosts 超时是 8 秒，正文足够长",
            "[2026-01-02] 另一条无关但足够长的正文，用来占位",
            "[2026-01-03] 更正「网关超时」：/etc/hosts 超时实际 30 秒，正文足够长",
        ],
    )
    claims = cleanup.resolve_claims(tmp_path / "corpus")
    card = claims["cards"][0]
    assert card["signal"] == "quoted"
    assert card["evidence"]["quoted"] == "网关超时"
    assert "8 秒" in card["old_clause"]


def test_no_shared_subject_is_counted_not_carded(cleanup, tmp_path):
    """Two entries about different things must not become a whole-entry question."""
    write(
        tmp_path / "corpus" / "MEMORY.md",
        [
            "[2026-01-01] 一条关于磁盘配额的旧结论，正文足够长但没有结构化主题",
            "[2026-01-02] 修正上一条：完全另一个主题的说明，正文足够长也没有主题",
        ],
    )
    claims = cleanup.resolve_claims(tmp_path / "corpus")
    assert claims["cards"] == []
    assert claims["counts"]["unresolved_no_shared_subject"] == 1


def test_claim_without_referent_is_counted_not_carded(cleanup, tmp_path):
    write(
        tmp_path / "corpus" / "MEMORY.md",
        ["[2026-01-01] 修正了「一个根本不在库里的说法」，正文足够长的一条"],
    )
    claims = cleanup.resolve_claims(tmp_path / "corpus")
    assert claims["cards"] == []
    assert claims["counts"]["unresolved_no_referent"] == 1


def test_daily_claims_and_edged_claims_are_excluded(cleanup, tmp_path):
    root = tmp_path / "corpus"
    write(root / "daily" / "2026-01-01.md", ["[2026-01-01] 日刊里的修正上一条，正文足够长"])
    write(
        root / "MEMORY.md",
        [
            "[id:11111111] [2026-01-01] `/etc/hosts` 旧结论，正文足够长",
            "[id:22222222] [supersedes:11111111] [2026-01-02] 已经带边的修正上一条 `/etc/hosts`，正文足够长",
        ],
    )
    claims = cleanup.resolve_claims(root)
    assert claims["counts"]["excluded_evidence_track"] == 1
    assert claims["counts"]["excluded_has_edge"] == 1
    assert claims["cards"] == []


def test_cards_are_ordered_positional_first(cleanup, tmp_path):
    write(
        tmp_path / "corpus" / "MEMORY.md",
        [
            "[2026-01-01] 「甲说法有误」的旧结论：`/etc/a.conf` 是旧的，正文足够长",
            "[2026-01-02] 更正「甲说法有误」：`/etc/a.conf` 变了，正文足够长",
            "[2026-01-03] `/etc/b.conf` 旧结论，正文足够长",
            "[2026-01-04] 修正上一条：`/etc/b.conf` 也变了，正文足够长",
        ],
    )
    claims = cleanup.resolve_claims(tmp_path / "corpus")
    assert [card["signal"] for card in claims["cards"]] == ["positional", "quoted"]


def test_cards_render_carries_the_choices(cleanup, tmp_path):
    text = cleanup.render_cards(cleanup.resolve_claims(pair_corpus(tmp_path / "corpus")))
    assert "1=新的取代旧的" in text
    assert "待判卡片 1 张" in text


def test_compact_prints_one_block_per_card(cleanup, tmp_path):
    claims = cleanup.resolve_claims(pair_corpus(tmp_path / "corpus"))
    text = cleanup.render_compact(claims)
    assert "答案格式 c1=1" in text
    assert claims["cards"][0]["card_id"] in text
    assert claims["cards"][0]["subject"][:10] in text


def test_claims_never_write_the_corpus(cleanup, tmp_path):
    corpus = pair_corpus(tmp_path / "corpus")
    before = {path: path.read_bytes() for path in corpus.rglob("*.md")}
    cleanup.resolve_claims(corpus)
    after = {path: path.read_bytes() for path in corpus.rglob("*.md")}
    assert before == after


# ---------------------------------------------------------------------- answers

def test_answers_accept_digits_and_names(cleanup, tmp_path):
    claims = cleanup.resolve_claims(pair_corpus(tmp_path / "corpus"))
    answers_path = tmp_path / "answers.txt"
    answers_path.write_text("# 注释\nc1=1\n", encoding="utf-8")
    assert cleanup.parse_answers(answers_path, claims) == {"c1": "supersede_clause"}

    answers_path.write_text("c1=old_still_valid\n", encoding="utf-8")
    assert cleanup.parse_answers(answers_path, claims) == {"c1": "old_still_valid"}


def test_answers_reject_unknown_card_and_choice(cleanup, tmp_path):
    claims = cleanup.resolve_claims(pair_corpus(tmp_path / "corpus"))
    bad_card = tmp_path / "bad1.txt"
    bad_card.write_text("c999=1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown card"):
        cleanup.parse_answers(bad_card, claims)

    bad_choice = tmp_path / "bad2.txt"
    bad_choice.write_text("c1=yes\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown choice"):
        cleanup.parse_answers(bad_choice, claims)


def test_supersede_makes_an_edge_and_old_valid_flags_a_contradiction(cleanup, tmp_path):
    claims = cleanup.resolve_claims(pair_corpus(tmp_path / "corpus"))
    edge = cleanup.build_edge_proposals(claims, {"c1": "supersede_clause"})
    assert [item["op"] for item in edge] == ["add_supersedes_edge"]
    assert edge[0]["granularity"] == "clause"
    assert edge[0]["subject"] == "/opt/gw/conf.yml"
    assert edge[0]["requires_human"] is True
    assert edge[0]["id_backfill_required"] is True

    contradiction = cleanup.build_edge_proposals(claims, {"c1": "old_still_valid"})
    assert [item["op"] for item in contradiction] == ["flag_contradiction"]

    for neutral in ("different_things", "unclear"):
        assert cleanup.build_edge_proposals(claims, {"c1": neutral}) == []


def test_cli_answers_write_ledger_and_proposals(cleanup, tmp_path, monkeypatch, capsys):
    corpus = pair_corpus(tmp_path / "corpus")
    monkeypatch.setenv("LOS_MEMORY_HYGIENE_DIR", str(tmp_path / "state"))
    answers = tmp_path / "answers.txt"
    answers.write_text("c1=1\n", encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    proposals = tmp_path / "prop.json"
    code = cleanup.main(
        [
            "plan", "--corpus", f"dsh-md:{corpus}", "--claims",
            "--answers", str(answers), "--answers-out", str(ledger),
            "--proposals-out", str(proposals), "--out", str(tmp_path / "plan.json"),
        ]
    )
    assert code == cleanup.EXIT_OK
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["card_id"] == "c1" and rows[0]["choice"] == "supersede_clause"
    assert len(json.loads(proposals.read_text(encoding="utf-8"))) == 1
    capsys.readouterr()


def test_answers_never_touch_the_corpus(cleanup, tmp_path):
    corpus = pair_corpus(tmp_path / "corpus")
    before = {path: path.read_bytes() for path in corpus.rglob("*.md")}
    claims = cleanup.resolve_claims(corpus)
    cleanup.build_edge_proposals(claims, {card["card_id"]: "supersede_clause" for card in claims["cards"]})
    after = {path: path.read_bytes() for path in corpus.rglob("*.md")}
    assert before == after


# ------------------------------------------------------------------------- html

def test_html_questionnaire_embeds_every_card(cleanup, tmp_path):
    claims = cleanup.resolve_claims(pair_corpus(tmp_path / "corpus"))
    html = cleanup.render_html(claims, "plan123", "2026-W45")
    assert "__CARDS_JSON__" not in html and "__PLAN_ID__" not in html
    start = html.index("const CARDS = ") + len("const CARDS = ")
    end = html.index(";\nconst KEY =", start)
    embedded = json.loads(html[start:end])
    assert [card["card_id"] for card in embedded] == [card["card_id"] for card in claims["cards"]]
    assert "2026-W45" in html and "plan123" in html


def test_html_questionnaire_is_offline_and_selfcontained(cleanup, tmp_path):
    claims = cleanup.resolve_claims(pair_corpus(tmp_path / "corpus"))
    html = cleanup.render_html(claims, "p", "2026-W45")
    assert "http://" not in html and "https://" not in html
    assert "<script src=" not in html and "<link" not in html
    for label in ("新的是当前", "旧的仍有效", "两件不同的事", "看不出"):
        assert label in html


def test_html_escapes_script_terminators(cleanup, tmp_path):
    """A memory containing ``</script>`` must not break out of the data block."""
    write(
        tmp_path / "corpus" / "MEMORY.md",
        [
            "[2026-01-01] `/etc/a.conf` 旧结论里带了 </script> 这种字符，正文足够长",
            "[2026-01-02] 修正上一条：`/etc/a.conf` 仍然带 </script>，正文足够长",
        ],
    )
    html = cleanup.render_html(cleanup.resolve_claims(tmp_path / "corpus"), "p", "2026-W45")
    assert html.count("</script>") == 1


def test_cli_html_flag_writes_the_file(cleanup, tmp_path, monkeypatch, capsys):
    corpus = pair_corpus(tmp_path / "corpus")
    monkeypatch.setenv("LOS_MEMORY_HYGIENE_DIR", str(tmp_path / "state"))
    target = tmp_path / "q.html"
    code = cleanup.main(
        ["plan", "--corpus", f"dsh-md:{corpus}", "--claims", "--html", str(target),
         "--out", str(tmp_path / "p.json")]
    )
    assert code == cleanup.EXIT_OK
    assert "const CARDS = [" in target.read_text(encoding="utf-8")
    capsys.readouterr()


def test_self_test_passes(cleanup):
    assert cleanup.self_test() == 0
