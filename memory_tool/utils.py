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
DEFAULT_PROFILE = os.environ.get("MEMORY_PROFILE", "claude").strip().lower() or "claude"
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


def get_profile_db_path(profile: str) -> str:
    """Get database path for a profile.

    Args:
        profile: Profile name (claude, codex, shared)

    Returns:
        Database path for the profile

    Raises:
        ValueError: If profile is unknown
    """
    profile_name = (profile or DEFAULT_PROFILE).strip().lower()
    if profile_name not in PROFILE_DB_PATHS:
        raise ValueError(f"Unknown profile '{profile_name}'. Expected one of: {', '.join(PROFILE_CHOICES)}")
    return os.path.expanduser(PROFILE_DB_PATHS[profile_name])


def normalize_text(value: str) -> str:
    """Normalize whitespace in text."""
    return re.sub(r"\s+", " ", value).strip()


def stem_token(token: str) -> str:
    """Simple stemming for common suffixes.

    Handles common English word endings:
    - running -> run (double consonant reduction)
    - files -> file (plural removal, just remove 's' not 'es')
    - configuration -> configur (ation removal)
    """
    # Handle 'ation' suffix (configuration -> configur)
    if token.endswith("ation") and len(token) > 6:
        return token[:-5]

    # Handle 'ing' suffix with double consonant reduction
    if token.endswith("ing"):
        base = token[:-3]
        if len(base) >= 3:
            # Reduce double consonant: running -> run, not runn
            if len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
            return base
        return token

    # Handle 'ed' suffix
    if token.endswith("ed"):
        base = token[:-2]
        if len(base) >= 2:
            return base
        return token

    # Handle simple 's' suffix (tests -> test, files -> file)
    # Just remove 's' at the end
    if token.endswith("s") and not token.endswith("ss"):
        base = token[:-1]
        if len(base) >= 2:
            return base

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
    """Parse comma-separated IDs into unique list of integers.

    Supports ranges like "1-3" which expands to [1, 2, 3].
    """
    ids: List[int] = []
    for part in ids_raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            # Range like "1-3"
            try:
                start, end = part.split("-", 1)
                start_val = int(start.strip())
                end_val = int(end.strip())
                ids.extend(range(start_val, end_val + 1))
            except ValueError:
                continue
        else:
            ids.append(int(part))

    # Remove duplicates while preserving order
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
    """Quote query for safe FTS parsing.

    Single words are returned as-is. Multi-word queries are quoted.
    Empty string returns empty string.
    """
    if not query:
        return ""
    # Single word - no quotes needed
    if " " not in query and "\t" not in query:
        return query
    # Multi-word - escape quotes and wrap in quotes
    escaped = query.replace('"', '""')
    return f'"{escaped}"'
