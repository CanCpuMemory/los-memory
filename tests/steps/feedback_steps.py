"""Step definitions for natural language feedback."""
from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from conftest import BDDTestContext


@when(parsers.parse('I provide feedback "{feedback}" on that observation'))
@when(parsers.parse("I provide feedback '{feedback}' on that observation"))
def provide_feedback(test_context: BDDTestContext, feedback: str):
    """Provide natural language feedback on the last observation."""
    from memory_tool.feedback import apply_feedback

    result = apply_feedback(
        test_context.conn,
        test_context.last_observation_id,
        feedback,
        auto_apply=True,
    )
    test_context.last_feedback_result = result


@when(parsers.parse('I preview feedback "{feedback}" on that observation'))
def preview_feedback(test_context: BDDTestContext, feedback: str):
    """Preview feedback without applying changes (dry run)."""
    from memory_tool.feedback import apply_feedback

    result = apply_feedback(
        test_context.conn,
        test_context.last_observation_id,
        feedback,
        auto_apply=False,
    )
    test_context.last_feedback_result = result


@given(parsers.parse('I have provided feedback "{feedback}" on that observation'))
def given_feedback_exists(test_context: BDDTestContext, feedback: str):
    """Create a feedback record for the observation."""
    from memory_tool.feedback import record_feedback

    record_feedback(
        test_context.conn,
        test_context.last_observation_id,
        "supplement",
        feedback,
    )


@when("I view feedback history for that observation")
def view_feedback_history(test_context: BDDTestContext):
    """Get feedback history for the observation."""
    from memory_tool.feedback import get_feedback_history

    test_context.feedback_history = get_feedback_history(
        test_context.conn,
        test_context.last_observation_id,
    )


@then(parsers.parse('the feedback should be recorded with action "{action}"'))
def check_feedback_recorded(test_context: BDDTestContext, action: str):
    """Verify feedback was recorded with correct action type."""
    from memory_tool.feedback import get_feedback_history

    history = get_feedback_history(test_context.conn, test_context.last_observation_id)
    assert len(history) > 0, "No feedback history found"
    assert history[0]["action_type"] == action, f"Expected action '{action}' but got '{history[0]['action_type']}'"


@then(parsers.parse('the observation summary should contain "{text}"'))
def check_summary_contains(test_context: BDDTestContext, text: str):
    """Verify observation summary contains specific text."""
    from memory_tool.operations import run_get

    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 1
    assert text in results[0].summary, f"Summary '{results[0].summary}' does not contain '{text}'"


@then(parsers.parse('the observation should have summary "{summary}"'))
def check_observation_has_summary(test_context: BDDTestContext, summary: str):
    """Verify observation has exact summary."""
    from memory_tool.operations import run_get

    results = run_get(test_context.conn, [test_context.last_observation_id])
    assert len(results) == 1
    assert results[0].summary == summary


@then(parsers.parse("I should see {count:d} feedback entries"))
def check_feedback_count(test_context: BDDTestContext, count: int):
    """Verify number of feedback entries."""
    assert len(test_context.feedback_history) == count


@then(parsers.parse('the latest feedback should contain "{text}"'))
def check_latest_feedback(test_context: BDDTestContext, text: str):
    """Verify latest feedback contains specific text."""
    assert len(test_context.feedback_history) > 0
    assert text in test_context.feedback_history[0]["feedback_text"]
