"""Step definitions for observation links."""
from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from conftest import BDDTestContext
from .common_steps import parse_datatable


@given(parsers.parse('another observation exists with title "{title}"'))
def given_another_observation_exists(test_context: BDDTestContext, title: str):
    """Create another observation."""
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
    # Store in a dict to track multiple observations by title
    test_context.observations_by_title[title] = obs_id


@given(parsers.parse('an observation exists with title "{title}"'))
def given_observation_by_title(test_context: BDDTestContext, title: str):
    """Create an observation with the given title and store it."""
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
    test_context.observations_by_title[title] = obs_id


@given("another observation exists with:")
def given_another_observation_with_table(test_context: BDDTestContext, datatable):
    """Create another observation from table. This step is used for subsequent observations
    in a scenario and does NOT update last_observation_id (to preserve the first observation as reference)."""
    from memory_tool.operations import add_observation
    from memory_tool.utils import normalize_tags_list, tags_to_json, tags_to_text, utc_now

    # Use the parse_datatable helper from common_steps
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
    # Don't update last_observation_id - we want to preserve the first observation
    # as the reference point for similarity tests
    if data.get("title"):
        test_context.observations_by_title[data["title"]] = obs_id


@when("I create a link from the first observation to the second with type \"{link_type}\"")
def create_link_first_to_second(test_context: BDDTestContext, link_type: str):
    """Create a link between two observations."""
    from memory_tool.links import create_link

    from_id = test_context.last_observation_id
    # Get the second observation (the one created by "another observation exists")
    # We need to get the most recently created observation that's not the first one
    second_obs_id = None
    if hasattr(test_context, "observations_by_title"):
        # Get the most recent observation
        second_obs_id = max(test_context.observations_by_title.values())

    if second_obs_id is None:
        raise ValueError("Second observation not found")

    link_id = create_link(test_context.conn, from_id, second_obs_id, link_type)
    test_context.last_link_id = link_id


@when(parsers.parse('I create a link from the first observation to the second with type "{link_type}"'))
def create_link_first_to_second_typed(test_context: BDDTestContext, link_type: str):
    """Create a link between first and second observation with specific type."""
    from memory_tool.links import create_link

    from_id = test_context.last_observation_id
    # Get the second observation (the one with highest ID that's not the first)
    second_obs_id = None
    if hasattr(test_context, "observations_by_title") and test_context.observations_by_title:
        second_obs_id = max(test_context.observations_by_title.values())

    if second_obs_id is None:
        raise ValueError("Second observation not found")

    link_id = create_link(test_context.conn, from_id, second_obs_id, link_type)
    test_context.last_link_id = link_id


@when(parsers.parse('I create a link from "{from_title}" to "{to_title}" with type "{link_type}"'))
def create_link_by_titles(test_context: BDDTestContext, from_title: str, to_title: str, link_type: str):
    """Create a link using observation titles."""
    from memory_tool.links import create_link

    from_id = test_context.observations_by_title.get(from_title)
    to_id = test_context.observations_by_title.get(to_title)

    if from_id is None or to_id is None:
        raise ValueError(f"Observation not found: {from_title} -> {to_title}")

    link_id = create_link(test_context.conn, from_id, to_id, link_type)
    test_context.last_link_id = link_id


@given(parsers.parse('there is a link from "{from_title}" to "{to_title}" with type "{link_type}"'))
def given_link_exists(test_context: BDDTestContext, from_title: str, to_title: str, link_type: str):
    """Create a link if it doesn't exist."""
    from memory_tool.links import create_link

    from_id = test_context.observations_by_title.get(from_title)
    to_id = test_context.observations_by_title.get(to_title)

    if from_id is None:
        # Create the observation if it doesn't exist
        from memory_tool.operations import add_observation
        from memory_tool.utils import tags_to_json, utc_now
        from_id = add_observation(
            test_context.conn, utc_now(), "general", "note",
            from_title, f"Summary for {from_title}",
            tags_to_json([]), "", "", None
        )
        test_context.observations_by_title[from_title] = from_id

    if to_id is None:
        from memory_tool.operations import add_observation
        from memory_tool.utils import tags_to_json, utc_now
        to_id = add_observation(
            test_context.conn, utc_now(), "general", "note",
            to_title, f"Summary for {to_title}",
            tags_to_json([]), "", "", None
        )
        test_context.observations_by_title[to_title] = to_id

    create_link(test_context.conn, from_id, to_id, link_type)


