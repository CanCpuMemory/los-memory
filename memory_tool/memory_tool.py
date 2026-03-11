#!/usr/bin/env python3
"""CLI entrypoint for backward compatibility.

WARNING: Python API Compatibility Notice
----------------------------------------
This file is a CLI wrapper ONLY. It does not export any Python API.

If you are importing from this file in external code:
    import memory_tool.memory_tool as m
    m.connect_db(...)  # This will BREAK - no longer available

Recommended Python API Usage:
-----------------------------
Use the official public API instead:

    from memory_tool import connect_db, add_observation
    # or
    from memory_tool import MemoryClient

    with MemoryClient(profile="codex") as client:
        obs = client.add_observation(title="...", summary="...")

CLI Usage (this file remains valid for CLI):
--------------------------------------------
    python3 memory_tool/memory_tool.py --profile codex init
    python3 memory_tool/memory_tool.py --profile codex observation add --title "..."

    # Or use the modern module syntax:
    python3 -m memory_tool --profile codex init

Migration Guide:
----------------
See docs/design/PYTHON_API_IMPROVEMENTS.md for detailed migration instructions
from the old procedural API to the new MemoryClient-based API.

Breaking Changes in v2.0.0:
---------------------------
- This file no longer re-exports Python functions (connect_db, add_observation, etc.)
- Active procedural modules live under the top-level memory_tool package
- Public API is now available through memory_tool package root only
"""
from __future__ import annotations

import sys
from pathlib import Path

# Bootstrap: add parent directory to path for imports when run as script
# This ensures `python3 memory_tool/memory_tool.py` works correctly
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Import and run the main CLI
from memory_tool.cli import main

if __name__ == "__main__":
    sys.exit(main())
