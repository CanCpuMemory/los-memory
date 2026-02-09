# los-memory

Local SQLite memory tool for Codex and Claude Code workflows.

## What it provides
- Durable memory records (`add`, `ingest`)
- Record maintenance (`edit`, `delete`)
- Fast retrieval (`search`, `timeline`, `get`, `list`)
- Local viewer UI (`viewer.py`)
- Maintenance and cleanup (`manage`, `clean`)

## Agent profiles
Use separate default databases by profile:

- `codex`: `~/.codex_memory/memory.db`
- `claude`: `~/.claude_memory/memory.db`
- `shared`: `~/.local/share/llm-memory/memory.db`

You can set a default profile:

```bash
export MEMORY_PROFILE=codex
```

Or override with `--profile` / `--db` on each command.

## Quick start
```bash
python3 memory_tool/memory_tool.py --profile codex init
python3 memory_tool/memory_tool.py --profile codex add --title "First note" --summary "Hello" --auto-tags
python3 memory_tool/memory_tool.py --profile codex edit --id 1 --summary "Hello updated"
python3 memory_tool/memory_tool.py --profile codex search "hello"
python3 memory_tool/memory_tool.py --profile codex delete "1" --dry-run
python3 memory_tool/memory_tool.py --profile codex manage stats
python3 memory_tool/memory_tool.py --profile codex clean --older-than-days 90 --dry-run
```

## Viewer
```bash
python3 memory_tool/viewer.py --profile codex
python3 memory_tool/viewer.py --profile claude --auth-token "mytoken"
```

If using `--auth-token`, open with `?token=...` or send `Authorization: Bearer ...`.

## Export
```bash
python3 memory_tool/memory_tool.py --profile codex export --format json --output export.json
python3 memory_tool/memory_tool.py --profile claude export --format csv --output export.csv
```

## General memory usage guide
See `docs/GENERAL_MEMORY.md` for practical workflows, cleanup strategy, and management commands.

## Codex / Claude install and usage
See `docs/CODEX_CLAUDE_INSTALL.md` for setup and daily usage from each assistant.

## Makefile shortcuts
```bash
make help
make init-codex
make init-claude
make stats PROFILE=codex
```

## Skill layout
- `skills/memory-retrieval/` contains retrieval guidance and references.
