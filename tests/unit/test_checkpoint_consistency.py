"""Checkpoint and session consistency tests."""

from memory_tool.checkpoints import create_checkpoint, get_checkpoint_observations
from memory_tool.operations import add_observation
from memory_tool.sessions import get_session_observations, start_session
from memory_tool.utils import metadata_to_json, tags_to_json, tags_to_text


def test_project_checkpoint_filters_future_observations(db_connection):
    """Project checkpoints should represent snapshot at checkpoint creation time."""
    add_observation(
        db_connection,
        "2026-01-01T00:00:00Z",
        "proj",
        "note",
        "before-checkpoint",
        "before-checkpoint",
        tags_to_json([]),
        tags_to_text([]),
        "",
    )
    checkpoint_id = create_checkpoint(
        db_connection,
        "snapshot",
        "",
        "",
        session_id=None,
        project="proj",
    )
    add_observation(
        db_connection,
        "2099-01-01T00:00:00Z",
        "proj",
        "note",
        "after-checkpoint",
        "after-checkpoint",
        tags_to_json([]),
        tags_to_text([]),
        "",
    )

    observations = get_checkpoint_observations(db_connection, checkpoint_id, limit=20)
    titles = {item.title for item in observations}
    assert "before-checkpoint" in titles
    assert "after-checkpoint" not in titles


def test_session_observations_keep_metadata(db_connection):
    """Session read path should preserve observation metadata."""
    session_id = start_session(
        db_connection,
        project="proj",
        working_dir="/tmp",
        agent_type="codex",
        summary="",
    )
    metadata = {"trace_id": "trace-session", "source": "test"}
    add_observation(
        db_connection,
        "2026-01-01T00:00:00Z",
        "proj",
        "note",
        "with-metadata",
        "with-metadata",
        tags_to_json([]),
        tags_to_text([]),
        "",
        session_id=session_id,
        metadata=metadata_to_json(metadata),
    )

    observations = get_session_observations(db_connection, session_id)
    assert observations
    assert observations[0].metadata == metadata


def test_checkpoint_observations_keep_metadata(db_connection):
    """Checkpoint read path should preserve observation metadata."""
    metadata = {"trace_id": "trace-checkpoint", "source": "test"}
    add_observation(
        db_connection,
        "2026-01-01T00:00:00Z",
        "proj",
        "note",
        "with-metadata",
        "with-metadata",
        tags_to_json([]),
        tags_to_text([]),
        "",
        metadata=metadata_to_json(metadata),
    )
    checkpoint_id = create_checkpoint(
        db_connection,
        "snapshot",
        "",
        "",
        session_id=None,
        project="proj",
    )

    observations = get_checkpoint_observations(db_connection, checkpoint_id, limit=20)
    assert observations
    assert observations[0].metadata == metadata
