---
name: memory-retrieval
description: Search and retrieve stored observations from the standalone memory tool (SQLite-based) for past sessions, tool runs, and notes. Use when asked to look up prior observations, timelines, or detailed records from the memory database using the memory_tool CLI.
---

# Memory Retrieval

## Overview
Use the standalone memory tool to search, timeline, and fetch stored observations as JSON. This skill is for retrieval only (not general coding changes).

## Quick Start (common workflow)
1. Run a search to get candidate observation IDs.
2. If needed, expand context with a timeline window.
3. Fetch full records by ID.

## Core Tasks

### 1) Search observations
Use the CLI search to get candidate IDs and summaries.

```bash
python3 memory_tool/memory_tool.py search "<query>" --db <db_path>
```

### 2) Launch local viewer
Run the local-only viewer (binds to 127.0.0.1:37777 by default).

```bash
python3 memory_tool/viewer.py --db <db_path>
```

### 3) Timeline around an observation
Use a window around a known ID to capture nearby context.

```bash
python3 memory_tool/memory_tool.py timeline --around-id <id> --window-minutes 120 --db <db_path>
```

### 4) Fetch full records
Use IDs from search/timeline to retrieve full JSON records.

```bash
python3 memory_tool/memory_tool.py get "1,2,3" --db <db_path>
```

## Database Location
Default DB path is `~/.codex_memory/memory.db`. Override with `--db` when needed.

## References
- See `references/cli.md` for all CLI flags and output shapes.
