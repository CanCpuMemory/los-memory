"""Utility functions for the memory tool."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from typing import List

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

PROFILE_DB_PATHS = {
    "codex": "~/.codex_memory/memory.db",
    "claude": "~/.claude_memory/memory.db",
    "shared": "~/.local/share/llm-memory/memory.db",
}
PROFILE_CHOICES = tuple(PROFILE_DB_PATHS.keys())
DEFAULT_PROFILE = os.environ.get("MEMORY_PROFILE", "codex").strip().lower() or "codex"
if DEFAULT_PROFILE not in PROFILE_DB_PATHS:
    DEFAULT_PROFILE = "codex"

TAG_BLACKLIST = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "if", "in", "into", "is", "it", "its", "of", "on",
    "or", "over", "that", "the", "their", "this", "to", "under", "was",
    "were", "with",
}

DEFAULT_LLM_HOOK = os.environ.get("MEMORY_LLM_HOOK", "")


def utc_now() -> str:
    """Get current UTC time in ISO format."""
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


def resolve_db_path(profile: str, explicit_db: str | None) -> str:
    """Resolve database path from profile or explicit path."""
    if explicit_db:
        return os.path.expanduser(explicit_db)
    profile_name = (profile or DEFAULT_PROFILE).strip().lower()
    if profile_name not in PROFILE_DB_PATHS:
        raise ValueError(f"Unknown profile '{profile_name}'. Expected one of: {', '.join(PROFILE_CHOICES)}")
    return os.path.expanduser(PROFILE_DB_PATHS[profile_name])


def normalize_text(value: str) -> str:
    """Normalize whitespace in text."""
    return re.sub(r"\s+", " ", value).strip()


def stem_token(token: str) -> str:
    """Simple stemming for common suffixes."""
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            return token[: -len(suffix)]
    return token


def normalize_tags_list(tags: object) -> List[str]:
    """Normalize tags to a list of unique, stemmed, lowercase strings."""
    if tags is None:
        return []
    if isinstance(tags, list):
        candidates = [str(tag) for tag in tags]
    elif isinstance(tags, str):
        raw = tags.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, list):
                    candidates = [str(tag) for tag in loaded]
                else:
                    candidates = [str(loaded)]
            except json.JSONDecodeError:
                candidates = [part.strip() for part in raw.split(",")]
        else:
            candidates = [part.strip() for part in raw.split(",")]
    else:
        candidates = [str(tags)]

    normalized: List[str] = []
    seen: set[str] = set()
    for token in candidates:
        clean = normalize_text(token).lower()
        if not clean:
            continue
        clean = stem_token(clean)
        if clean in TAG_BLACKLIST:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


def tags_to_json(tags_list: List[str]) -> str:
    """Convert tags list to JSON string."""
    return json.dumps(tags_list, ensure_ascii=False)


def tags_to_text(tags_list: List[str]) -> str:
    """Convert tags list to space-separated text."""
    return " ".join(tags_list)


def parse_tags_json(tags_json: str) -> List[str]:
    """Parse JSON string to tags list."""
    if not tags_json:
        return []
    try:
        result = json.loads(tags_json)
        if isinstance(result, list):
            return result
        return []
    except json.JSONDecodeError:
        return []


def auto_tags_from_text(title: str, summary: str, limit: int = 6) -> List[str]:
    """Auto-generate tags from title and summary."""
    text = normalize_text(f"{title} {summary}").lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9\\-]{2,}", text)
    counts: dict[str, int] = {}
    for token in tokens:
        token = stem_token(token)
        if token in TAG_BLACKLIST:
            continue
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [tag for tag, _ in ranked[:limit]]


def parse_ids(ids_raw: str) -> List[int]:
    """Parse comma-separated IDs into unique list of integers."""
    ids = [int(part.strip()) for part in ids_raw.split(",") if part.strip()]
    unique_ids: List[int] = []
    seen: set[int] = set()
    for item in ids:
        if item in seen:
            continue
        seen.add(item)
        unique_ids.append(item)
    return unique_ids


def run_llm_hook(payload: dict, hook_cmd: str | List[str]) -> dict:
    """Run LLM hook command and return result."""
    if not hook_cmd:
        return {}
    if isinstance(hook_cmd, str):
        try:
            cmd_parts = shlex.split(hook_cmd)
        except ValueError:
            return {}
    else:
        cmd_parts = list(hook_cmd)
    if not cmd_parts:
        return {}
    try:
        proc = subprocess.run(
            cmd_parts,
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return {}
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def quote_fts_query(query: str) -> str:
    """Quote query for safe FTS parsing."""
    escaped = query.replace('"', '""')
    return f'"{escaped}"'
