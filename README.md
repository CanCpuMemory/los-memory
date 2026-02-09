# los-memory

Standalone SQLite-backed memory tool with a local-only web viewer and a Codex retrieval skill.

## Layout
- `memory_tool/` – CLI, ingestion helper, viewer, and LLM hook example
- `skills/memory-retrieval/` – Codex skill for search/retrieval

## Quick start
```bash
python3 memory_tool/memory_tool.py init
python3 memory_tool/memory_tool.py add --title "First note" --summary "Hello" --auto-tags
python3 memory_tool/memory_tool.py search "hello"
python3 memory_tool/memory_tool.py list --limit 5
```

## Local viewer
```bash
python3 memory_tool/viewer.py --db ~/.codex_memory/memory.db
python3 memory_tool/viewer.py --db ~/.codex_memory/memory.db --auth-token "mytoken"
```

When using `--auth-token`, open the viewer with `?token=...` in the URL
or send an `Authorization: Bearer ...` header for API calls.

## Export
```bash
python3 memory_tool/memory_tool.py export --format json --output export.json
python3 memory_tool/memory_tool.py export --format csv --output export.csv
```

## LLM hook (optional)
```bash
export MEMORY_LLM_HOOK="python3 memory_tool/llm_hook_example.py"
python3 memory_tool/memory_tool.py add --title "Hooked" --summary "" --raw "raw text" --auto-tags
```
