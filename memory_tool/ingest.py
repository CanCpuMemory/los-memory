#!/usr/bin/env python3
"""Ingest helper for the memory tool.

Reads raw input (stdin or --raw-file), derives title/summary if missing,
then calls the memory_tool API directly with optional auto-tagging/LLM hook.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Bootstrap: add parent directory to path for imports when run as script
# This ensures `python3 memory_tool/ingest.py` works correctly
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

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
    from memory_tool.database import connect_db, ensure_schema, ensure_fts
    from memory_tool.operations import add_observation
    from memory_tool.utils import tags_to_json, tags_to_text, normalize_tags_list, auto_tags_from_text

    parser = argparse.ArgumentParser(description="Ingest helper for memory tool")
    parser.add_argument("--profile", choices=["codex", "claude", "shared"], default="codex")
    parser.add_argument("--db", default=None, help="SQLite database path (overrides --profile)")
    parser.add_argument("--project", default="general")
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

    # Resolve database path
    if args.db:
        db_path = args.db
    else:
        from memory_tool.utils import get_profile_db_path
        db_path = get_profile_db_path(args.profile)

    # Connect and ensure schema
    conn = connect_db(db_path)
    ensure_schema(conn)
    ensure_fts(conn)

    # Process tags
    tags_list = normalize_tags_list(args.tags)
    if args.auto_tags:
        auto_tags = auto_tags_from_text(title, summary)
        tags_list = list(set(tags_list + auto_tags))

    # Add observation
    obs_id = add_observation(
        conn,
        timestamp="now",
        project=args.project,
        kind=args.kind,
        title=title,
        summary=summary,
        tags=tags_to_json(tags_list),
        tags_text=tags_to_text(tags_list),
        raw=raw,
        session_id=None,
    )

    conn.close()
    print(f"Added observation {obs_id}")


if __name__ == "__main__":
    main()
