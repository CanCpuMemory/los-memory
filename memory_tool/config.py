"""Configuration management for los-memory.

This module provides hierarchical configuration management with support for:
- Command-line arguments
- Environment variables
- Project local config (./.memory/config.yaml)
- User global config (~/.config/los-memory/config.yaml)
- System default config (/etc/los-memory/config.yaml)
- Hard-coded defaults
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory_tool.errors import (
    CFG_INVALID_PROFILE,
    CFG_INVALID_CONFIG,
    CFG_MISSING_CONFIG,
    format_error_response,
)


# Default configuration values
DEFAULT_PROFILE = "claude"
VALID_PROFILES = ["claude", "codex", "shared"]
VALID_OUTPUTS = ["json", "table", "yaml"]
VALID_COLORS = ["auto", "always", "never"]

# Configuration file paths (in priority order)
PROJECT_CONFIG_PATH = Path(".memory/config.yaml")
USER_CONFIG_DIR = Path.home() / ".config/los-memory"
USER_CONFIG_PATH = USER_CONFIG_DIR / "config.yaml"
SYSTEM_CONFIG_PATH = Path("/etc/los-memory/config.yaml")


@dataclass
class Config:
    """Configuration dataclass with validation."""

    profile: str = DEFAULT_PROFILE
    db_path: Optional[str] = None
    output: str = "json"
    color: str = "auto"
    verbose: bool = False
    debug: bool = False

    # Extra fields from config files
    extra: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate configuration values.

        Raises:
            ValueError: If configuration is invalid
        """
        errors = []

        if self.profile not in VALID_PROFILES:
            errors.append(
                format_error_response(
                    CFG_INVALID_PROFILE,
                    profile=self.profile,
                )["error"]
            )

        if self.output not in VALID_OUTPUTS:
            errors.append(
                format_error_response(
                    CFG_INVALID_CONFIG,
                    reason=f"Invalid output format: {self.output}",
                )["error"]
            )

        if self.color not in VALID_COLORS:
            errors.append(
                format_error_response(
                    CFG_INVALID_CONFIG,
                    reason=f"Invalid color option: {self.color}",
                )["error"]
            )

        if errors:
            raise ValueError(f"Configuration validation failed: {errors}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        result = {
            "profile": self.profile,
            "output": self.output,
            "color": self.color,
            "verbose": self.verbose,
            "debug": self.debug,
        }
        if self.db_path:
            result["db_path"] = self.db_path
        if self.extra:
            result.update(self.extra)
        return result


