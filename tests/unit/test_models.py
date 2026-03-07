"""Unit tests for memory_tool.models module."""
import pytest

from memory_tool.models import (
    Observation,
    Session,
    Checkpoint,
    Feedback,
    ObservationLink,
    ToolCall,
)


class TestObservation:
    """Test Observation dataclass."""

    def test_create_observation(self):
        """Test creating an Observation."""
        obs = Observation(
            id=1,
            timestamp="2024-01-15T10:30:00Z",
            project="test",
            kind="note",
            title="Test Title",
            summary="Test Summary",
            tags=["tag1", "tag2"],
            raw="Raw content",
            session_id=None,
        )
        assert obs.id == 1
        assert obs.title == "Test Title"
        assert obs.tags == ["tag1", "tag2"]

    def test_observation_with_session(self):
        """Test Observation with session ID."""
        obs = Observation(
            id=1,
            timestamp="2024-01-15T10:30:00Z",
            project="test",
            kind="note",
            title="Test",
            summary="Summary",
            tags=[],
            raw="",
            session_id=5,
        )
        assert obs.session_id == 5

    def test_observation_defaults(self):
        """Test Observation with default values."""
        obs = Observation(
            id=1,
            timestamp="2024-01-15T10:30:00Z",
            project="general",
            kind="note",
            title="Test",
            summary="Summary",
            tags=[],
            raw="",
        )
        # session_id should be None by default
        assert obs.session_id is None


class TestSession:
    """Test Session dataclass."""

    def test_active_session(self):
        """Test active session (no end_time)."""
        session = Session(
            id=1,
            start_time="2024-01-15T10:00:00Z",
            end_time=None,
            project="test",
            working_dir="/tmp",
            agent_type="claude",
            summary="",
            status="active",
        )
        assert session.status == "active"
        assert session.end_time is None

    def test_completed_session(self):
        """Test completed session."""
        session = Session(
            id=1,
            start_time="2024-01-15T10:00:00Z",
            end_time="2024-01-15T11:00:00Z",
            project="test",
            working_dir="/tmp",
            agent_type="claude",
            summary="Done",
            status="completed",
        )
        assert session.status == "completed"
        assert session.end_time is not None

    def test_session_fields(self):
        """Test all Session fields."""
        session = Session(
            id=1,
            start_time="2024-01-15T10:00:00Z",
            end_time=None,
            project="my-project",
            working_dir="/home/user/project",
            agent_type="codex",
            summary="Test session",
            status="active",
        )
        assert session.id == 1
        assert session.project == "my-project"
        assert session.working_dir == "/home/user/project"
        assert session.agent_type == "codex"
        assert session.summary == "Test session"


class TestCheckpoint:
    """Test Checkpoint dataclass."""

    def test_create_checkpoint(self):
        """Test creating a Checkpoint."""
        checkpoint = Checkpoint(
            id=1,
            timestamp="2024-01-15T10:00:00Z",
            name="v1.0",
            description="Version 1.0 checkpoint",
            tag="release",
            session_id=5,
            observation_count=100,
            project="my-project",
        )
        assert checkpoint.id == 1
        assert checkpoint.name == "v1.0"
        assert checkpoint.tag == "release"
        assert checkpoint.observation_count == 100

    def test_checkpoint_defaults(self):
        """Test Checkpoint with default values."""
        checkpoint = Checkpoint(
            id=1,
            timestamp="2024-01-15T10:00:00Z",
            name="test",
            project="general",
        )
        assert checkpoint.description == ""
        assert checkpoint.tag == ""
        assert checkpoint.session_id is None
        assert checkpoint.observation_count == 0


class TestFeedback:
    """Test Feedback dataclass."""

    def test_create_feedback(self):
        """Test creating Feedback."""
        feedback = Feedback(
            id=1,
            target_observation_id=5,
            action_type="correct",
            feedback_text="Corrected summary",
            timestamp="2024-01-15T10:00:00Z",
        )
        assert feedback.id == 1
        assert feedback.target_observation_id == 5
        assert feedback.action_type == "correct"
        assert feedback.feedback_text == "Corrected summary"


class TestObservationLink:
    """Test ObservationLink dataclass."""

    def test_create_link(self):
        """Test creating ObservationLink."""
        link = ObservationLink(
            id=1,
            from_id=10,
            to_id=20,
            link_type="related",
            created_at="2024-01-15T10:00:00Z",
        )
        assert link.id == 1
        assert link.from_id == 10
        assert link.to_id == 20
        assert link.link_type == "related"

    def test_link_types(self):
        """Test different link types."""
        for link_type in ["related", "child", "parent", "refines"]:
            link = ObservationLink(
                id=1,
                from_id=1,
                to_id=2,
                link_type=link_type,
                created_at="2024-01-15T10:00:00Z",
            )
            assert link.link_type == link_type


class TestToolCall:
    """Test ToolCall dataclass."""

    def test_create_tool_call(self):
        """Test creating ToolCall."""
        tool_call = ToolCall(
            id=1,
            tool_name="search",
            tool_input={"query": "test"},
            tool_output={"results": []},
            call_status="success",
            duration_ms=100,
            timestamp="2024-01-15T10:00:00Z",
        )
        assert tool_call.id == 1
        assert tool_call.tool_name == "search"
        assert tool_call.call_status == "success"
        assert tool_call.duration_ms == 100

    def test_tool_call_error(self):
        """Test ToolCall with error status."""
        tool_call = ToolCall(
            id=1,
            tool_name="search",
            tool_input={"query": "test"},
            tool_output={"error": "Failed"},
            call_status="error",
            duration_ms=50,
            timestamp="2024-01-15T10:00:00Z",
        )
        assert tool_call.call_status == "error"

    def test_tool_call_no_duration(self):
        """Test ToolCall without duration."""
        tool_call = ToolCall(
            id=1,
            tool_name="search",
            tool_input={},
            tool_output={},
            call_status="success",
            timestamp="2024-01-15T10:00:00Z",
        )
        assert tool_call.duration_ms is None
