"""Contracts and schemas for los-memory.

This module defines the interface contracts between core and extensions,
as well as external integration contracts.

Contracts:
  - Core/Extension boundary interfaces
  - Database schema versions
  - API contract definitions
  - Event/message schemas
"""
from __future__ import annotations

from memory_tool.database import SCHEMA_VERSION as DATABASE_SCHEMA_VERSION

# Version of the core/extension interface contract
CONTRACT_VERSION = "1.0.0"

# Schema version for database (single source of truth: memory_tool.database)
SCHEMA_VERSION = DATABASE_SCHEMA_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "SCHEMA_VERSION",
]