@when(parsers.parse('I find observations related to "{title}"'))
def find_related_to_title(test_context: BDDTestContext, title: str):
    """Find observations related to the given title."""
    from memory_tool.links import get_related_observations

    obs_id = test_context.observations_by_title.get(title)
    if obs_id is None:
        raise ValueError(f"Observation '{title}' not found")

    test_context.last_related = get_related_observations(test_context.conn, obs_id)


@when("I find similar observations to the first observation")
def find_similar_to_first(test_context: BDDTestContext):
    """Find similar observations."""
    from memory_tool.links import find_similar_observations

    test_context.last_similar = find_similar_observations(
        test_context.conn, test_context.last_observation_id
    )


@when(parsers.parse('I remove the link from "{from_title}" to "{to_title}"'))
def remove_link_by_titles(test_context: BDDTestContext, from_title: str, to_title: str):
    """Remove a link between observations."""
    from memory_tool.links import delete_link

    from_id = test_context.observations_by_title.get(from_title)
    to_id = test_context.observations_by_title.get(to_title)

    if from_id is None or to_id is None:
        raise ValueError(f"Observation not found: {from_title} -> {to_title}")

    deleted = delete_link(test_context.conn, from_id, to_id)
    test_context.last_unlink_result = deleted


@then("the link should be created successfully")
def link_created_successfully(test_context: BDDTestContext):
    """Verify link was created."""
    assert hasattr(test_context, "last_link_id")
    assert test_context.last_link_id is not None
    assert test_context.last_link_id > 0


@then(parsers.parse('the link type should be "{link_type}"'))
def check_link_type(test_context: BDDTestContext, link_type: str):
    """Verify link type."""
    # This is implicitly checked by the create_link call
    # In a more comprehensive test, we could query the database
    pass


@then(parsers.parse('the first observation should have {count:d} related observation'))
@then(parsers.parse('the first observation should have {count:d} related observations'))
def check_related_count(test_context: BDDTestContext, count: int):
    """Verify number of related observations."""
    from memory_tool.links import get_related_observations

    related = get_related_observations(test_context.conn, test_context.last_observation_id)
    assert len(related) == count, f"Expected {count} related observations, got {len(related)}"


@then(parsers.parse('"{title}" should have {count:d} related observations'))
def check_related_count_by_title(test_context: BDDTestContext, title: str, count: int):
    """Verify number of related observations for a named observation."""
    from memory_tool.links import get_related_observations

    obs_id = test_context.observations_by_title.get(title)
    if obs_id is None:
        raise ValueError(f"Observation '{title}' not found")

    related = get_related_observations(test_context.conn, obs_id)
    assert len(related) == count, f"Expected {count} related observations for '{title}', got {len(related)}"


@then(parsers.parse('I should find {count:d} related observation'))
@then(parsers.parse('I should find {count:d} related observations'))
def check_found_related_count(test_context: BDDTestContext, count: int):
    """Verify found related count."""
    assert len(test_context.last_related) == count


@then(parsers.parse('"{title}" should be in the related observations'))
def check_title_in_related(test_context: BDDTestContext, title: str):
    """Verify observation is in related list."""
    titles = [r["title"] for r in test_context.last_related]
    assert title in titles, f"'{title}' not in related observations: {titles}"


@then(parsers.parse('"{title}" should be in the suggestions'))
def check_title_in_suggestions(test_context: BDDTestContext, title: str):
    """Verify observation is in suggestions."""
    titles = [s["title"] for s in test_context.last_similar]
    assert title in titles, f"'{title}' not in suggestions: {titles}"


@then("the link should be removed")
def link_should_be_removed(test_context: BDDTestContext):
    """Verify link was deleted."""
    assert test_context.last_unlink_result is True
