"""Tests for configuration management."""
import os
from pathlib import Path

import pytest

from memory_tool.config import (
    Config,
    ConfigLoader,
    get_config,
    reset_config,
    DEFAULT_PROFILE,
    VALID_PROFILES,
    VALID_OUTPUTS,
    VALID_COLORS,
)


class TestConfigValidation:
    """Test Config dataclass validation."""

    def test_valid_config(self):
        """Test valid configuration passes validation."""
        config = Config(profile="claude", output="json")
        config.validate()  # Should not raise

    def test_invalid_profile(self):
        """Test invalid profile raises ValueError."""
        config = Config(profile="invalid")
        with pytest.raises(ValueError, match="profile"):
            config.validate()

    def test_invalid_output(self):
        """Test invalid output format raises ValueError."""
        config = Config(output="xml")
        with pytest.raises(ValueError, match="output"):
            config.validate()

    def test_invalid_color(self):
        """Test invalid color option raises ValueError."""
        config = Config(color="rainbow")
        with pytest.raises(ValueError, match="color"):
            config.validate()

    def test_to_dict(self):
        """Test Config to_dict method."""
        config = Config(profile="claude", output="json", verbose=True)
        result = config.to_dict()
        assert result["profile"] == "claude"
        assert result["output"] == "json"
        assert result["verbose"] is True

    def test_to_dict_excludes_none(self):
        """Test to_dict excludes None values."""
        config = Config(db_path=None)
        result = config.to_dict()
        assert "db_path" not in result


class TestConfigLoader:
    """Test ConfigLoader functionality."""

    def test_load_default_config(self):
        """Test loading default configuration."""
        reset_config()
        config = ConfigLoader.load()
        assert config.profile == DEFAULT_PROFILE

    def test_cli_args_override_defaults(self):
        """Test CLI args override defaults."""
        config = ConfigLoader.load(cli_args={"profile": "codex"})
        assert config.profile == "codex"

    def test_env_vars_override_files(self, isolated_env, tmp_path):
        """Test environment variables override file configs."""
        config_dir = tmp_path / ".config" / "los-memory"
        config_dir.mkdir(parents=True)

        with patch("memory_tool.config.USER_CONFIG_DIR", config_dir):
            with patch("memory_tool.config.USER_CONFIG_PATH", config_dir / "config.yaml"):
                os.environ["MEMORY_PROFILE"] = "shared"
                config = ConfigLoader.load()
                assert config.profile == "shared"

    def test_boolean_env_var_conversion_true(self, isolated_env):
        """Test boolean env vars are converted (true values)."""
        for val in ["true", "1", "yes", "on"]:
            reset_config()
            os.environ["MEMORY_VERBOSE"] = val
            config = ConfigLoader.load()
            assert config.verbose is True, f"Failed for value: {val}"

    def test_boolean_env_var_conversion_false(self, isolated_env):
        """Test boolean env vars are converted (false values)."""
        for val in ["false", "0", "no", "off", ""]:
            reset_config()
            os.environ["MEMORY_VERBOSE"] = val
            config = ConfigLoader.load()
            assert config.verbose is False, f"Failed for value: {val}"


class TestConfigSingleton:
    """Test config singleton behavior."""

    def test_singleton_returns_same_instance(self):
        """Test that singleton returns the same instance."""
        reset_config()
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

    def test_force_reload_creates_new_instance(self):
        """Test force_reload creates a new instance."""
        reset_config()
        config1 = get_config()
        config2 = get_config(force_reload=True)
        assert config1 is not config2

    def test_cli_args_invalidate_cache(self):
        """Test CLI args invalidate cache."""
        reset_config()
        config1 = get_config()
        config2 = get_config(cli_args={"profile": "codex"})
        assert config1 is not config2
        assert config2.profile == "codex"


class TestValidConstants:
    """Test valid configuration constants."""

    def test_valid_profiles(self):
        """Test valid profiles list."""
        assert "claude" in VALID_PROFILES
        assert "codex" in VALID_PROFILES
        assert "shared" in VALID_PROFILES

    def test_valid_outputs(self):
        """Test valid output formats."""
        assert "json" in VALID_OUTPUTS
        assert "table" in VALID_OUTPUTS
        assert "yaml" in VALID_OUTPUTS

    def test_valid_colors(self):
        """Test valid color options."""
        assert "auto" in VALID_COLORS
        assert "always" in VALID_COLORS
        assert "never" in VALID_COLORS
