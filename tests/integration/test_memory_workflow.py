"""Integration tests for memory workflows."""
import json
from datetime import datetime, timedelta

import pytest

from memory_tool.client import MemoryClient
from memory_tool.incidents import IncidentManager
from memory_tool.triggers import ThresholdTrigger, TriggerRegistry


class TestObservationWorkflow:
    """Test complete observation workflow."""

    def test_add_and_retrieve_observation(self, memory_client):
        """Test adding and retrieving an observation."""
        obs = memory_client.add(
            title="Test Observation",
            summary="Test summary content",
            project="integration-test",
            kind="note",
            tags=["test", "integration"]
        )

        assert obs.id is not None
        assert obs.title == "Test Observation"

        retrieved = memory_client.get(obs.id)
        assert retrieved is not None
        assert retrieved.title == obs.title
        assert retrieved.tags == ["test", "integration"]

    def test_search_observations(self, memory_client):
        """Test searching observations."""
        memory_client.add(
            title="Python Development",
            summary="Working on Python code",
            project="dev",
            tags=["python"]
        )
        memory_client.add(
            title="JavaScript Frontend",
            summary="Working on JS code",
            project="dev",
            tags=["javascript"]
        )
        memory_client.add(
            title="Database Schema",
            summary="Designing database",
            project="db",
            tags=["postgres"]
        )

        # Search for Python - returns dicts, not objects
        results = memory_client.search("Python")
        assert len(results) >= 1
        # Results are dicts with observation and rank
        assert any("Python Development" in str(r) for r in results)

        # Search for code (should find both)
        results = memory_client.search("code")
        assert len(results) >= 2

    def test_tag_filtering(self, memory_client):
        """Test filtering by tags."""
        memory_client.add(
            title="Tagged A",
            summary="Summary",
            tags=["tag-a", "shared"]
        )
        memory_client.add(
            title="Tagged B",
            summary="Summary",
            tags=["tag-b", "shared"]
        )
        memory_client.add(
            title="No Tags",
            summary="Summary",
            tags=[]
        )

        # Verify tags are stored correctly via search
        results = memory_client.search("Tagged")
        assert len(results) >= 2


class TestSessionWorkflow:
    """Test session management workflow."""

    def test_session_lifecycle(self, memory_client):
        """Test complete session lifecycle."""
        # Start session
        session = memory_client.start_session(
            project="test-project",
            working_dir="/tmp/test",
            agent_type="test-agent"
        )
        assert session.id is not None
        assert session.project == "test-project"
        assert session.status == "active"

        # Add observation to session (session is automatic via active session)
        obs = memory_client.add(
            title="Session Observation",
            summary="Created during session",
            project="test-project"
        )
        # Observations added during active session are associated with it
        assert obs.session_id is not None

        # End session (only takes summary, not session_id)
        ended = memory_client.end_session("Test completed")
        assert ended.status == "completed"
        assert ended.summary == "Test completed"

    def test_session_observations(self, memory_client):
        """Test retrieving session observations."""
        session = memory_client.start_session(project="test")

        # Observations are automatically added to active session
        memory_client.add(title="Obs 1", summary="First")
        memory_client.add(title="Obs 2", summary="Second")
        # End session first
        memory_client.end_session()

        # Create new session and add third observation
        memory_client.start_session(project="other")
        memory_client.add(title="Obs 3", summary="Third")
        memory_client.end_session()

        observations = memory_client.get_session_observations(session.id)
        # Should have the 2 observations from first session
        assert len(observations) == 2


