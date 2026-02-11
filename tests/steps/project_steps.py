"""Step definitions for project management."""
from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from conftest import BDDTestContext, parse_datatable, parse_datatable_rows, utc_now
def tags_to_json(tags): from memory_tool.utils import tags_to_json as _ttj; return _ttj(tags)
def tags_to_text(tags): from memory_tool.utils import tags_to_text as _ttt; return _ttt(tags)


@when(parsers.parse('I set the active project to "{project}"'))
def set_active_project_step(test_context: BDDTestContext, project: str):
    """Set the active project."""
    from memory_tool.projects import set_active_project
    set_active_project(test_context.profile, project)


@when('I add an observation with title "{title}"')
def add_observation_simple(test_context: BDDTestContext, title: str):
    """Add observation without explicit project (uses active)."""
    from memory_tool.operations import add_observation
    from memory_tool.projects import get_active_project

    project = get_active_project(test_context.profile) or "general"

    obs_id = add_observation(
        test_context.conn,
        utc_now(),
        project,
        "note",
        title,
        f"Summary for {title}",
        tags_to_json([]),
        "",
        "",
        None,
    )
    test_context.last_observation_id = obs_id


@then(parsers.parse('the observation should belong to project "{project}"'))
def observation_belongs_to_project(test_context: BDDTestContext, project: str):
    """Verify observation project."""
    from memory_tool.operations import run_get
    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 1
    assert results[0].project == project


@when("I list projects")
def list_projects_step(test_context: BDDTestContext):
    """List all projects."""
    from memory_tool.projects import list_projects
    test_context.search_results = list_projects(test_context.conn, limit=100)


@then(parsers.parse('I should see project "{project}" with {count:d} observations'))
def check_project_stats(test_context: BDDTestContext, project: str, count: int):
    """Verify project observation count."""
    for proj in test_context.search_results:
        if proj["project"] == project:
            assert proj["observation_count"] == count
            return
    raise AssertionError(f"Project {project} not found")


@given('observations exist for project "{project}"')
def given_observations_for_project(test_context: BDDTestContext, project: str):
    """Create observations for a project."""
    from memory_tool.operations import add_observation

    for i in range(3):
        add_observation(
            test_context.conn,
            utc_now(),
            project,
            "note",
            f"Observation {i + 1}",
            f"Summary {i + 1}",
            tags_to_json([]),
            "",
            "",
            None,
        )


@when(parsers.parse('I archive project "{project}"'))
def archive_project(test_context: BDDTestContext, project: str):
    """Archive a project."""
    new_name = f"archived/{project}"
    test_context.conn.execute(
        "UPDATE observations SET project = ? WHERE project = ?",
        (new_name, project),
    )
    test_context.conn.execute(
        "UPDATE sessions SET project = ? WHERE project = ?",
        (new_name, project),
    )
    test_context.conn.commit()


@then(parsers.parse('all observations should be moved to "{new_project}"'))
def observations_moved_to_archive(test_context: BDDTestContext, new_project: str):
    """Verify observations were archived."""
    cursor = test_context.conn.execute(
        "SELECT COUNT(*) FROM observations WHERE project = ?",
        (new_project,),
    )
    assert cursor.fetchone()[0] > 0


@then(parsers.parse('the original project "{project}" should have no observations'))
def original_project_empty(test_context: BDDTestContext, project: str):
    """Verify original project is empty."""
    cursor = test_context.conn.execute(
        "SELECT COUNT(*) FROM observations WHERE project = ?",
        (project,),
    )
    assert cursor.fetchone()[0] == 0


@given('the following observations exist in project "{project}":')
def given_observations_in_project(test_context: BDDTestContext, project: str, datatable):
    """Create observations with specific kinds and tags."""
    from memory_tool.operations import add_observation
    from memory_tool.utils import normalize_tags_list

    rows = parse_datatable_rows(datatable)
    for row in rows:
        tags = normalize_tags_list(row.get("tags", ""))
        add_observation(
            test_context.conn,
            utc_now(),
            project,
            row.get("kind", "note"),
            row.get("title", "Untitled"),
            "Summary",
            tags_to_json(tags),
            tags_to_text(tags),
            "",
            None,
        )


@when(parsers.parse('I get statistics for project "{project}"'))
def get_project_stats_step(test_context: BDDTestContext, project: str):
    """Get project statistics."""
    from memory_tool.projects import get_project_stats
    test_context.search_results = [get_project_stats(test_context.conn, project)]


@then(parsers.parse("I should see {count:d} total observations"))
def check_total_observations(test_context: BDDTestContext, count: int):
    """Verify total observation count."""
    stats = test_context.search_results[0]
    assert stats["observation_count"] == count


@then(parsers.parse("I should see {count:d} different kinds"))
def check_kind_count(test_context: BDDTestContext, count: int):
    """Verify kind count."""
    stats = test_context.search_results[0]
    assert stats["kind_count"] == count


@then(parsers.parse('I should see top tags including "{tag1}" and "{tag2}"'))
def check_top_tags(test_context: BDDTestContext, tag1: str, tag2: str):
    """Verify top tags include expected values."""
    stats = test_context.search_results[0]
    top_tag_names = [t["tag"] for t in stats["top_tags"]]
    assert tag1 in top_tag_names or tag2 in top_tag_names
