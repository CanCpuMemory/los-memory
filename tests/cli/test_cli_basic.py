"""Basic CLI tests for los-memory."""
import json
import os
import subprocess
import sys


class TestCLIEntryPoint:
    """Test CLI entry point."""

    def test_cli_help(self):
        """Test CLI help command."""
        result = subprocess.run(
            [sys.executable, "-m", "memory_tool", "--help"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "los-memory" in result.stdout

    def test_cli_version_info(self):
        """Test CLI shows version/help info."""
        result = subprocess.run(
            [sys.executable, "-m", "memory_tool", "--help"],
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
            [sys.executable, "-m", "memory_tool", "--db", str(db_path), "init"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert db_path.exists()

    def test_init_outputs_json(self, tmp_path):
        """Test init command outputs JSON."""
        db_path = tmp_path / "test.db"
        result = subprocess.run(
            [sys.executable, "-m", "memory_tool", "--db", str(db_path), "init"],
            capture_output=True,
            text=True
        )
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert "db" in output

    def test_init_uses_memory_db_path_env(self, tmp_path):
        """Test init command respects MEMORY_DB_PATH when --db is omitted."""
        db_path = tmp_path / "env-test.db"
        env = dict(os.environ)
        env["MEMORY_DB_PATH"] = str(db_path)

        result = subprocess.run(
            [sys.executable, "-m", "memory_tool", "init"],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0
        assert db_path.exists()
        output = json.loads(result.stdout)
        assert output["db"] == str(db_path)


class TestCLIObservation:
    """Test observation commands."""

    def test_observation_add(self, tmp_path):
        """Test observation add command."""
        db_path = tmp_path / "test.db"

        # Initialize DB first
        subprocess.run(
            [sys.executable, "-m", "memory_tool", "--db", str(db_path), "init"],
            capture_output=True
        )

        # Add observation
        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool",
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
            [sys.executable, "-m", "memory_tool", "--db", str(db_path), "init"],
            capture_output=True
        )
        subprocess.run(
            [
                sys.executable, "-m", "memory_tool",
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
                sys.executable, "-m", "memory_tool",
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

    def test_memory_export_json_stdout_is_plain_export_payload(self, tmp_path):
        """Test memory export to stdout does not append CLI wrapper JSON."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool", "--db", str(db_path), "init"],
            capture_output=True,
        )
        subprocess.run(
            [
                sys.executable, "-m", "memory_tool",
                "--db", str(db_path),
                "observation", "add",
                "--title", "Export test",
                "--summary", "payload only",
            ],
            capture_output=True,
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool",
                "--db", str(db_path),
                "memory", "export",
                "--format", "json",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert isinstance(output, list)
        assert output[0]["title"] == "Export test"

    def test_memory_export_csv_stdout_is_plain_csv(self, tmp_path):
        """Test CSV export to stdout does not append structured wrapper output."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool", "--db", str(db_path), "init"],
            capture_output=True,
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool",
                "--db", str(db_path),
                "memory", "export",
                "--format", "csv",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert result.stdout.startswith("id,timestamp,project,kind,title,summary,tags,raw,session_id")
        assert '"ok"' not in result.stdout


class TestCLISession:
    """Test session commands."""

    def test_session_start_stop(self, tmp_path):
        """Test session start and stop commands."""
        db_path = tmp_path / "test.db"

        # Initialize DB
        subprocess.run(
            [sys.executable, "-m", "memory_tool", "--db", str(db_path), "init"],
            capture_output=True
        )

        # Start session
        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool",
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
                sys.executable, "-m", "memory_tool",
                "--db", str(db_path),
                "session", "stop"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        stop_output = json.loads(result.stdout)
        assert stop_output["ok"] is True

    def test_session_show_missing_returns_not_found_exit_code(self, tmp_path):
        """Test session show returns standardized not-found error."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool", "--db", str(db_path), "init"],
            capture_output=True,
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool",
                "--db", str(db_path),
                "--output", "json",
                "session", "show", "999",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 5
        output = json.loads(result.stdout)
        assert output["ok"] is False
        assert output["error_code"] == "NF_SESSION"

    def test_session_stop_without_active_session_returns_not_found_error(self, tmp_path):
        """Test session stop without an active session uses standardized error."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True,
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "--output", "json",
                "session", "stop",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 5
        output = json.loads(result.stdout)
        assert output["ok"] is False
        assert output["error_code"] == "NF_ACTIVE_SESSION"
        assert output["help_command"] == "los-memory session start --help"

    def test_session_list_accepts_ended_status_filter(self, tmp_path):
        """CLI should accept ended as a valid status filter for legacy rows."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True,
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "session", "list",
                "--status", "ended",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert isinstance(output["sessions"], list)

    def test_review_apply_invalid_shape_returns_validation_error(self, tmp_path):
        """Test review apply rejects invalid top-level JSON structure."""
        db_path = tmp_path / "test.db"
        review_path = tmp_path / "review.json"
        review_path.write_text('"invalid"', encoding="utf-8")

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True,
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "--output", "json",
                "review", "apply",
                "--file", str(review_path),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 4
        output = json.loads(result.stdout)
        assert output["ok"] is False
        assert output["error_code"] == "VAL_INVALID_FORMAT"

    def test_list_with_unopenable_db_path_returns_database_error(self, tmp_path):
        """Test regular command DB open failures use standardized DB error codes."""
        db_path = tmp_path

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "--output", "json",
                "memory", "list",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 3
        output = json.loads(result.stdout)
        assert output["ok"] is False
        assert output["error_code"] == "DB_NOT_FOUND"
        assert output["help_command"] == "los-memory init --help"

    def test_tool_log_invalid_json_returns_validation_error(self, tmp_path):
        """Test JSON CLI arguments return standardized validation errors."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True,
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "--output", "json",
                "tool", "log",
                "--tool", "search_files",
                "--input", "{",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 4
        output = json.loads(result.stdout)
        assert output["ok"] is False
        assert output["error_code"] == "VAL_INVALID_FORMAT"

    def test_memory_get_invalid_ids_returns_validation_error(self, tmp_path):
        """Test invalid id lists use standardized validation errors."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True,
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "--output", "json",
                "memory", "get", "abc",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 4
        output = json.loads(result.stdout)
        assert output["ok"] is False
        assert output["error_code"] == "VAL_INVALID_FORMAT"

    def test_memory_clean_conflicting_filters_return_validation_error(self, tmp_path):
        """Test incompatible clean filters use standardized validation errors."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True,
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "--output", "json",
                "memory", "clean",
                "--before", "2026-01-01T00:00:00Z",
                "--older-than-days", "7",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 4
        output = json.loads(result.stdout)
        assert output["ok"] is False
        assert output["error_code"] == "VAL_INVALID_FORMAT"


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

    Legacy flat commands must remain executable while external adapters migrate
    to the nested command structure.
    """

    def test_legacy_add_command(self, tmp_path):
        """Test legacy 'add' command still maps to observation add."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "add",
                "--title", "Test Observation",
                "--summary", "Test"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["ok"] is True

    def test_legacy_search_command(self, tmp_path):
        """Test legacy 'search' command still maps to memory search."""
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

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "search", "Searchable"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["ok"] is True

    def test_legacy_list_command(self, tmp_path):
        """Test legacy 'list' command still maps to memory list."""
        db_path = tmp_path / "test.db"

        subprocess.run(
            [sys.executable, "-m", "memory_tool.cli", "--db", str(db_path), "init"],
            capture_output=True
        )
        subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "add",
                "--title", "Listed Content",
                "--summary", "Test content"
            ],
            capture_output=True
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "memory_tool.cli",
                "--db", str(db_path),
                "list"
            ],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert isinstance(output["results"], list)


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
