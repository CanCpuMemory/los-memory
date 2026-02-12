"""Step definitions for tool memory tracking."""
from __future__ import annotations

import json

from pytest_bdd import given, parsers, then, when

from conftest import BDDTestContext


@when("I log a tool call with:")
def log_tool_call_with_table(test_context: BDDTestContext, datatable):
    """Log a tool call from table data."""
    from memory_tool.analytics import log_tool_call
    from memory_tool.utils import utc_now

    data = parse_datatable(datatable)
    if isinstance(data, list):
        data = data[0] if data else {}

    tool_input = json.loads(data.get("input", "{}"))
    tool_output = json.loads(data.get("output", "{}"))

    obs_id = log_tool_call(
        test_context.conn,
        tool_name=data.get("tool", "unknown"),
        tool_input=tool_input,
        tool_output=tool_output,
        status=data.get("status", "success"),
        duration_ms=int(data.get("duration", 0)) if data.get("duration") else None,
        project=data.get("project", "general"),
        session_id=None,
    )
    test_context.last_observation_id = obs_id


@given("the following tool calls have been logged:")
def given_tool_calls_logged(test_context: BDDTestContext, datatable):
    """Create multiple tool call observations."""
    from memory_tool.analytics import log_tool_call
    from memory_tool.utils import utc_now

    rows = parse_datatable_rows(datatable)
    for row in rows:
        log_tool_call(
            test_context.conn,
            tool_name=row.get("tool", "unknown"),
            tool_input={"test": True},
            tool_output={"status": row.get("status"), "error": "Error"} if row.get("status") == "error" else {"result": "ok"},
            status=row.get("status", "success"),
            duration_ms=int(row.get("duration", 100)) if row.get("duration") else None,
            project=row.get("project", "general"),
            session_id=None,
        )


@then("the tool call should be recorded successfully")
def tool_call_recorded(test_context: BDDTestContext):
    """Verify tool call was recorded."""
    assert test_context.last_observation_id is not None
    assert test_context.last_observation_id > 0


@then(parsers.parse('the observation kind should be "{kind}"'))
def check_observation_kind(test_context: BDDTestContext, kind: str):
    """Verify observation kind."""
    from memory_tool.operations import run_get

    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 1
    assert results[0].kind == kind


@then(parsers.parse('the observation should have tag "{tag}"'))
def check_observation_has_tag(test_context: BDDTestContext, tag: str):
    """Verify observation has specific tag."""
    from memory_tool.operations import run_get

    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 1
    assert tag in results[0].tags, f"Expected tag '{tag}' not found in {results[0].tags}"


@when("I view tool statistics")
@when(parsers.parse('I view tool statistics for project "{project}"'))
def view_tool_stats(test_context: BDDTestContext, project: str = None):
    """Get tool statistics."""
    from memory_tool.analytics import get_tool_stats

    test_context.last_tool_stats = get_tool_stats(test_context.conn, project)


@then(parsers.parse("I should see {count:d} total calls"))
def check_total_calls(test_context: BDDTestContext, count: int):
    """Verify total call count in stats."""
    assert test_context.last_tool_stats["total_calls"] == count


@then(parsers.parse("I should see {count:d} successful calls"))
def check_success_count(test_context: BDDTestContext, count: int):
    """Verify success count in stats."""
    assert test_context.last_tool_stats["success_count"] == count


@then(parsers.parse("I should see {count:d} error"))
@then(parsers.parse("I should see {count:d} errors"))
def check_error_count(test_context: BDDTestContext, count: int):
    """Verify error count in stats."""
    assert test_context.last_tool_stats["error_count"] == count


@then(parsers.parse('"{tool}" should have {count:d} calls'))
def check_tool_call_count(test_context: BDDTestContext, tool: str, count: int):
    """Verify specific tool call count."""
    tools = test_context.last_tool_stats["tools"]
    found = False
    for t in tools:
        if t["name"] == tool:
            assert t["calls"] == count
            found = True
            break
    assert found, f"Tool '{tool}' not found in stats"


@then(parsers.parse('"{tool}" should be in the stats'))
def check_tool_in_stats(test_context: BDDTestContext, tool: str):
    """Verify tool is in stats."""
    tools = test_context.last_tool_stats["tools"]
    tool_names = [t["name"] for t in tools]
    assert tool in tool_names, f"Tool '{tool}' not in stats. Got: {tool_names}"


@then(parsers.parse('"{tool}" should not be in the stats'))
def check_tool_not_in_stats(test_context: BDDTestContext, tool: str):
    """Verify tool is not in stats."""
    tools = test_context.last_tool_stats["tools"]
    tool_names = [t["name"] for t in tools]
    assert tool not in tool_names, f"Tool '{tool}' should not be in stats"


@when(parsers.parse('I ask for tool suggestions for "{task}"'))
def ask_tool_suggestions(test_context: BDDTestContext, task: str):
    """Get tool suggestions for a task."""
    from memory_tool.analytics import suggest_tools_for_task

    test_context.last_tool_suggestions = suggest_tools_for_task(test_context.conn, task)


@then("I should receive tool suggestions")
def check_suggestions_received(test_context: BDDTestContext):
    """Verify suggestions were received."""
    assert test_context.last_tool_suggestions["ok"] is True
    assert isinstance(test_context.last_tool_suggestions["suggestions"], list)


@then(parsers.parse('"{tool}" should be in the suggestions'))
def check_tool_in_suggestions(test_context: BDDTestContext, tool: str):
    """Verify tool is in suggestions."""
    suggestions = test_context.last_tool_suggestions["suggestions"]
    tool_names = [s["name"] for s in suggestions]
    assert tool in tool_names, f"Tool '{tool}' not in suggestions. Got: {tool_names}"


@then("the tool stats should show 1 error")
def check_stats_show_one_error(test_context: BDDTestContext):
    """Verify stats show one error."""
    from memory_tool.analytics import get_tool_stats

    stats = get_tool_stats(test_context.conn)
    assert stats["error_count"] == 1


def parse_datatable(datatable: list[list[str]]) -> dict[str, str]:
    """Convert pytest-bdd datatable to dict."""
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
    """Convert pytest-bdd datatable to list of dicts."""
    if not datatable:
        return []
    headers = datatable[0]
    return [dict(zip(headers, row)) for row in datatable[1:]]
