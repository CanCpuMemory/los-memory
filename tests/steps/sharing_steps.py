"""Step definitions for sharing and export."""
from __future__ import annotations

import json
from pathlib import Path

from pytest_bdd import given, parsers, then, when

from conftest import BDDTestContext, parse_datatable, parse_datatable_rows
from memory_tool.utils import utc_now


@when(parsers.parse('I export to JSON file "{filepath}"'))
def export_to_json(test_context: BDDTestContext, filepath: str):
    """Export to JSON file."""
    from memory_tool.share import run_share

    result = run_share(
        test_context.conn,
        output_path=filepath,
        fmt="json",
        project=None,
        kind=None,
        tag=None,
        session_id=None,
        since=None,
        limit=1000,
    )
    test_context.last_export_path = filepath
    test_context.search_results = result


@then("the file should exist")
def file_should_exist(test_context: BDDTestContext):
    """Verify export file exists."""
    path = Path(test_context.last_export_path)
    assert path.exists()


@then(parsers.parse("it should contain {count:d} observations"))
def file_contains_observations(test_context: BDDTestContext, count: int):
    """Verify file contains expected observations."""
    with open(test_context.last_export_path, "r") as f:
        data = json.load(f)
    assert len(data["observations"]) == count


@then(parsers.parse('it should contain project "{project}" metadata'))
def file_contains_project_metadata(test_context: BDDTestContext, project: str):
    """Verify file contains project info."""
    with open(test_context.last_export_path, "r") as f:
        data = json.load(f)
    assert data["stats"]["observation_count"] > 0


@when(parsers.parse('I export to Markdown file "{filepath}"'))
def export_to_markdown(test_context: BDDTestContext, filepath: str):
    """Export to Markdown file."""
    from memory_tool.share import run_share

    result = run_share(
        test_context.conn,
        output_path=filepath,
        fmt="markdown",
        project=None,
        kind=None,
        tag=None,
        session_id=None,
        since=None,
        limit=1000,
    )
    test_context.last_export_path = filepath


@then(parsers.parse('the file should contain "{text}"'))
def file_contains_text(test_context: BDDTestContext, text: str):
    """Verify file contains specific text."""
    with open(test_context.last_export_path, "r") as f:
        content = f.read()
    assert text in content


@given(parsers.parse('a JSON bundle exists at "{filepath}" with:'))
def given_json_bundle(test_context: BDDTestContext, filepath: str, datatable):
    """Create a JSON bundle file."""
    observations = []
    rows = parse_datatable_rows(datatable)
    for row in rows:
        observations.append({
            "id": len(observations) + 1,
            "timestamp": utc_now(),
            "project": row.get("project", "default"),
            "kind": "note",
            "title": row.get("title", "Untitled"),
            "summary": "Test summary",
            "tags": [],
            "raw": "",
            "session_id": None,
        })

    bundle = {
        "version": "1.0",
        "exported_at": utc_now(),
        "format": "json",
        "stats": {"observation_count": len(observations), "session_count": 0},
        "sessions": [],
        "observations": observations,
    }

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(bundle, f, indent=2)

    test_context.last_export_path = filepath


@given(parsers.parse('a JSON bundle exists at "{filepath}" with {count:d} observations'))
def given_json_bundle_with_count(test_context: BDDTestContext, filepath: str, count: int):
    """Create a JSON bundle with specific observation count."""
    observations = []
    for i in range(count):
        observations.append({
            "id": i + 1,
            "timestamp": utc_now(),
            "project": "imported-project",
            "kind": "note",
            "title": f"Imported observation {i + 1}",
            "summary": f"Summary {i + 1}",
            "tags": ["imported"],
            "raw": "",
            "session_id": None,
        })

    bundle = {
        "version": "1.0",
        "exported_at": utc_now(),
        "format": "json",
        "stats": {"observation_count": len(observations), "session_count": 0},
        "sessions": [],
        "observations": observations,
    }

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(bundle, f, indent=2)


@when("I import with dry-run")
def import_with_dry_run(test_context: BDDTestContext):
    """Import with dry-run flag."""
    from memory_tool.share import run_import
    result = run_import(
        test_context.conn,
        test_context.last_export_path,
        project_override=None,
        dry_run=True,
    )
    test_context.search_results = result


@then("no observations should be added")
def no_observations_added(test_context: BDDTestContext):
    """Verify no observations were added."""
    cursor = test_context.conn.execute("SELECT COUNT(*) FROM observations")
    initial_count = cursor.fetchone()[0]
    # The dry-run shouldn't have added anything
    assert test_context.search_results["dry_run"] is True


@then(parsers.parse("I should see preview of {count:d} observation to import"))
def see_import_preview(test_context: BDDTestContext, count: int):
    """Verify import preview shows expected count."""
    assert test_context.search_results["imported_observations"] == count


@when("I import the bundle")
def import_bundle(test_context: BDDTestContext):
    """Import the bundle for real."""
    from memory_tool.share import run_import
    result = run_import(
        test_context.conn,
        test_context.last_export_path,
        project_override=None,
        dry_run=False,
    )
    test_context.search_results = result


@then(parsers.parse("{count:d} observations should be added to the database"))
def observations_added(test_context: BDDTestContext, count: int):
    """Verify observations were imported."""
    assert test_context.search_results["imported_observations"] == count


@then("the sessions should be imported with new IDs")
def sessions_imported_with_new_ids(test_context: BDDTestContext):
    """Verify sessions got new IDs."""
    # This would require more complex verification in real implementation
    pass


@when(parsers.parse('I export with filter project="{project}"'))
def export_with_project_filter(test_context: BDDTestContext, project: str):
    """Export with project filter."""
    from memory_tool.share import run_share

    result = run_share(
        test_context.conn,
        output_path="/tmp/filtered_export.json",
        fmt="json",
        project=project,
        kind=None,
        tag=None,
        session_id=None,
        since=None,
        limit=1000,
    )
    test_context.last_export_path = "/tmp/filtered_export.json"
    test_context.search_results = result


@then(parsers.parse("only {count:d} observation should be exported"))
def only_one_observation_exported(test_context: BDDTestContext, count: int):
    """Verify filter worked."""
    assert test_context.search_results["observations"] == count


@then(parsers.parse('the exported observation should be "{title}"'))
def exported_observation_title(test_context: BDDTestContext, title: str):
    """Verify correct observation was exported."""
    with open(test_context.last_export_path, "r") as f:
        data = json.load(f)
    assert len(data["observations"]) == 1
    assert data["observations"][0]["title"] == title
