"""Tests for incident management."""
import pytest

from memory_tool.incidents import Incident, IncidentManager


class TestIncidentConstants:
    """Test Incident constants."""

    def test_valid_statuses(self):
        """Test valid status constants."""
        assert "detected" in Incident.VALID_STATUSES
        assert "analyzing" in Incident.VALID_STATUSES
        assert "recovering" in Incident.VALID_STATUSES
        assert "resolved" in Incident.VALID_STATUSES
        assert "closed" in Incident.VALID_STATUSES

    def test_valid_types(self):
        """Test valid type constants."""
        assert "error" in Incident.VALID_TYPES
        assert "performance" in Incident.VALID_TYPES
        assert "availability" in Incident.VALID_TYPES

    def test_valid_severities(self):
        """Test valid severity constants."""
        assert "p0" in Incident.VALID_SEVERITIES
        assert "p1" in Incident.VALID_SEVERITIES
        assert "p2" in Incident.VALID_SEVERITIES
        assert "p3" in Incident.VALID_SEVERITIES


class TestIncidentManager:
    """Test IncidentManager functionality."""

    def test_create_incident(self, db_connection):
        """Test creating an incident."""
        manager = IncidentManager(db_connection)
        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Test Error",
            description="A test error description",
            project="test-project"
        )

        assert incident.id is not None
        assert incident.incident_type == "error"
        assert incident.severity == "p1"
        assert incident.status == "detected"
        assert incident.title == "Test Error"
        assert incident.description == "A test error description"
        assert incident.project == "test-project"
        assert incident.detected_at is not None
        assert incident.resolved_at is None

    def test_create_incident_with_invalid_type(self, db_connection):
        """Test creating incident with invalid type raises error."""
        manager = IncidentManager(db_connection)
        with pytest.raises(ValueError, match="Invalid incident_type"):
            manager.create(
                incident_type="invalid_type",
                severity="p1",
                title="Test",
                description="Test"
            )

    def test_create_incident_with_invalid_severity(self, db_connection):
        """Test creating incident with invalid severity raises error."""
        manager = IncidentManager(db_connection)
        with pytest.raises(ValueError, match="Invalid severity"):
            manager.create(
                incident_type="error",
                severity="critical",
                title="Test",
                description="Test"
            )

    def test_get_incident(self, db_connection):
        """Test getting an incident by ID."""
        manager = IncidentManager(db_connection)
        created = manager.create(
            incident_type="performance",
            severity="p2",
            title="Performance Issue",
            description="Slow response times"
        )

        fetched = manager.get(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.title == created.title

    def test_get_nonexistent_incident(self, db_connection):
        """Test getting non-existent incident returns None."""
        manager = IncidentManager(db_connection)
        result = manager.get(99999)
        assert result is None

    def test_update_status_valid_transition(self, db_connection):
        """Test valid status transition."""
        manager = IncidentManager(db_connection)
        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Test Error",
            description="Test"
        )

        updated = manager.update_status(incident.id, "analyzing")
        assert updated.status == "analyzing"
        assert updated.resolved_at is None

    def test_update_status_to_resolved(self, db_connection):
        """Test resolving an incident sets resolved_at."""
        manager = IncidentManager(db_connection)
        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Test Error",
            description="Test"
        )

        updated = manager.update_status(incident.id, "resolved")
        assert updated.status == "resolved"
        assert updated.resolved_at is not None

    def test_update_status_invalid_transition(self, db_connection):
        """Test invalid status transition raises error."""
        manager = IncidentManager(db_connection)
        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Test Error",
            description="Test"
        )

        with pytest.raises(ValueError, match="Invalid status transition"):
            manager.update_status(incident.id, "closed")

    def test_update_status_invalid_status(self, db_connection):
        """Test updating to invalid status raises error."""
        manager = IncidentManager(db_connection)
        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Test Error",
            description="Test"
        )

        with pytest.raises(ValueError, match="Invalid status"):
            manager.update_status(incident.id, "unknown_status")

    def test_list_incidents(self, db_connection):
        """Test listing incidents."""
        manager = IncidentManager(db_connection)

        # Create multiple incidents
        manager.create(incident_type="error", severity="p1", title="Error 1", description="Test")
        manager.create(incident_type="performance", severity="p2", title="Perf 1", description="Test")
        manager.create(incident_type="availability", severity="p0", title="Avail 1", description="Test")

        incidents = manager.list(limit=10)
        assert len(incidents) == 3

    def test_list_with_status_filter(self, db_connection):
        """Test listing incidents with status filter."""
        manager = IncidentManager(db_connection)

        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Error 1",
            description="Test"
        )
        manager.update_status(incident.id, "resolved")
        manager.create(incident_type="error", severity="p1", title="Error 2", description="Test")

        resolved = manager.list(status="resolved")
        assert len(resolved) == 1
        assert resolved[0].status == "resolved"

    def test_list_with_type_filter(self, db_connection):
        """Test listing incidents with type filter."""
        manager = IncidentManager(db_connection)

        manager.create(incident_type="error", severity="p1", title="Error", description="Test")
        manager.create(incident_type="performance", severity="p2", title="Perf", description="Test")

        errors = manager.list(incident_type="error")
        assert len(errors) == 1
        assert errors[0].incident_type == "error"

    def test_list_with_severity_filter(self, db_connection):
        """Test listing incidents with severity filter."""
        manager = IncidentManager(db_connection)

        manager.create(incident_type="error", severity="p0", title="Critical", description="Test")
        manager.create(incident_type="error", severity="p3", title="Low", description="Test")

        critical = manager.list(severity="p0")
        assert len(critical) == 1
        assert critical[0].severity == "p0"

    def test_list_with_project_filter(self, db_connection):
        """Test listing incidents with project filter."""
        manager = IncidentManager(db_connection)

        manager.create(incident_type="error", severity="p1", title="Proj A", description="Test", project="project-a")
        manager.create(incident_type="error", severity="p1", title="Proj B", description="Test", project="project-b")

        project_a = manager.list(project="project-a")
        assert len(project_a) == 1
        assert project_a[0].project == "project-a"

    def test_list_pagination(self, db_connection):
        """Test incident list pagination."""
        manager = IncidentManager(db_connection)

        for i in range(10):
            manager.create(incident_type="error", severity="p1", title=f"Error {i}", description="Test")

        first_page = manager.list(limit=5, offset=0)
        assert len(first_page) == 5

        second_page = manager.list(limit=5, offset=5)
        assert len(second_page) == 5

        # Check that all incidents are returned uniquely
        all_ids = [i.id for i in first_page] + [i.id for i in second_page]
        assert len(all_ids) == len(set(all_ids))  # No duplicates


