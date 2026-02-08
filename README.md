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
```

## Local viewer
```bash
python3 memory_tool/viewer.py --db ~/.codex_memory/memory.db
```

## LLM hook (optional)
```bash
export MEMORY_LLM_HOOK="python3 memory_tool/llm_hook_example.py"
python3 memory_tool/memory_tool.py add --title "Hooked" --summary "" --raw "raw text" --auto-tags
```
