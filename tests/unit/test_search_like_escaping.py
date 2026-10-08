"""Regression tests for LIKE wildcard escaping in user-facing search.

SQLite's LIKE treats `%` and `_` as wildcards. Before this was escaped, a search
for the identifier `los_memory` returned 895 of 5,355 real records that do not
contain it, because `_` matched the hyphen in `los-memory`. Searching for a bare
`%` or `_` returned the whole table. These tests pin the literal behaviour so the
over-match cannot come back silently.
"""
import sqlite3

from memory_tool.database import ensure_schema
from memory_tool.operations import run_search
from memory_tool.utils import escape_like, like_pattern


def make_conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def add(conn, title, summary, tags="[]"):
    conn.execute(
        "INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw)"
        " VALUES ('2026-01-01T00:00:00Z', 'proj', 'note', ?, ?, ?, '', '')",
        (title, summary, tags),
    )
    conn.commit()


# --- the helper itself -----------------------------------------------------

def test_escape_like_escapes_both_wildcards():
    assert escape_like("los_memory") == "los\\_memory"
    assert escape_like("100%") == "100\\%"


def test_escape_like_escapes_backslash_first():
    # A naive ordering would turn `a\b` into `a\\b` and then escape the escapes.
    assert escape_like("a\\b") == "a\\\\b"
    assert escape_like("a\\_b") == "a\\\\\\_b"


def test_like_pattern_wraps_the_escaped_value():
    assert like_pattern("los_memory") == "%los\\_memory%"


# --- the search path -------------------------------------------------------

def test_underscore_is_literal_and_does_not_match_a_hyphen(tmp_path):
    conn = make_conn(tmp_path)
    add(conn, "los-memory writeback contract", "hyphenated identifier")
    add(conn, "los memory notes", "space separated")
    add(conn, "los_memory literal", "the real one")

    titles = [row["title"] for row in run_search(conn, "los_memory", limit=50, mode="like")]
    assert titles == ["los_memory literal"]


def test_bare_wildcards_do_not_return_the_whole_table(tmp_path):
    conn = make_conn(tmp_path)
    add(conn, "alpha", "first")
    add(conn, "beta", "second")

    assert run_search(conn, "%", limit=50, mode="like") == []
    assert run_search(conn, "_", limit=50, mode="like") == []


def test_percent_matches_only_a_literal_percent(tmp_path):
    conn = make_conn(tmp_path)
    add(conn, "coverage 80% reached", "has a percent sign")
    add(conn, "coverage 80 reached", "no percent sign")

    titles = [row["title"] for row in run_search(conn, "80%", limit=50, mode="like")]
    assert titles == ["coverage 80% reached"]


def test_backslash_in_query_is_literal(tmp_path):
    conn = make_conn(tmp_path)
    add(conn, "path C:\\tmp\\out", "windows path")
    add(conn, "path C:tmpout", "no separators")

    titles = [row["title"] for row in run_search(conn, "C:\\tmp", limit=50, mode="like")]
    assert titles == ["path C:\\tmp\\out"]


def test_literal_substring_search_still_works(tmp_path):
    """The escaping must not break the ordinary case it was built for."""
    conn = make_conn(tmp_path)
    add(conn, "记忆双轨格局确立", "memory evolve 项目工作记忆")

    titles = [row["title"] for row in run_search(conn, "记忆双轨", limit=50, mode="like")]
    assert titles == ["记忆双轨格局确立"]


def test_tag_filter_escapes_wildcards(tmp_path):
    conn = make_conn(tmp_path)
    add(conn, "tagged with underscore", "body", tags='["los_memory"]')
    add(conn, "tagged with hyphen", "body", tags='["los-memory"]')

    titles = [row["title"] for row in run_search(conn, "body", limit=50, mode="like",
                                                 required_tags=["los_memory"])]
    assert titles == ["tagged with underscore"]


# --- documented limitation, not a guarantee --------------------------------
#
# `mode="auto"` tries FTS first and only falls back to LIKE when FTS returns
# nothing. The FTS5 tokenizer splits `los_memory` into `los` + `memory`, so an
# identifier query still over-matches there even though the LIKE path is now
# literal. This test records the current behaviour so the gap stays visible and
# any change to it is deliberate; it is NOT a statement that the behaviour is
# desirable. Fixing it means deciding that a literal-containment match should
# outrank a token match, which is a ranking change and needs the measurement in
# `scripts/measure_core_search.py` before it lands.

def test_known_limitation_auto_mode_still_tokenizes_underscore_identifiers(tmp_path):
    conn = make_conn(tmp_path)
    add(conn, "los-memory writeback contract", "hyphenated identifier")
    add(conn, "los_memory literal", "the real one")

    auto_titles = [row["title"] for row in run_search(conn, "los_memory", limit=50)]
    assert "los-memory writeback contract" in auto_titles, (
        "auto mode stopped matching the tokenized form; if that is intentional, "
        "update this documented limitation and the measurement baseline")