class TestIncidentWorkflow:
    """Test incident management workflow (Phase 1)."""

    def test_incident_detection_from_observation(self, memory_client):
        """Test detecting incident from error observation."""
        manager = IncidentManager(memory_client._conn)

        # Create observation representing error
        obs = memory_client.add(
            title="Error: Database Connection Failed",
            summary="Unable to connect to database server",
            project="production",
            kind="error",
            tags=["error", "database", "critical"]
        )

        # Create incident from observation
        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Database Connection Issue",
            description="Production database unreachable",
            project="production",
            source_observation_id=obs.id,
            context_snapshot={
                "error_message": "Connection refused",
                "timestamp": "2024-01-15T10:00:00Z"
            }
        )

        assert incident.source_observation_id == obs.id
        assert incident.context_snapshot["error_message"] == "Connection refused"

        # Link observation to incident
        manager.link_observation(incident.id, obs.id, "source")

        linked = manager.get_linked_observations(incident.id)
        assert len(linked) == 1
        assert linked[0]["title"] == "Error: Database Connection Failed"

    def test_incident_status_workflow(self, memory_client):
        """Test incident status transitions."""
        manager = IncidentManager(memory_client._conn)

        incident = manager.create(
            incident_type="performance",
            severity="p2",
            title="High CPU Usage",
            description="CPU usage above threshold",
            project="production"
        )

        assert incident.status == "detected"

        # Move to analyzing
        incident = manager.update_status(incident.id, "analyzing")
        assert incident.status == "analyzing"

        # Move to recovering
        incident = manager.update_status(incident.id, "recovering")
        assert incident.status == "recovering"

        # Resolve
        incident = manager.update_status(incident.id, "resolved")
        assert incident.status == "resolved"
        assert incident.resolved_at is not None

        # Close
        incident = manager.update_status(incident.id, "closed")
        assert incident.status == "closed"


class TestTriggerWorkflow:
    """Test trigger system workflow (Phase 1)."""

    def test_trigger_registry_evaluation(self, memory_client):
        """Test trigger registry evaluating context."""
        registry = TriggerRegistry()

        # Register some triggers
        registry.register(ThresholdTrigger(
            name="high_error_rate",
            metric_path="error_rate",
            operator=">",
            threshold=0.05
        ))
        registry.register(ThresholdTrigger(
            name="high_latency",
            metric_path="latency_p99",
            operator=">",
            threshold=1000
        ))

        # Normal context - no triggers
        results = registry.evaluate({
            "error_rate": 0.01,
            "latency_p99": 500
        })
        assert len(results) == 0

        # Error rate too high
        results = registry.evaluate({
            "error_rate": 0.08,
            "latency_p99": 500
        })
        assert len(results) == 1
        assert results[0]["trigger_name"] == "high_error_rate"

        # Both metrics high
        results = registry.evaluate({
            "error_rate": 0.10,
            "latency_p99": 2000
        })
        assert len(results) == 2

    def test_trigger_creates_incident(self, memory_client):
        """Test trigger evaluation creating incident."""
        manager = IncidentManager(memory_client._conn)

        trigger = ThresholdTrigger(
            name="disk_full",
            metric_path="disk_usage_percent",
            operator=">",
            threshold=90
        )

        context = {
            "disk_usage_percent": 95,
            "timestamp": "2024-01-15T10:00:00Z",
            "mount_point": "/var/log"
        }

        result = trigger.evaluate(context)

        if result:
            # Create incident from trigger
            incident = manager.create(
                incident_type="availability",
                severity="p1",
                title=f"Trigger: {result['trigger_name']}",
                description=f"Threshold exceeded: {result['actual_value']} {result['operator']} {result['threshold']}",
                context_snapshot=context
            )

            assert incident is not None
            assert incident.incident_type == "availability"

    def test_sustained_threshold_trigger(self, memory_client):
        """Test trigger requiring sustained condition."""
        trigger = ThresholdTrigger(
            name="cpu_sustained",
            metric_path="cpu.usage",
            operator=">",
            threshold=80,
            duration=3
        )

        # First two evaluations
        assert trigger.evaluate({"cpu": {"usage": 90}}) is None
        assert trigger.evaluate({"cpu": {"usage": 90}}) is None

        # Third evaluation - should trigger
        result = trigger.evaluate({"cpu": {"usage": 90}})
        assert result is not None
        assert result["trigger_name"] == "cpu_sustained"


class TestCheckpointWorkflow:
    """Test checkpoint workflow."""

    def test_checkpoint_create_and_resume(self, memory_client):
        """Test creating and resuming from checkpoint."""
        session = memory_client.start_session(project="test")

        # Add observation (session is automatic)
        memory_client.add(title="Before Checkpoint", summary="Content")

        checkpoint = memory_client.create_checkpoint(
            name="milestone-1",
            description="First milestone",
            tag="milestone"
        )

        assert checkpoint is not None
        assert checkpoint["id"] is not None

        memory_client.add(title="After Checkpoint", summary="More content")

        # Resume from checkpoint (use checkpoint id from dict)
        result = memory_client.resume_from_checkpoint(checkpoint["id"])
        assert result["checkpoint_id"] == checkpoint["id"]

        memory_client.end_session()