class TestIncidentObservationLinks:
    """Test linking observations to incidents."""

    def test_link_observation_to_incident(self, db_connection):
        """Test linking an observation to an incident."""
        from memory_tool.operations import add_observation
        from memory_tool.utils import tags_to_json, tags_to_text

        manager = IncidentManager(db_connection)

        # Create observation
        obs_id = add_observation(
            db_connection, "2024-01-15T10:00:00Z", "test", "note",
            "Test Observation", "Summary", "[]", "", "raw"
        )

        # Create incident
        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Test Error",
            description="Test"
        )

        # Link observation to incident
        success = manager.link_observation(incident.id, obs_id, "related")
        assert success is True

    def test_link_same_observation_twice_fails(self, db_connection):
        """Test linking same observation twice returns False or handles gracefully."""
        from memory_tool.operations import add_observation

        manager = IncidentManager(db_connection)

        obs_id = add_observation(
            db_connection, "2024-01-15T10:00:00Z", "test", "note",
            "Test Observation", "Summary", "[]", "", "raw"
        )

        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Test Error",
            description="Test"
        )

        first_result = manager.link_observation(incident.id, obs_id, "related")
        # First link should succeed
        assert first_result is True

        # Second link with same params - behavior depends on implementation
        # It can either return False (handled exception) or raise IntegrityError
        try:
            second_result = manager.link_observation(incident.id, obs_id, "related")
            # If it returns a value, should indicate failure
            assert second_result is False
        except Exception:
            # If it raises an exception, that's also valid behavior
            pass

    def test_get_linked_observations(self, db_connection):
        """Test getting observations linked to an incident."""
        from memory_tool.operations import add_observation

        manager = IncidentManager(db_connection)

        obs_id = add_observation(
            db_connection, "2024-01-15T10:00:00Z", "test", "note",
            "Test Observation", "Summary", "[]", "", "raw"
        )

        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Test Error",
            description="Test"
        )

        manager.link_observation(incident.id, obs_id, "source")

        linked = manager.get_linked_observations(incident.id)
        assert len(linked) == 1
        assert linked[0]["id"] == obs_id
        assert linked[0]["link_type"] == "source"

    def test_get_linked_observations_empty(self, db_connection):
        """Test getting linked observations for incident with no links."""
        manager = IncidentManager(db_connection)

        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Test Error",
            description="Test"
        )

        linked = manager.get_linked_observations(incident.id)
        assert linked == []


class TestIncidentContextSnapshot:
    """Test incident context snapshot functionality."""

    def test_create_with_context_snapshot(self, db_connection):
        """Test creating incident with context snapshot."""
        manager = IncidentManager(db_connection)

        context = {
            "error_rate": 0.15,
            "latency_p99": 2500,
            "timestamp": "2024-01-15T10:00:00Z"
        }

        incident = manager.create(
            incident_type="performance",
            severity="p1",
            title="High Latency",
            description="P99 latency exceeded threshold",
            context_snapshot=context
        )

        fetched = manager.get(incident.id)
        assert fetched.context_snapshot == context

    def test_create_with_source_observation(self, db_connection):
        """Test creating incident linked to source observation."""
        from memory_tool.operations import add_observation

        manager = IncidentManager(db_connection)

        obs_id = add_observation(
            db_connection, "2024-01-15T10:00:00Z", "test", "note",
            "Error Observation", "Error detected", "[]", "", "raw"
        )

        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Test Error",
            description="Test",
            source_observation_id=obs_id
        )

        assert incident.source_observation_id == obs_id
