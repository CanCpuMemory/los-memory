"""Exit code definitions for los-memory CLI.

This module defines standardized exit codes for CLI processes according to
the implementation plan specification. It provides integration with the
error code system and helper functions for shell script integration.

Exit Code Specification (from docs/IMPLEMENTATION_PLAN.md):
- 0: Success
- 1: Business error (SYS_*, general errors)
- 2: Configuration error (CFG_*)
- 3: Database error (DB_*)
- 4: Validation error (VAL_*)
- 5: Not found (NF_*)
- 127: Command not found
"""

from __future__ import annotations

from enum import IntEnum
from typing import Optional, Dict, Set

from memory_tool.errors import (
    ErrorCode,
    ErrorCategory,
    get_error_code,
)


class ExitCode(IntEnum):
    """Standardized exit codes for los-memory CLI.

    These codes follow the specification in docs/IMPLEMENTATION_PLAN.md
    and are compatible with Unix exit code conventions.
    """

    SUCCESS = 0
    """Operation completed successfully."""

    BUSINESS_ERROR = 1
    """General business/system error (SYS_* errors)."""

    CONFIG_ERROR = 2
    """Configuration error (CFG_* errors). Run doctor command."""

    DATABASE_ERROR = 3
    """Database error (DB_* errors). Check DB path and permissions."""

    VALIDATION_ERROR = 4
    """Validation error (VAL_* errors). Check parameters."""

    NOT_FOUND = 5
    """Resource not found (NF_* errors). Confirm resource exists."""

    COMMAND_NOT_FOUND = 127
    """Command not found. Check installation."""

    # Aliases for clarity
    OK = 0
    ERROR = 1
    SYS_ERROR = 1


# Mapping from error category to exit code
CATEGORY_TO_EXIT_CODE: Dict[ErrorCategory, int] = {
    ErrorCategory.SYSTEM: ExitCode.BUSINESS_ERROR,
    ErrorCategory.CONFIGURATION: ExitCode.CONFIG_ERROR,
    ErrorCategory.DATABASE: ExitCode.DATABASE_ERROR,
    ErrorCategory.VALIDATION: ExitCode.VALIDATION_ERROR,
    ErrorCategory.NOT_FOUND: ExitCode.NOT_FOUND,
}

# Exit codes that indicate retryable errors
RETRYABLE_EXIT_CODES: Set[int] = {
    ExitCode.DATABASE_ERROR,  # DB_LOCKED can be retried
}

# Exit code descriptions for documentation
EXIT_CODE_DESCRIPTIONS: Dict[int, str] = {
    ExitCode.SUCCESS: "Success",
    ExitCode.BUSINESS_ERROR: "Business error - check error.message",
    ExitCode.CONFIG_ERROR: "Configuration error - run doctor command",
    ExitCode.DATABASE_ERROR: "Database error - check DB path and permissions",
    ExitCode.VALIDATION_ERROR: "Validation error - check parameters",
    ExitCode.NOT_FOUND: "Not found - confirm resource exists",
    ExitCode.COMMAND_NOT_FOUND: "Command not found - check installation",
}

# Exit code handling suggestions
EXIT_CODE_SUGGESTIONS: Dict[int, str] = {
    ExitCode.SUCCESS: "No action needed",
    ExitCode.BUSINESS_ERROR: "View error details in output",
    ExitCode.CONFIG_ERROR: "Run 'los-memory admin doctor' to diagnose",
    ExitCode.DATABASE_ERROR: "Check database file exists and is writable",
    ExitCode.VALIDATION_ERROR: "Review command parameters and try again",
    ExitCode.NOT_FOUND: "Use search or list commands to find valid resources",
    ExitCode.COMMAND_NOT_FOUND: "Verify los-memory is installed and in PATH",
}


def get_exit_code_for_error(error_code: ErrorCode | str) -> int:
    """Get the exit code for a given error code.

    Args:
        error_code: ErrorCode instance or error code string (e.g., "VAL_MISSING_PARAM")

    Returns:
        int: Exit code (0-5, 127)

    Example:
        >>> get_exit_code_for_error("VAL_MISSING_PARAM")
        4
        >>> get_exit_code_for_error(DB_LOCKED)
        3
    """
    if isinstance(error_code, str):
        error_def = get_error_code(error_code)
        if error_def is None:
            return ExitCode.BUSINESS_ERROR
        return error_def.exit_code

    return error_code.exit_code


