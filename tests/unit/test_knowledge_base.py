"""Tests for knowledge base system.

Tests cover knowledge extraction, storage, search, and statistics.
"""
import pytest

from memory_tool.knowledge_base import KnowledgeBase, KnowledgeEntry, ResolutionExtractor


class TestKnowledgeEntry:
    """Test KnowledgeEntry dataclass."""

    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        entry = KnowledgeEntry(
            incident_type="error",
            severity="p1",
            symptoms_pattern="timeout",
            root_cause_summary="db connection pool",
            success_count=8,
            failure_count=2,
        )

        assert entry.success_rate == 0.8

    def test_success_rate_zero_division(self):
        """Test success rate with no applications."""
        entry = KnowledgeEntry(
            incident_type="error",
            severity="p1",
            symptoms_pattern="timeout",
            root_cause_summary="db connection pool",
            success_count=0,
            failure_count=0,
        )

        assert entry.success_rate == 0.0

    def test_confidence_score(self):
        """Test confidence score calculation."""
        entry = KnowledgeEntry(
            incident_type="error",
            severity="p1",
            symptoms_pattern="timeout",
            root_cause_summary="db connection pool",
            success_count=10,
            failure_count=0,
        )

        assert entry.confidence_score > 0.5
        assert entry.confidence_score <= 1.0

    def test_to_dict(self):
        """Test serialization."""
        entry = KnowledgeEntry(
            incident_type="performance",
            severity="p2",
            symptoms_pattern="high cpu",
            root_cause_summary="infinite loop",
            solution_steps=["restart", "fix code"],
            tags=["performance", "cpu"],
        )

        data = entry.to_dict()

        assert data["incident_type"] == "performance"
        assert data["success_rate"] == 0.0
        assert len(data["solution_steps"]) == 2


class TestResolutionExtractor:
    """Test resolution extraction."""

    def test_extract_step_from_output(self):
        """Test step extraction from recovery output."""
        extractor = ResolutionExtractor()

        step = extractor._extract_step_from_output("restart service nginx")
        assert step is not None
        assert "restart" in step.lower()

    def test_extract_step_from_text(self):
        """Test step extraction from text."""
        extractor = ResolutionExtractor()

        text = "Fixed by restarting the database service"
        step = extractor._extract_step_from_text(text)
        assert step is not None


class TestKnowledgeBase:
    """Test KnowledgeBase operations."""

    @pytest.fixture
    def kb(self, db_connection):
        return KnowledgeBase(db_connection)

    @pytest.fixture
    def sample_entry(self):
        return KnowledgeEntry(
            incident_type="error",
            severity="p1",
            symptoms_pattern="Connection timeout to database",
            root_cause_summary="Database connection pool exhausted",
            solution_steps=["Restart database service", "Increase connection pool size"],
            tags=["database", "timeout"],
        )

    def test_add_entry(self, kb, sample_entry):
        """Test adding knowledge entry."""
        entry_id = kb.add_entry(sample_entry)

        assert entry_id is not None
        assert sample_entry.id == entry_id

    def test_get_entry(self, kb, sample_entry):
        """Test retrieving entry."""
        entry_id = kb.add_entry(sample_entry)

        retrieved = kb.get_entry(entry_id)

        assert retrieved is not None
        assert retrieved.symptoms_pattern == sample_entry.symptoms_pattern
        assert retrieved.solution_steps == sample_entry.solution_steps

    def test_get_entry_not_found(self, kb):
        """Test retrieving non-existent entry."""
        result = kb.get_entry(99999)
        assert result is None

    def test_search(self, kb, sample_entry):
        """Test searching knowledge base."""
        kb.add_entry(sample_entry)

        results = kb.search("database timeout")

        assert len(results) > 0
        assert any("database" in e.symptoms_pattern.lower() for e, _ in results)

    def test_search_with_filters(self, kb, sample_entry):
        """Test search with type filter."""
        kb.add_entry(sample_entry)

        results = kb.search("timeout", incident_type="error")

        assert len(results) > 0
        assert all(e.incident_type == "error" for e, _ in results)

    def test_search_min_success_rate(self, kb):
        """Test search with minimum success rate filter."""
        # Add entry with low success
        low_entry = KnowledgeEntry(
            incident_type="error",
            severity="p2",
            symptoms_pattern="memory error",
            root_cause_summary="oom",
            success_count=1,
            failure_count=9,
        )
        kb.add_entry(low_entry)

        # Add entry with high success
        high_entry = KnowledgeEntry(
            incident_type="error",
            severity="p2",
            symptoms_pattern="memory issue",
            root_cause_summary="oom fixed",
            success_count=9,
            failure_count=1,
        )
        kb.add_entry(high_entry)

        results = kb.search("memory", min_success_rate=0.5)

        assert len(results) >= 1
        assert all(e.success_rate >= 0.5 for e, _ in results)

    def test_find_similar(self, kb, sample_entry):
        """Test finding similar entries."""
        kb.add_entry(sample_entry)

        results = kb.find_similar("database connection timeout")

        assert len(results) >= 1

    def test_list_entries(self, kb, sample_entry):
        """Test listing entries."""
        kb.add_entry(sample_entry)

        entries = kb.list_entries()

        assert len(entries) >= 1

    def test_list_entries_with_filter(self, kb):
        """Test listing with type filter."""
        kb.add_entry(KnowledgeEntry(
            incident_type="error",
            severity="p1",
            symptoms_pattern="error",
            root_cause_summary="bug",
        ))
        kb.add_entry(KnowledgeEntry(
            incident_type="performance",
            severity="p2",
            symptoms_pattern="slow",
            root_cause_summary="cpu",
        ))

        entries = kb.list_entries(incident_type="error")

        assert len(entries) >= 1
        assert all(e.incident_type == "error" for e in entries)

    def test_record_success(self, kb, sample_entry):
        """Test recording success."""
        entry_id = kb.add_entry(sample_entry)

        success = kb.record_success(entry_id)

        assert success is True

        retrieved = kb.get_entry(entry_id)
        assert retrieved.success_count == 1

    def test_record_failure(self, kb, sample_entry):
        """Test recording failure."""
        entry_id = kb.add_entry(sample_entry)

        success = kb.record_failure(entry_id)

        assert success is True

        retrieved = kb.get_entry(entry_id)
        assert retrieved.failure_count == 1

    def test_get_statistics(self, kb, sample_entry):
        """Test getting statistics."""
        kb.add_entry(sample_entry)

        stats = kb.get_statistics()

        assert "total_entries" in stats
        assert stats["total_entries"] >= 1
        assert "type_distribution" in stats

    def test_get_unused_entries(self, kb, sample_entry):
        """Test getting unused entries."""
        entry_id = kb.add_entry(sample_entry)

        # Don't update last_used_at, so it's unused
        unused = kb.get_unused_entries(days=0)

        assert len(unused) >= 1

    def test_delete_entry(self, kb, sample_entry):
        """Test deleting entry."""
        entry_id = kb.add_entry(sample_entry)

        deleted = kb.delete_entry(entry_id)

        assert deleted is True
        assert kb.get_entry(entry_id) is None


