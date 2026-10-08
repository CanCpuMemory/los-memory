"""Tests for FTS query-shape handling in `memory search`.

FTS5's query syntax gives `-`, `/`, `.` and `:` operator meaning, so a
path/filename/identifier query used to raise `sqlite3.OperationalError`. In
`mode="auto"` that was swallowed and the query fell through to a full-table LIKE
scan; under an explicit `--mode fts` it surfaced a raw SQLite error ("no such column:
memory") plus a useless "run doctor" suggestion.

Measured on the shared ledger before the fix: 100% of identifier queries carrying one
of those characters raised, and answering them by scan cost ~35x the indexed path
(7.0 ms vs 0.2 ms).
"""
import sqlite3

import pytest

from memory_tool.database import ensure_schema
from memory_tool.operations import _fts_query_candidates, run_search


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "memory.db")
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    yield connection
    connection.close()


def add(conn, title, summary):
    conn.execute(
        "INSERT INTO observations (timestamp, project, kind, title, summary, tags,"
        " tags_text, raw) VALUES ('2026-01-01T00:00:00Z','p','note',?,?,'[]','','')",
        (title, summary))
    conn.commit()


# --- candidate shapes -------------------------------------------------------

def test_single_term_gets_a_quoted_fallback():
    assert _fts_query_candidates("los-memory") == ["los-memory", '"los-memory"']
    assert _fts_query_candidates("route/mapping") == ["route/mapping", '"route/mapping"']


def test_multi_word_queries_keep_their_and_semantics():
    """Quoting a multi-word query would turn FTS5's implicit AND into an adjacency
    requirement — a different decision, and not one to make implicitly."""
    assert _fts_query_candidates("los memory") == ["los memory"]
    assert _fts_query_candidates("a -b") == ["a -b"]


def test_already_quoted_input_is_not_quoted_again():
    assert _fts_query_candidates('"already"') == ['"already"']
    assert _fts_query_candidates('"a b"') == ['"a b"']


# --- behaviour --------------------------------------------------------------

def test_fts_mode_no_longer_raises_on_a_hyphenated_identifier(conn):
    add(conn, "los-memory ledger", "the local ledger")

    results = run_search(conn, "los-memory", limit=10, mode="fts")

    assert [row["title"] for row in results] == ["los-memory ledger"]


def test_auto_mode_still_finds_a_hyphenated_identifier(conn):
    add(conn, "los-memory ledger", "the local ledger")

    assert [row["title"] for row in run_search(conn, "los-memory", limit=10)] == \
        ["los-memory ledger"]


def test_a_quoting_fallback_is_separator_agnostic(conn):
    """A quoted phrase matches adjacent tokens, so `foo/bar` also finds `foo-bar`."""
    add(conn, "foo-bar baz", "content")
    add(conn, "unrelated", "nothing here")

    for query in ("foo/bar", "foo-bar", "foo.bar"):
        titles = [row["title"] for row in run_search(conn, query, limit=10, mode="fts")]
        assert "foo-bar baz" in titles, query


@pytest.mark.parametrize("query", ["(", ")", "*", "NEAR("])
def test_bare_operator_characters_are_recovered(conn, query):
    add(conn, "some record", "content")

    # Must not raise: the quoted form is valid even though the raw form is not.
    assert isinstance(run_search(conn, query, limit=10, mode="fts"), list)


def test_a_query_that_fails_every_form_still_raises_in_fts_mode(conn):
    """The old contract for a genuinely malformed FTS query is preserved."""
    add(conn, "some record", "content")

    with pytest.raises(sqlite3.OperationalError):
        run_search(conn, "a AND", limit=10, mode="fts")


def test_auto_mode_still_falls_back_to_like_when_fts_matches_nothing(conn):
    """Chinese substring queries have no FTS match, so the LIKE path must remain."""
    add(conn, "记忆双轨格局确立", "memory evolve 项目工作记忆")

    assert [row["title"] for row in run_search(conn, "记忆双轨", limit=10)] == \
        ["记忆双轨格局确立"]
