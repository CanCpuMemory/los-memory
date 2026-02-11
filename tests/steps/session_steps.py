"""Step definitions for session management."""
from __future__ import annotations

import json
from pathlib import Path

from pytest_bdd import given, parsers, then, when

from conftest import BDDTestContext, parse_datatable, parse_datatable_rows, utc_now
def tags_to_json(tags): from memory_tool.utils import tags_to_json as _ttj; return _ttj(tags)
def tags_to_text(tags): from memory_tool.utils import tags_to_text as _ttt; return _ttt(tags)


@when("I start a session with:")
def start_session_with_table(test_context: BDDTestContext, datatable):
    """Start a new session from table data."""
    from memory_tool.sessions import start_session, set_active_session

    data = parse_datatable(datatable)
    if isinstance(data, list):
        data = data[0] if data else {}

    session_id = start_session(
        test_context.conn,
        project=data.get("project", "general"),
        working_dir=str(Path.cwd()),
        agent_type=data.get("agent_type", test_context.profile),
        summary=data.get("summary", ""),
    )
    test_context.last_session_id = session_id
    set_active_session(test_context.profile, session_id, str(test_context.db_path))


@given(parsers.parse('I have an active session for project "{project}"'))
def given_active_session(test_context: BDDTestContext, project: str):
    """Create an active session."""
    from memory_tool.sessions import start_session, set_active_session

    session_id = start_session(
        test_context.conn,
        project=project,
        working_dir=str(Path.cwd()),
        agent_type=test_context.profile,
        summary="",
    )
    test_context.last_session_id = session_id
    set_active_session(test_context.profile, session_id, str(test_context.db_path))


@given(parsers.parse('I have an active session with {count:d} observations'))
def given_active_session_with_observations(test_context: BDDTestContext, count: int):
    """Create an active session with observations."""
    from memory_tool.sessions import start_session, set_active_session
    from memory_tool.operations import add_observation

    session_id = start_session(
        test_context.conn,
        project="test-project",
        working_dir=str(Path.cwd()),
        agent_type=test_context.profile,
        summary="",
    )
    test_context.last_session_id = session_id
    set_active_session(test_context.profile, session_id, str(test_context.db_path))

    # Add observations to the session
    for i in range(count):
        add_observation(
            test_context.conn,
            utc_now(),
            "test-project",
            "note",
            f"Observation {i + 1}",
            f"Summary {i + 1}",
            tags_to_json([]),
            "",
            "",
            session_id,
        )


@when("I add another observation with title \"{title}\"")
def add_another_observation(test_context: BDDTestContext, title: str):
    """Add another observation to the active session."""
    from memory_tool.operations import add_observation
    from memory_tool.sessions import get_active_session

    active_session = get_active_session(test_context.profile)
    session_id = active_session["session_id"] if active_session else None

    obs_id = add_observation(
        test_context.conn,
        utc_now(),
        "test-project",
        "note",
        title,
        f"Summary for {title}",
        tags_to_json([]),
        "",
        "",
        session_id,
    )
    test_context.last_observation_id = obs_id


@then("a session should be created")
def session_created(test_context: BDDTestContext):
    """Verify session was created."""
    assert test_context.last_session_id is not None
    assert test_context.last_session_id > 0


@then("the session should be active")
def session_is_active(test_context: BDDTestContext):
    """Verify session has active status."""
    from memory_tool.sessions import get_session
    session = get_session(test_context.conn, test_context.last_session_id)
    assert session is not None
    assert session.status == "active"


@then(parsers.parse('the session should have project "{project}"'))
def session_has_project(test_context: BDDTestContext, project: str):
    """Verify session project."""
    from memory_tool.sessions import get_session
    session = get_session(test_context.conn, test_context.last_session_id)
    assert session is not None
    assert session.project == project


@then("the observation should belong to the active session")
def observation_belongs_to_session(test_context: BDDTestContext):
    """Verify observation has session ID."""
    from memory_tool.operations import run_get
    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 1
    assert results[0].session_id == test_context.last_session_id


@then("both observations should be in the same session")
def both_observations_same_session(test_context: BDDTestContext):
    """Verify all observations share session."""
    from memory_tool.sessions import get_session_observations
    observations = get_session_observations(test_context.conn, test_context.last_session_id)
    assert len(observations) >= 2
    for obs in observations:
        assert obs.session_id == test_context.last_session_id


