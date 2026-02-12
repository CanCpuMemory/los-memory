"""Pytest configuration and fixtures for BDD tests."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_tool.database import connect_db, ensure_schema, ensure_fts
from memory_tool.utils import tags_to_json, tags_to_text, utc_now


class BDDTestContext:
    """Holds test state across steps."""

    def __init__(self):
        self.db_path: Path | None = None
        self.conn = None
        self.profile: str = "codex"
        self.last_observation_id: int | None = None
        self.last_session_id: int | None = None
        self.last_checkpoint_id: int | None = None
        self.last_export_path: str | None = None
        self.search_results: list = []
        self.feedback_history: list = []
        self.last_feedback_result: dict | None = None
        self.last_tool_stats: dict | None = None
        self.last_tool_suggestions: dict | None = None
        self.observations_by_title: dict[str, int] = {}
        self.last_link_id: int | None = None
        self.last_related: list = []
        self.last_similar: list = []
        self.last_unlink_result: bool = False


@pytest.fixture
def test_context():
    """Create a fresh test context with temporary database."""
    ctx = BDDTestContext()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        ctx.db_path = Path(f.name)
    ctx.conn = connect_db(str(ctx.db_path))
    ensure_schema(ctx.conn)
    ensure_fts(ctx.conn)

    yield ctx

    # Cleanup
    if ctx.conn:
        ctx.conn.close()
    if ctx.db_path and ctx.db_path.exists():
        ctx.db_path.unlink()


def parse_datatable(datatable: list[list[str]]) -> dict[str, str]:
    """Convert pytest-bdd 8.x datatable (list of lists) to dict.

    Assumes first row is header with 'field' and 'value' columns,
    or header with column names that become keys.
    """
    if not datatable:
        return {}
    headers = datatable[0]
    if len(datatable) == 1:
        return {}
    # If table has 'field' and 'value' columns, return dict from those
    if "field" in headers and "value" in headers:
        field_idx = headers.index("field")
        value_idx = headers.index("value")
        return {row[field_idx]: row[value_idx] for row in datatable[1:]}
    # Otherwise, return list of dicts for each row
    return [dict(zip(headers, row)) for row in datatable[1:]]


def parse_datatable_rows(datatable: list[list[str]]) -> list[dict[str, str]]:
    """Convert pytest-bdd 8.x datatable to list of dicts."""
    if not datatable:
        return []
    headers = datatable[0]
    return [dict(zip(headers, row)) for row in datatable[1:]]


# Re-export common step functions for pytest-bdd discovery
# These are defined in steps/common_steps.py but need to be discoverable


@given("a new memory database")
def given_new_memory_database(test_context: BDDTestContext):
    """Database is already created by fixture."""
    pass


@given(parsers.parse('I am using the "{profile}" profile'))
def given_set_profile(test_context: BDDTestContext, profile: str):
    """Set the active profile."""
    test_context.profile = profile


@given(parsers.parse('the active project is "{project}"'))
def given_set_active_project(test_context: BDDTestContext, project: str):
    """Set the active project in context."""
    from memory_tool.projects import set_active_project

    set_active_project(test_context.profile, project)
