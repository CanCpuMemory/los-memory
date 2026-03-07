# los-memory JSON Schema Specification

## Overview

This document defines the standardized JSON output format for all los-memory CLI commands. The goal is to provide a stable, versioned, and predictable interface for programmatic consumption.

## Schema Versioning

### Version Format

Schema versions follow Semantic Versioning: `MAJOR.MINOR.PATCH`

- **MAJOR**: Breaking changes to response structure
- **MINOR**: New fields added (backward compatible)
- **PATCH**: Bug fixes, documentation improvements

### Current Version

```json
{
  "schema_version": "1.0.0"
}
```

### Version Negotiation

Future versions may support version negotiation via:
- Environment variable: `MEMORY_JSON_SCHEMA_VERSION=1.0`
- CLI flag: `--json-version=1.0`

## Base Response Structure

All responses follow this base structure:

```json
{
  "schema_version": "string",
  "timestamp": "string (ISO 8601)",
  "ok": "boolean",
  "command": "string",
  "profile": "string",
  "db": "string"
}
```

## Success Response Format

### Structure

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "command_name",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": { /* command-specific data */ },
  "meta": {
    "duration_ms": 45,
    "row_count": 10
  }
}
```

### Command-Specific Examples

#### `add` Command

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "add",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
    "id": 123,
    "session_id": 456
  },
  "meta": {
    "duration_ms": 23
  }
}
```

#### `search` Command

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "search",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
    "query": "authentication",
    "results": [
      {
        "id": 1,
        "timestamp": "2026-03-06T14:30:00Z",
        "project": "myapp",
        "kind": "decision",
        "title": "JWT Authentication Strategy",
        "summary": "Decided to use JWT tokens for API authentication",
        "tags": ["auth", "jwt", "api"],
        "score": -2.5,
        "session_id": 10
      }
    ],
    "pagination": {
      "limit": 10,
      "offset": 0,
      "total": 45,
      "has_more": true
    }
  },
  "meta": {
    "duration_ms": 156,
    "row_count": 10
  }
}
```

#### `get` Command

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "get",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
    "ids_requested": [1, 2, 3],
    "results": [
      {
        "id": 1,
        "timestamp": "2026-03-06T14:30:00Z",
        "project": "myapp",
        "kind": "decision",
        "title": "JWT Authentication Strategy",
        "summary": "Decided to use JWT tokens for API authentication",
        "tags": ["auth", "jwt", "api"],
        "raw": "Full context here...",
        "session_id": 10
      }
    ],
    "ids_found": [1],
    "ids_missing": [2, 3]
  },
  "meta": {
    "duration_ms": 12,
    "row_count": 1
  }
}
```

#### `list` Command

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "list",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
    "results": [
      {
        "id": 100,
        "timestamp": "2026-03-07T09:00:00Z",
        "project": "myapp",
        "kind": "note",
        "title": "API Design Notes",
        "summary": "Initial thoughts on REST API structure",
        "tags": ["api", "design"],
        "session_id": null
      }
    ],
    "pagination": {
      "limit": 20,
      "offset": 0,
      "total": 150
    }
  },
  "meta": {
    "duration_ms": 34,
    "row_count": 20
  }
}
```

#### `edit` Command

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "edit",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
    "id": 123,
    "updated": {
      "id": 123,
      "timestamp": "2026-03-06T14:30:00Z",
      "project": "myapp",
      "kind": "decision",
      "title": "Updated Title",
      "summary": "Updated summary",
      "tags": ["auth", "jwt"],
      "raw": "Updated context",
      "session_id": 10
    },
    "fields_changed": ["title", "summary"]
  },
  "meta": {
    "duration_ms": 45
  }
}
```

#### `delete` Command

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "delete",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
    "ids_requested": [100, 101, 102],
    "matched": 3,
    "deleted": 3,
    "dry_run": false
  },
  "meta": {
    "duration_ms": 23
  }
}
```

#### `timeline` Command

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "timeline",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
    "range": {
      "start": "2026-03-06T00:00:00Z",
      "end": "2026-03-07T23:59:59Z"
    },
    "results": [
      {
        "id": 1,
        "timestamp": "2026-03-06T14:30:00Z",
        "project": "myapp",
        "kind": "decision",
        "title": "JWT Authentication Strategy",
        "summary": "Decided to use JWT tokens",
        "tags": ["auth", "jwt"],
        "session_id": 10
      }
    ],
    "visual": "📅 Visual Timeline\n..."
  },
  "meta": {
    "duration_ms": 67,
    "row_count": 5
  }
}
```

#### `session` Command

**session start:**
```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "session",
  "subcommand": "start",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
    "session_id": 456,
    "project": "myapp",
    "working_dir": "/home/user/projects/myapp",
    "agent_type": "codex",
    "start_time": "2026-03-07T10:30:00Z"
  },
  "meta": {
    "duration_ms": 15
  }
}
```

