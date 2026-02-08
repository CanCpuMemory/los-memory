#!/usr/bin/env python3
"""Ingest helper for the memory tool.

Reads raw input (stdin or --raw-file), derives title/summary if missing,
then calls memory_tool.py add with optional auto-tagging/LLM hook.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

MEMORY_TOOL = Path(__file__).resolve().parent / "memory_tool.py"


def read_raw(args: argparse.Namespace) -> str:
    if args.raw_file:
        return Path(args.raw_file).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def derive_title_summary(raw: str, title: str | None, summary: str | None) -> tuple[str, str]:
    raw = raw.strip()
    first_line = raw.splitlines()[0] if raw else "Observation"
    derived_title = title or first_line[:80]
    derived_summary = summary or (raw[:240] if raw else derived_title)
    return derived_title, derived_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest helper for memory tool")
    parser.add_argument("--db", default=None, help="SQLite database path")
    parser.add_argument("--project", default="cantool")
    parser.add_argument("--kind", default="note")
    parser.add_argument("--title", default=None)
    parser.add_argument("--summary", default=None)
    parser.add_argument("--tags", default="")
    parser.add_argument("--raw-file", default=None)
    parser.add_argument("--auto-tags", action="store_true")
    parser.add_argument("--llm-hook", default=None)

    args = parser.parse_args()
    raw = read_raw(args)
    title, summary = derive_title_summary(raw, args.title, args.summary)

    cmd = [sys.executable, str(MEMORY_TOOL), "add", "--title", title, "--summary", summary]
    if args.db:
        cmd += ["--db", args.db]
    if args.project:
        cmd += ["--project", args.project]
    if args.kind:
        cmd += ["--kind", args.kind]
    if args.tags:
        cmd += ["--tags", args.tags]
    if raw:
        cmd += ["--raw", raw]
    if args.auto_tags:
        cmd += ["--auto-tags"]
    if args.llm_hook:
        cmd += ["--llm-hook", args.llm_hook]

    subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
