# los-memory Doctor Command Design

## Overview

The `doctor` command provides comprehensive environment health checks for los-memory, diagnosing common issues and providing actionable fix suggestions.

## Check Items

### 1. Python Environment Checks

| Check | Description | Severity | Auto-fixable |
|-------|-------------|----------|--------------|
| `python_version` | Python >= 3.9 | ERROR | No |
| `sqlite_version` | SQLite >= 3.35.0 (FTS5 support) | ERROR | No |
| `sqlite_fts5` | FTS5 extension available | WARNING | No |

### 2. Database Checks

| Check | Description | Severity | Auto-fixable |
|-------|-------------|----------|--------------|
| `db_path_exists` | Database directory exists | ERROR | Yes |
| `db_path_writable` | Database directory writable | ERROR | No |
| `db_file_readable` | Database file readable | ERROR | No |
| `db_schema_version` | Schema version matches tool | ERROR | Partial |
| `db_corruption` | Database integrity check | CRITICAL | No |
| `db_size` | Database size reasonable (< 1GB) | INFO | No |

### 3. Profile Checks

| Check | Description | Severity | Auto-fixable |
|-------|-------------|----------|--------------|
| `profile_valid` | Profile name valid | ERROR | No |
| `profile_env` | MEMORY_PROFILE env valid | WARNING | No |
| `profile_path_expansion` | Path expansion works | ERROR | Yes |

### 4. Session/State Checks

| Check | Description | Severity | Auto-fixable |
|-------|-------------|----------|--------------|
| `active_session_valid` | Active session exists in DB | WARNING | Yes |
| `active_project_valid` | Active project set | INFO | No |

### 5. Permission Checks

| Check | Description | Severity | Auto-fixable |
|-------|-------------|----------|--------------|
| `parent_dir_writable` | Parent directory writable | ERROR | No |
| `temp_dir_writable` | Temp directory writable | WARNING | No |

## Severity Levels

- `CRITICAL` - Data loss risk, immediate attention required
- `ERROR` - Functionality broken, must fix
- `WARNING` - Suboptimal state, should fix
- `INFO` - Informational, no action needed

## Output Formats

### Human-Readable (Default)

```
$ memory_tool doctor

🔍 los-memory Environment Health Check
======================================

✅ Python Environment
   Python 3.11.4 (>= 3.9) ........................ OK
   SQLite 3.39.5 (>= 3.35.0) ..................... OK
   FTS5 support .................................. OK

✅ Database
   Path: ~/.codex_memory/memory.db
   Directory exists .............................. OK
   Directory writable ............................ OK
   Database readable ............................. OK
   Schema version: 6 (current) ................... OK
   Integrity check ............................... OK
   Size: 12.4 MB ................................. OK

⚠️  Profile Configuration
   Profile: codex ................................ OK
   MEMORY_PROFILE env ............................ NOT SET
   → Suggestion: Set MEMORY_PROFILE=codex in your shell profile

✅ Session State
   Active session ................................ NONE
   Active project ................................ general

======================================
Result: HEALTHY (4 OK, 1 warning, 0 errors)
```

### JSON Output (--json)

```json
{
  "schema_version": "1.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "status": "healthy",
  "summary": {
    "total": 15,
    "ok": 13,
    "warning": 1,
    "error": 0,
    "critical": 0,
    "info": 1
  },
  "checks": [
    {
      "id": "python_version",
      "category": "environment",
      "name": "Python Version",
      "status": "ok",
      "severity": "error",
      "message": "Python 3.11.4 meets requirement (>= 3.9)",
      "details": {
        "current": "3.11.4",
        "required": ">= 3.9"
      }
    },
    {
      "id": "sqlite_fts5",
      "category": "environment",
      "name": "SQLite FTS5",
      "status": "ok",
      "severity": "warning",
      "message": "FTS5 extension is available"
    },
    {
      "id": "db_path_writable",
      "category": "database",
      "name": "Database Path Writable",
      "status": "ok",
      "severity": "error",
      "message": "Directory ~/.codex_memory is writable",
      "details": {
        "path": "~/.codex_memory/memory.db",
        "expanded": "/home/user/.codex_memory/memory.db"
      }
    },
    {
      "id": "db_schema_version",
      "category": "database",
      "name": "Schema Version",
      "status": "ok",
      "severity": "error",
      "message": "Schema is up to date",
      "details": {
        "current": 6,
        "expected": 6
      }
    },
    {
      "id": "profile_env",
      "category": "profile",
      "name": "MEMORY_PROFILE Environment",
      "status": "warning",
      "severity": "warning",
      "message": "MEMORY_PROFILE not set, using default",
      "suggestion": "Set MEMORY_PROFILE=codex in your shell profile for consistency",
      "details": {
        "current": null,
        "default": "codex",
        "resolved": "codex"
      }
    },
    {
      "id": "active_session_valid",
      "category": "session",
      "name": "Active Session Valid",
      "status": "info",
      "severity": "warning",
      "message": "No active session",
      "details": {
        "session_id": null
      }
    }
  ],
  "fixes_available": [
    {
      "check_id": "profile_env",
      "description": "Add MEMORY_PROFILE to shell profile",
      "command": "echo 'export MEMORY_PROFILE=codex' >> ~/.bashrc",
      "risk": "low"
    }
  ]
}
```

