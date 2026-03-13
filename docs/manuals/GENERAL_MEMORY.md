# General Memory Tool Guide

This project is a local SQLite memory system for agents and developers.

Use it to store short durable context: decisions, incidents, debugging notes, runbooks, and snippets you want to retrieve later.

## 1) Choose a profile
The CLI supports separate memory stores by profile:

- `codex` -> `~/.codex_memory/memory.db`
- `claude` -> `~/.claude_memory/memory.db`
- `shared` -> `~/.local/share/llm-memory/memory.db`

Use `--profile` on any command, or set:

```bash
export MEMORY_PROFILE=codex
```

You can always override directly with `--db /path/to/memory.db`.

## 2) Initialize
```bash
python3 -m memory_tool --profile codex init
python3 -m memory_tool --profile claude init
```

## 3) Add memories
Direct add:

```bash
python3 -m memory_tool --profile codex observation add \
  --project "payments" \
  --kind "decision" \
  --title "Retry policy" \
  --summary "API retries capped at 3 with exponential backoff" \
  --tags "reliability,api"
```

Edit existing memory:

```bash
python3 -m memory_tool --profile codex observation edit --id 42 --summary "Updated summary" --tags "api,retry"
```

Delete by id:

```bash
python3 -m memory_tool --profile codex observation delete "42" --dry-run
python3 -m memory_tool --profile codex observation delete "42"
```

Ingest from stdin/file:

```bash
cat incident.txt | python3 -m memory_tool.ingest --profile codex --project "ops" --auto-tags
python3 -m memory_tool.ingest --profile claude --raw-file notes.md --kind "meeting"
```

## 4) Retrieve memories
Search:

```bash
python3 -m memory_tool --profile codex memory search "retry policy"
python3 -m memory_tool --profile codex memory search "retry policy" --metadata-filter '{"tenant_id":"tenant-a"}'
```

Timeline around an event:

```bash
python3 -m memory_tool --profile codex memory timeline --around-id 42 --window-minutes 180
```

Fetch full records:

```bash
python3 -m memory_tool --profile codex memory get "42,43"
```

Bulk write from stdin JSON:

```bash
printf '%s' '{"items":[{"project":"ops","kind":"decision","title":"Bulk note","summary":"Created from stdin JSON","metadata":{"tenant_id":"tenant-a","trace_id":"trace-bulk-1"}}]}' | \
python3 -m memory_tool --profile codex observation bulk --input @-
```

## 5) Manage memories
Summary stats:

```bash
python3 -m memory_tool --profile codex admin manage stats
```

Top projects:

```bash
python3 -m memory_tool --profile codex admin manage projects --limit 10
```

Top tags:

```bash
python3 -m memory_tool --profile codex admin manage tags --limit 25
```

Compact DB:

```bash
python3 -m memory_tool --profile codex admin manage vacuum
```

## 6) Clean memories safely
Start with dry run:

```bash
python3 -m memory_tool --profile codex memory clean --older-than-days 90 --dry-run
```

Delete old notes from one project:

```bash
python3 -m memory_tool --profile codex memory clean --older-than-days 90 --project ops
```

Delete by tag:

```bash
python3 -m memory_tool --profile codex memory clean --tag temp,noise
```

Delete everything (explicit):

```bash
python3 -m memory_tool --profile codex memory clean --all
```

Reclaim space after cleanup:

```bash
python3 -m memory_tool --profile codex memory clean --older-than-days 365 --vacuum
```

## 7) Viewer
```bash
python3 -m memory_tool.viewer --profile codex
python3 -m memory_tool.viewer --profile claude --auth-token "secret-token"
```

## 8) Additional Commands

### Feedback and Corrections
Provide natural language feedback to correct or supplement memories:
```bash
python3 -m memory_tool --profile codex observation feedback "修正: API密钥是yyy" --id 42
```

### Tool Memory Tracking
Log and analyze tool usage:
```bash
python3 -m memory_tool --profile codex tool log --tool search_files --input '{"q":"TODO"}' --status success
python3 -m memory_tool --profile codex tool stats
```

### Memory Linking
Create relationships between related memories:
```bash
python3 -m memory_tool --profile codex observation link --from 123 --to 456 --type refines
python3 -m memory_tool --profile codex observation related 123
```

## 9) Cross-agent workflow patterns
Separate stores:
- Keep Codex and Claude data isolated by profile.

Shared store:
- Use `--profile shared` when both agents should access the same memory.

Recommended fields:
- `project`: team/app name (`payments`, `infra`, `frontend`)
- `kind`: `note`, `decision`, `incident`, `meeting`, `todo`, `tool_call`
- `tags`: short retrieval keys (`postgres`, `rollback`, `oauth`)

## 10) Backup and export
```bash
python3 -m memory_tool --profile codex memory export --format json --output codex-memory.json
python3 -m memory_tool --profile claude memory export --format csv --output claude-memory.csv
```

For full backup, copy the DB files directly.

## 11) Shortcut commands
This repo includes a `Makefile`:

```bash
make help
make init-codex
make stats PROFILE=claude
make clean-preview PROFILE=codex DAYS=60
```
