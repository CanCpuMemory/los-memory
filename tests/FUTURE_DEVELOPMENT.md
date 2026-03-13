# Future Development Guide for los-memory

This note tracks realistic next steps for the test and quality surface.
It should stay aligned with live `pytest` results, `README.md`, `TODO.md`,
and `.github/workflows/ci.yml`.

## Current Baseline

- Full local `pytest` currently covers CLI, unit, integration, and BDD flows.
- The active test layout is:
  - `tests/cli/` for CLI entrypoint and contract checks
  - `tests/integration/` for runtime and adapter smoke coverage
  - `tests/unit/` for module-level behavior
  - `tests/features/` + `tests/steps/` + `tests/test_*_bdd.py` for BDD scenarios
- CI already runs:
  - targeted Ruff checks on maintained surfaces
  - docs command lint
  - CLI contract tests
  - unit tests
  - selected integration smoke
  - approval migration E2E
  - hub-lite integration
  - BDD smoke

## Near-Term Priorities

1. Keep the suite green.
   - Fix regressions before expanding scope.
   - Prefer updating this file only after re-running `pytest`.

2. Add coverage for the current deferred product backlog.
   - Expand regression coverage for bulk write / stdin JSON paths
   - Expand regression coverage for metadata-native filters on `memory search` / `memory list`

3. Strengthen warning hygiene.
   - Keep expected approval migration deprecation warnings explicitly filtered
   - Treat new `ResourceWarning` regressions as test failures

## High-Value Test Gaps

### Functional gaps

- Empty-database behavior for more admin/reporting commands
- Very long text and high-cardinality tag inputs
- Metadata filter combinations and pagination edge cases
- Bulk ingest/write validation and dry-run rollback paths

### Integration gaps

- Cross-profile sharing workflows using explicit `--profile` boundaries
- Viewer smoke checks around auth token and API routes
- Additional writeback contract checks for upstream integrators

### Reliability gaps

- Concurrent session handling and repeated active-session switching
- Corrupted or partially migrated SQLite database handling
- Performance smoke for larger search/list datasets

## CI and Tooling Follow-Ups

- Consider adding a dedicated warning-clean test job once the suite remains stable under default warning mode.
- Expand Ruff coverage gradually from targeted maintained surfaces to broader repo slices.
- Add a lightweight developer shortcut for running the same smoke contract enforced in CI.

## Working Rules for New Features

1. Start from executable behavior.
   - Add or update unit/CLI/integration/BDD coverage before broad refactors.

2. Prefer contract-preserving refactors.
   - For CLI, storage, and migration code, preserve output shape and exit-code behavior.

3. Keep docs and tests in sync.
   - If command examples or workflow expectations change, update docs and their linted examples together.

## When To Update This File

- After the active CI matrix changes
- After the test layout changes materially
- After deferred roadmap items become implemented or obsolete
