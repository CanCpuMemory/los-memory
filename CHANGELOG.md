# Changelog

All notable changes to los-memory will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0] - 2026-03-07

### Overview

Architecture Convergence Release - Core/Extension/Migrating three-layer separation.

### Added

#### Core Architecture
- **Core module structure** (`memory_tool/core/`)
  - Core operations migrated to `core/operations.py`
  - Session management in `core/sessions.py`
  - Checkpoint management in `core/checkpoints.py`
  - Feedback processing in `core/feedback.py`
  - Link management in `core/links.py`
  - Analytics in `core/analytics.py`
  - Core CLI handlers in `core/cli.py`

#### Extension System
- **Extension framework** (`memory_tool/extensions/`)
  - Static registration mechanism (no dynamic import)
  - Environment-based disabling via `MEMORY_DISABLE_EXTENSIONS`
  - Experimental status warnings
  - Extension management commands: `admin extensions list/status`

- **Incident extension** (`extensions/incident/`)
  - Full module structure with models and CLI
  - Incident lifecycle management
  - Marked as experimental

- **Recovery extension** (`extensions/recovery/`)
  - Recovery action management
  - Policy management
  - Marked as experimental

- **Knowledge extension** (`extensions/knowledge/`)
  - Knowledge base management
  - Marked as experimental

- **Attribution extension** (`extensions/attribution/`)
  - Root cause analysis support
  - Marked as experimental, binds to Incident

#### Migrating Out
- **Approval migration preparation** (`migrate_out/approval/`)
  - Deprecation warnings on import and use
  - 12-month migration timeline to VPS Agent Web
  - Documentation: `docs/MIGRATION_APPROVAL.md`

#### Documentation
- **EXTENSIONS.md** - Comprehensive extension guide
- **MIGRATION_APPROVAL.md** - Approval migration timeline
- Updated **ARCHITECTURE_BOUNDARY_SPEC.md** with extension section
- Updated **README.md** with architecture diagram

#### CLI Improvements
- Added `[EXT]` marker to extension commands in help
- Added `[DEPRECATED]` marker to approval command
- New `admin extensions` command group
- `__main__.py` for `python -m memory_tool` execution

### Changed

- **Architecture**: Clear separation of core vs extension capabilities
- **CLI Help**: Extension commands now marked with `[EXT]`
- **Version**: Bumped to 2.0.0 to reflect architecture changes

### Deprecated

- **Approval system** - Moving to VPS Agent Web
  - Timeline: 12 months (target removal: 2027-03-07)
  - Phase 1 (now): Freeze features, add warnings
  - Phase 2 (4-8m): Parallel build in VPS Agent Web
  - Phase 3 (9-12m): Data migration and removal

### Compatibility

| Capability Tier | Compatibility Promise |
|----------------|---------------------|
| Core (observation, session, checkpoint, feedback, link, tool) | Full backward compatibility |
| Extension (incident, recovery, knowledge, attribution) | Best effort, may change |
| Migrating (approval) | 12 months then removed |

---

## [0.2.0] - Earlier

### Added
- Initial implementation
- Observation CRUD
- Session management
- Checkpoint management
- Feedback system
- Link management
- Tool tracking
- Viewer UI
- BDD tests

---

[Unreleased]: https://github.com/yourusername/los-memory/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/yourusername/los-memory/compare/v0.2.0...v2.0.0
[0.2.0]: https://github.com/yourusername/los-memory/releases/tag/v0.2.0
