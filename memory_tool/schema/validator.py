"""Schema validation utilities for los-memory.

This module provides response validation against JSON schemas.
It tries to use jsonschema library if available, falling back to
basic validation otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Union

# Try to import jsonschema, but don't fail if not available
try:
    from jsonschema import ValidationError as SchemaValidationError
    from jsonschema import validate

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False
    SchemaValidationError = Exception

from . import load_schema, SCHEMA_VERSION


class ValidationError(Exception):
    """Validation error with detailed message."""

    def __init__(self, message: str, path: Optional[str] = None, schema: Optional[str] = None):
        self.message = message
        self.path = path
        self.schema = schema
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        parts = [self.message]
        if self.path:
            parts.append(f"at path: {self.path}")
        if self.schema:
            parts.append(f"schema: {self.schema}")
        return " | ".join(parts)


def validate_response(
    data: dict[str, Any],
    schema_name: str,
    strict: bool = False,
) -> None:
    """Validate a response against a schema.

    Args:
        data: Response data to validate
        schema_name: Name of the schema to validate against
        strict: If True, raise exception on validation failure

    Raises:
        ValidationError: If validation fails and strict=True

    Returns:
        None if validation passes
    """
    if not HAS_JSONSCHEMA:
        # Fallback to basic validation
        _basic_validate(data, schema_name, strict)
        return

    try:
        schema = load_schema(schema_name)
        validate(instance=data, schema=schema)
    except SchemaValidationError as e:
        if strict:
            path = ".".join(str(p) for p in e.path) if hasattr(e, "path") else None
            raise ValidationError(
                message=str(e.message) if hasattr(e, "message") else str(e),
                path=path,
                schema=schema_name,
            ) from e


def _basic_validate(
    data: dict[str, Any],
    schema_name: str,
    strict: bool = False,
) -> tuple[bool, list[str]]:
    """Basic validation without jsonschema library.

    Performs minimal checks: type validation and required field presence.
    """
    errors = []

    try:
        schema = load_schema(schema_name)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        if strict:
            raise ValidationError(f"Failed to load schema: {e}", schema=schema_name) from e
        return False, [str(e)]

    # Type check
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(data, dict):
            errors.append(f"Expected object, got {type(data).__name__}")
    elif schema_type == "array":
        if not isinstance(data, list):
            errors.append(f"Expected array, got {type(data).__name__}")
    elif schema_type == "string":
        if not isinstance(data, str):
            errors.append(f"Expected string, got {type(data).__name__}")

    # Check required fields
    if isinstance(data, dict):
        required = schema.get("required", [])
        for field in required:
            if field not in data:
                errors.append(f"Missing required field: {field}")

        # Check property types
        properties = schema.get("properties", {})
        for prop_name, prop_schema in properties.items():
            if prop_name in data:
                prop_value = data[prop_name]
                prop_type = prop_schema.get("type")

                if prop_type == "string" and not isinstance(prop_value, str):
                    if not (prop_value is None and "null" in str(prop_schema.get("type", ""))):
                        errors.append(f"Field '{prop_name}' should be string")
                elif prop_type == "integer" and not isinstance(prop_value, int):
                    if not (prop_value is None and "null" in str(prop_schema.get("type", ""))):
                        errors.append(f"Field '{prop_name}' should be integer")
                elif prop_type == "boolean" and not isinstance(prop_value, bool):
                    errors.append(f"Field '{prop_name}' should be boolean")
                elif prop_type == "array" and not isinstance(prop_value, list):
                    errors.append(f"Field '{prop_name}' should be array")
                elif prop_type == "object" and not isinstance(prop_value, dict):
                    errors.append(f"Field '{prop_name}' should be object")

    if strict and errors:
        raise ValidationError("; ".join(errors), schema=schema_name)

    return len(errors) == 0, errors


def validate_success_response(
    data: dict[str, Any],
    data_schema_name: Optional[str] = None,
    strict: bool = False,
) -> None:
    """Validate a success response (ok=true).

    Args:
        data: Response data
        data_schema_name: Optional schema name for the data field
        strict: If True, raise exception on validation failure
    """
    # First validate against base schema
    validate_response(data, "base", strict)

    # Check ok=true
    if data.get("ok") is not True:
        if strict:
            raise ValidationError("Expected ok=true for success response")

    # Validate data field if schema provided
    if data_schema_name and "data" in data:
        validate_response(data["data"], data_schema_name, strict)


def validate_error_response(
    data: dict[str, Any],
    expected_code: Optional[str] = None,
    strict: bool = False,
) -> None:
    """Validate an error response (ok=false).

    Args:
        data: Response data
        expected_code: Optional expected error code
        strict: If True, raise exception on validation failure
    """
    # First validate against error schema
    validate_response(data, "error", strict)

    # Check ok=false
    if data.get("ok") is not False:
        if strict:
            raise ValidationError("Expected ok=false for error response")

    # Check error code if provided
    if expected_code:
        actual_code = data.get("error", {}).get("code")
        if actual_code != expected_code:
            if strict:
                raise ValidationError(
                    f"Expected error code {expected_code}, got {actual_code}"
                )


def get_schema_info() -> dict[str, Any]:
    """Get information about available schemas.

    Returns:
        Dictionary with schema version and available schemas
    """
    from . import SCHEMA_DIR

    schemas = []
    for schema_file in SCHEMA_DIR.glob("*.schema.json"):
        try:
            with open(schema_file, "r", encoding="utf-8") as f:
                schema_data = json.load(f)
                schemas.append({
                    "name": schema_file.stem.replace(".schema", ""),
                    "title": schema_data.get("title", "Unknown"),
                    "version": schema_data.get("version", "unknown"),
                    "description": schema_data.get("description", ""),
                })
        except (json.JSONDecodeError, IOError):
            continue

    return {
        "schema_version": SCHEMA_VERSION,
        "jsonschema_available": HAS_JSONSCHEMA,
        "schemas": sorted(schemas, key=lambda x: x["name"]),
    }


__all__ = [
    "ValidationError",
    "validate_response",
    "validate_success_response",
    "validate_error_response",
    "get_schema_info",
    "HAS_JSONSCHEMA",
]
