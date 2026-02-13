# Memory Tool CLI Reference

## Location
`memory_tool/memory_tool.py`

## Global Flags
- `--profile <codex|claude|shared>`: choose profile-specific default DB
- `--db <path>`: explicit SQLite database path (overrides profile)

Profile defaults:
- `codex` -> `~/.codex_memory/memory.db`
- `claude` -> `~/.claude_memory/memory.db`
- `shared` -> `~/.local/share/llm-memory/memory.db`

## Commands

### init
Initialize the database schema.

```bash
python3 memory_tool/memory_tool.py --profile codex init
```

### add
Add an observation record.

```bash
python3 memory_tool/memory_tool.py --profile codex add \
  --title "<title>" \
  --summary "<summary>" \
  --project "general" \
  --kind "note" \
  --tags "tag1,tag2" \
  --auto-tags \
  --llm-hook "<shell command>" \
  --raw "<raw payload>" \
  --timestamp "2026-02-08T00:00:00Z"
```

Notes:
- `--auto-tags` derives tags from title/summary when tags is empty.
- `--llm-hook` (or `MEMORY_LLM_HOOK`) reads JSON from stdin and may return `title`, `summary`, `tags`.

### search
Returns summary results with IDs and optional scores.

```bash
python3 memory_tool/memory_tool.py --profile codex search "<query>" --limit 10
python3 memory_tool/memory_tool.py --profile codex search "<query>" \
  --require-tags "tenant:default,user:alice"
```

### timeline
Returns observations in a time window (timestamp or around an ID).

```bash
python3 memory_tool/memory_tool.py --profile codex timeline --around-id 42 --window-minutes 120
```

```bash
python3 memory_tool/memory_tool.py --profile codex timeline --start 2026-02-01T00:00:00Z --end 2026-02-08T00:00:00Z
```

### get
Fetch full records by ID.

```bash
python3 memory_tool/memory_tool.py --profile codex get "1,2,3"
```

### edit
Edit one observation by id.

```bash
python3 memory_tool/memory_tool.py --profile codex edit --id 10 --summary "Updated text" --tags "ops,incident"
```

Editable fields:
- `--timestamp`
- `--project`
- `--kind`
- `--title`
- `--summary`
- `--tags`
- `--raw`
- `--auto-tags` (recompute tags from title/summary)

### delete
Delete observations by ids.

```bash
python3 memory_tool/memory_tool.py --profile codex delete "10,11" --dry-run
python3 memory_tool/memory_tool.py --profile codex delete "10,11"
```

### list
List latest observations.

```bash
python3 memory_tool/memory_tool.py --profile codex list --limit 20 --offset 0
python3 memory_tool/memory_tool.py --profile codex list \
  --require-tags "tenant:default,user:alice"
```

### transition-log
Write a structured agent transition record (`kind=agent_transition`).

```bash
python3 memory_tool/memory_tool.py --profile codex transition-log \
  --phase "review" \
  --action "check-regression" \
  --input '{"files":["a.py"]}' \
  --output '{"ok":true,"issues":0}' \
  --status success \
  --reward 1.0 \
  --project "tenant:default"
```

### review-feedback
Batch apply review findings as feedback updates.

Input JSON can be either an array or an object with `items`:

```json
{
  "items": [
    { "observation_id": 12, "feedback": "修正: ..."},
    { "id": 13, "text": "补充: ..."}
  ]
}
```

```bash
python3 memory_tool/memory_tool.py --profile codex review-feedback --file review.json
python3 memory_tool/memory_tool.py --profile codex review-feedback --file review.json --dry-run
```

### export
Export observations.

```bash
python3 memory_tool/memory_tool.py --profile codex export --format json --output export.json
python3 memory_tool/memory_tool.py --profile codex export --format csv --output export.csv
```

### clean
Delete observations with safety checks.

```bash
python3 memory_tool/memory_tool.py --profile codex clean --older-than-days 90 --dry-run
python3 memory_tool/memory_tool.py --profile codex clean --older-than-days 90 --project ops
python3 memory_tool/memory_tool.py --profile codex clean --tag temp,noise
python3 memory_tool/memory_tool.py --profile codex clean --all
```

Flags:
- `--before <ISO>` or `--older-than-days <N>`
- optional filters: `--project`, `--kind`, `--tag`
- `--dry-run` for preview
- `--vacuum` to compact DB after deletion
- requires at least one filter unless `--all` is set

### manage
Inspect and maintain memory.

```bash
python3 memory_tool/memory_tool.py --profile codex manage stats
python3 memory_tool/memory_tool.py --profile codex manage projects --limit 20
python3 memory_tool/memory_tool.py --profile codex manage tags --limit 20
python3 memory_tool/memory_tool.py --profile codex manage vacuum
```

### ingest helper
Convenience wrapper that reads stdin or a file and calls `add`.

```bash
cat build.log | python3 memory_tool/ingest.py --profile codex --auto-tags
python3 memory_tool/ingest.py --profile claude --raw-file notes.txt --title "Meeting notes"
```

### viewer
Run the local-only viewer UI.

```bash
python3 memory_tool/viewer.py --profile codex
```

## Output
Commands return JSON with `ok`; list operations include `results`.
