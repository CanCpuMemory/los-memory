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

# Version of the core/extension interface contract
CONTRACT_VERSION = "1.0.0"

# Schema version for database
SCHEMA_VERSION = 12

__all__ = [
    "CONTRACT_VERSION",
    "SCHEMA_VERSION",
]
