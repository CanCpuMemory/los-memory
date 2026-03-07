"""Doctor command for environment diagnostics.

This module provides comprehensive environment health checks for los-memory,
including Python environment, SQLite, database, profile, and functional checks.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .output import JSONResponse, success, error
from .schema import SCHEMA_VERSION
from .errors import (
    DB_NOT_FOUND,
    DB_SCHEMA_MISMATCH,
    CFG_INVALID_PROFILE,
    format_error_response,
)


@dataclass
class Check:
    """Definition of a health check."""
    name: str
    description: str
    category: str
    priority: str  # "P0" or "P1"
    auto_fixable: bool = False
    check_func: Optional[Callable[..., Tuple[bool, str, Optional[str]]]] = None


@dataclass
class CheckResult:
    """Result of a health check."""
    name: str
    ok: bool
    message: str
    suggestion: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


# Registry of all checks
_CHECK_REGISTRY: List[Check] = []


def register_check(
    name: str,
    description: str,
    category: str,
    priority: str = "P0",
    auto_fixable: bool = False,
) -> Callable:
    """Decorator to register a health check."""
    def decorator(func: Callable) -> Callable:
        check = Check(
            name=name,
            description=description,
            category=category,
            priority=priority,
            auto_fixable=auto_fixable,
            check_func=func,
        )
        _CHECK_REGISTRY.append(check)
        return func
    return decorator


def get_all_checks() -> List[Check]:
    """Get all registered checks sorted by priority."""
    return sorted(_CHECK_REGISTRY, key=lambda c: (0 if c.priority == "P0" else 1, c.name))


# =============================================================================
# Python Environment Checks
# =============================================================================

@register_check(
    name="python_version",
    description="Python version >= 3.8",
    category="python",
    priority="P0",
)
def check_python_version() -> Tuple[bool, str, Optional[str]]:
    """Check Python version."""
    version = sys.version_info
    ok = version.major >= 3 and version.minor >= 8
    message = f"Python {version.major}.{version.minor}.{version.micro}"
    suggestion = None if ok else "Upgrade to Python 3.8 or later"
    return ok, message, suggestion


@register_check(
    name="python_stdlib",
    description="Required standard library modules available",
    category="python",
    priority="P0",
)
def check_python_stdlib() -> Tuple[bool, str, Optional[str]]:
    """Check required standard library modules."""
    required = ["sqlite3", "json", "dataclasses", "pathlib"]
    missing = []
    for module in required:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)

    ok = len(missing) == 0
    message = "All required modules available" if ok else f"Missing modules: {', '.join(missing)}"
    suggestion = None if ok else "Check Python installation"
    return ok, message, suggestion


# =============================================================================
# SQLite Checks
# =============================================================================

@register_check(
    name="sqlite_version",
    description="SQLite version >= 3.25",
    category="sqlite",
    priority="P0",
)
def check_sqlite_version() -> Tuple[bool, str, Optional[str]]:
    """Check SQLite version."""
    version = sqlite3.sqlite_version_info
    ok = version[0] > 3 or (version[0] == 3 and version[1] >= 25)
    message = f"SQLite {version[0]}.{version[1]}.{version[2]}"
    suggestion = None if ok else "Upgrade SQLite to 3.25 or later"
    return ok, message, suggestion


@register_check(
    name="sqlite_fts5",
    description="FTS5 extension available",
    category="sqlite",
    priority="P0",
)
def check_sqlite_fts5() -> Tuple[bool, str, Optional[str]]:
    """Check FTS5 extension."""
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE test USING fts5(content)")
        conn.execute("DROP TABLE test")
        conn.close()
        return True, "FTS5 extension available", None
    except sqlite3.OperationalError as e:
        return False, f"FTS5 not available: {e}", "Recompile SQLite with FTS5 enabled"


@register_check(
    name="sqlite_wal",
    description="WAL journal mode supported",
    category="sqlite",
    priority="P1",
    auto_fixable=True,
)
def check_sqlite_wal(conn: Optional[sqlite3.Connection] = None) -> Tuple[bool, str, Optional[str]]:
    """Check WAL mode support."""
    try:
        test_conn = conn or sqlite3.connect(":memory:")
        test_conn.execute("PRAGMA journal_mode=WAL")
        result = test_conn.execute("PRAGMA journal_mode").fetchone()[0]
        if not conn:
            test_conn.close()
        ok = result.upper() == "WAL"
        return ok, f"Journal mode: {result}", None if ok else "Enable WAL mode"
    except sqlite3.Error as e:
        return False, f"WAL mode error: {e}", "Check SQLite configuration"


# =============================================================================
# Database Checks
# =============================================================================

@register_check(
    name="db_exists",
    description="Database file exists",
    category="database",
    priority="P0",
    auto_fixable=True,
)
def check_db_exists(db_path: str) -> Tuple[bool, str, Optional[str]]:
    """Check database file exists."""
    path = Path(db_path)
    ok = path.exists()
    message = f"Database: {db_path}"
    suggestion = None if ok else f"Run 'los-memory admin init' to create database"
    return ok, message, suggestion


@register_check(
    name="db_readable",
    description="Database is readable",
    category="database",
    priority="P0",
)
def check_db_readable(db_path: str) -> Tuple[bool, str, Optional[str]]:
    """Check database is readable."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("SELECT 1")
        conn.close()
        return True, "Database is readable", None
    except sqlite3.Error as e:
        return False, f"Cannot read database: {e}", "Check file permissions"


