"""Retrieval-quality gate for the core ledger.

The rest of the suite asserts *behaviour* ("this call returns these rows"); none
of it can tell a ranking change from a regression. `scripts/measure_core_search.py`
measures quality on the real (private) ledger; this file is the CI-able version
that needs no private corpus.

What it locks in: a query that is a literal substring of exactly one record's own
summary must come back at rank 1 on the default path. That is the property the
LIKE-wildcard bug violated (`los_memory` matched 895 records that do not contain
it) and the property `--semantic` violates today.
"""
import sqlite3

import pytest

from memory_tool.database import ensure_schema
from memory_tool.operations import run_search, run_semantic_search

# Distinctive windows, one per record, so the answer to each query is unambiguous.
FIXTURE = [
    ("记录甲", "甲组件的冻结副本迁移流程与校验口径说明。"),
    ("记录乙", "乙服务的热数据回取分卷校验与摘要比对流程。"),
    ("记录丙", "丙项目的检索索引投影重建卡死与释放条件记录。"),
    ("记录丁", "丁脚本的加密备份恢复演练计时与身份摘要一致性。"),
    ("记录戊", "戊目录的日志轮转上限与告警阈值逐条触发验证。"),
    ("记录己", "己节点的会话索引覆盖率探测与映射可行性结论。"),
]


@pytest.fixture
def quality_conn(tmp_path):
    conn = sqlite3.connect(tmp_path / "quality.db")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    for title, summary in FIXTURE:
        conn.execute(
            "INSERT INTO observations (timestamp, project, kind, title, summary,"
            " tags, tags_text, raw) VALUES ('2026-01-01T00:00:00Z','p','note',?,?,'[]','','')",
            (title, summary))
    conn.commit()
    yield conn
    conn.close()


def windows():
    """The first 4 CJK characters of each fixture summary."""
    import re
    cjk = re.compile(r"[\u4e00-\u9fa5]")
    for title, summary in FIXTURE:
        index = next(i for i in range(len(summary) - 4)
                     if all(cjk.match(ch) for ch in summary[i:i + 4]))
        yield title, summary[index:index + 4]


def test_default_path_finds_a_unique_literal_substring_at_rank_one(quality_conn):
    for title, query in windows():
        results = run_search(quality_conn, query, limit=10)
        assert results, f"the default path found nothing for {query!r} (from {title})"
        assert results[0]["title"] == title, (
            f"{query!r} should rank {title} first, got {results[0]['title']}")


def test_default_path_hit_at_1_is_complete_on_the_fixture(quality_conn):
    """The measured property `scripts/measure_core_search.py` reports as Hit@1."""
    hits = 0
    cases = list(windows())
    for title, query in cases:
        results = run_search(quality_conn, query, limit=10)
        hits += bool(results and results[0]["title"] == title)
    assert hits == len(cases), f"Hit@1 = {hits}/{len(cases)} on unambiguous answers"


def test_seeded_wildcards_cannot_inflate_the_default_path(quality_conn):
    """A query with no literal match must return nothing, not everything."""
    for query in ("%", "_", "不存在片段", "记录%甲"):
        assert run_search(quality_conn, query, limit=50, mode="like") == [], query


# --- what `--semantic` may and may not be ----------------------------------
#
# A CJK bigram fix (see memory_tool/embedding.py) lifted `--semantic` from
# Hit@1 0.033 / Hit@5 0.100 to roughly parity with the default path on rank 1.
# Measured across three seeds on the real 5,355-record ledger (40 cases each),
# it still trails the default path on Hit@5 (0.908 vs 0.992) at ~70x the latency
# (≈925 ms vs ≈13 ms). It is a lexical-overlap re-ranker, not a semantic
# retriever, and it must not become the default. These tests pin both halves:
# it works on unambiguous fixture answers, and it stays opt-in.

