# los-memory CLI Interface Specification

## Overview

This document defines the standardized CLI interface for los-memory, including command parameters, global options, exit codes, and usage examples.

## Global Options

These options are available for all commands:

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--profile` | | `codex` | Memory profile (codex, claude, shared) |
| `--db` | | auto | Path to SQLite database (overrides --profile) |
| `--output` | | `json` | Output format (json, yaml, table) |
| `--human` | | false | Human-readable output |
| `--color` | | `auto` | Color output (auto, always, never) |
| `--verbose` | | false | Verbose output |
| `--help` | | | Show help message |

### Global Option Examples

```bash
# Use specific profile
memory_tool --profile claude memory search "query"

# Use custom database
memory_tool --db ~/custom/memory.db memory list

# JSON output
memory_tool --output json memory search "query"

# Combined options
memory_tool --profile claude --output json memory search "query"
```

## Command Reference

### Core Commands

#### `init` - Initialize Database

Initialize the database schema and FTS index.

```bash
memory_tool init [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| 无 | `init` 为幂等初始化，重复执行安全 |

**Examples:**
```bash
# Initialize default database
memory_tool init

# Initialize with specific profile
memory_tool --profile shared init

# Reinitialize (idempotent)
memory_tool init
```

**Output:**
```json
{
  "schema_version": "1.0.0",
  "timestamp": "2026-03-07T10:30:00Z",
  "ok": true,
  "command": "init",
  "profile": "codex",
  "db": "~/.codex_memory/memory.db",
  "data": {
    "initialized": true,
    "schema_version": 6,
    "fts_enabled": true
  }
}
```

---

#### `add` - Add Observation

Add a new observation to the memory store.

```bash
memory_tool observation add [OPTIONS]
```

**Options:**
| Option | Default | Description |
|--------|---------|-------------|
| `--title` | required | Observation title |
| `--summary` | required | Observation summary |
| `--project` | `general` | Project name |
| `--kind` | `note` | Observation kind (note, decision, fix, incident) |
| `--tags` | | Comma-separated tags |
| `--raw` | | Raw content/context |
| `--auto-tags` | false | Auto-generate tags from content |
| `--timestamp` | now | ISO timestamp |
| `--llm-hook` | env | LLM hook command for enhancement |

**Examples:**
```bash
# Basic observation
memory_tool observation add --title "API Design" --summary "Designed REST endpoints"

# With tags
memory_tool observation add --title "Bug Fix" --summary "Fixed null pointer" --kind fix --tags "bug,critical"

# Auto-generate tags
memory_tool observation add --title "Database Migration" --summary "Migrated to PostgreSQL" --auto-tags

# With raw context
memory_tool observation add --title "Decision" --summary "Use JWT" --raw "Meeting notes..."
```

**Output:**
```json
{
  "ok": true,
  "data": {
    "id": 123,
    "session_id": 456
  }
}
```

---

#### `search` - Search Observations

Search observations using FTS or LIKE queries.

```bash
memory_tool memory search [OPTIONS] QUERY
```

**Arguments:**
| Argument | Description |
|----------|-------------|
| `QUERY` | Search query string |

**Options:**
| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--limit` | `-n` | 10 | Maximum results |
| `--offset` | | 0 | Result offset |
| `--mode` | | `auto` | Search mode (auto, fts, like) |
| `--fts-quote` | | false | Quote query for FTS safety |
| `--require-tags` | | | Comma-separated required tags |

**Examples:**
```bash
# Basic search
memory_tool memory search "authentication"

# With limit
memory_tool memory search "API" --limit 20

# FTS mode with quoted query
memory_tool memory search "JWT token" --mode fts --fts-quote

# Require specific tags
memory_tool memory search "database" --require-tags "migration,postgres"

# Pagination
memory_tool memory search "bug" --limit 10 --offset 20
```

**Output:**
```json
{
  "ok": true,
  "data": {
    "query": "authentication",
    "results": [...],
    "pagination": {
      "limit": 10,
      "offset": 0,
      "total": 45,
      "has_more": true
    }
  }
}
```

---

#### `get` - Get Observations by ID

Retrieve specific observations by their IDs.

```bash
memory_tool memory get IDS
```

**Arguments:**
| Argument | Description |
|----------|-------------|
| `IDS` | Comma-separated observation IDs |

**Examples:**
```bash
# Single ID
memory_tool memory get 123

# Multiple IDs
memory_tool memory get 1,2,3,4,5

# Range (if supported)
memory_tool memory get 100-110
```

**Output:**
```json
{
  "ok": true,
  "data": {
    "ids_requested": [1, 2, 3],
    "results": [...],
    "ids_found": [1],
    "ids_missing": [2, 3]
  }
}
```

---

#### `list` - List Recent Observations

List the most recent observations.

```bash
memory_tool memory list [OPTIONS]
```

**Options:**
| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--limit` | `-n` | 20 | Maximum results |
| `--offset` | | 0 | Result offset |
| `--require-tags` | | | Filter by required tags |
| `--project` | | | Filter by project |
| `--kind` | | | Filter by kind |

**Examples:**
```bash
# List recent
memory_tool memory list

# With filters
memory_tool memory list --project myapp --kind decision --limit 50

# Tagged observations
memory_tool memory list --require-tags "critical"
```

---

#### `edit` - Edit Observation

Edit an existing observation.

```bash
memory_tool observation edit [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--id` | Observation ID to edit (required) |
| `--title` | New title |
| `--summary` | New summary |
| `--project` | New project |
| `--kind` | New kind |
| `--tags` | New tags (replaces existing) |
| `--raw` | New raw content |
| `--timestamp` | New timestamp |
| `--auto-tags` | Auto-generate tags from new content |

**Examples:**
```bash
# Edit title
memory_tool observation edit --id 123 --title "Updated Title"

# Edit multiple fields
memory_tool observation edit --id 123 --title "New" --summary "New summary" --tags "updated"

# Auto-update tags
memory_tool observation edit --id 123 --title "New Title" --summary "New Summary" --auto-tags
```

---

#### `delete` - Delete Observations

Delete observations by ID.

```bash
memory_tool observation delete [OPTIONS] IDS
```

**Arguments:**
| Argument | Description |
|----------|-------------|
| `IDS` | Comma-separated observation IDs |

**Options:**
| Option | Description |
|--------|-------------|
| `--dry-run` | Preview deletion without executing |

**Examples:**
```bash
# Delete single
memory_tool observation delete 123

# Delete multiple
memory_tool observation delete 1,2,3

# Preview deletion
memory_tool observation delete 100,101 --dry-run
```

**Output:**
```json
{
  "ok": true,
  "data": {
    "ids_requested": [100, 101],
    "matched": 2,
    "deleted": 2,
    "dry_run": false
  }
}
```

---

#### `timeline` - Timeline Query

Query observations by time range.

```bash
memory_tool memory timeline [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--start` | Start timestamp (ISO 8601) |
| `--end` | End timestamp (ISO 8601) |
| `--around-id` | Find observations around this ID |
| `--window-minutes` | Window around --around-id (default: 120) |
| `--limit` | Maximum results (default: 20) |
| `--offset` | Result offset |
| `--visual` | Show visual timeline |
| `--group-by` | Group by (hour, day, session) |

**Examples:**
```bash
# Recent observations
memory_tool memory timeline

# Specific range
memory_tool memory timeline --start 2026-03-01 --end 2026-03-07

# Around observation
memory_tool memory timeline --around-id 123 --window-minutes 60

# Visual timeline
memory_tool memory timeline --visual --group-by day
```

---

### Session Commands

#### `session start` - Start Session

```bash
memory_tool session start [OPTIONS]
```

**Options:**
| Option | Default | Description |
|--------|---------|-------------|
| `--project` | `general` | Project name |
| `--working-dir` | current | Working directory |
| `--agent-type` | profile | Agent type |
| `--summary` | | Session summary |

---

#### `session stop` - Stop Session

```bash
memory_tool session stop [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--summary` | Final session summary |

---

#### `session list` - List Sessions

```bash
memory_tool session list [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--status` | Filter by status (active, completed) |
| `--limit` | Maximum results |

---

#### `session show` - Show Session Details

```bash
memory_tool session show [OPTIONS] SESSION_ID
```

**Options:**
| Option | Description |
|--------|-------------|
| `--observations` | Include observations |

---

#### `session resume` - Resume Session

```bash
memory_tool session resume [SESSION_ID]
```

If SESSION_ID omitted, resumes current active session.

---

### Project Commands

#### `project list` - List Projects

```bash
memory_tool project list [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--limit` | Maximum results |

---

#### `project switch` - Switch Project

```bash
memory_tool project switch PROJECT_NAME
```

---

#### `project active` - Show/Set Active Project

```bash
memory_tool project active [PROJECT_NAME]
```

---

#### `project stats` - Project Statistics

```bash
memory_tool project stats [PROJECT_NAME]
```

---

#### `project archive` - Archive Project

```bash
memory_tool project archive PROJECT_NAME
```

---

### Checkpoint Commands

#### `checkpoint create` - Create Checkpoint

```bash
memory_tool checkpoint create [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--name` | Checkpoint name (required) |
| `--description` | Description |
| `--tag` | Tag |

---

#### `checkpoint list` - List Checkpoints

```bash
memory_tool checkpoint list [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--limit` | Maximum results |

---

#### `checkpoint show` - Show Checkpoint

```bash
memory_tool checkpoint show CHECKPOINT_ID
```

---

#### `checkpoint resume` - Resume from Checkpoint

```bash
memory_tool checkpoint resume CHECKPOINT_ID
```

---

### Management Commands

#### `manage stats` - Database Statistics

```bash
memory_tool admin manage stats [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--limit` | Maximum projects/kinds to show |

---

#### `manage projects` - List Projects

```bash
memory_tool admin manage projects [OPTIONS]
```

---

#### `manage tags` - List Tags

```bash
memory_tool admin manage tags [OPTIONS]
```

---

#### `manage vacuum` - Vacuum Database

```bash
memory_tool admin manage vacuum
```

---

### Utility Commands

#### `capture` - Quick Capture

Quickly capture a thought or note.

```bash
memory_tool capture TEXT...
```

**Examples:**
```bash
memory_tool capture "Remember to update the API docs"
memory_tool capture "Bug: login fails with special chars" --kind fix --tags "bug"
```

---

#### `clean` - Clean Old Observations

Delete old or filtered observations.

```bash
memory_tool memory clean [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--before` | Delete before timestamp |
| `--older-than-days` | Delete older than N days |
| `--project` | Filter by project |
| `--kind` | Filter by kind |
| `--tag` | Filter by tag |
| `--all` | Delete all (requires confirmation) |
| `--dry-run` | Preview only |
| `--vacuum` | Vacuum after deletion |

**Examples:**
```bash
# Clean old observations
memory_tool memory clean --older-than-days 90

# Clean specific project
memory_tool memory clean --project oldproject --dry-run
memory_tool memory clean --project oldproject

# Vacuum database
memory_tool memory clean --vacuum
```

---

#### `export` - Export Observations

```bash
memory_tool memory export [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--format` | Output format (json, csv) |
| `--output` | Output file (default: stdout) |
| `--limit` | Maximum observations |
| `--offset` | Offset for pagination |

---

#### `import` - Import Bundle

```bash
memory_tool import [OPTIONS] FILE
```

**Options:**
| Option | Description |
|--------|-------------|
| `--project` | Import to specific project |
| `--dry-run` | Preview import |

---

#### `share` - Create Shareable Bundle

```bash
memory_tool share [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--output` | Output file (required) |
| `--format` | Format (json, markdown, html) |
| `--project` | Filter by project |
| `--kind` | Filter by kind |
| `--tag` | Filter by tag |
| `--session` | Filter by session |
| `--since` | Filter by date |
| `--limit` | Maximum observations |

---

### Link Commands

#### `link` - Create Link

```bash
memory_tool observation link [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--from` | Source observation ID (required) |
| `--to` | Target observation ID (required) |
| `--type` | Link type (related, child, parent, refines) |

---

#### `unlink` - Remove Link

```bash
memory_tool observation unlink [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--from` | Source observation ID (required) |
| `--to` | Target observation ID (required) |
| `--type` | Specific link type to remove |

---

#### `related` - Find Related Observations

```bash
memory_tool observation related [OPTIONS] ID
```

**Options:**
| Option | Description |
|--------|-------------|
| `--type` | Filter by link type |
| `--limit` | Maximum results |
| `--suggest` | Suggest similar observations |

---

### Feedback Commands

#### `feedback` - Provide Feedback

```bash
memory_tool observation feedback [OPTIONS] TEXT...
```

**Options:**
| Option | Description |
|--------|-------------|
| `--id` | Target observation ID (required) |
| `--dry-run` | Preview changes |
| `--history` | Show feedback history |

---

#### `review apply` - Batch Apply Feedback

```bash
memory_tool review apply [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--file` | JSON file with feedback items (required) |
| `--dry-run` | Preview without applying |

---

### Tool Memory Commands

#### `tool log` - Log Tool Call

```bash
memory_tool tool log [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--tool` | Tool name (required) |
| `--input` | Tool input JSON (required) |
| `--output` | Tool output JSON |
| `--status` | Status (success, error) |
| `--duration` | Duration in milliseconds |
| `--project` | Project name |

---

#### `tool transition` - Log Agent Transition

```bash
memory_tool tool transition [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--phase` | Phase (required) |
| `--action` | Action (required) |
| `--input` | Input JSON (required) |
| `--output` | Output JSON |
| `--status` | Status |
| `--reward` | Reward score |
| `--project` | Project name |

---

#### `tool stats` - Tool Usage Statistics

```bash
memory_tool tool stats [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--project` | Filter by project |
| `--limit` | Maximum tools to show |

---

#### `tool suggest` - Suggest Tools

```bash
memory_tool tool suggest [OPTIONS] TASK...
```

**Options:**
| Option | Description |
|--------|-------------|
| `--limit` | Maximum suggestions |

---

### Diagnostic Commands

#### `admin doctor` - Environment Health Check

```bash
memory_tool admin doctor [OPTIONS]
```

**Options:**
| Option | Description |
|--------|-------------|
| `--fix` | Apply auto-fixes |
| `--output {json,yaml,table}` | Set output format (global option) |
| `--human` | Use human-readable format (global option) |

**Examples:**
```bash
# Run all checks
memory_tool admin doctor

# JSON output
memory_tool --output json admin doctor

# Apply fixes
memory_tool admin doctor --fix
```

---

## Exit Codes

| Exit Code | Meaning | When Used |
|-----------|---------|-----------|
| 0 | Success | Command completed successfully |
| 1 | Command failed | 参数错误、业务失败或 doctor 检查未通过 |

### Exit Code Examples

```bash
# Success
memory_tool memory list; echo $?  # 0

# Not found
memory_tool memory get 99999; echo $?  # 4

# Validation error
memory_tool observation add --title; echo $?  # 1

# Doctor degraded
memory_tool admin doctor; echo $?  # 0 (degraded)

# Doctor unhealthy
memory_tool admin doctor; echo $?  # 1 (unhealthy)
```

## Error Handling Strategy

### Error Propagation

```python
def handle_command(args) -> None:
    import sys

    try:
        result = execute_command(args)
        print_json_response(result)
        sys.exit(0)

    except ValueError as e:
        # Validation errors
        result = create_error_response(
            code="VAL_INVALID_INPUT",
            message=str(e),
            category="validation"
        )
        print_json_response(result)
        sys.exit(2)

    except ObservationNotFoundError as e:
        result = create_error_response(
            code="NF_OBSERVATION",
            message=str(e),
            category="not_found"
        )
        print_json_response(result)
        sys.exit(4)

    except sqlite3.Error as e:
        result = create_error_response(
            code="DB_ERROR",
            message=f"Database error: {e}",
            category="database"
        )
        print_json_response(result)
        sys.exit(3)

    except PermissionError as e:
        result = create_error_response(
            code="SYS_PERMISSION",
            message=str(e),
            category="system"
        )
        print_json_response(result)
        sys.exit(5)
```

### Error Recovery Suggestions

Every error response includes a `suggestion` field with actionable guidance:

```json
{
  "ok": false,
  "error": {
    "code": "DB_SCHEMA_VERSION",
    "message": "Schema version mismatch",
    "suggestion": "Run 'memory_tool init' to migrate the database"
  }
}
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MEMORY_PROFILE` | Default profile | `codex` |
| `MEMORY_DB_PATH` | Default database path | (from profile) |
| `MEMORY_LLM_HOOK` | Default LLM hook | `` |
| `MEMORY_JSON_SCHEMA_VERSION` | Preferred JSON schema | `1.0` |

## Configuration File

Configuration file location: `~/.config/los-memory/config.json`

```json
{
  "default_profile": "codex",
  "profiles": {
    "codex": {
      "db_path": "~/.codex_memory/memory.db"
    },
    "claude": {
      "db_path": "~/.claude_memory/memory.db"
    }
  },
  "llm_hook": "",
  "auto_tags": true,
  "default_limit": 20
}
```

## Usage Patterns

### Shell Integration

```bash
# Alias for common use
alias mt='memory_tool'
alias mtj='memory_tool --output json'

# Function for quick capture
mcapture() {
    memory_tool observation add --title "Quick Capture" --summary "$*"
}

# Function for project-aware search
msearch() {
    local project=$(memory_tool --output json project active | jq -r '.data.project')
    memory_tool memory search "$1" --project "$project"
}
```

### Scripting Examples

```bash
#!/bin/bash
# Backup observations before cleanup

# Export all observations
memory_tool --output json memory export --output backup.json

# Clean old observations
memory_tool memory clean --older-than-days 90 --vacuum

# Verify cleanup
memory_tool --output json admin manage stats
```

```python
#!/usr/bin/env python3
# Example: Process observations programmatically

import subprocess
import json

def get_recent_observations(limit=10):
    result = subprocess.run(
        ['memory_tool', '--output', 'json', 'memory', 'list', '--limit', str(limit)],
        capture_output=True,
        text=True
    )
    data = json.loads(result.stdout)
    return data['data']['results']

for obs in get_recent_observations():
    print(f"{obs['id']}: {obs['title']}")
```

## Backward Compatibility

兼容模式通过旧命令别名维持（如 `tool-log`、`review-feedback` 等），推荐优先使用分组命令。