@register_check(
    name="db_writable",
    description="Database is writable",
    category="database",
    priority="P0",
)
def check_db_writable(db_path: str) -> Tuple[bool, str, Optional[str]]:
    """Check database is writable."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        conn.close()
        return True, "Database is writable", None
    except sqlite3.Error as e:
        return False, f"Cannot write database: {e}", "Check file permissions and disk space"


@register_check(
    name="db_schema_version",
    description="Database schema version is current",
    category="database",
    priority="P0",
    auto_fixable=True,
)
def check_db_schema_version(conn: sqlite3.Connection) -> Tuple[bool, str, Optional[str]]:
    """Check database schema version."""
    from .database import get_schema_version

    try:
        version = get_schema_version(conn)
        ok = version == SCHEMA_VERSION
        message = f"Schema version: {version} (expected {SCHEMA_VERSION})"
        suggestion = None if ok else f"Run 'los-memory admin migrate' to upgrade"
        return ok, message, suggestion
    except sqlite3.Error as e:
        return False, f"Schema check failed: {e}", "Run 'los-memory admin init'"


@register_check(
    name="db_integrity",
    description="Database integrity check passed",
    category="database",
    priority="P1",
)
def check_db_integrity(conn: sqlite3.Connection) -> Tuple[bool, str, Optional[str]]:
    """Check database integrity."""
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        ok = result == "ok"
        return ok, f"Integrity: {result}", None if ok else "Database may be corrupted"
    except sqlite3.Error as e:
        return False, f"Integrity check failed: {e}", "Check database file"


@register_check(
    name="db_tables",
    description="Required tables exist",
    category="database",
    priority="P0",
    auto_fixable=True,
)
def check_db_tables(conn: sqlite3.Connection) -> Tuple[bool, str, Optional[str]]:
    """Check required tables exist."""
    required = ["observations", "sessions", "meta"]
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    existing = {row[0] for row in cursor.fetchall()}
    missing = [t for t in required if t not in existing]

    ok = len(missing) == 0
    message = f"Tables: {', '.join(existing & set(required))}"
    suggestion = None if ok else f"Missing tables: {', '.join(missing)}. Run 'los-memory admin init'"
    return ok, message, suggestion


# =============================================================================
# Profile Checks
# =============================================================================

@register_check(
    name="profile_valid",
    description="Profile configuration is valid",
    category="profile",
    priority="P0",
    auto_fixable=True,
)
def check_profile_valid(profile: str) -> Tuple[bool, str, Optional[str]]:
    """Check profile is valid."""
    valid_profiles = ["claude", "codex", "shared"]
    ok = profile in valid_profiles
    message = f"Profile: {profile}"
    suggestion = None if ok else f"Valid profiles: {', '.join(valid_profiles)}"
    return ok, message, suggestion


@register_check(
    name="profile_path",
    description="Profile database path is valid",
    category="profile",
    priority="P0",
)
def check_profile_path(profile: str) -> Tuple[bool, str, Optional[str]]:
    """Check profile database path can be resolved."""
    from .utils import get_profile_db_path

    try:
        path = get_profile_db_path(profile)
        ok = True
        message = f"Database path: {path}"
        suggestion = None
    except Exception as e:
        ok = False
        message = f"Cannot resolve database path for profile '{profile}'"
        suggestion = str(e)

    return ok, message, suggestion


@register_check(
    name="disk_space",
    description="Sufficient disk space available",
    category="profile",
    priority="P1",
)
def check_disk_space(db_path: str) -> Tuple[bool, str, Optional[str]]:
    """Check disk space."""
    try:
        directory = os.path.dirname(db_path) or "."
        stat = os.statvfs(directory)
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
        ok = free_gb >= 0.5  # At least 500MB
        message = f"Free space: {free_gb:.1f} GB"
        suggestion = None if ok else "Free up disk space"
        return ok, message, suggestion
    except Exception as e:
        return False, f"Cannot check disk space: {e}", "Check disk permissions"


# =============================================================================
# Functional Checks
# =============================================================================

@register_check(
    name="fts_index",
    description="FTS index is healthy",
    category="functional",
    priority="P1",
    auto_fixable=True,
)
def check_fts_index(conn: sqlite3.Connection) -> Tuple[bool, str, Optional[str]]:
    """Check FTS index health."""
    try:
        # Check if FTS table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='observations_fts'"
        )
        if not cursor.fetchone():
            return False, "FTS table not found", "Rebuild FTS index"

        # Try a simple FTS query
        conn.execute("SELECT * FROM observations_fts LIMIT 1")
        return True, "FTS index is healthy", None
    except sqlite3.Error as e:
        return False, f"FTS index error: {e}", "Rebuild FTS index with 'los-memory admin rebuild-fts'"


# =============================================================================
# Doctor Report
# =============================================================================

def run_all_checks(
    db_path: str,
    profile: str,
    conn: Optional[sqlite3.Connection] = None,
    fix: bool = False,
) -> Dict[str, Any]:
    """Run all health checks and generate report.

    Args:
        db_path: Path to database file
        profile: Profile name
        conn: Optional database connection
        fix: Whether to attempt auto-fixes

    Returns:
        Doctor report dictionary
    """
    checks = get_all_checks()
    results: List[CheckResult] = []
    warnings: List[Dict[str, str]] = []
    suggestions: List[str] = []

    capabilities = {
        "can_read": True,
        "can_write": True,
        "can_search": True,
        "can_migrate": True,
    }

    for check in checks:
        if check.check_func is None:
            continue

        # Run check with appropriate arguments
        try:
            if check.name in ["db_exists", "db_readable", "db_writable"]:
                ok, message, suggestion = check.check_func(db_path)
            elif check.name in ["db_schema_version", "db_integrity", "db_tables", "fts_index"]:
                if conn:
                    ok, message, suggestion = check.check_func(conn)
                else:
                    ok = False
                    message = "No database connection"
                    suggestion = "Check database path"
            elif check.name in ["profile_valid", "profile_path"]:
                ok, message, suggestion = check.check_func(profile)
            elif check.name == "disk_space":
                ok, message, suggestion = check.check_func(db_path)
            elif check.name == "sqlite_wal":
                ok, message, suggestion = check.check_func(conn if conn else None)
            else:
                ok, message, suggestion = check.check_func()
        except Exception as e:
            ok = False
            message = f"Check failed: {e}"
            suggestion = "Check logs for details"

        result = CheckResult(
            name=check.name,
            ok=ok,
            message=message,
            suggestion=suggestion,
            details={
                "category": check.category,
                "priority": check.priority,
                "auto_fixable": check.auto_fixable,
            },
        )
        results.append(result)

        if not ok:
            if check.priority == "P0":
                if suggestion:
                    suggestions.append(suggestion)
            else:
                warnings.append({
                    "code": check.name.upper(),
                    "message": message,
                })

            # Update capabilities
            if check.category == "database" and check.name in ["db_readable", "db_tables"]:
                capabilities["can_read"] = False
            if check.category == "database" and check.name == "db_writable":
                capabilities["can_write"] = False
            if check.name == "fts_index":
                capabilities["can_search"] = False
            if check.name == "db_schema_version":
                capabilities["can_migrate"] = True  # Can migrate even if outdated

    # Determine overall status
    p0_failures = [r for r in results if not r.ok and r.details.get("priority") == "P0"]
    p1_failures = [r for r in results if not r.ok and r.details.get("priority") == "P1"]

    if p0_failures:
        status = "unhealthy"
        overall_ok = False
    elif p1_failures:
        status = "degraded"
        overall_ok = True
    else:
        status = "healthy"
        overall_ok = True

    # Build check results by category
    checks_by_category: Dict[str, Dict[str, Any]] = {}
    for result in results:
        category = result.details["category"]
        if category not in checks_by_category:
            checks_by_category[category] = {}
        checks_by_category[category][result.name] = {
            "ok": result.ok,
            "message": result.message,
        }
        if result.suggestion:
            checks_by_category[category][result.name]["suggestion"] = result.suggestion

    return {
        "ok": overall_ok,
        "status": status,
        "capabilities": capabilities,
        "checks": checks_by_category,
        "warnings": warnings,
        "suggestions": suggestions,
    }


def format_human_output(report: Dict[str, Any]) -> str:
    """Format report for human-readable output."""
    lines = []

    # Status header
    status = report["status"]
    if status == "healthy":
        lines.append("✓ All checks passed!")
    elif status == "degraded":
        lines.append("⚠ System is degraded (warnings present)")
    else:
        lines.append("✗ System is unhealthy")

    lines.append("")

    # Capabilities
    lines.append("Capabilities:")
    for cap, enabled in report["capabilities"].items():
        icon = "✓" if enabled else "✗"
        lines.append(f"  {icon} {cap}")

    lines.append("")

    # Checks by category
    for category, checks in report["checks"].items():
        lines.append(f"{category.upper()}:")
        for name, result in checks.items():
            icon = "✓" if result["ok"] else "✗"
            lines.append(f"  {icon} {name}: {result['message']}")
        lines.append("")

    # Warnings
    if report["warnings"]:
        lines.append("Warnings:")
        for warning in report["warnings"]:
            lines.append(f"  ⚠ {warning['code']}: {warning['message']}")
        lines.append("")

    # Suggestions
    if report["suggestions"]:
        lines.append("Suggestions:")
        for suggestion in set(report["suggestions"]):
            lines.append(f"  → {suggestion}")

    return "\n".join(lines)


def doctor_command(
    db_path: str,
    profile: str,
    conn: Optional[sqlite3.Connection] = None,
    fix: bool = False,
    human: bool = False,
) -> JSONResponse:
    """Run doctor command and return response.

    Args:
        db_path: Path to database file
        profile: Profile name
        conn: Optional database connection
        fix: Whether to attempt auto-fixes
        human: Whether to use human-readable output

    Returns:
        JSONResponse with doctor report
    """
    report = run_all_checks(db_path, profile, conn, fix)

    if human:
        output = format_human_output(report)
        print(output)
        return JSONResponse(ok=report["ok"], meta={})  # Human output is printed directly

    return success(
        data=report,
        profile=profile,
        db_path=db_path,
    )


__all__ = [
    "Check",
    "CheckResult",
    "register_check",
    "get_all_checks",
    "run_all_checks",
    "doctor_command",
    "format_human_output",
]