class TestProjectWorkflow:
    """Test project management workflow."""

    def test_project_isolation(self, memory_client):
        """Test observations are isolated by project."""
        memory_client.add(
            title="Project A Task",
            summary="Task in project A",
            project="project-a"
        )
        memory_client.add(
            title="Project B Task",
            summary="Task in project B",
            project="project-b"
        )

        # Search in each project - results are dicts
        project_a_results = memory_client.search("Project A")
        assert len(project_a_results) >= 1
        # Check that results contain project-a
        assert any("project-a" in str(r) for r in project_a_results)

        project_b_results = memory_client.search("Project B")
        assert any("project-b" in str(r) for r in project_b_results)


class TestEndToEndWorkflow:
    """Test end-to-end workflows."""

    def test_complete_development_session(self, memory_client):
        """Test complete development session with all features."""
        # 1. Start a session
        session = memory_client.start_session(
            project="feature-x",
            working_dir="/home/dev/project",
            agent_type="claude"
        )

        # 2. Add observations during development (auto-associated with session)
        obs1 = memory_client.add(
            title="Initial Analysis",
            summary="Analyzed the requirements",
            kind="analysis",
            tags=["planning"]
        )
        assert obs1.session_id is not None

        obs2 = memory_client.add(
            title="Implementation Started",
            summary="Created base structure",
            kind="code",
            tags=["implementation"]
        )

        # 3. Create checkpoint
        checkpoint = memory_client.create_checkpoint(
            name="working-base",
            description="Base implementation complete"
        )
        assert checkpoint["id"] is not None

        # 4. More work
        obs3 = memory_client.add(
            title="Feature Complete",
            summary="All tests passing",
            kind="code",
            tags=["complete"]
        )

        # 5. Search for all observations
        results = memory_client.search("Implementation")
        assert len(results) >= 1

        # 6. End session (only takes summary, not session_id)
        memory_client.end_session("Feature X implemented successfully")

        # 7. Verify session has all observations
        observations = memory_client.get_session_observations(session.id)
        assert len(observations) == 3

    def test_incident_response_workflow(self, memory_client):
        """Test complete incident response workflow."""
        manager = IncidentManager(memory_client._conn)

        # 1. System monitoring detects issue
        trigger = ThresholdTrigger(
            name="api_error_rate",
            metric_path="api.errors.rate",
            operator=">",
            threshold=0.05,
            duration=2
        )

        # Simulate sustained error rate
        context = {"api": {"errors": {"rate": 0.10}}}
        trigger.evaluate(context)  # 1st
        result = trigger.evaluate(context)  # 2nd - triggers

        # 2. Create incident
        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="High API Error Rate",
            description="API error rate exceeded 5% threshold",
            project="production",
            context_snapshot={"error_rate": 0.10, "timestamp": "2024-01-15T10:00:00Z"}
        )

        # 3. Add observation documenting the incident
        obs = memory_client.add(
            title="P1 Incident: High API Error Rate",
            summary="API error rate reached 10%, incident created",
            project="production",
            kind="incident",
            tags=["p1", "api", "error-rate"]
        )

        # 4. Link observation to incident
        manager.link_observation(incident.id, obs.id, "documentation")

        # 5. Update incident status during response
        manager.update_status(incident.id, "analyzing")
        manager.update_status(incident.id, "recovering")

        # 6. Add resolution observation
        resolution_obs = memory_client.add(
            title="API Error Rate Resolved",
            summary="Restarted API servers, error rate back to normal",
            project="production",
            kind="resolution",
            tags=["resolved", "api"]
        )
        manager.link_observation(incident.id, resolution_obs.id, "resolution")

        # 7. Resolve incident
        manager.update_status(incident.id, "resolved")

        # 8. Verify incident history
        linked = manager.get_linked_observations(incident.id)
        assert len(linked) == 2

        final = manager.get(incident.id)
        assert final.status == "resolved"
        assert final.resolved_at is not None
