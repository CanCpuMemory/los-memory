"""Error code definitions for los-memory.

This module defines a unified error code system with:
- Categorized error prefixes (VAL_*, NF_*, DB_*, CFG_*, SYS_*)
- Human-readable messages
- Actionable suggestions
- HTTP status code mappings

Error Code Format: CATEGORY_SPECIFIC_ERROR
Example: VAL_MISSING_PARAM, DB_NOT_FOUND, SYS_PYTHON_ERROR
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorCategory(Enum):
    """Error code categories."""

    VALIDATION = "VAL"      # Input validation errors
    NOT_FOUND = "NF"        # Resource not found
    DATABASE = "DB"         # Database errors
    CONFIGURATION = "CFG"   # Configuration errors
    SYSTEM = "SYS"          # System/internal errors


@dataclass(frozen=True)
class ErrorCode:
    """Definition of an error code."""

    code: str
    message: str
    suggestion: str
    help_command: Optional[str] = None
    http_status: int = 500
    exit_code: int = 1

    def format_message(self, **kwargs) -> str:
        """Format the error message with provided values."""
        try:
            return self.message.format(**kwargs)
        except KeyError:
            return self.message

    def format_suggestion(self, **kwargs) -> str:
        """Format the suggestion with provided values."""
        try:
            return self.suggestion.format(**kwargs)
        except KeyError:
            return self.suggestion


# =============================================================================
# Validation Errors (VAL_*)
# =============================================================================

VAL_MISSING_PARAM = ErrorCode(
    code="VAL_MISSING_PARAM",
    message="Missing required parameter: {param}",
    suggestion="Provide the required parameter using --{param}",
    help_command="los-memory {command} --help",
    http_status=400,
    exit_code=4,
)

VAL_INVALID_FORMAT = ErrorCode(
    code="VAL_INVALID_FORMAT",
    message="Invalid format for parameter '{param}': {value}",
    suggestion="Check the expected format in the documentation",
    help_command="los-memory {command} --help",
    http_status=400,
    exit_code=4,
)

VAL_INVALID_VALUE = ErrorCode(
    code="VAL_INVALID_VALUE",
    message="Invalid value for '{param}': {value}",
    suggestion="Valid values are: {valid_values}",
    http_status=400,
    exit_code=4,
)

VAL_TOO_LONG = ErrorCode(
    code="VAL_TOO_LONG",
    message="Value for '{param}' exceeds maximum length of {max_length}",
    suggestion="Shorten the value to {max_length} characters or less",
    http_status=400,
    exit_code=4,
)

VAL_EMPTY_VALUE = ErrorCode(
    code="VAL_EMPTY_VALUE",
    message="Value for '{param}' cannot be empty",
    suggestion="Provide a non-empty value",
    http_status=400,
    exit_code=4,
)

# =============================================================================
# Not Found Errors (NF_*)
# =============================================================================

NF_OBSERVATION = ErrorCode(
    code="NF_OBSERVATION",
    message="Observation {id} not found",
    suggestion="Use 'search' to find valid observation IDs",
    help_command="los-memory memory search --help",
    http_status=404,
    exit_code=5,
)

NF_SESSION = ErrorCode(
    code="NF_SESSION",
    message="Session {id} not found",
    suggestion="Use 'session list' to see active sessions",
    help_command="los-memory session list --help",
    http_status=404,
    exit_code=5,
)

NF_CHECKPOINT = ErrorCode(
    code="NF_CHECKPOINT",
    message="Checkpoint {id} not found",
    suggestion="Use 'checkpoint list' to see available checkpoints",
    help_command="los-memory checkpoint list --help",
    http_status=404,
    exit_code=5,
)

NF_PROJECT = ErrorCode(
    code="NF_PROJECT",
    message="Project '{project}' not found",
    suggestion="Use 'project list' to see available projects",
    help_command="los-memory project list --help",
    http_status=404,
    exit_code=5,
)

NF_COMMAND = ErrorCode(
    code="NF_COMMAND",
    message="Unknown command: {command}",
    suggestion="Use 'los-memory --help' to see available commands",
    http_status=404,
    exit_code=127,
)

# =============================================================================
# Database Errors (DB_*)
# =============================================================================

DB_NOT_FOUND = ErrorCode(
    code="DB_NOT_FOUND",
    message="Database file not found: {path}",
    suggestion="Run 'los-memory admin init' to create a new database",
    help_command="los-memory admin init --help",
    http_status=500,
    exit_code=3,
)

DB_LOCKED = ErrorCode(
    code="DB_LOCKED",
    message="Database is locked by another process",
    suggestion="Wait for the other process to finish, or check for stale locks",
    http_status=500,
    exit_code=3,
)

DB_READONLY = ErrorCode(
    code="DB_READONLY",
    message="Database is read-only: {path}",
    suggestion="Check file permissions or use a different database path",
    http_status=500,
    exit_code=3,
)

DB_CORRUPTED = ErrorCode(
    code="DB_CORRUPTED",
    message="Database file appears to be corrupted",
    suggestion="Run 'los-memory admin doctor' to diagnose and repair",
    help_command="los-memory admin doctor --help",
    http_status=500,
    exit_code=3,
)

DB_SCHEMA_MISMATCH = ErrorCode(
    code="DB_SCHEMA_MISMATCH",
    message="Database schema version {current} is incompatible (expected {expected})",
    suggestion="Run 'los-memory admin migrate' to upgrade the database schema",
    help_command="los-memory admin migrate --help",
    http_status=500,
    exit_code=3,
)

DB_QUERY_ERROR = ErrorCode(
    code="DB_QUERY_ERROR",
    message="Database query failed: {details}",
    suggestion="Check the query syntax and try again",
    http_status=500,
    exit_code=3,
)

# =============================================================================
# Configuration Errors (CFG_*)
# =============================================================================

CFG_INVALID_PROFILE = ErrorCode(
    code="CFG_INVALID_PROFILE",
    message="Invalid profile: {profile}",
    suggestion="Valid profiles are: claude, codex, shared",
    http_status=400,
    exit_code=2,
)

CFG_MISSING_CONFIG = ErrorCode(
    code="CFG_MISSING_CONFIG",
    message="Configuration file not found: {path}",
    suggestion="Create a configuration file or use default settings",
    http_status=400,
    exit_code=2,
)

CFG_INVALID_CONFIG = ErrorCode(
    code="CFG_INVALID_CONFIG",
    message="Invalid configuration: {reason}",
    suggestion="Check the configuration file syntax and values",
    http_status=400,
    exit_code=2,
)

CFG_PATH_NOT_FOUND = ErrorCode(
    code="CFG_PATH_NOT_FOUND",
    message="Configuration path does not exist: {path}",
    suggestion="Create the directory or specify a different path",
    http_status=400,
    exit_code=2,
)

# =============================================================================
# System Errors (SYS_*)
# =============================================================================

SYS_PYTHON_ERROR = ErrorCode(
    code="SYS_PYTHON_ERROR",
    message="Internal Python error: {details}",
    suggestion="This is a bug. Please report it with the error details",
    http_status=500,
    exit_code=1,
)

SYS_SQLITE_ERROR = ErrorCode(
    code="SYS_SQLITE_ERROR",
    message="SQLite error: {details}",
    suggestion="Run 'los-memory admin doctor' to diagnose the issue",
    help_command="los-memory admin doctor --help",
    http_status=500,
    exit_code=3,
)

SYS_IO_ERROR = ErrorCode(
    code="SYS_IO_ERROR",
    message="I/O error: {details}",
    suggestion="Check file permissions and disk space",
    http_status=500,
    exit_code=1,
)

SYS_MEMORY_ERROR = ErrorCode(
    code="SYS_MEMORY_ERROR",
    message="Out of memory",
    suggestion="Close other applications or reduce the operation size",
    http_status=500,
    exit_code=1,
)

SYS_INTERRUPTED = ErrorCode(
    code="SYS_INTERRUPTED",
    message="Operation interrupted by user",
    suggestion="The operation was cancelled. No changes were made.",
    http_status=500,
    exit_code=130,  # 128 + SIGINT(2)
)


# =============================================================================
# Error Registry
# =============================================================================

_ERROR_REGISTRY: dict[str, ErrorCode] = {
    # Validation
    "VAL_MISSING_PARAM": VAL_MISSING_PARAM,
    "VAL_INVALID_FORMAT": VAL_INVALID_FORMAT,
    "VAL_INVALID_VALUE": VAL_INVALID_VALUE,
    "VAL_TOO_LONG": VAL_TOO_LONG,
    "VAL_EMPTY_VALUE": VAL_EMPTY_VALUE,
    # Not Found
    "NF_OBSERVATION": NF_OBSERVATION,
    "NF_SESSION": NF_SESSION,
    "NF_CHECKPOINT": NF_CHECKPOINT,
    "NF_PROJECT": NF_PROJECT,
    "NF_COMMAND": NF_COMMAND,
    # Database
    "DB_NOT_FOUND": DB_NOT_FOUND,
    "DB_LOCKED": DB_LOCKED,
    "DB_READONLY": DB_READONLY,
    "DB_CORRUPTED": DB_CORRUPTED,
    "DB_SCHEMA_MISMATCH": DB_SCHEMA_MISMATCH,
    "DB_QUERY_ERROR": DB_QUERY_ERROR,
    # Configuration
    "CFG_INVALID_PROFILE": CFG_INVALID_PROFILE,
    "CFG_MISSING_CONFIG": CFG_MISSING_CONFIG,
    "CFG_INVALID_CONFIG": CFG_INVALID_CONFIG,
    "CFG_PATH_NOT_FOUND": CFG_PATH_NOT_FOUND,
    # System
    "SYS_PYTHON_ERROR": SYS_PYTHON_ERROR,
    "SYS_SQLITE_ERROR": SYS_SQLITE_ERROR,
    "SYS_IO_ERROR": SYS_IO_ERROR,
    "SYS_MEMORY_ERROR": SYS_MEMORY_ERROR,
    "SYS_INTERRUPTED": SYS_INTERRUPTED,
}


def get_error_code(code: str) -> Optional[ErrorCode]:
    """Get an error code definition by its code string.

    Args:
        code: Error code string (e.g., "VAL_MISSING_PARAM")

    Returns:
        ErrorCode definition or None if not found
    """
    return _ERROR_REGISTRY.get(code)


def get_all_error_codes() -> dict[str, ErrorCode]:
    """Get all registered error codes.

    Returns:
        Dictionary mapping error code strings to definitions
    """
    return _ERROR_REGISTRY.copy()


def get_error_codes_by_category(category: ErrorCategory) -> dict[str, ErrorCode]:
    """Get error codes for a specific category.

    Args:
        category: Error category

    Returns:
        Dictionary of error codes in that category
    """
    prefix = category.value + "_"
    return {
        code: definition
        for code, definition in _ERROR_REGISTRY.items()
        if code.startswith(prefix)
    }


def register_error_code(error_code: ErrorCode) -> None:
    """Register a new error code.

    Args:
        error_code: Error code definition to register

    Raises:
        ValueError: If code already exists
    """
    if error_code.code in _ERROR_REGISTRY:
        raise ValueError(f"Error code {error_code.code} already exists")
    _ERROR_REGISTRY[error_code.code] = error_code


def format_error_response(
    error_code: ErrorCode,
    **kwargs
) -> dict:
    """Format an error response dictionary.

    Args:
        error_code: Error code definition
        **kwargs: Values to format into message and suggestion

    Returns:
        Error response dictionary
    """
    response = {
        "ok": False,
        "error": {
            "code": error_code.code,
            "message": error_code.format_message(**kwargs),
            "suggestion": error_code.format_suggestion(**kwargs),
        },
    }

    if error_code.help_command:
        response["error"]["help_command"] = error_code.format_suggestion(
            **kwargs
        )

    return response


__all__ = [
    # Categories
    "ErrorCategory",
    "ErrorCode",
    # Validation errors
    "VAL_MISSING_PARAM",
    "VAL_INVALID_FORMAT",
    "VAL_INVALID_VALUE",
    "VAL_TOO_LONG",
    "VAL_EMPTY_VALUE",
    # Not found errors
    "NF_OBSERVATION",
    "NF_SESSION",
    "NF_CHECKPOINT",
    "NF_PROJECT",
    "NF_COMMAND",
    # Database errors
    "DB_NOT_FOUND",
    "DB_LOCKED",
    "DB_READONLY",
    "DB_CORRUPTED",
    "DB_SCHEMA_MISMATCH",
    "DB_QUERY_ERROR",
    # Configuration errors
    "CFG_INVALID_PROFILE",
    "CFG_MISSING_CONFIG",
    "CFG_INVALID_CONFIG",
    "CFG_PATH_NOT_FOUND",
    # System errors
    "SYS_PYTHON_ERROR",
    "SYS_SQLITE_ERROR",
    "SYS_IO_ERROR",
    "SYS_MEMORY_ERROR",
    "SYS_INTERRUPTED",
    # Functions
    "get_error_code",
    "get_all_error_codes",
    "get_error_codes_by_category",
    "register_error_code",
    "format_error_response",
]