## Error Response Format

When doctor command itself fails:

```json
{
  "schema_version": "1.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "status": "error",
  "error": {
    "code": "DOCTOR_EXECUTION_FAILED",
    "message": "Failed to execute database integrity check",
    "details": "sqlite3.OperationalError: database is locked"
  }
}
```

## Implementation Pseudocode

```python
"""Doctor command implementation for los-memory."""
from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Severity(str, Enum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class CheckStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class CheckResult:
    id: str
    category: str
    name: str
    status: CheckStatus
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    suggestion: Optional[str] = None
    auto_fix: Optional[str] = None


@dataclass
class DoctorReport:
    schema_version: str = "1.0"
    timestamp: str = ""
    status: str = "healthy"  # healthy, degraded, unhealthy
    summary: dict[str, int] = field(default_factory=dict)
    checks: list[CheckResult] = field(default_factory=list)
    fixes_available: list[dict] = field(default_factory=list)


def check_python_version() -> CheckResult:
    """Check Python version >= 3.9."""
    version_info = sys.version_info
    current = f"{version_info.major}.{version_info.minor}.{version_info.micro}"
    required = (3, 9)

    if version_info >= required:
        return CheckResult(
            id="python_version",
            category="environment",
            name="Python Version",
            status=CheckStatus.OK,
            severity=Severity.ERROR,
            message=f"Python {current} meets requirement (>= 3.9)",
            details={"current": current, "required": ">= 3.9"}
        )
    else:
        return CheckResult(
            id="python_version",
            category="environment",
            name="Python Version",
            status=CheckStatus.ERROR,
            severity=Severity.ERROR,
            message=f"Python {current} is too old (requires >= 3.9)",
            suggestion="Upgrade to Python 3.9 or later",
            details={"current": current, "required": ">= 3.9"}
        )


def check_sqlite_version() -> CheckResult:
    """Check SQLite version and FTS5 support."""
    conn = sqlite3.connect(":memory:")
    version = conn.execute("SELECT sqlite_version()").fetchone()[0]

    # Parse version
    parts = version.split(".")
    major, minor = int(parts[0]), int(parts[1])

    # Check FTS5
    try:
        conn.execute("CREATE VIRTUAL TABLE test USING fts5(content)")
        fts5_available = True
        conn.execute("DROP TABLE test")
    except sqlite3.OperationalError:
        fts5_available = False
    conn.close()

    # Version check
    version_ok = (major > 3) or (major == 3 and minor >= 35)

    if not version_ok:
        return CheckResult(
            id="sqlite_version",
            category="environment",
            name="SQLite Version",
            status=CheckStatus.ERROR,
            severity=Severity.ERROR,
            message=f"SQLite {version} is too old (requires >= 3.35.0)",
            suggestion="Upgrade SQLite to 3.35.0 or later for FTS5 support",
            details={"current": version, "required": ">= 3.35.0"}
        )

    if not fts5_available:
        return CheckResult(
            id="sqlite_fts5",
            category="environment",
            name="SQLite FTS5",
            status=CheckStatus.WARNING,
            severity=Severity.WARNING,
            message="FTS5 extension not available",
            suggestion="Install SQLite with FTS5 support for full-text search",
            details={"version": version, "fts5": False}
        )

    return CheckResult(
        id="sqlite_fts5",
        category="environment",
        name="SQLite FTS5",
        status=CheckStatus.OK,
        severity=Severity.WARNING,
        message=f"SQLite {version} with FTS5 support",
        details={"version": version, "fts5": True}
    )


def check_database_path(db_path: str, profile: str) -> list[CheckResult]:
    """Check database path accessibility."""
    results = []
    expanded_path = os.path.expanduser(db_path)
    dir_path = os.path.dirname(expanded_path) or "."

    # Check directory exists
    if os.path.exists(dir_path):
        results.append(CheckResult(
            id="db_path_exists",
            category="database",
            name="Database Directory Exists",
            status=CheckStatus.OK,
            severity=Severity.ERROR,
            message=f"Directory {dir_path} exists",
            details={"path": dir_path}
        ))
    else:
        results.append(CheckResult(
            id="db_path_exists",
            category="database",
            name="Database Directory Exists",
            status=CheckStatus.ERROR,
            severity=Severity.ERROR,
            message=f"Directory {dir_path} does not exist",
            suggestion=f"Create directory: mkdir -p {dir_path}",
            auto_fix=f"mkdir -p {dir_path}",
            details={"path": dir_path}
        ))
        return results

    # Check writable
    if os.access(dir_path, os.W_OK):
        results.append(CheckResult(
            id="db_path_writable",
            category="database",
            name="Database Path Writable",
            status=CheckStatus.OK,
            severity=Severity.ERROR,
            message=f"Directory {dir_path} is writable",
            details={"path": db_path, "expanded": expanded_path}
        ))
    else:
        results.append(CheckResult(
            id="db_path_writable",
            category="database",
            name="Database Path Writable",
            status=CheckStatus.ERROR,
            severity=Severity.ERROR,
            message=f"Directory {dir_path} is not writable",
            suggestion=f"Fix permissions: chmod 755 {dir_path}",
            details={"path": dir_path}
        ))

    return results


def check_database_integrity(db_path: str) -> CheckResult:
    """Run SQLite integrity check."""
    try:
        conn = sqlite3.connect(db_path)
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()

        if result == "ok":
            return CheckResult(
                id="db_corruption",
                category="database",
                name="Database Integrity",
                status=CheckStatus.OK,
                severity=Severity.CRITICAL,
                message="Database integrity check passed"
            )
        else:
            return CheckResult(
                id="db_corruption",
                category="database",
                name="Database Integrity",
                status=CheckStatus.ERROR,
                severity=Severity.CRITICAL,
                message=f"Database integrity check failed: {result}",
                suggestion="Restore from backup or reinitialize database",
                details={"result": result}
            )
    except sqlite3.Error as e:
        return CheckResult(
            id="db_corruption",
            category="database",
            name="Database Integrity",
            status=CheckStatus.ERROR,
            severity=Severity.CRITICAL,
            message=f"Cannot check integrity: {e}",
            details={"error": str(e)}
        )


def check_schema_version(conn: sqlite3.Connection) -> CheckResult:
    """Check database schema version."""
    from .database import SCHEMA_VERSION, get_schema_version

    try:
        current = get_schema_version(conn)

        if current == SCHEMA_VERSION:
            return CheckResult(
                id="db_schema_version",
                category="database",
                name="Schema Version",
                status=CheckStatus.OK,
                severity=Severity.ERROR,
                message=f"Schema is up to date (version {current})",
                details={"current": current, "expected": SCHEMA_VERSION}
            )
        elif current < SCHEMA_VERSION:
            return CheckResult(
                id="db_schema_version",
                category="database",
                name="Schema Version",
                status=CheckStatus.WARNING,
                severity=Severity.ERROR,
                message=f"Schema outdated (current: {current}, expected: {SCHEMA_VERSION})",
                suggestion="Run: memory_tool init to migrate schema",
                auto_fix="memory_tool init",
                details={"current": current, "expected": SCHEMA_VERSION}
            )
        else:
            return CheckResult(
                id="db_schema_version",
                category="database",
                name="Schema Version",
                status=CheckStatus.ERROR,
                severity=Severity.ERROR,
                message=f"Schema newer than tool supports (current: {current}, max: {SCHEMA_VERSION})",
                suggestion="Upgrade los-memory to a newer version",
                details={"current": current, "expected": SCHEMA_VERSION}
            )
    except sqlite3.Error as e:
        return CheckResult(
            id="db_schema_version",
            category="database",
            name="Schema Version",
            status=CheckStatus.ERROR,
            severity=Severity.ERROR,
            message=f"Cannot check schema version: {e}",
            details={"error": str(e)}
        )


def check_profile(profile: str) -> list[CheckResult]:
    """Check profile configuration."""
    from .utils import PROFILE_CHOICES, DEFAULT_PROFILE

    results = []

    # Check profile valid
    if profile in PROFILE_CHOICES:
        results.append(CheckResult(
            id="profile_valid",
            category="profile",
            name="Profile Valid",
            status=CheckStatus.OK,
            severity=Severity.ERROR,
            message=f"Profile '{profile}' is valid",
            details={"profile": profile, "valid_profiles": list(PROFILE_CHOICES)}
        ))
    else:
        results.append(CheckResult(
            id="profile_valid",
            category="profile",
            name="Profile Valid",
            status=CheckStatus.ERROR,
            severity=Severity.ERROR,
            message=f"Profile '{profile}' is invalid",
            suggestion=f"Use one of: {', '.join(PROFILE_CHOICES)}",
            details={"profile": profile, "valid_profiles": list(PROFILE_CHOICES)}
        ))

    # Check env var
    env_profile = os.environ.get("MEMORY_PROFILE")
    if env_profile:
        if env_profile.strip().lower() == profile:
            results.append(CheckResult(
                id="profile_env",
                category="profile",
                name="MEMORY_PROFILE Environment",
                status=CheckStatus.OK,
                severity=Severity.WARNING,
                message=f"MEMORY_PROFILE={env_profile} matches current profile",
                details={"env_value": env_profile, "current": profile}
            ))
        else:
            results.append(CheckResult(
                id="profile_env",
                category="profile",
                name="MEMORY_PROFILE Environment",
                status=CheckStatus.WARNING,
                severity=Severity.WARNING,
                message=f"MEMORY_PROFILE={env_profile} differs from --profile={profile}",
                suggestion="Ensure consistency between env var and --profile flag",
                details={"env_value": env_profile, "current": profile}
            ))
    else:
        results.append(CheckResult(
            id="profile_env",
            category="profile",
            name="MEMORY_PROFILE Environment",
            status=CheckStatus.WARNING,
            severity=Severity.WARNING,
            message="MEMORY_PROFILE not set, using default",
            suggestion=f"Set MEMORY_PROFILE={profile} in your shell profile",
            details={"current": None, "default": DEFAULT_PROFILE, "resolved": profile}
        ))

    return results


def run_doctor(profile: str, db_path: Optional[str], json_output: bool = False) -> dict:
    """Run all health checks and return report."""
    from .utils import resolve_db_path, utc_now

    report = DoctorReport(timestamp=utc_now())

    # Environment checks
    report.checks.append(check_python_version())
    report.checks.append(check_sqlite_version())

    # Profile checks
    report.checks.extend(check_profile(profile))

    # Resolve DB path
    try:
        resolved_db = resolve_db_path(profile, db_path)
    except ValueError as e:
        report.checks.append(CheckResult(
            id="db_path_resolution",
            category="database",
            name="Database Path Resolution",
            status=CheckStatus.ERROR,
            severity=Severity.ERROR,
            message=str(e)
        ))
        resolved_db = None

    # Database path checks
    if resolved_db:
        report.checks.extend(check_database_path(resolved_db, profile))

    # Database integrity and schema (only if file exists)
    if resolved_db and os.path.exists(resolved_db):
        report.checks.append(check_database_integrity(resolved_db))

        try:
            conn = sqlite3.connect(resolved_db)
            conn.row_factory = sqlite3.Row
            report.checks.append(check_schema_version(conn))

            # Check active session
            from .sessions import get_active_session
            active_session = get_active_session(profile)
            if active_session:
                session_id = active_session.get("session_id")
                # Verify session exists in DB
                row = conn.execute(
                    "SELECT id FROM sessions WHERE id = ?",
                    (session_id,)
                ).fetchone()
                if row:
                    report.checks.append(CheckResult(
                        id="active_session_valid",
                        category="session",
                        name="Active Session Valid",
                        status=CheckStatus.OK,
                        severity=Severity.WARNING,
                        message=f"Active session {session_id} is valid",
                        details={"session_id": session_id}
                    ))
                else:
                    report.checks.append(CheckResult(
                        id="active_session_valid",
                        category="session",
                        name="Active Session Valid",
                        status=CheckStatus.WARNING,
                        severity=Severity.WARNING,
                        message=f"Active session {session_id} not found in database",
                        suggestion="Clear stale session: memory_tool session stop",
                        auto_fix="memory_tool session stop",
                        details={"session_id": session_id}
                    ))
            else:
                report.checks.append(CheckResult(
                    id="active_session_valid",
                    category="session",
                    name="Active Session Valid",
                    status=CheckStatus.INFO,
                    severity=Severity.WARNING,
                    message="No active session",
                    details={"session_id": None}
                ))

            conn.close()
        except sqlite3.Error as e:
            report.checks.append(CheckResult(
                id="db_connection",
                category="database",
                name="Database Connection",
                status=CheckStatus.ERROR,
                severity=Severity.ERROR,
                message=f"Cannot connect to database: {e}",
                details={"error": str(e)}
            ))

    # Calculate summary
    summary = {"total": len(report.checks), "ok": 0, "warning": 0, "error": 0, "critical": 0, "info": 0}
    for check in report.checks:
        if check.status == CheckStatus.OK:
            summary["ok"] += 1
        elif check.status == CheckStatus.WARNING:
            summary["warning"] += 1
        elif check.status == CheckStatus.ERROR:
            if check.severity == Severity.CRITICAL:
                summary["critical"] += 1
            else:
                summary["error"] += 1
        elif check.status == CheckStatus.SKIPPED:
            summary["info"] += 1

    report.summary = summary

    # Determine overall status
    if summary["critical"] > 0 or summary["error"] > 0:
        report.status = "unhealthy"
    elif summary["warning"] > 0:
        report.status = "degraded"
    else:
        report.status = "healthy"

    # Collect available fixes
    for check in report.checks:
        if check.auto_fix:
            report.fixes_available.append({
                "check_id": check.id,
                "description": check.suggestion or f"Fix {check.name}",
                "command": check.auto_fix,
                "risk": "low" if check.severity != Severity.CRITICAL else "medium"
            })

    # Output
    if json_output:
        return _report_to_dict(report)
    else:
        _print_human_readable(report)
        return _report_to_dict(report)


def _report_to_dict(report: DoctorReport) -> dict:
    """Convert report to dictionary."""
    return {
        "schema_version": report.schema_version,
        "timestamp": report.timestamp,
        "status": report.status,
        "summary": report.summary,
        "checks": [
            {
                "id": c.id,
                "category": c.category,
                "name": c.name,
                "status": c.status.value,
                "severity": c.severity.value,
                "message": c.message,
                "details": c.details,
                **({"suggestion": c.suggestion} if c.suggestion else {}),
                **({"auto_fix": c.auto_fix} if c.auto_fix else {})
            }
            for c in report.checks
        ],
        "fixes_available": report.fixes_available
    }


def _print_human_readable(report: DoctorReport) -> None:
    """Print report in human-readable format."""
    # Implementation for human-readable output
    pass


# CLI Integration
def handle_doctor(args) -> None:
    """Handle doctor command."""
    import json
    import sys

    result = run_doctor(args.profile, args.db, json_output=args.json)

    if args.json:
        print(json.dumps(result, indent=2))

    # Exit code based on status
    if result["status"] == "unhealthy":
        sys.exit(2)
    elif result["status"] == "degraded":
        sys.exit(1)
    else:
        sys.exit(0)
```

## CLI Integration

Add to CLI parser:

```python
doctor_parser = subparsers.add_parser("doctor", help="Run environment health checks")
doctor_parser.add_argument("--json", action="store_true", help="Output JSON format")
doctor_parser.add_argument("--fix", action="store_true", help="Apply auto-fixes")
```

## Exit Codes

| Exit Code | Meaning |
|-----------|---------|
| 0 | Healthy - all checks passed |
| 1 | Degraded - warnings present |
| 2 | Unhealthy - errors present |
| 3 | Doctor command failed |

## Testing Strategy

1. **Unit Tests**: Mock each check independently
2. **Integration Tests**: Test with real database
3. **Error Injection**: Simulate corruption, permission issues
4. **Cross-Platform**: Test on Linux, macOS, Windows
