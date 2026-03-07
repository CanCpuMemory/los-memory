"""Migrate-out directory for capabilities being moved to other projects.

This directory contains modules that are being migrated out of los-memory
to their appropriate home projects.

Current Migrations:
  approval/ -> VPS Agent Web (12-month timeline)

Status Indicators:
  - Active: Still functional, but deprecated
  - Frozen: No new features, bug fixes only
  - Dual: Running in parallel with replacement
  - Removed: Fully migrated, module deleted
"""
from __future__ import annotations
