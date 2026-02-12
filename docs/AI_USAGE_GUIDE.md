# AI Agent Usage Guide for los-memory

## Overview

This guide explains how AI agents (Claude, Codex, etc.) should use the los-memory tool to maintain context across conversations and sessions.

## Core Philosophy

1. **Durable Context**: Store important decisions, discoveries, and context that should persist beyond the current conversation
2. **Retrievable**: Use clear titles, tags, and project names to make memories easy to find
3. **Concise**: Store summaries, not full transcripts - focus on key insights
4. **Organized**: Group related memories by project and kind

## When to Store Memories

### Always Store
- Important architectural decisions
- Bug fixes with root cause analysis
- API contracts or interface definitions
- Security-related configurations
- Performance optimization results
- Setup steps that took significant time to figure out
- Workarounds for known issues
- Links to relevant documentation or resources

### Sometimes Store
- Meeting summaries with action items
- Intermediate debugging steps that might be useful later
- Code snippets that are frequently reused
- Configuration examples

### Don't Store
- Temporary thoughts or brainstorming
- Information easily found in official docs
- Duplicate information already stored
- Transient error messages without context

## Memory Structure

### Kinds
Use the appropriate `kind` for each memory:

| Kind | Use For | Example Title |
|------|---------|---------------|
| `decision` | Architecture/design choices | "Chose PostgreSQL over MongoDB" |
| `incident` | Problems and resolutions | "Database connection pool exhaustion" |
| `note` | General observations | "Redis cache TTL behavior" |
| `meeting` | Discussion summaries | "Sprint planning decisions" |
| `todo` | Future work items | "Refactor auth middleware" |
| `snippet` | Reusable code blocks | "JWT validation helper" |
| `tool_call` | Tool execution records | "Search files for TODO patterns" |

### Projects
Use consistent project names:

```
# Good - specific and consistent
--project "payments-service"
--project "frontend-dashboard"
--project "infra-deployment"

# Avoid - vague or inconsistent
--project "backend"  # Too vague
--project "API"      # Inconsistent with "api"
```

### Tags
Add relevant tags for discoverability:

```bash
# Good tags - specific and meaningful
--tags "postgresql,migration,performance"
--tags "oauth2,security,auth0"

# Avoid - generic or redundant
--tags "important,code"  # Too generic
--tags "api"  # Already implied by context
```

Use `--auto-tags` to automatically generate tags from content:

```bash
python3 memory_tool/memory_tool.py --profile claude add \
  --title "Database connection pool optimization" \
  --summary "Increased max connections from 20 to 100, added connection retry logic" \
  --auto-tags
# Generates tags: connection, pool, database, optimization
```

## Daily Workflow

### Starting a Session

1. **Check recent context**:
```bash
python3 memory_tool/memory_tool.py --profile claude timeline --limit 10
```

2. **Search for relevant context**:
```bash
python3 memory_tool/memory_tool.py --profile claude search "authentication"
python3 memory_tool/memory_tool.py --profile claude search "project:payments decision"
```

3. **Resume from checkpoint** (if available):
```bash
python3 memory_tool/memory_tool.py --profile claude checkpoint resume checkpoint-name
```

### During Development

1. **Store decisions immediately**:
```bash
python3 memory_tool/memory_tool.py --profile claude add \
  --project "payments-service" \
  --kind "decision" \
  --title "Stripe webhook handling strategy" \
  --summary "Using idempotency keys to handle duplicate webhooks. Storing processed webhook IDs in Redis with 24h TTL." \
  --tags "stripe,webhook,idempotency,redis"
```

2. **Record incidents with root cause**:
```bash
python3 memory_tool/memory_tool.py --profile claude add \
  --project "infra" \
  --kind "incident" \
  --title "Memory leak in image processing worker" \
  --summary "Leak caused by not closing PIL Image objects. Fixed by adding explicit .close() calls and using context managers." \
  --tags "memory-leak,pil,python,worker"
```

3. **Create checkpoints at milestones**:
```bash
python3 memory_tool/memory_tool.py --profile claude checkpoint create \
  --name "feature-auth-complete" \
  --tag "milestone" \
  --description "Authentication system fully implemented and tested"
```

### Ending a Session

1. **Create session checkpoint**:
```bash
python3 memory_tool/memory_tool.py --profile claude checkpoint create \
  --name "end-of-session" \
  --description "Current work on payment refactoring"
```

2. **List recent memories to confirm storage**:
```bash
python3 memory_tool/memory_tool.py --profile claude manage stats
```

### Feedback and Corrections

Correct or supplement existing memories using natural language:

```bash
# Correct an observation
python3 memory_tool/memory_tool.py --profile claude feedback \
  "修正: API密钥是yyy而非xxx" \
  --id 123

# Supplement with additional information
python3 memory_tool/memory_tool.py --profile claude feedback \
  "补充: 还需要考虑边界情况" \
  --id 456

# Preview changes without applying
python3 memory_tool/memory_tool.py --profile claude feedback \
  "修正: 数据库使用PostgreSQL而非MySQL" \
  --id 789 \
  --dry-run

# View feedback history for an observation
python3 memory_tool/memory_tool.py --profile claude feedback \
  "查看历史" \
  --id 123 \
  --history
```