@when("I end the session")
def end_session(test_context: BDDTestContext):
    """End the active session."""
    from memory_tool.sessions import end_session, generate_session_summary, clear_active_session

    summary = generate_session_summary(test_context.conn, test_context.last_session_id)
    end_session(test_context.conn, test_context.last_session_id, summary)
    clear_active_session(test_context.profile)


@then(parsers.parse('the session status should be "{status}"'))
def session_status_is(test_context: BDDTestContext, status: str):
    """Verify session status."""
    from memory_tool.sessions import get_session
    session = get_session(test_context.conn, test_context.last_session_id)
    assert session.status == status


@then("the session should have an end_time")
def session_has_end_time(test_context: BDDTestContext):
    """Verify session has end time."""
    from memory_tool.sessions import get_session
    session = get_session(test_context.conn, test_context.last_session_id)
    assert session.end_time is not None


@then("there should be no active session")
def no_active_session(test_context: BDDTestContext):
    """Verify no active session."""
    from memory_tool.sessions import get_active_session
    assert get_active_session(test_context.profile) is None


@given(parsers.parse('a completed session with ID {session_id:d} exists'))
def given_completed_session(test_context: BDDTestContext, session_id: int):
    """Create a completed session."""
    from memory_tool.sessions import start_session, end_session, set_active_session

    actual_id = start_session(
        test_context.conn,
        project="completed-project",
        working_dir=str(Path.cwd()),
        agent_type=test_context.profile,
        summary="Completed session",
    )
    end_session(test_context.conn, actual_id)
    test_context.last_session_id = actual_id


@when(parsers.parse('I resume session {session_id:d}'))
def resume_session(test_context: BDDTestContext, session_id: int):
    """Resume a session."""
    from memory_tool.sessions import set_active_session
    set_active_session(test_context.profile, test_context.last_session_id, str(test_context.db_path))


@then(parsers.parse('session {session_id:d} should be the active session'))
def session_should_be_active(test_context: BDDTestContext, session_id: int):
    """Verify session is active."""
    from memory_tool.sessions import get_active_session
    active = get_active_session(test_context.profile)
    assert active is not None
    assert active["session_id"] == test_context.last_session_id


@given("the following sessions exist:")
def given_multiple_sessions(test_context: BDDTestContext, datatable):
    """Create multiple sessions."""
    from memory_tool.sessions import start_session, end_session

    rows = parse_datatable_rows(datatable)
    for row in rows:
        session_id = start_session(
            test_context.conn,
            project=row["project"],
            working_dir=str(Path.cwd()),
            agent_type=test_context.profile,
            summary="",
        )
        if row.get("status") == "completed":
            end_session(test_context.conn, session_id)


@when("I list all sessions")
def list_all_sessions(test_context: BDDTestContext):
    """List all sessions."""
    from memory_tool.sessions import list_sessions
    test_context.search_results = list_sessions(test_context.conn, limit=100)


@when("I list only active sessions")
def list_active_sessions(test_context: BDDTestContext):
    """List only active sessions."""
    from memory_tool.sessions import list_sessions
    test_context.search_results = list_sessions(test_context.conn, status="active", limit=100)


@then(parsers.parse("I should see {count:d} session"))
@then(parsers.parse("I should see {count:d} sessions"))
def check_session_count(test_context: BDDTestContext, count: int):
    """Verify session count."""
    assert len(test_context.search_results) == count


@when(parsers.parse('I add another observation with title "{title}"'))
def add_another_observation(test_context: BDDTestContext, title: str):
    """Add another observation to the active session."""
    from memory_tool.operations import add_observation
    from memory_tool.sessions import get_active_session
    from memory_tool.utils import tags_to_json

    active_session = get_active_session(test_context.profile)
    session_id = active_session["session_id"] if active_session else None

    obs_id = add_observation(
        test_context.conn,
        utc_now(),
        "test-project",
        "note",
        title,
        f"Summary for {title}",
        tags_to_json([]),
        "",
        "",
        session_id,
    )
    test_context.last_observation_id = obs_id