def get_exit_code_for_category(category: ErrorCategory) -> int:
    """Get the exit code for an error category.

    Args:
        category: Error category enum value

    Returns:
        int: Exit code for that category

    Example:
        >>> get_exit_code_for_category(ErrorCategory.VALIDATION)
        4
    """
    return CATEGORY_TO_EXIT_CODE.get(category, ExitCode.BUSINESS_ERROR)


def is_retryable(exit_code: int) -> bool:
    """Check if an exit code indicates a retryable error.

    Currently only database errors (exit code 3) are considered retryable,
    specifically for cases like DB_LOCKED where waiting and retrying may help.

    Args:
        exit_code: Exit code to check

    Returns:
        bool: True if the error is potentially retryable

    Example:
        >>> is_retryable(3)  # Database error (e.g., locked)
        True
        >>> is_retryable(4)  # Validation error
        False
    """
    return exit_code in RETRYABLE_EXIT_CODES


def get_exit_code_description(exit_code: int) -> str:
    """Get human-readable description for an exit code.

    Args:
        exit_code: Exit code value

    Returns:
        str: Description of the exit code
    """
    return EXIT_CODE_DESCRIPTIONS.get(exit_code, "Unknown error")


def get_exit_code_suggestion(exit_code: int) -> str:
    """Get handling suggestion for an exit code.

    Args:
        exit_code: Exit code value

    Returns:
        str: Suggested action for the exit code
    """
    return EXIT_CODE_SUGGESTIONS.get(exit_code, "Check error details")


def format_exit_summary(exit_code: int, error_code: Optional[str] = None) -> dict:
    """Format a structured exit summary for JSON output.

    Args:
        exit_code: Exit code value
        error_code: Optional error code string

    Returns:
        dict: Structured exit summary
    """
    return {
        "exit_code": exit_code,
        "success": exit_code == ExitCode.SUCCESS,
        "description": get_exit_code_description(exit_code),
        "suggestion": get_exit_code_suggestion(exit_code),
        "retryable": is_retryable(exit_code),
        "error_code": error_code,
    }


# Shell integration helpers
SHELL_INTEGRATION = '''
# los-memory Exit Code Integration for Shell Scripts
# Source this in your .bashrc or .zshrc for helper functions

# Check if last command succeeded
lm_success() {
    return $(( $? == 0 ))
}

# Check if last command failed with specific error
lm_failed_with() {
    local expected_code=$1
    local actual_code=$?
    return $(( actual_code == expected_code ))
}

# Handle los-memory errors automatically
lm_handle_error() {
    local exit_code=$?
    case $exit_code in
        0) return 0 ;;
        1) echo "Error: Business logic failed" >&2 ;;
        2) echo "Error: Configuration issue - run 'los-memory admin doctor'" >&2 ;;
        3) echo "Error: Database issue - check permissions" >&2 ;;
        4) echo "Error: Invalid parameters - check usage" >&2 ;;
        5) echo "Error: Resource not found" >&2 ;;
        127) echo "Error: Command not found - check installation" >&2 ;;
        *) echo "Error: Unknown exit code $exit_code" >&2 ;;
    esac
    return $exit_code
}

# Retry wrapper for database-locked scenarios
lm_with_retry() {
    local max_attempts=3
    local delay=1
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        "$@"
        local exit_code=$?

        if [ $exit_code -eq 0 ]; then
            return 0
        elif [ $exit_code -eq 3 ] && [ $attempt -lt $max_attempts ]; then
            echo "Database locked, retrying in ${delay}s... (attempt $attempt/$max_attempts)" >&2
            sleep $delay
            delay=$((delay * 2))
            attempt=$((attempt + 1))
        else
            return $exit_code
        fi
    done
}
'''


__all__ = [
    "ExitCode",
    "get_exit_code_for_error",
    "get_exit_code_for_category",
    "is_retryable",
    "get_exit_code_description",
    "get_exit_code_suggestion",
    "format_exit_summary",
    "CATEGORY_TO_EXIT_CODE",
    "SHELL_INTEGRATION",
]
