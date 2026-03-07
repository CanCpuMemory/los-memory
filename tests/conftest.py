"""Pytest configuration and fixtures for BDD and unit tests."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Generator, List
from unittest.mock import MagicMock, patch

import pytest
from pytest_bdd import given, parsers

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_tool.database import connect_db, ensure_schema, ensure_fts
from memory_tool.utils import tags_to_json, tags_to_text, utc_now


class BDDTestContext:
    """Holds test state across steps."""

    def __init__(self):
        self.db_path: Path | None = None
        self.conn = None
        self.profile: str = "codex"
        self.last_observation_id: int | None = None
        self.last_session_id: int | None = None
        self.last_checkpoint_id: int | None = None
        self.last_export_path: str | None = None
        self.search_results: list = []
        self.feedback_history: list = []
        self.last_feedback_result: dict | None = None
        self.last_tool_stats: dict | None = None
        self.last_tool_suggestions: dict | None = None
        self.observations_by_title: dict[str, int] = {}
        self.last_link_id: int | None = None
        self.last_related: list = []
        self.last_similar: list = []
        self.last_unlink_result: bool = False


@pytest.fixture
def test_context():
    """Create a fresh test context with temporary database."""
    ctx = BDDTestContext()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        ctx.db_path = Path(f.name)
    ctx.conn = connect_db(str(ctx.db_path))
    ensure_schema(ctx.conn)
    ensure_fts(ctx.conn)

    yield ctx

    # Cleanup
    if ctx.conn:
        ctx.conn.close()
    if ctx.db_path and ctx.db_path.exists():
        ctx.db_path.unlink()


# =============================================================================
# Unit Test Fixtures (T016-T023)
# =============================================================================


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    """Provide a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    yield path
    # Cleanup
    if path.exists():
        path.unlink()


@pytest.fixture
def db_connection(temp_db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Provide a connected database with schema initialized."""
    from memory_tool.database import connect_db, ensure_schema, ensure_fts

    conn = connect_db(str(temp_db_path))
    ensure_schema(conn)
    ensure_fts(conn)

    yield conn

    conn.close()


@pytest.fixture
def memory_db() -> Generator[sqlite3.Connection, None, None]:
    """Provide an in-memory database connection."""
    from memory_tool.database import connect_db, ensure_schema, ensure_fts

    conn = connect_db(":memory:")
    ensure_schema(conn)
    ensure_fts(conn)

    yield conn

    conn.close()


@pytest.fixture
def empty_db(temp_db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Provide an empty database connection (no schema)."""
    from memory_tool.database import connect_db

    conn = connect_db(str(temp_db_path))
    yield conn
    conn.close()


@pytest.fixture
def sample_observation_data() -> Dict[str, Any]:
    """Return sample observation data."""
    return {
        "timestamp": "2024-01-15T10:30:00Z",
        "project": "test-project",
        "kind": "note",
        "title": "Test Observation",
        "summary": "This is a test observation for unit testing",
        "tags": ["test", "unit", "sample"],
        "raw": "Raw content of the test observation",
    }


@pytest.fixture
def sample_observations() -> List[Dict[str, Any]]:
    """Return multiple sample observations."""
    return [
        {
            "timestamp": f"2024-01-{15+i:02d}T10:00:00Z",
            "project": "project-a" if i % 2 == 0 else "project-b",
            "kind": "note" if i % 3 == 0 else "decision",
            "title": f"Observation {i+1}",
            "summary": f"Summary for observation {i+1}",
            "tags": [f"tag{i}", "test"],
            "raw": f"Raw content {i+1}",
        }
        for i in range(10)
    ]


@pytest.fixture
def populated_db(db_connection: sqlite3.Connection, sample_observations: List[Dict[str, Any]]) -> sqlite3.Connection:
    """Provide a database pre-populated with sample observations."""
    from memory_tool.operations import add_observation
    from memory_tool.utils import tags_to_json, tags_to_text

    for obs in sample_observations:
        add_observation(
            db_connection,
            obs["timestamp"],
            obs["project"],
            obs["kind"],
            obs["title"],
            obs["summary"],
            tags_to_json(obs["tags"]),
            tags_to_text(obs["tags"]),
            obs["raw"],
        )

    return db_connection


@pytest.fixture
def mock_llm_hook() -> Generator[MagicMock, None, None]:
    """Mock the LLM hook subprocess call."""
    with patch("memory_tool.utils.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "title": "Enhanced Title",
                "summary": "Enhanced summary from LLM",
                "tags": ["ai-generated", "enhanced"]
            }).encode(),
            stderr=b"",
        )
        yield mock_run


