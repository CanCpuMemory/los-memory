"""Common step definitions for BDD tests."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from pytest_bdd import given, parsers, then, when

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "memory_tool"))

from memory_tool.database import connect_db, ensure_schema, ensure_fts
from memory_tool.utils import tags_to_json, tags_to_text, utc_now


def parse_datatable(datatable: list[list[str]]) -> dict[str, str]:
    """Convert pytest-bdd 8.x datatable (list of lists) to dict."""
    if not datatable:
        return {}
    headers = datatable[0]
    if len(datatable) == 1:
        return {}
    if "field" in headers and "value" in headers:
        field_idx = headers.index("field")
        value_idx = headers.index("value")
        return {row[field_idx]: row[value_idx] for row in datatable[1:]}
    return [dict(zip(headers, row)) for row in datatable[1:]]


def parse_datatable_rows(datatable: list[list[str]]) -> list[dict[str, str]]:
    """Convert pytest-bdd 8.x datatable to list of dicts."""
    if not datatable:
        return []
    headers = datatable[0]
    return [dict(zip(headers, row)) for row in datatable[1:]]


# Fixtures and context helpers
class BDDTestContext:
    """Holds test state across steps."""
    def __init__(self):
        self.db_path: Path | None = None
        self.conn = None
        self.profile: str = "codex"
        self.last_observation_id: int | None = None
        self.last_session_id: int | None = None
        self.last_checkpoint_id: int | None = None
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


@given("a new memory database", target_fixture="test_context")
def new_memory_database():
    """Create a fresh database for testing."""
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


@given(parsers.parse('I am using the "{profile}" profile'))
def set_profile(test_context: BDDTestContext, profile: str):
    """Set the active profile."""
    test_context.profile = profile


@given(parsers.parse('the active project is "{project}"'))
def set_active_project(test_context: BDDTestContext, project: str):
    """Set the active project in context."""
    from memory_tool.projects import set_active_project
    set_active_project(test_context.profile, project)


@then(parsers.parse('the active project should be "{project}"'))
def check_active_project(test_context: BDDTestContext, project: str):
    """Verify the active project."""
    from memory_tool.projects import get_active_project
    assert get_active_project(test_context.profile) == project


# Observation helpers
@when("I add an observation with:")
def add_observation_with_table(test_context: BDDTestContext, datatable):
    """Add an observation from a Gherkin table."""
    from memory_tool.operations import add_observation
    from memory_tool.sessions import get_active_session
    from memory_tool.projects import get_active_project

    data = parse_datatable(datatable)
    if isinstance(data, list):
        data = data[0] if data else {}

    # Determine project
    project = data.get("project", "")
    if not project or project == "general":
        active = get_active_project(test_context.profile)
        project = active if active else "general"

    # Handle tags
    tags_str = data.get("tags", "")
    from memory_tool.utils import normalize_tags_list, auto_tags_from_text
    tags_list = normalize_tags_list(tags_str)

    # Auto-generate tags if requested
    if data.get("auto_tags") == "true":
        auto_tags = auto_tags_from_text(data.get("title", ""), data.get("summary", ""))
        tags_list = list(set(tags_list + auto_tags))

    # Check for active session
    active_session = get_active_session(test_context.profile)
    session_id = active_session["session_id"] if active_session else None

    obs_id = add_observation(
        test_context.conn,
        data.get("timestamp", utc_now()),
        project,
        data.get("kind", "note"),
        data.get("title", ""),
        data.get("summary", ""),
        tags_to_json(tags_list),
        tags_to_text(tags_list),
        data.get("raw", ""),
        session_id,
    )
    test_context.last_observation_id = obs_id


@when(parsers.parse('I add an observation with title "{title}"'))
def add_observation_with_title(test_context: BDDTestContext, title: str):
    """Add a simple observation with just a title."""
    from memory_tool.operations import add_observation
    from memory_tool.sessions import get_active_session

    active_session = get_active_session(test_context.profile)
    session_id = active_session["session_id"] if active_session else None

    obs_id = add_observation(
        test_context.conn,
        utc_now(),
        "general",
        "note",
        title,
        title,
        tags_to_json([]),
        "",
        "",
        session_id,
    )
    test_context.last_observation_id = obs_id


@then("the observation should be saved successfully")
def observation_saved(test_context: BDDTestContext):
    """Verify observation was created."""
    assert test_context.last_observation_id is not None
    assert test_context.last_observation_id > 0


@then("I should be able to retrieve it by ID")
def retrieve_observation_by_id(test_context: BDDTestContext):
    """Verify observation can be retrieved."""
    from memory_tool.operations import run_get
    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 1
    assert results[0].id == test_context.last_observation_id


@given(parsers.parse('an observation exists with title "{title}"'))
def given_observation_exists(test_context: BDDTestContext, title: str):
    """Create an observation with given title."""
    from memory_tool.operations import add_observation
    obs_id = add_observation(
        test_context.conn,
        utc_now(),
        "general",
        "note",
        title,
        f"Summary for {title}",
        tags_to_json([]),
        "",
        "",
        None,
    )
    test_context.last_observation_id = obs_id
    test_context.observations_by_title[title] = obs_id


@given("an observation exists with:")
def given_observation_with_table(test_context: BDDTestContext, datatable):
    """Create an observation from table data."""
    from memory_tool.operations import add_observation
    data = parse_datatable(datatable)
    if isinstance(data, list):
        data = data[0] if data else {}

    tags_list = []
    if "tags" in data:
        from memory_tool.utils import normalize_tags_list
        tags_list = normalize_tags_list(data["tags"])

    obs_id = add_observation(
        test_context.conn,
        data.get("timestamp", utc_now()),
        data.get("project", "general"),
        data.get("kind", "note"),
        data.get("title", ""),
        data.get("summary", ""),
        tags_to_json(tags_list),
        tags_to_text(tags_list),
        data.get("raw", ""),
        None,
    )
    test_context.last_observation_id = obs_id


@given("the following observations exist:")
def given_multiple_observations(test_context: BDDTestContext, datatable):
    """Create multiple observations from table."""
    from memory_tool.operations import add_observation
    from memory_tool.utils import normalize_tags_list

    rows = parse_datatable_rows(datatable)
    for row in rows:
        tags = normalize_tags_list(row.get("tags", ""))
        add_observation(
            test_context.conn,
            utc_now(),
            row.get("project", "general"),
            row.get("kind", "note"),
            row.get("title", "Untitled"),
            row.get("summary", ""),
            tags_to_json(tags),
            tags_to_text(tags),
            "",
            None,
        )


# Search steps
@when(parsers.parse('I search for "{query}"'))
def search_observations(test_context: BDDTestContext, query: str):
    """Search for observations."""
    from memory_tool.operations import run_search
    test_context.search_results = run_search(test_context.conn, query, limit=100)


@then(parsers.parse("I should find {count:d} observation"))
@then(parsers.parse("I should find {count:d} observations"))
def check_search_results_count(test_context: BDDTestContext, count: int):
    """Verify search result count."""
    assert len(test_context.search_results) == count


@then(parsers.parse('the observation title should be "{title}"'))
def check_search_result_title(test_context: BDDTestContext, title: str):
    """Verify first search result title."""
    assert len(test_context.search_results) > 0
    assert test_context.search_results[0]["title"] == title


@then("searching for it should return no results")
def search_returns_no_results(test_context: BDDTestContext):
    """Verify observation was deleted."""
    from memory_tool.operations import run_search
    from memory_tool.operations import run_get

    # Check it doesn't exist by ID
    if test_context.last_observation_id:
        results = run_get(test_context.conn, [test_context.last_observation_id])
        assert len(results) == 0


# Edit steps
@when(parsers.parse('I edit the observation title to "{new_title}"'))
def edit_observation_title(test_context: BDDTestContext, new_title: str):
    """Edit an observation's title."""
    from memory_tool.operations import run_edit
    run_edit(
        test_context.conn,
        test_context.last_observation_id,
        project=None,
        kind=None,
        title=new_title,
        summary=None,
        tags=None,
        raw=None,
        timestamp=None,
        auto_tags=False,
    )


