"""Mechanical ``project`` slug lookup from the workspace project registry.

``evolve`` names a project directory ``sha1(<literal absolute cwd>)[:12]``
(``lib/store.js:483``), so the hash is reversible by hashing candidates. The
candidates come from a declared registry — the workspace topology file — which
makes this a **lookup**, not a guess.

Two rules this module exists to enforce:

* **No directory-name fallback.** An unknown hash stays unknown (the caller renders
  ``project_slug: null``). Guessing a project is how cross-project isolation breaks.
* **A missing or unrecognised registry is a judgement failure**, not "no projects".
  The import path turns that into exit ``2``; the read path (``mem snapshot``) may
  degrade with a named reason instead, because a read must not fail because an
  optional mapping file is absent.

The registry shape handled here is the one ``tools/check-project-registry.mjs``
validates: a top-level object with ``projects`` as a list of entries carrying
``key`` and either ``path`` or (nested) ``children``. A child without its own
``path`` lives under its parent's directory and is derived as
``<parent path>/<child key>`` — that is the registry's own nesting contract, not a
guess.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .schema import LedgerUnavailableError

__all__ = ["DEFAULT_REGISTRY", "project_hash", "load_project_slugs", "try_load_project_slugs"]

#: The workspace topology registry: the single source of truth for project roots.
DEFAULT_REGISTRY = "~/syncfolder/project/los-workspace/.workspace/projects.json"


def project_hash(path: str) -> str:
    """Return the ``evolve`` project directory name for a literal absolute path."""
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


def load_project_slugs(path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Build ``{evolve directory hash: registry key}`` from the project registry.

    Args:
        path: Registry path; defaults to :data:`DEFAULT_REGISTRY`.

    Returns:
        Mapping from a 12-hex project directory name to the registry ``key``.

    Raises:
        LedgerUnavailableError: The registry is missing, unreadable or does not have
            the expected shape (``projects`` list with ``key`` entries). Callers that
            treat the mapping as optional should use :func:`try_load_project_slugs`.
    """
    registry_path = Path(path or DEFAULT_REGISTRY).expanduser()
    if not registry_path.is_file():
        raise LedgerUnavailableError("project_registry_missing", path=str(registry_path))
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerUnavailableError(
            "project_registry_unreadable", path=str(registry_path), detail=str(exc)
        ) from exc

    projects = payload.get("projects") if isinstance(payload, dict) else None
    if not isinstance(projects, list):
        raise LedgerUnavailableError("project_registry_unrecognized", path=str(registry_path))

    slugs: dict[str, str] = {}
    for key, project_path in _walk_projects(projects):
        slugs[project_hash(project_path)] = key
    if not slugs:
        raise LedgerUnavailableError("project_registry_unrecognized", path=str(registry_path))
    return slugs


def try_load_project_slugs(
    path: str | os.PathLike[str] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Best-effort variant for read commands: never raise, report why instead.

    Returns:
        ``(mapping, info)`` where ``info`` carries ``source``, ``available`` and, on
        failure, ``reason``. An unavailable map yields ``{}`` — callers must not read
        an empty mapping as "no project is registered".
    """
    registry_path = str(Path(path or DEFAULT_REGISTRY).expanduser())
    try:
        return load_project_slugs(registry_path), {"source": registry_path, "available": True}
    except LedgerUnavailableError as exc:
        return {}, {
            "source": registry_path,
            "available": False,
            "reason": getattr(exc, "detail_code", "project_registry_unavailable"),
        }


def _walk_projects(
    projects: list[Any], parent_path: str | None = None
) -> list[tuple[str, str]]:
    """Flatten the registry tree into ``(key, absolute path)`` pairs."""
    found: list[tuple[str, str]] = []
    for item in projects:
        if not isinstance(item, dict):
            continue
        raw_key = item.get("key")
        key = raw_key if isinstance(raw_key, str) and raw_key.strip() else None
        raw_path = item.get("path")
        resolved = raw_path if isinstance(raw_path, str) and raw_path.strip() else None
        if resolved is None and key and parent_path:
            resolved = os.path.join(parent_path, key)
        if key and resolved:
            found.append((key.strip(), resolved))
        children = item.get("children")
        if isinstance(children, list):
            found.extend(_walk_projects(children, resolved or parent_path))
    return found
