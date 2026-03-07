# los-memory v2.0.0

Local SQLite memory tool for Codex and Claude Code workflows.

## Architecture (v2.0.0+)

```
┌─────────────────────────────────────────────────────────┐
│  Core (稳定, 完整兼容)                                    │
│  observation, session, checkpoint, feedback, link       │
├─────────────────────────────────────────────────────────┤
│  Extensions (实验性, 可禁用)                               │
│  incident, recovery, knowledge, attribution [EXT]       │
├─────────────────────────────────────────────────────────┤
│  Migrating (12个月移除)                                   │
│  approval [DEPRECATED] → VPS Agent Web                  │
└─────────────────────────────────────────────────────────┘
```

**核心能力** (始终启用, 向后兼容):
- Durable memory records (`add`, `ingest`)
- Record maintenance (`edit`, `delete`)
- Fast retrieval (`search`, `timeline`, `get`, `list`)
- Session & Checkpoint management
- Feedback & linking

**扩展能力** (默认启用, 可禁用):
- Incident/Recovery management (`incident`, `recovery`)
- Knowledge base (`knowledge`)
- Attribution analysis (`attribution`)

**管理扩展**:
```bash
# 列出扩展
los-memory admin extensions list

# 禁用扩展
export MEMORY_DISABLE_EXTENSIONS="incident,recovery"
```

详见 [EXTENSIONS.md](docs/EXTENSIONS.md)

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

### v2.0.0+ CLI (推荐)
```bash
# 初始化
los-memory init --profile codex

# 添加观察记录
los-memory memory add --title "First note" --summary "Hello"
los-memory observation add --title "API设计决策" --summary "使用REST而非GraphQL"

# 检索
los-memory memory search "hello"
los-memory memory list --limit 10
los-memory memory get 1

# 编辑和删除
los-memory memory edit --id 1 --summary "Hello updated"
los-memory memory delete 1 --execute

# 会话和检查点
los-memory session start --description "Sprint planning"
los-memory checkpoint create --name "before-refactor"

# 管理
los-memory admin doctor
los-memory admin extensions list
```

### 传统 CLI (向后兼容)
```bash
python3 memory_tool/memory_tool.py --profile codex init
python3 memory_tool/memory_tool.py --profile codex add --title "First note" --summary "Hello" --auto-tags
python3 memory_tool/memory_tool.py --profile codex edit --id 1 --summary "Hello updated"
python3 memory_tool/memory_tool.py --profile codex search "hello"
python3 memory_tool/memory_tool.py --profile codex delete "1" --dry-run
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

## AI Agent usage guide
See `docs/AI_USAGE_GUIDE.md` for comprehensive guidance on how AI agents should use this tool effectively, including best practices, workflows, and examples.

## Makefile shortcuts
```bash
make help
make init-codex
make init-claude
make stats PROFILE=codex
```

## Latency benchmark
```bash
python3 memory_tool/benchmark.py --iterations 20
```

## Skill layout
- `skills/memory-retrieval/` contains retrieval guidance and references.
