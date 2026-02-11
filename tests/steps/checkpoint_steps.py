"""Step definitions for checkpoint management."""
from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from conftest import BDDTestContext, parse_datatable, parse_datatable_rows


@when("I create a checkpoint named \"{name}\" with tag \"{tag}\"")
def create_checkpoint_step(test_context: BDDTestContext, name: str, tag: str):
    """Create a checkpoint."""
    from memory_tool.checkpoints import create_checkpoint
    from memory_tool.sessions import get_active_session
    from memory_tool.projects import get_active_project

    active_session = get_active_session(test_context.profile)
    session_id = active_session["session_id"] if active_session else None
    project = get_active_project(test_context.profile) or "general"

    checkpoint_id = create_checkpoint(
        test_context.conn,
        name=name,
        description="",
        tag=tag,
        session_id=session_id,
        project=project,
    )
    test_context.last_checkpoint_id = checkpoint_id


@then("the checkpoint should be created successfully")
def checkpoint_created(test_context: BDDTestContext):
    """Verify checkpoint was created."""
    assert test_context.last_checkpoint_id is not None
    assert test_context.last_checkpoint_id > 0


@then("the checkpoint should reference the current session")
def checkpoint_references_session(test_context: BDDTestContext):
    """Verify checkpoint has session ID."""
    from memory_tool.checkpoints import get_checkpoint
    checkpoint = get_checkpoint(test_context.conn, test_context.last_checkpoint_id)
    assert checkpoint.session_id == test_context.last_session_id


@then(parsers.parse("the checkpoint should record {count:d} observations"))
def checkpoint_records_observations(test_context: BDDTestContext, count: int):
    """Verify checkpoint observation count."""
    from memory_tool.checkpoints import get_checkpoint
    checkpoint = get_checkpoint(test_context.conn, test_context.last_checkpoint_id)
    assert checkpoint.observation_count == count


@given("the following checkpoints exist:")
def given_multiple_checkpoints(test_context: BDDTestContext, datatable):
    """Create multiple checkpoints."""
    from memory_tool.checkpoints import create_checkpoint
    from memory_tool.projects import get_active_project

    project = get_active_project(test_context.profile) or "general"

    rows = parse_datatable_rows(datatable)
    for row in rows:
        create_checkpoint(
            test_context.conn,
            name=row["name"],
            description="",
            tag=row["tag"],
            session_id=None,
            project=project,
        )


@when("I list checkpoints")
def list_checkpoints_step(test_context: BDDTestContext):
    """List all checkpoints."""
    from memory_tool.checkpoints import list_checkpoints
    test_context.search_results = list_checkpoints(test_context.conn, limit=100)


@when(parsers.parse('I list checkpoints with tag "{tag}"'))
def list_checkpoints_by_tag(test_context: BDDTestContext, tag: str):
    """List checkpoints filtered by tag."""
    from memory_tool.checkpoints import list_checkpoints
    test_context.search_results = list_checkpoints(test_context.conn, tag=tag, limit=100)


@given(parsers.parse('a checkpoint exists with ID {checkpoint_id:d}'))
def given_checkpoint_exists(test_context: BDDTestContext, checkpoint_id: int):
    """Create a checkpoint with specific ID concept."""
    from memory_tool.checkpoints import create_checkpoint

    actual_id = create_checkpoint(
        test_context.conn,
        name=f"checkpoint-{checkpoint_id}",
        description="",
        tag="",
        session_id=None,
        project="test-project",
    )
    test_context.last_checkpoint_id = actual_id


@given("a checkpoint exists with:")
def given_checkpoint_with_data(test_context: BDDTestContext, datatable):
    """Create a checkpoint from table data."""
    from memory_tool.checkpoints import create_checkpoint

    data = parse_datatable(datatable)
    if isinstance(data, list):
        data = data[0] if data else {}

    actual_id = create_checkpoint(
        test_context.conn,
        name=data.get("name", "test-checkpoint"),
        description=data.get("description", ""),
        tag=data.get("tag", ""),
        session_id=int(data["session_id"]) if "session_id" in data else None,
        project=data.get("project", "general"),
    )
    test_context.last_checkpoint_id = actual_id


@given("a checkpoint exists with {count:d} observations")
def given_checkpoint_with_observations(test_context: BDDTestContext, count: int):
    """Create a checkpoint with linked observations."""
    from memory_tool.checkpoints import create_checkpoint
    from memory_tool.sessions import start_session, set_active_session
    from memory_tool.operations import add_observation

    # Create a session and add observations
    session_id = start_session(
        test_context.conn,
        project="checkpoint-test",
        working_dir="/tmp",
        agent_type="test",
    )

    for i in range(count):
        add_observation(
            test_context.conn,
            utc_now(),
            "checkpoint-test",
            "note",
            f"Checkpoint observation {i + 1}",
            f"Summary {i + 1}",
            tags_to_json([]),
            "",
            "",
            session_id,
        )

    checkpoint_id = create_checkpoint(
        test_context.conn,
        name="test-checkpoint",
        description="",
        tag="",
        session_id=session_id,
        project="checkpoint-test",
    )
    test_context.last_checkpoint_id = checkpoint_id
    test_context.last_session_id = session_id


@when(parsers.parse('I resume from checkpoint {checkpoint_id:d}'))
def resume_from_checkpoint_step(test_context: BDDTestContext, checkpoint_id: int):
    """Resume from a checkpoint."""
    from memory_tool.checkpoints import resume_from_checkpoint
    result = resume_from_checkpoint(test_context.conn, test_context.last_checkpoint_id, test_context.profile)
    test_context.search_results = result.get("recent_observations", [])


@then("I should see recent observations from the checkpoint")
def see_recent_observations(test_context: BDDTestContext):
    """Verify recent observations are shown."""
    assert len(test_context.search_results) > 0


@when("I show checkpoint details")
def show_checkpoint_details(test_context: BDDTestContext):
    """Show checkpoint details."""
    from memory_tool.checkpoints import get_checkpoint, get_checkpoint_observations

    checkpoint = get_checkpoint(test_context.conn, test_context.last_checkpoint_id)
    observations = get_checkpoint_observations(test_context.conn, test_context.last_checkpoint_id)

    test_context.search_results = {
        "checkpoint": checkpoint,
        "observations": observations,
    }


@then("I should see the checkpoint metadata")
def see_checkpoint_metadata(test_context: BDDTestContext):
    """Verify checkpoint metadata is shown."""
    assert test_context.search_results["checkpoint"] is not None


@then(parsers.parse("I should see all {count:d} observations"))
def see_all_observations(test_context: BDDTestContext, count: int):
    """Verify all observations are shown."""
    assert len(test_context.search_results["observations"]) == count


@given(parsers.parse('I have an active session with {count:d} observations'))
def given_active_session_with_observations(test_context: BDDTestContext, count: int):
    """Create an active session with observations."""
    from memory_tool.sessions import start_session, set_active_session
    from memory_tool.operations import add_observation
    from memory_tool.utils import utc_now, tags_to_json

    session_id = start_session(
        test_context.conn,
        project="test-project",
        working_dir=str(Path.cwd()),
        agent_type=test_context.profile,
        summary="",
    )
    test_context.last_session_id = session_id
    set_active_session(test_context.profile, session_id, str(test_context.db_path))

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


@then(parsers.parse("I should see {count:d} checkpoints"))
def check_checkpoint_count(test_context: BDDTestContext, count: int):
    """Verify checkpoint count."""
    assert len(test_context.search_results) == count