**session list:**
```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "session",
  "subcommand": "list",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
    "sessions": [
      {
        "id": 456,
        "start_time": "2026-03-07T10:30:00Z",
        "end_time": null,
        "project": "myapp",
        "working_dir": "/home/user/projects/myapp",
        "agent_type": "codex",
        "summary": "",
        "status": "active"
      }
    ],
    "active_session_id": 456
  },
  "meta": {
    "duration_ms": 23,
    "row_count": 1
  }
}
```

#### `manage stats` Command

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "manage",
  "subcommand": "stats",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
    "total": 150,
    "earliest": "2026-01-01T00:00:00Z",
    "latest": "2026-03-07T10:30:00Z",
    "projects": [
      {"project": "myapp", "count": 100},
      {"project": "docs", "count": 50}
    ],
    "kinds": [
      {"kind": "note", "count": 80},
      {"kind": "decision", "count": 40},
      {"kind": "fix", "count": 30}
    ]
  },
  "meta": {
    "duration_ms": 45
  }
}
```

#### `doctor` Command

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "doctor",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
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
  },
  "meta": {
    "duration_ms": 234
  }
}
```

## Error Response Format

### Structure

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": false,
  "command": "command_name",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable error message",
    "category": "validation|database|system|not_found|conflict",
    "details": { /* error-specific details */ },
    "suggestion": "How to fix this error",
    "documentation_url": "https://docs.example.com/errors/ERROR_CODE"
  },
  "meta": {
    "duration_ms": 12
  }
}
```

### Error Categories

| Category | Description | HTTP Equivalent |
|----------|-------------|-----------------|
| `validation` | Invalid input parameters | 400 |
| `not_found` | Resource not found | 404 |
| `conflict` | Resource conflict | 409 |
| `database` | Database error | 500 |
| `system` | System/IO error | 500 |
| `permission` | Permission denied | 403 |

### Error Codes

#### Validation Errors (VAL_*)

| Code | Description |
|------|-------------|
| `VAL_INVALID_ID` | Invalid observation ID format |
| `VAL_MISSING_REQUIRED` | Missing required parameter |
| `VAL_INVALID_TIMESTAMP` | Invalid timestamp format |
| `VAL_INVALID_PROFILE` | Invalid profile name |
| `VAL_EMPTY_QUERY` | Empty search query |
| `VAL_NO_CHANGES` | No changes provided for edit |

#### Not Found Errors (NF_*)

| Code | Description |
|------|-------------|
| `NF_OBSERVATION` | Observation not found |
| `NF_SESSION` | Session not found |
| `NF_CHECKPOINT` | Checkpoint not found |
| `NF_PROJECT` | Project not found |

#### Conflict Errors (CONF_*)

| Code | Description |
|------|-------------|
| `CONF_DUPLICATE_LINK` | Link already exists |
| `CONF_SELF_LINK` | Cannot link observation to itself |
| `CONF_ACTIVE_SESSION` | Already have active session |

#### Database Errors (DB_*)

| Code | Description |
|------|-------------|
| `DB_CONNECTION` | Database connection failed |
| `DB_INTEGRITY` | Database integrity check failed |
| `DB_LOCKED` | Database is locked |
| `DB_SCHEMA_VERSION` | Schema version mismatch |

#### System Errors (SYS_*)

| Code | Description |
|------|-------------|
| `SYS_IO` | I/O error |
| `SYS_PERMISSION` | Permission denied |
| `SYS_PATH_TOO_LONG` | Path exceeds maximum length |

### Error Examples

#### Observation Not Found

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": false,
  "command": "get",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "error": {
    "code": "NF_OBSERVATION",
    "message": "Observation 999 not found",
    "category": "not_found",
    "details": {
      "id": 999,
      "ids_requested": [999]
    },
    "suggestion": "Use 'memory_tool list' to see available observations"
  },
  "meta": {
    "duration_ms": 5
  }
}
```

#### Invalid Input

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": false,
  "command": "add",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "error": {
    "code": "VAL_MISSING_REQUIRED",
    "message": "Missing required parameter: --title",
    "category": "validation",
    "details": {
      "missing": ["title"]
    },
    "suggestion": "Provide --title 'Your observation title'"
  },
  "meta": {
    "duration_ms": 2
  }
}
```

#### Database Connection Error

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": false,
  "command": "search",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "error": {
    "code": "DB_CONNECTION",
    "message": "Cannot connect to database: unable to open database file",
    "category": "database",
    "details": {
      "path": "~/.codex_memory/memory.db",
      "expanded": "/home/user/.codex_memory/memory.db"
    },
    "suggestion": "Run 'memory_tool doctor' to diagnose database issues"
  },
  "meta": {
    "duration_ms": 10
  }
}
```

#### Schema Version Mismatch

```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": false,
  "command": "add",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "error": {
    "code": "DB_SCHEMA_VERSION",
    "message": "Database schema version 5 is older than required 6",
    "category": "database",
    "details": {
      "current": 5,
      "required": 6
    },
    "suggestion": "Run 'memory_tool init' to migrate the database schema"
  },
  "meta": {
    "duration_ms": 15
  }
}
```

