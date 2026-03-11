"""Compatibility shim for historical ``memory_tool.core.links`` imports."""
from __future__ import annotations

from memory_tool.links import (
    create_link,
    delete_link,
    find_similar_observations,
    get_links_for_observations,
    get_related_observations,
)

__all__ = [
    "create_link",
    "delete_link",
    "find_similar_observations",
    "get_links_for_observations",
    "get_related_observations",
]