def test_semantic_finds_unique_literal_windows_on_the_fixture(quality_conn):
    cases = list(windows())
    hits = 0
    for title, query in cases:
        try:
            results = run_semantic_search(quality_conn, query, limit=10)
        except Exception as error:  # noqa: BLE001 - a crash is also a finding
            pytest.fail(f"--semantic raised on {query!r}: {type(error).__name__}: {error}")
        hits += bool(results and results[0]["title"] == title)
    assert hits == len(cases), (
        f"--semantic Hit@1 = {hits}/{len(cases)} on unambiguous literal answers; "
        "it regressed from the measured post-bigram behaviour")


def test_semantic_is_not_the_default_mode():
    """The slow path stays opt-in: `--mode` must default to `auto`."""
    import argparse

    from memory_tool.cli import _build_parser
    parser = _build_parser()
    search = None
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        memory = action.choices.get("memory")
        if memory is None:
            continue
        for sub in memory._actions:
            if isinstance(sub, argparse._SubParsersAction) and "search" in sub.choices:
                search = sub.choices["search"]
    assert search is not None, "the `memory search` subcommand was not found"
    mode = next(a for a in search._actions if a.dest == "mode")
    semantic = next(a for a in search._actions if a.dest == "semantic")
    assert mode.default == "auto", "the default search mode changed away from `auto`"
    assert semantic.default is False, "`--semantic` became the default"



# --- the re-ranker must be bounded -------------------------------------------
#
# `--semantic` used to rank the whole table: O(N) per query, measured p50 994 ms
# at 5,919 observations (~68x the literal path) and Hit@1 0.267 vs 0.867 on
# identifiers. It is now a re-ranker over the literal candidate set. These tests
# pin the two properties that make it bounded, plus the honest consequence.

def test_semantic_scores_only_the_candidate_pool(quality_conn):
    """A bounded pool must actually bound what gets scored."""
    from memory_tool.operations import run_semantic_search

    total = quality_conn.execute("SELECT count(*) FROM observations").fetchone()[0]
    assert total > 3, "fixture too small to show a bound"

    unbounded = run_semantic_search(quality_conn, "retry", limit=total)
    bounded = run_semantic_search(quality_conn, "retry", limit=total, candidate_limit=2)

    assert len(bounded) <= 2
    assert len(bounded) < len(unbounded) or len(unbounded) <= 2


def test_semantic_returns_nothing_when_no_literal_candidate_exists(quality_conn):
    """The honest consequence of being a *re-ranker* rather than a retriever.

    A token-hash embedding gives a record that shares no tokens with the query a
    score of ~0, so the old full scan ranked 5,919 zeros and returned noise. An
    empty candidate set returning empty is the truthful answer, and it is why
    `auto` stays the default.
    """
    from memory_tool.operations import run_semantic_search

    assert run_semantic_search(quality_conn, "zzzznonexistenttokenzzzz") == []


def test_semantic_pool_always_covers_the_requested_window(quality_conn):
    """`offset + limit` must fit inside the pool, or paging would silently drop rows."""
    from memory_tool.operations import run_semantic_search

    page1 = run_semantic_search(quality_conn, "retry", limit=2, offset=0)
    page2 = run_semantic_search(quality_conn, "retry", limit=2, offset=2,
                                candidate_limit=4)

    assert page2 == run_semantic_search(quality_conn, "retry", limit=2, offset=2), (
        "a pool smaller than the window would change what page 2 returns")
    assert page1 != page2 or len(page1) < 2


def test_semantic_payload_shape_is_unchanged(quality_conn):
    """Only *which* rows are scored changed, not what a caller receives."""
    from memory_tool.operations import run_semantic_search

    results = run_semantic_search(quality_conn, "retry", limit=2)

    for item in results:
        assert set(item) >= {"id", "title", "summary", "tags",
                             "vectorScore", "keywordScore", "combinedScore"}
    scores = [item["combinedScore"] for item in results]
    assert scores == sorted(scores, reverse=True)