@pytest.fixture
def mock_config_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Provide a mock config directory."""
    config_dir = tmp_path / ".config" / "los-memory"
    config_dir.mkdir(parents=True)

    with patch("memory_tool.config.USER_CONFIG_DIR", config_dir):
        with patch("memory_tool.config.USER_CONFIG_PATH", config_dir / "config.yaml"):
            yield config_dir


@pytest.fixture
def isolated_env() -> Generator[None, None, None]:
    """Provide an isolated environment for config tests."""
    # Save original env
    original_env = {k: v for k, v in os.environ.items()}

    # Clear memory-related env vars
    for key in list(os.environ.keys()):
        if key.startswith("MEMORY_"):
            del os.environ[key]

    # Reset config singleton
    from memory_tool.config import reset_config
    reset_config()

    yield

    # Restore original env
    os.environ.clear()
    os.environ.update(original_env)
    reset_config()


@pytest.fixture
def memory_client(temp_db_path: Path) -> Generator["MemoryClient", None, None]:
    """Provide a connected MemoryClient."""
    from memory_tool.client import MemoryClient

    client = MemoryClient(db_path=str(temp_db_path))
    client.connect()

    yield client

    client.close()


@pytest.fixture
def memory_client_with_data(
    memory_client: "MemoryClient", sample_observations: List[Dict[str, Any]]
) -> "MemoryClient":
    """Provide a MemoryClient with sample data."""
    for obs in sample_observations[:3]:  # Add first 3 observations
        memory_client.add(
            title=obs["title"],
            summary=obs["summary"],
            project=obs["project"],
            kind=obs["kind"],
            tags=obs["tags"],
        )
    return memory_client


# =============================================================================
# BDD Helpers
# =============================================================================


def parse_datatable(datatable: list[list[str]]) -> dict[str, str]:
    """Convert pytest-bdd 8.x datatable (list of lists) to dict.

    Assumes first row is header with 'field' and 'value' columns,
    or header with column names that become keys.
    """
    if not datatable:
        return {}
    headers = datatable[0]
    if len(datatable) == 1:
        return {}
    # If table has 'field' and 'value' columns, return dict from those
    if "field" in headers and "value" in headers:
        field_idx = headers.index("field")
        value_idx = headers.index("value")
        return {row[field_idx]: row[value_idx] for row in datatable[1:]}
    # Otherwise, return list of dicts for each row
    return [dict(zip(headers, row)) for row in datatable[1:]]


def parse_datatable_rows(datatable: list[list[str]]) -> list[dict[str, str]]:
    """Convert pytest-bdd 8.x datatable to list of dicts."""
    if not datatable:
        return []
    headers = datatable[0]
    return [dict(zip(headers, row)) for row in datatable[1:]]


# Re-export common step functions for pytest-bdd discovery
# These are defined in steps/common_steps.py but need to be discoverable


@given("a new memory database")
def given_new_memory_database(test_context: BDDTestContext):
    """Database is already created by fixture."""
    pass


@given(parsers.parse('I am using the "{profile}" profile'))
def given_set_profile(test_context: BDDTestContext, profile: str):
    """Set the active profile."""
    test_context.profile = profile


@given(parsers.parse('the active project is "{project}"'))
def given_set_active_project(test_context: BDDTestContext, project: str):
    """Set the active project in context."""
    from memory_tool.projects import set_active_project

    set_active_project(test_context.profile, project)