### Tool Memory Tracking

Log tool calls to track usage patterns and get recommendations:

```bash
# Log a successful tool call
python3 memory_tool/memory_tool.py --profile claude tool-log \
  --tool "search_files" \
  --input '{"query": "TODO"}' \
  --output '{"results": [1, 2, 3]}' \
  --status success \
  --duration 150

# Log a failed tool call
python3 memory_tool/memory_tool.py --profile claude tool-log \
  --tool "api_request" \
  --input '{"url": "http://api.test"}' \
  --output '{"error": "Timeout"}' \
  --status error

# View tool usage statistics
python3 memory_tool/memory_tool.py --profile claude tool-stats

# Filter by project
python3 memory_tool/memory_tool.py --profile claude tool-stats \
  --project "api-service"

# Get tool suggestions for a task
python3 memory_tool/memory_tool.py --profile claude tool-suggest \
  "find code patterns in the codebase"
```

### Memory Linking

Create relationships between related observations:

```bash
# Create a link between observations
python3 memory_tool/memory_tool.py --profile claude link \
  --from 123 \
  --to 456 \
  --type refines

# Link types: related, child, parent, refines
python3 memory_tool/memory_tool.py --profile claude link \
  --from 100 \
  --to 101 \
  --type child

# Find related observations
python3 memory_tool/memory_tool.py --profile claude related 123

# Filter by link type
python3 memory_tool/memory_tool.py --profile claude related 123 \
  --type refines

# Suggest potentially related observations based on similarity
python3 memory_tool/memory_tool.py --profile claude related 123 \
  --suggest

# Remove a link
python3 memory_tool/memory_tool.py --profile claude unlink \
  --from 123 \
  --to 456
```

## Python API Usage

For programmatic access within Python:

```python
from memory_tool.database import connect_db, ensure_schema
from memory_tool.operations import add_observation, run_search
from memory_tool.projects import get_active_project, set_active_project
from memory_tool.sessions import start_session, end_session, get_active_session
from memory_tool.checkpoints import create_checkpoint, resume_from_checkpoint
from memory_tool.share import run_share, run_import

# New in v2.x: Feedback and corrections
from memory_tool.feedback import apply_feedback, get_feedback_history

# New in v2.x: Tool memory and analytics
from memory_tool.analytics import log_tool_call, get_tool_stats, suggest_tools_for_task

# New in v2.x: Memory linking and relationships
from memory_tool.links import (
    create_link, delete_link, get_related_observations, find_similar_observations
)

# Connect to database
conn = connect_db("~/.claude_memory/memory.db")
ensure_schema(conn)

# Add observation
obs_id = add_observation(
    conn,
    timestamp="2025-02-11T10:30:00Z",
    project="my-project",
    kind="decision",
    title="Architecture decision",
    summary="We chose X because of Y",
    tags='["architecture", "decision"]',
    tags_text="architecture decision",
    raw="",
    session_id=None
)

# Search
results = run_search(conn, "architecture decision", limit=10)
for row in results:
    print(f"{row['id']}: {row['title']} ({row['project']})")

# Provide feedback to correct an observation
result = apply_feedback(
    conn,
    observation_id=123,
    feedback_text="修正: API密钥是yyy而非xxx",
    auto_apply=True
)

# Log a tool call
log_tool_call(
    conn,
    tool_name="search_files",
    tool_input={"query": "TODO"},
    tool_output={"results": [1, 2, 3]},
    status="success",
    duration_ms=150,
    project="my-project",
    session_id=None
)

# Get tool usage statistics
stats = get_tool_stats(conn)
print(f"Total calls: {stats['total_calls']}, Success rate: {stats['success_rate']}%")

# Create a link between observations
link_id = create_link(conn, from_id=123, to_id=456, link_type="refines")

# Find related observations
related = get_related_observations(conn, observation_id=123)
for obs in related:
    print(f"{obs['id']}: {obs['title']} ({obs['link_type']})")

# Find similar observations
similar = find_similar_observations(conn, observation_id=123)
for obs in similar:
    print(f"Suggested: {obs['title']} (score: {obs['similarity_score']})")
```

## Retrieval Patterns

### Effective Search Queries

```bash
# Search by keyword
search "database migration"

# Search by project
search "project:payments"

# Search by kind
search "kind:incident"

# Search by tag
search "tags:security"

# Combined search
search "project:api kind:decision auth"
```

### Timeline Views

```bash
# Recent activity
timeline --limit 20

# Around a specific event
timeline --around-id 42 --window-minutes 60

# Since a date
timeline --since "2025-02-01"
```

## Best Practices

### 1. Write Clear Titles