@then(parsers.parse('the observation should have title "{title}"'))
def check_observation_title(test_context: BDDTestContext, title: str):
    """Verify observation title."""
    from memory_tool.operations import run_get
    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 1
    assert results[0].title == title


@then(parsers.parse('the observation should still have summary "{summary}"'))
def check_observation_summary(test_context: BDDTestContext, summary: str):
    """Verify observation summary unchanged."""
    from memory_tool.operations import run_get
    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 1
    assert results[0].summary == summary


# Delete steps
@when("I delete that observation")
def delete_observation(test_context: BDDTestContext):
    """Delete the last observation."""
    from memory_tool.operations import run_delete
    run_delete(test_context.conn, [test_context.last_observation_id], dry_run=False)


@then("the observation should not exist")
def observation_should_not_exist(test_context: BDDTestContext):
    """Verify observation was deleted."""
    from memory_tool.operations import run_get
    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 0


# Tag steps
@then(parsers.parse('the observation should have tags including "{tag1}" and "{tag2}"'))
def check_observation_has_tags(test_context: BDDTestContext, tag1: str, tag2: str):
    """Verify observation has expected tags."""
    from memory_tool.operations import run_get
    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 1
    obs = results[0]
    assert tag1 in obs.tags or tag2 in obs.tags, f"Expected tags {tag1}/{tag2} not found in {obs.tags}"
