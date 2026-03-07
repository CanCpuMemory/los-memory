"""Basic CLI tests for los-memory."""
import json
import subprocess
import sys

import pytest


class TestCLIEntryPoint:
    """Test CLI entry point."""

    def test_cli_help(self):
        """Test CLI help command."""
        # Use cli.py directly since __main__.py doesn't exist
        result = subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--help"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "los-memory" in result.stdout

    def test_cli_version_info(self):
        """Test CLI shows version/help info."""
        result = subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--help"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0


class TestCLIInit:
    """Test init command."""

    def test_init_creates_database(self, tmp_path):
        """Test init command creates database file."""
        db_path = tmp_path / "test.db"
        result = subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert db_path.exists()

    def test_init_outputs_json(self, tmp_path):
        """Test init command outputs JSON."""
        db_path = tmp_path / "test.db"
        result = subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True,
            text=True
        )
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert "db" in output


class TestCLIObservation:
    """Test observation commands."""

    def test_observation_add(self, tmp_path):
        """Test observation add command."""
        db_path = tmp_path / "test.db"

        # Initialize DB first
        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True
        )

        # Add observation
        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "observation", "add",
                "--title", "Test Observation",
                "--summary", "Test summary",
                "--project", "test"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert "id" in output

    def test_observation_list(self, tmp_path):
        """Test observation list command."""
        db_path = tmp_path / "test.db"

        # Initialize and add observation
        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True
        )
        subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "observation", "add",
                "--title", "Test",
                "--summary", "Test"
            ],
            capture_output=True
        )

        # List observations
        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "memory", "list"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert "results" in output


class TestCLISession:
    """Test session commands."""

    def test_session_start_stop(self, tmp_path):
        """Test session start and stop commands."""
        db_path = tmp_path / "test.db"

        # Initialize DB
        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True
        )

        # Start session
        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "session", "start",
                "--project", "test"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        start_output = json.loads(result.stdout)
        assert start_output["ok"] is True
        assert "session_id" in start_output

        # Stop session
        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "session", "stop"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        stop_output = json.loads(result.stdout)
        assert stop_output["ok"] is True


class TestCLIIncident:
    """Test incident commands (Phase 1)."""

    def test_incident_create(self, tmp_path):
        """Test incident create command."""
        db_path = tmp_path / "test.db"

        # Initialize DB
        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True
        )

        # Create incident
        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "incident", "create",
                "--type", "error",
                "--severity", "p1",
                "--title", "Test Incident",
                "--description", "Test description",
                "--project", "test"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["success"] is True
        assert "incident" in output

    def test_incident_list(self, tmp_path):
        """Test incident list command."""
        db_path = tmp_path / "test.db"

        # Initialize and create incident
        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True
        )
        subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "incident", "create",
                "--type", "error",
                "--severity", "p1",
                "--title", "Test",
                "--description", "Test"
            ],
            capture_output=True
        )

        # List incidents
        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "incident", "list"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["success"] is True
        assert "incidents" in output


class TestCLIBackwardCompat:
    """Test backward compatibility with old command names.

    Note: Legacy commands (add, search, etc.) are mapped to new nested commands.
    We test the new command structure which provides equivalent functionality.
    """

    def test_observation_add_equivalent(self, tmp_path):
        """Test 'observation add' command (replacement for legacy 'add')."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True
        )

        # Use new nested command structure
        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "observation", "add",
                "--title", "Test Observation",
                "--summary", "Test"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["ok"] is True

    def test_memory_search_equivalent(self, tmp_path):
        """Test 'memory search' command (replacement for legacy 'search')."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True
        )
        subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "observation", "add",
                "--title", "Searchable Content",
                "--summary", "Test content"
            ],
            capture_output=True
        )

        # Use new nested command structure
        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "memory", "search", "Searchable"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["ok"] is True


class TestCLIErrorHandling:
    """Test CLI error handling."""

    def test_missing_required_argument(self, tmp_path):
        """Test error on missing required argument."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True
        )

        # Try to add observation without required --title
        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "observation", "add",
                "--summary", "No title"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 2  # argparse exit code for missing argument

    def test_invalid_incident_type(self, tmp_path):
        """Test error on invalid incident type."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "incident", "create",
                "--type", "invalid",
                "--severity", "p1",
                "--title", "Test",
                "--description", "Test"
            ],
            capture_output=True,
            text=True
        )
        # Should fail due to invalid choice
        assert result.returncode != 0
