"""cwd alias canonicalization for the ledger's project identity.

``evolve`` names a project directory ``sha1(<literal cwd>)[:12]``. A repository
that moved (``~/Downloads/projects/x`` → ``~/syncfolder/project/x``) therefore
lands in a *second* project directory, and a session whose cwd is the old path
silently sees none of the project's KEY entries. ``evolve`` fixed this on the
write side (``lib/path-alias.js`` + ``cwdCanonicalization: true``); the ledger
read path needs the same rule, or the injection surface loses the project KEY
track for exactly the sessions that run in a stale/aliased directory.

Rules (deliberately identical to ``dsh-memory-evolve/lib/path-alias.js`` so the
two stores cannot disagree about which project a session is in):

1. exact match first, then the **longest** declared prefix;
2. a prefix only matches on a ``/`` boundary (``/a/b`` never matches ``/a/bc``);
3. **at most one hop** — an alias whose target is itself an alias key stays put
   (chasing chains turns a bad table into silent data movement);
4. self-mappings (``from == to``) are dropped.

Failure policy: never guess. A missing / unreadable / malformed table yields the
identity mapping **plus a machine-readable reason**, so callers can tell "no
table" / "table is broken" / "cwd simply is not mapped" apart — the three cases
have different remedies and must not collapse into one.
"""

from __future__ import annotations

import json
import os
from typing import Any

__all__ = [
    "DEFAULT_ALIAS_MAP",
    "ALIAS_REASONS",
    "normalize_path",
    "parse_alias_map",
    "canonical_cwd",
]

#: Same table ``dsh-memory-evolve`` reads (schema v2, produced by
#: ``dsfolder/scripts/path-split-report.mjs --write``).
DEFAULT_ALIAS_MAP = "~/.dsh/storages/path-alias-map.json"

#: Reasons a canonicalization could not be established. ``None`` (not listed)
#: means "the table was usable"; an unmapped cwd is *normal*, not a reason.
ALIAS_REASONS = ("alias_map_missing", "alias_map_unreadable", "alias_map_malformed")


def normalize_path(path: Any) -> str | None:
    """Trim trailing slashes so ``/a/b/`` and ``/a/b`` compare equal."""
    if not isinstance(path, str) or path == "":
        return None
    if path == "/":
        return path
    return path.rstrip("/") or "/"


def parse_alias_map(raw: Any) -> list[tuple[str, str]]:
    """Return ``(from, to)`` pairs, longest ``from`` first, self-mappings dropped.

    Tolerates unknown/missing fields and skips unusable rows instead of failing
    the whole table: one bad row must not disable canonicalization.
    """
    if not isinstance(raw, dict):
        return []
    declared = raw.get("declared")
    if not isinstance(declared, list):
        return []
    pairs: list[tuple[str, str]] = []
    for item in declared:
        if not isinstance(item, dict):
            continue
        src = normalize_path(item.get("from"))
        dst = normalize_path(item.get("to"))
        if src is None or dst is None or src == dst:
            continue
        pairs.append((src, dst))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return pairs


def _load_pairs(map_path: str | None) -> tuple[list[tuple[str, str]], str | None]:
    if not map_path:
        # Explicitly disabled is not the same as "the table is missing": the caller
        # asked for the identity mapping, so there is nothing to report.
        return [], None
    path = os.path.abspath(os.path.expanduser(map_path))
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return [], "alias_map_missing"
    except OSError:
        return [], "alias_map_unreadable"
    except json.JSONDecodeError:
        return [], "alias_map_malformed"
    if not isinstance(raw, dict):
        return [], "alias_map_malformed"
    return parse_alias_map(raw), None


def canonical_cwd(cwd: str, map_path: str | None = DEFAULT_ALIAS_MAP) -> tuple[str, str | None, str | None]:
    """Map ``cwd`` onto its current location before the project hash is computed.

    Args:
        cwd: Absolute working directory (already ``expanduser``/``abspath``-ed by the caller).
        map_path: Alias table path; ``None`` disables canonicalization.

    Returns:
        ``(canonical_cwd, applied_from, reason)`` — ``applied_from`` is the alias
        key that fired (``None`` when the cwd was already canonical or unmapped);
        ``reason`` is one of :data:`ALIAS_REASONS` when the table was unusable.
    """
    normalized = normalize_path(cwd) or cwd
    pairs, reason = _load_pairs(map_path)
    if reason is not None:
        return normalized, None, reason
    if not pairs:
        return normalized, None, None
    for src, dst in pairs:
        if normalized == src:
            return dst, src, None
    for src, dst in pairs:
        # ``src + "/"`` is the boundary test: /a/b must not match /a/bc.
        if normalized.startswith(src + "/"):
            return dst + normalized[len(src):], src, None
    return normalized, None, None