```bash
# Good - specific and searchable
--title "OAuth2 token refresh logic for Google APIs"

# Avoid - vague
--title "Fixed auth issue"
```

### 2. Include Context in Summaries

```bash
# Good - explains why and what
--summary "Increased connection pool from 10 to 50 connections to handle peak load of 1000 req/s. Monitored with Datadog dashboard."

# Avoid - too brief
--summary "Increased pool size"
```

### 3. Use Consistent Terminology

```bash
# Good - consistent with existing tags
--tags "postgresql,database,migration"
# (assuming "postgresql" was used before, not "postgres" or "psql")

# Avoid - inconsistent
--tags "postgres,db,upgrade"  # Mixing variants
```

### 4. Link Related Memories

When creating a memory that relates to existing ones, reference the ID:

```bash
--summary "Follow-up to #42: The connection pool fix resolved the immediate issue, but we should monitor for connection leaks."
```

### 5. Regular Maintenance

```bash
# Weekly: Review and clean up
manage stats
clean --older-than-days 90 --dry-run

# Monthly: Export and backup
export --format json --output backup-$(date +%Y%m).json

# Quarterly: Vacuum and optimize
manage vacuum
```

## Profile Selection

### Default Profile

Use `claude` profile when working with Claude Code:

```bash
export MEMORY_PROFILE=claude
```

### Cross-Agent Collaboration

For memories that should be shared across agents:

```bash
# Store in shared profile
python3 memory_tool/memory_tool.py --profile shared add \
  --project "shared-docs" \
  --title "Project onboarding guide" \
  ...

# Retrieve from shared profile
python3 memory_tool/memory_tool.py --profile shared search "onboarding"
```

## Error Handling

### Database Locked

If you encounter "database is locked":

```bash
# The database may be in use by the viewer or another process
# Wait a moment and retry, or close the viewer
```

### Import/Export Issues

```bash
# For large exports, use JSON format
export --format json --output large-export.json

# For import validation, always use dry-run first
import --input bundle.json --dry-run
```

## Advanced Features

### LLM Hooks

Configure automatic summarization:

```bash
export MEMORY_LLM_HOOK="python3 /path/to/summarize_hook.py"
```

Then use with auto-tags:

```bash
add --title "Complex discussion" --raw-file meeting.txt --auto-tags
```

### Checkpoints

Save and resume work states:

```bash
# Create checkpoint
checkpoint create --name "before-refactor" --tag "safe-point"

# List checkpoints
checkpoint list

# Resume from checkpoint (restores project and shows recent observations)
checkpoint resume checkpoint-id
```

### Web Viewer

Launch the local viewer for browsing:

```bash
python3 memory_tool/viewer.py --profile claude
# Open http://localhost:8080

# With authentication
python3 memory_tool/viewer.py --profile claude --auth-token "my-secret"
# Open http://localhost:8080?token=my-secret
```

## Examples by Scenario

### Debugging Session

```bash
# Store the bug and fix
add --project "api-service" --kind "incident" \
  --title "Race condition in user registration" \
  --summary "Concurrent registrations with same email caused unique constraint violations. Fixed by adding SELECT FOR SKIP LOCKED before INSERT." \
  --tags "race-condition,postgres,registration,bug"

# Link to related code
add --project "api-service" --kind "snippet" \
  --title "Registration race condition fix" \
  --summary "Code pattern for handling concurrent registration attempts" \
  --tags "race-condition,python,postgres"
```

### Architecture Decision

```bash
add --project "data-pipeline" --kind "decision" \
  --title "Chose Apache Kafka over RabbitMQ" \
  --summary "Selected Kafka for event streaming due to better throughput (1M+ msg/s), persistence guarantees, and replay capability. Trade-off: operational complexity." \
  --tags "kafka,architecture,event-streaming,decision-record"
```

### Performance Optimization

```bash
add --project "frontend" --kind "note" \
  --title "Bundle size optimization results" \
  --summary "Tree-shaking reduced bundle from 450KB to 180KB. Main wins: removed lodash (use native), dynamic imports for charts, lazy loading for routes." \
  --tags "performance,bundle-size,webpack,optimization"
```

## Quick Reference Card

```bash
# Initialize
python3 memory_tool/memory_tool.py --profile claude init

# Add memory
python3 memory_tool/memory_tool.py --profile claude add \
  --project "X" --kind "decision" \
  --title "Y" --summary "Z" --tags "a,b"

# Search
python3 memory_tool/memory_tool.py --profile claude search "query"

# Recent activity
python3 memory_tool/memory_tool.py --profile claude timeline

# Stats
python3 memory_tool/memory_tool.py --profile claude manage stats

# Export
python3 memory_tool/memory_tool.py --profile claude export --format json --output backup.json
```

## See Also

- `GENERAL_MEMORY.md` - General usage guide
- `CODEX_CLAUDE_INSTALL.md` - Installation and setup
- `../tests/BDD_TESTING_GUIDE.md` - Testing guide (if contributing)