## Common Data Types

### Observation

```json
{
  "id": 123,
  "timestamp": "2026-03-07T10:30:00Z",
  "project": "myapp",
  "kind": "decision",
  "title": "Title here",
  "summary": "Summary here",
  "tags": ["tag1", "tag2"],
  "raw": "Raw content",
  "session_id": 456
}
```

### Session

```json
{
  "id": 456,
  "start_time": "2026-03-07T10:30:00Z",
  "end_time": "2026-03-07T12:00:00Z",
  "project": "myapp",
  "working_dir": "/home/user/projects/myapp",
  "agent_type": "codex",
  "summary": "Session summary",
  "status": "active"
}
```

### Checkpoint

```json
{
  "id": 789,
  "timestamp": "2026-03-07T10:30:00Z",
  "name": "v1.0-release",
  "description": "Before v1.0 release",
  "tag": "release",
  "session_id": 456,
  "observation_count": 25,
  "project": "myapp"
}
```

### Pagination

```json
{
  "limit": 20,
  "offset": 0,
  "total": 150,
  "has_more": true,
  "next_offset": 20
}
```

## Implementation Guidelines

### Python Implementation

```python
import json
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA_VERSION = "1.0.0"

def create_base_response(
    command: str,
    profile: str,
    db: str,
    ok: bool = True
) -> dict:
    """Create base response structure."""
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "command": command,
        "profile": profile,
        "db": db,
    }

def create_success_response(
    command: str,
    profile: str,
    db: str,
    data: dict,
    meta: Optional[dict] = None
) -> dict:
    """Create success response."""
    response = create_base_response(command, profile, db, ok=True)
    response["data"] = data
    if meta:
        response["meta"] = meta
    return response

def create_error_response(
    command: str,
    profile: str,
    db: str,
    code: str,
    message: str,
    category: str,
    details: Optional[dict] = None,
    suggestion: Optional[str] = None
) -> dict:
    """Create error response."""
    response = create_base_response(command, profile, db, ok=False)
    response["error"] = {
        "code": code,
        "message": message,
        "category": category,
    }
    if details:
        response["error"]["details"] = details
    if suggestion:
        response["error"]["suggestion"] = suggestion
    return response

def print_json_response(response: dict) -> None:
    """Print JSON response with consistent formatting."""
    print(json.dumps(response, indent=2, ensure_ascii=False))
```

### Exit Codes

| Exit Code | Meaning |
|-----------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Validation error |
| 3 | Database error |
| 4 | Not found |
| 5 | Permission denied |

### Error Handling Pattern

```python
def handle_command(args) -> None:
    import sys
    from .utils import resolve_db_path

    db_path = resolve_db_path(args.profile, args.db)

    try:
        conn = connect_db(db_path)
        # ... execute command
        result = create_success_response(
            command="search",
            profile=args.profile,
            db=db_path,
            data={"results": results},
            meta={"duration_ms": duration, "row_count": len(results)}
        )
        print_json_response(result)
        sys.exit(0)

    except ValueError as e:
        # Validation errors
        result = create_error_response(
            command="search",
            profile=args.profile,
            db=db_path,
            code="VAL_INVALID_INPUT",
            message=str(e),
            category="validation",
            suggestion="Check your input parameters"
        )
        print_json_response(result)
        sys.exit(2)

    except sqlite3.Error as e:
        # Database errors
        result = create_error_response(
            command="search",
            profile=args.profile,
            db=db_path,
            code="DB_ERROR",
            message=f"Database error: {e}",
            category="database",
            suggestion="Run 'memory_tool doctor' to diagnose"
        )
        print_json_response(result)
        sys.exit(3)
```

## Migration Guide

### From Legacy Format

Legacy responses had inconsistent structures. Migration steps:

1. Wrap all responses in base structure
2. Move command-specific data to `data` field
3. Add `ok` boolean field
4. Add `schema_version` field
5. Standardize error format

### Backward Compatibility

For transitional period, support legacy mode:

```bash
# Legacy mode (old format)
memory_tool search "query" --legacy

# New format (default)
memory_tool search "query" --json
```

## Testing

### JSON Schema Validation

```python
import jsonschema

SUCCESS_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "timestamp", "ok", "command", "profile", "db", "data"],
    "properties": {
        "schema_version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
        "timestamp": {"type": "string", "format": "date-time"},
        "ok": {"type": "boolean", "enum": [True]},
        "command": {"type": "string"},
        "profile": {"type": "string"},
        "db": {"type": "string"},
        "data": {"type": "object"},
        "meta": {
            "type": "object",
            "properties": {
                "duration_ms": {"type": "integer"},
                "row_count": {"type": "integer"}
            }
        }
    }
}

def validate_success_response(response: dict) -> None:
    jsonschema.validate(response, SUCCESS_SCHEMA)
```

## References

- [JSON Schema Specification](https://json-schema.org/)
- [ISO 8601 Date Format](https://en.wikipedia.org/wiki/ISO_8601)
- [Semantic Versioning](https://semver.org/)
