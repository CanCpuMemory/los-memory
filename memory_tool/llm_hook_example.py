#!/usr/bin/env python3
"""Example hook for MEMORY_LLM_HOOK.

Reads JSON from stdin and returns JSON with optional overrides.
Replace this with an actual LLM call if desired.
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    payload = json.load(sys.stdin)
    summary = payload.get("summary", "")
    raw = payload.get("raw", "")
    if not summary and raw:
        summary = raw[:200]
    result = {
        "summary": summary,
        "tags": payload.get("tags", ""),
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
