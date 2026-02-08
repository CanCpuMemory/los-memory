# Memory Tool CLI Reference

## Location
`memory_tool/memory_tool.py`

## Global Flags
- `--db <path>`: SQLite database path (default: `~/.codex_memory/memory.db`)

## Commands

### init
Initialize the database schema.

```bash
python3 memory_tool/memory_tool.py init --db <db_path>
```

### add
Add an observation record.

```bash
python3 memory_tool/memory_tool.py add \
  --title "<title>" \
  --summary "<summary>" \
  --project "cantool" \
  --kind "note" \
  --tags "tag1,tag2" \
  --auto-tags \
  --llm-hook "<shell command>" \
  --raw "<raw payload>" \
  --timestamp "2026-02-08T00:00:00Z" \
  --db <db_path>
```

Notes:
- `--auto-tags` derives tags from title/summary when tags is empty.
- `--llm-hook` (or `MEMORY_LLM_HOOK` env var) runs a command that reads JSON from stdin and returns JSON with optional `title`, `summary`, and `tags` fields.

Example hook:

```bash
export MEMORY_LLM_HOOK="python3 memory_tool/llm_hook_example.py"
```

### search
Returns summary results with IDs and optional scores.

```bash
python3 memory_tool/memory_tool.py search "<query>" --limit 10 --db <db_path>
```

### timeline
Returns observations in a time window (timestamp or around an ID).

```bash
python3 memory_tool/memory_tool.py timeline --around-id 42 --window-minutes 120 --db <db_path>
```

```bash
python3 memory_tool/memory_tool.py timeline --start 2026-02-01T00:00:00Z --end 2026-02-08T00:00:00Z --db <db_path>
```

### get
Fetch full records by ID.

```bash
python3 memory_tool/memory_tool.py get "1,2,3" --db <db_path>
```

### ingest helper
Convenience wrapper that reads stdin or a file and calls `add`.

```bash
cat build.log | python3 memory_tool/ingest.py --auto-tags --db <db_path>
```

```bash
python3 memory_tool/ingest.py --raw-file notes.txt --title "Meeting notes" --summary "Standup summary" --db <db_path>
```

### viewer
Run the local-only viewer UI.

```bash
python3 memory_tool/viewer.py --db <db_path>
```

## Output
Commands return JSON with an `ok` field and `results` for list-returning operations.
