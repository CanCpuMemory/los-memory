"""Step definitions for observation management."""
from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from conftest import BDDTestContext, parse_datatable, parse_datatable_rows


@when("I add an observation with:")
def add_observation_with_table(test_context: BDDTestContext, datatable):
    """Add an observation from a Gherkin table."""
    from memory_tool.operations import add_observation
    from memory_tool.sessions import get_active_session
    from memory_tool.projects import get_active_project
    from memory_tool.utils import normalize_tags_list, auto_tags_from_text

    data = parse_datatable(datatable)
    if isinstance(data, list):
        data = data[0] if data else {}

    project = data.get("project", "")
    if not project or project == "general":
        active = get_active_project(test_context.profile)
        project = active if active else "general"

    tags_str = data.get("tags", "")
    tags_list = normalize_tags_list(tags_str)

    if data.get("auto_tags") == "true":
        auto_tags = auto_tags_from_text(data.get("title", ""), data.get("summary", ""))
        tags_list = list(set(tags_list + auto_tags))

    active_session = get_active_session(test_context.profile)
    session_id = active_session["session_id"] if active_session else None

    from memory_tool.utils import tags_to_json, tags_to_text, utc_now
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
    from memory_tool.utils import tags_to_json, utc_now

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
    from memory_tool.utils import tags_to_json, utc_now

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


@given("an observation exists with:")
def given_observation_with_table(test_context: BDDTestContext, datatable):
    """Create an observation from table data."""
    from memory_tool.operations import add_observation
    from memory_tool.utils import normalize_tags_list, tags_to_json, tags_to_text, utc_now

    data = parse_datatable(datatable)
    if isinstance(data, list):
        data = data[0] if data else {}

    tags_list = []
    if "tags" in data:
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
    from memory_tool.utils import normalize_tags_list, tags_to_json, tags_to_text, utc_now

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
    from memory_tool.operations import run_get

    if test_context.last_observation_id:
        results = run_get(test_context.conn, [test_context.last_observation_id])
        assert len(results) == 0


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


@then(parsers.parse('the observation should have tags including "{tag1}" and "{tag2}"'))
def check_observation_has_tags(test_context: BDDTestContext, tag1: str, tag2: str):
    """Verify observation has expected tags."""
    from memory_tool.operations import run_get
    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 1
    obs = results[0]
    assert tag1 in obs.tags or tag2 in obs.tags, f"Expected tags {tag1}/{tag2} not found in {obs.tags}"
