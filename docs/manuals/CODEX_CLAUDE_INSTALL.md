# Codex and Claude Code Setup

This guide shows how to install and use `los-memory` from Codex and Claude Code environments.

## 1) Clone and initialize
```bash
git clone <your-repo-url> los-memory
cd los-memory
python3 -m memory_tool --profile codex init
python3 -m memory_tool --profile claude init
```

Optional shared DB:
```bash
python3 -m memory_tool --profile shared init
```

## 2) Optional defaults
Set default profile for your shell session:

```bash
export MEMORY_PROFILE=codex
```

Then commands can omit `--profile`.

## 3) Use from Codex
Common commands:

```bash
python3 -m memory_tool --profile codex observation add --project app --kind note --title "Deploy fix" --summary "Use migration flag"
python3 -m memory_tool --profile codex memory search "deploy fix"
python3 -m memory_tool --profile codex admin manage stats
```

Skill files live at `skills/memory-retrieval/` for retrieval-oriented workflows.

## 4) Use from Claude Code
Common commands:

```bash
python3 -m memory_tool --profile claude observation add --project api --kind decision --title "Timeout budget" --summary "30s total request timeout"
python3 -m memory_tool --profile claude memory search "timeout"
python3 -m memory_tool --profile claude admin manage tags
```

Agent prompt metadata includes `skills/memory-retrieval/agents/anthropic.yaml`.

## 5) Add, edit, delete, retrieve
Add:

```bash
python3 -m memory_tool --profile codex observation add --title "T1" --summary "S1"
```

Edit by id:

```bash
python3 -m memory_tool --profile codex observation edit --id 1 --summary "Updated summary" --tags "ops,incident"
```

Delete by ids:

```bash
python3 -m memory_tool --profile codex observation delete "1,2" --dry-run
python3 -m memory_tool --profile codex observation delete "1,2"
```

Retrieve:

```bash
python3 -m memory_tool --profile codex memory search "incident"
python3 -m memory_tool --profile codex memory timeline --around-id 10 --window-minutes 120
python3 -m memory_tool --profile codex memory get "10,11"
```

## 6) Clean and manage
Preview cleanup:

```bash
python3 -m memory_tool --profile codex memory clean --older-than-days 90 --dry-run
```

Apply cleanup:

```bash
python3 -m memory_tool --profile codex memory clean --older-than-days 90 --project api
```

Manage:

```bash
python3 -m memory_tool --profile codex admin manage stats
python3 -m memory_tool --profile codex admin manage projects --limit 10
python3 -m memory_tool --profile codex admin manage tags --limit 20
python3 -m memory_tool --profile codex admin manage vacuum
```

## 7) Makefile shortcuts
```bash
make init-codex
make init-claude
make stats PROFILE=codex
make clean-preview PROFILE=claude DAYS=30
make viewer PROFILE=shared PORT=37777
```

## 8) Viewer
```bash
python3 -m memory_tool.viewer --profile codex
python3 -m memory_tool.viewer --profile claude --auth-token "secret"
```