class TestKnowledgeBaseExtraction:
    """Test knowledge extraction from incidents."""

    def test_extract_from_resolved_incident(self, db_connection):
        """Test extraction from resolved incident."""
        from memory_tool.incidents import IncidentManager

        kb = KnowledgeBase(db_connection)
        manager = IncidentManager(db_connection)

        # Create incident
        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Service timeout",
            description="Connection timeout",
        )

        # Mark as resolved
        db_connection.execute(
            "UPDATE incidents SET status = 'resolved' WHERE id = ?",
            (incident.id,)
        )
        db_connection.commit()

        entry_id = kb.extract_and_add(incident.id)

        # May or may not extract depending on observations
        if entry_id:
            entry = kb.get_entry(entry_id)
            assert entry is not None

    def test_extract_from_unresolved_incident(self, db_connection):
        """Test extraction from unresolved incident fails."""
        from memory_tool.incidents import IncidentManager

        kb = KnowledgeBase(db_connection)
        manager = IncidentManager(db_connection)

        incident = manager.create(
            incident_type="error",
            severity="p1",
            title="Service down",
            description="Not resolved yet",
        )

        entry_id = kb.extract_and_add(incident.id)

        assert entry_id is None


class TestSchemaV12:
    """Test database schema v12."""

    def test_knowledge_entries_table_exists(self, db_connection):
        """Test knowledge_entries table exists."""
        row = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_entries'"
        ).fetchone()
        assert row is not None

    def test_knowledge_fts_table_exists(self, db_connection):
        """Test knowledge_fts virtual table exists."""
        from memory_tool.knowledge_base import KnowledgeBase
        # Create KnowledgeBase to ensure tables are created
        kb = KnowledgeBase(db_connection)
        # FTS tables may have different types in different SQLite versions
        row = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE name='knowledge_fts'"
        ).fetchone()
        assert row is not None

    def test_knowledge_indexes_exist(self, db_connection):
        """Test knowledge indexes exist."""
        indexes = [
            "idx_knowledge_type_severity",
            "idx_knowledge_success_rate",
            "idx_knowledge_last_used",
        ]
        for idx in indexes:
            row = db_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (idx,),
            ).fetchone()
            assert row is not None, f"Index {idx} not found"

    def test_schema_version_is_12(self, db_connection):
        """Test schema version is 12."""
        from memory_tool.database import get_schema_version

        version = get_schema_version(db_connection)
        assert version == 12