class ConfigLoader:
    """Load and merge configuration from multiple sources."""

    # Environment variable mapping
    ENV_MAPPING = {
        "MEMORY_PROFILE": "profile",
        "MEMORY_DB_PATH": "db_path",
        "MEMORY_OUTPUT": "output",
        "MEMORY_COLOR": "color",
        "MEMORY_VERBOSE": "verbose",
        "MEMORY_DEBUG": "debug",
    }

    @classmethod
    def load(
        cls,
        cli_args: Optional[Dict[str, Any]] = None,
        validate: bool = True,
    ) -> Config:
        """Load configuration with priority merging.

        Priority (high to low):
        1. CLI arguments
        2. Environment variables
        3. Project local config
        4. User global config
        5. System default config
        6. Hard-coded defaults

        Args:
            cli_args: Command-line arguments
            validate: Whether to validate the configuration

        Returns:
            Config instance
        """
        # Start with defaults
        config = Config()

        # Load system config
        if SYSTEM_CONFIG_PATH.exists():
            config = cls._merge_config(config, cls._load_yaml(SYSTEM_CONFIG_PATH))

        # Load user config
        if USER_CONFIG_PATH.exists():
            config = cls._merge_config(config, cls._load_yaml(USER_CONFIG_PATH))

        # Load project config
        if PROJECT_CONFIG_PATH.exists():
            config = cls._merge_config(config, cls._load_yaml(PROJECT_CONFIG_PATH))

        # Load environment variables
        config = cls._merge_env(config)

        # Override with CLI args
        if cli_args:
            config = cls._merge_config(config, cli_args)

        if validate:
            config.validate()

        return config

    @classmethod
    def _load_yaml(cls, path: Path) -> Dict[str, Any]:
        """Load YAML configuration file.

        Args:
            path: Path to YAML file

        Returns:
            Dictionary with configuration values
        """
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            # Fallback to simple parsing if yaml not available
            return cls._parse_simple_config(path)
        except Exception as e:
            raise ValueError(f"Failed to load config from {path}: {e}")

    @classmethod
    def _parse_simple_config(cls, path: Path) -> Dict[str, Any]:
        """Parse simple key=value config file.

        Fallback when YAML is not available.
        """
        result = {}
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    result[key.strip()] = value.strip().strip('"\'')
        return result

    @classmethod
    def _merge_config(cls, config: Config, overrides: Dict[str, Any]) -> Config:
        """Merge configuration overrides.

        Args:
            config: Base configuration
            overrides: Override values

        Returns:
            New Config with merged values
        """
        data = config.to_dict()
        data.update(overrides)

        # Filter to known fields
        known_fields = {"profile", "db_path", "output", "color", "verbose", "debug"}
        extra = {k: v for k, v in data.items() if k not in known_fields}

        return Config(
            profile=data.get("profile", DEFAULT_PROFILE),
            db_path=data.get("db_path"),
            output=data.get("output", "json"),
            color=data.get("color", "auto"),
            verbose=data.get("verbose", False),
            debug=data.get("debug", False),
            extra=extra,
        )

    @classmethod
    def _merge_env(cls, config: Config) -> Config:
        """Merge environment variables into configuration.

        Args:
            config: Base configuration

        Returns:
            Config with environment overrides
        """
        overrides = {}

        for env_var, config_key in cls.ENV_MAPPING.items():
            value = os.environ.get(env_var)
            if value is not None:
                # Convert boolean strings
                if config_key in ("verbose", "debug"):
                    value = value.lower() in ("true", "1", "yes", "on")
                overrides[config_key] = value

        if overrides:
            return cls._merge_config(config, overrides)
        return config

    @classmethod
    def ensure_user_config_dir(cls) -> None:
        """Ensure user configuration directory exists."""
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def save_user_config(cls, config: Config) -> None:
        """Save configuration to user config file.

        Args:
            config: Configuration to save
        """
        cls.ensure_user_config_dir()

        data = config.to_dict()
        # Don't save extra fields to user config
        data.pop("extra", None)

        try:
            import yaml
            with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False)
        except ImportError:
            # Fallback to simple format
            with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write("# los-memory configuration\n")
                for key, value in data.items():
                    f.write(f"{key}: {value}\n")


# Singleton instance
_config_instance: Optional[Config] = None


def get_config(
    cli_args: Optional[Dict[str, Any]] = None,
    force_reload: bool = False,
) -> Config:
    """Get configuration singleton.

    Args:
        cli_args: Optional CLI arguments to override
        force_reload: Force reload configuration

    Returns:
        Config instance
    """
    global _config_instance

    if _config_instance is None or force_reload or cli_args:
        _config_instance = ConfigLoader.load(cli_args)

    return _config_instance


def reset_config() -> None:
    """Reset configuration singleton.

    Useful for testing.
    """
    global _config_instance
    _config_instance = None


def get_db_path(config: Optional[Config] = None) -> str:
    """Get database path for current configuration.

    Args:
        config: Optional configuration (uses singleton if None)

    Returns:
        Database path
    """
    from memory_tool.utils import get_profile_db_path

    if config is None:
        config = get_config()

    if config.db_path:
        return config.db_path

    return get_profile_db_path(config.profile)


__all__ = [
    "Config",
    "ConfigLoader",
    "get_config",
    "reset_config",
    "get_db_path",
    "DEFAULT_PROFILE",
    "VALID_PROFILES",
    "VALID_OUTPUTS",
    "VALID_COLORS",
]
