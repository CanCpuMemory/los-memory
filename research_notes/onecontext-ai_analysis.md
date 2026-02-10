# OneContext AI (onecontext-ai npm) - Research Notes

## Overview

**onecontext-ai** is an npm wrapper package for the Python package `aline-ai`. It provides a bridge that allows installing and running the OneContext AI tool (a Python CLI) via npm, making it accessible to Node.js users without directly managing Python dependencies.

- **npm Package**: `onecontext-ai`
- **Python Package**: `aline-ai`
- **Latest Version**: 0.8.3
- **Repository**: https://github.com/TheAgentContextLab/OneContext
- **Homepage**: https://one-context.com/
- **License**: MIT

---

## What It Does

OneContext is an **Agent Self-Managed Context layer** - a unified context system for AI agents that:

1. **Records agent trajectories** - Captures agent work sessions with automatic summaries
2. **Enables context sharing** - Share agent context via Slack or shareable links
3. **Allows context loading** - Anyone can continue from the same context state

---

## Architecture

### NPM Wrapper Architecture

The npm package is a thin Node.js wrapper around a Python CLI tool:

```
User CLI Command
      ↓
  npm binary (bin/*.js)
      ↓
  run.js wrapper
      ↓
  Python binary (onecontext/oc)
      ↓
  aline-ai Python package
```

### Package Structure

```
onecontext-ai/
├── bin/
│   ├── default.js      # Entry for "onecontext-ai" command
│   ├── onecontext.js   # Entry for "onecontext" command
│   └── oc.js           # Entry for "oc" command
├── postinstall.js      # Installs Python package on npm install
├── run.js              # Runtime wrapper that proxies to Python
├── package.json
└── README.md
```

---

## How It Works

### 1. Installation Flow (postinstall.js)

When you run `npm install -g onecontext-ai`:

1. **Detects Python package manager** - Tries in order: `uv` > `pipx` > `pip3` > `pip`
2. **Installs `aline-ai`** Python package using the detected manager
3. **Discovers binary paths** - Finds `onecontext` and `oc` executables in PATH
   - Excludes npm directories to avoid circular references
   - Uses `isOwnBinary()` to prevent resolving to itself via symlinks
4. **Writes configuration** - Saves paths to `.paths.json` and records metadata to `~/.aline/install-state.json`
5. **Prompts user** - Suggests running `onecontext init` to complete setup

### 2. Runtime Flow (run.js)

When you run any onecontext command:

1. **Resolves binary** using tiered lookup:
   - First checks `.paths.json` cache
   - Then searches PATH excluding npm directories
   - Uses `which -a` (Unix) or `where` (Windows)

2. **Safety checks**:
   - `GUARD_ENV` environment variable prevents recursion
   - If set, exits with: "Circular reference detected"

3. **Execution**:
   - Spawns Python binary with `stdio: "inherit"`
   - Forwards signals (`SIGINT`, `SIGTERM`, `SIGHUP`) to child
   - Propagates exit codes/signals back to parent

4. **Error handling**:
   - If binary not found: "Please install the Python package first:\n  pip install aline-ai"

---

## Available Commands

After installation, three equivalent CLI commands are available:

| Command | Entry Point | Description |
|---------|-------------|-------------|
| `onecontext-ai` | `bin/default.js` | Full name command |
| `onecontext` | `bin/onecontext.js` | Short name command |
| `oc` | `bin/oc.js` | Abbreviated command |

All commands proxy to the underlying Python CLI.

### Core Python Commands (from Documentation)

- `onecontext init` - Initialize setup
- `onecontext version` - Show version
- `onecontext update` - Update to latest version
- `onecontext doctor --fix-upgrade` - Repair broken upgrades
- `onecontext --help` - Show help

---

## Prerequisites

- **Node.js**: >= 16
- **Python**: 3.8+
- **Python Package Manager**: One of `uv`, `pipx`, `pip3`, or `pip`

---

## Key Implementation Details

### Binary Wrapper (bin/*.js)
```javascript
#!/usr/bin/env node
"use strict";
const { run } = require("../run.js");
run("onecontext");  // or "oc" for oc.js
```

### Path Resolution Strategy
1. Check `.paths.json` cache file
2. Search PATH with npm directories excluded
3. Filter out self-references using `isOwnBinary()`
4. Fallback to error message if not found

### Package Manager Detection Priority
```
uv > pipx > pip3 > pip
```

### Safety Mechanisms
- **Recursion Guard**: `GUARD_ENV` environment variable
- **Self-Detection**: `isOwnBinary()` checks if path resolves within package directory
- **PATH Filtering**: Excludes npm global/local bin directories from search

---

## Installation State

The wrapper maintains state at `~/.aline/install-state.json`:
- Python package manager used
- Installation timestamp
- Version information

---

## Troubleshooting Commands

| Issue | Solution |
|-------|----------|
| Broken upgrade | `onecontext doctor --fix-upgrade && onecontext update` |
| Stale links | `npm rebuild onecontext-ai` |
| Command not found | Check PATH with `which onecontext` |

---

## Comparison with Similar Tools

| Aspect | onecontext-ai | pyright |
|--------|---------------|---------|
| Purpose | npm wrapper for Python CLI | npm wrapper for Python CLI |
| Python Package | `aline-ai` | `pyright` |
| Auto-install | Yes (postinstall.js) | Yes |
| Binaries | `onecontext`, `oc` | `pyright`, `pyright-langserver` |
| Package Manager Priority | uv > pipx > pip3 > pip | pip only |

---

## Research Summary

OneContext AI is a novel approach to making Python CLI tools accessible through npm. The architecture demonstrates:

1. **Smart Package Manager Detection** - Prioritizes modern Python tools (uv, pipx) over pip
2. **Defensive Programming** - Multiple safeguards against recursion and self-reference
3. **Transparent Proxying** - Full stdio inheritance and signal forwarding
4. **State Management** - Caches binary paths for faster subsequent launches

The underlying `aline-ai` Python package provides a web-based context management system for AI agents with features like session recording, automatic summarization, and Slack integration. However, detailed API documentation for the Python package is not publicly accessible.

---

## References

- npm Package: https://www.npmjs.com/package/onecontext-ai
- Source Code: https://github.com/TheAgentContextLab/OneContext/tree/main/npm/onecontext-ai
- Website: https://one-context.com/
- Unpkg Browse: https://unpkg.com/onecontext-ai@0.8.3/
