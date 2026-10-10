"""Regression tests for LIKE wildcard escaping in user-facing search.

SQLite's LIKE treats `%` and `_` as wildcards. Before this was escaped, a search
for the identifier `los_memory` returned 895 of 5,355 real records that do not
contain it, because `_` matched the hyphen in `los-memory`. Searching for a bare
`%` or `_` returned the whole table. These tests pin the literal behaviour so the
over-match cannot come back silently.
"""
import sqlite3

import pytest

from memory_tool.database import ensure_schema
from memory_tool.operations import run_search
from memory_tool.utils import escape_like, like_pattern


def make_conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    """A schema-initialised connection that is closed at teardown.

    This used to be a bare `make_conn(tmp_path)` call at the top of each test,
    so all seven of them leaked a connection and the suite emitted intermittent
    `ResourceWarning: unclosed database` (surfaced as
    `PytestUnraisableExceptionWarning`) whenever GC happened to run. That is why
    the warnings moved around between test files instead of pointing at their
    real owner.
    """
    connection = make_conn(tmp_path)
    yield connection
    connection.close()


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

def test_underscore_is_literal_and_does_not_match_a_hyphen(conn):
    add(conn, "los-memory writeback contract", "hyphenated identifier")
    add(conn, "los memory notes", "space separated")
    add(conn, "los_memory literal", "the real one")

    titles = [row["title"] for row in run_search(conn, "los_memory", limit=50, mode="like")]
    assert titles == ["los_memory literal"]


def test_bare_wildcards_do_not_return_the_whole_table(conn):
    add(conn, "alpha", "first")
    add(conn, "beta", "second")

    assert run_search(conn, "%", limit=50, mode="like") == []
    assert run_search(conn, "_", limit=50, mode="like") == []


def test_percent_matches_only_a_literal_percent(conn):
    add(conn, "coverage 80% reached", "has a percent sign")
    add(conn, "coverage 80 reached", "no percent sign")

    titles = [row["title"] for row in run_search(conn, "80%", limit=50, mode="like")]
    assert titles == ["coverage 80% reached"]


def test_backslash_in_query_is_literal(conn):
    add(conn, "path C:\\tmp\\out", "windows path")
    add(conn, "path C:tmpout", "no separators")

    titles = [row["title"] for row in run_search(conn, "C:\\tmp", limit=50, mode="like")]
    assert titles == ["path C:\\tmp\\out"]


def test_literal_substring_search_still_works(conn):
    """The escaping must not break the ordinary case it was built for."""
    add(conn, "记忆双轨格局确立", "memory evolve 项目工作记忆")

    titles = [row["title"] for row in run_search(conn, "记忆双轨", limit=50, mode="like")]
    assert titles == ["记忆双轨格局确立"]


def test_tag_filter_escapes_wildcards(conn):
    add(conn, "tagged with underscore", "body", tags='["los_memory"]')
    add(conn, "tagged with hyphen", "body", tags='["los-memory"]')

    titles = [row["title"] for row in run_search(conn, "body", limit=50, mode="like",
                                                 required_tags=["los_memory"])]
    assert titles == ["tagged with underscore"]


# --- documented behaviour, measured ----------------------------------------
#
# `mode="auto"` tries FTS first and only falls back to LIKE when FTS returns
# nothing. FTS5's tokenizer splits `los_memory` into `los` + `memory`, so a query
# whose literal appears nowhere can still return token matches. This test records
# that so the behaviour stays visible and any change to it is deliberate; it is NOT
# a claim that the behaviour is desirable, nor that it is a large problem.
#
# Magnitude, from sampling 80 unique-identifier queries per separator group on the
# shared ledger: the ranking difference against the literal path was mixed and
# within noise (Hit@1 0.775 → 0.812 for underscore identifiers, 0.887 → 0.863 for
# other separators). The original `los_memory` example that motivated this was a
# tail case — its tokens are individually very common in this corpus. Deciding that
# literal containment should outrank a separator-variant match needs a larger frozen
# set than that; measure with `scripts/measure_core_search.py --family identifier`.

def test_auto_mode_still_matches_the_tokenized_form_of_an_underscore_identifier(conn):
    add(conn, "los-memory writeback contract", "hyphenated identifier")
    add(conn, "los_memory literal", "the real one")

    auto_titles = [row["title"] for row in run_search(conn, "los_memory", limit=50)]
    assert "los-memory writeback contract" in auto_titles, (
        "auto mode stopped matching the tokenized form; if that is intentional, "
        "update this documented behaviour and the measurement baseline")

    # The LIKE path stays literal, which is the contrast this documents.
    literal = [row["title"] for row in run_search(conn, "los_memory", limit=50, mode="like")]
    assert literal == ["los_memory literal"]

