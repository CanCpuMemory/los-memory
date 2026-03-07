"""JSON Schema definitions for los-memory CLI.

This package contains JSON Schema files defining the structure of all
CLI command responses. Schemas follow draft-07 specification.

Schema Version: 1.0.0
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

# Schema version follows semver: MAJOR.MINOR.PATCH
# MAJOR: Breaking changes to response structure
# MINOR: Additive changes (new optional fields)
# PATCH: Documentation fixes, no structural changes
SCHEMA_VERSION = "1.0.0"

# Schema directory
SCHEMA_DIR = Path(__file__).parent


def load_schema(name: str) -> dict[str, Any]:
    """Load a JSON schema by name.

    Args:
        name: Schema name without extension (e.g., "base", "observation")

    Returns:
        Parsed schema as dictionary

    Raises:
        FileNotFoundError: If schema file doesn't exist
        json.JSONDecodeError: If schema file is invalid JSON
    """
    schema_path = SCHEMA_DIR / f"{name}.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_all_schemas() -> dict[str, dict[str, Any]]:
    """Load all available schemas.

    Returns:
        Dictionary mapping schema names to schema definitions
    """
    schemas = {}
    for schema_file in SCHEMA_DIR.glob("*.schema.json"):
        name = schema_file.stem.replace(".schema", "")
        with open(schema_file, "r", encoding="utf-8") as f:
            schemas[name] = json.load(f)
    return schemas


def validate_response(data: dict[str, Any], schema_name: str) -> tuple[bool, Optional[str]]:
    """Validate a response against a schema.

    This is a lightweight validation that checks basic structure.
    For full validation, use jsonschema library.

    Args:
        data: Response data to validate
        schema_name: Name of the schema to validate against

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        schema = load_schema(schema_name)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return False, f"Failed to load schema: {e}"

    # Basic type check
    if schema.get("type") == "object":
        if not isinstance(data, dict):
            return False, f"Expected object, got {type(data).__name__}"

        # Check required fields
        required = schema.get("required", [])
        for field in required:
            if field not in data:
                return False, f"Missing required field: {field}"

    return True, None


# Convenience references to common schemas
BASE_SCHEMA = "base"
OBSERVATION_SCHEMA = "observation"
SEARCH_RESULT_SCHEMA = "search-result"
DOCTOR_SCHEMA = "doctor"
ERROR_SCHEMA = "error"
SESSION_SCHEMA = "session"
CHECKPOINT_SCHEMA = "checkpoint"
TOOL_CALL_SCHEMA = "tool-call"
FEEDBACK_SCHEMA = "feedback"
OBSERVATION_LINK_SCHEMA = "observation-link"

__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_DIR",
    "load_schema",
    "get_all_schemas",
    "validate_response",
    "BASE_SCHEMA",
    "OBSERVATION_SCHEMA",
    "SEARCH_RESULT_SCHEMA",
    "DOCTOR_SCHEMA",
    "ERROR_SCHEMA",
    "SESSION_SCHEMA",
    "CHECKPOINT_SCHEMA",
    "TOOL_CALL_SCHEMA",
    "FEEDBACK_SCHEMA",
    "OBSERVATION_LINK_SCHEMA",
]
