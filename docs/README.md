# Docs Index

This repository separates documentation by usage instead of keeping every note at the top level.

## Current

- `docs/current/`: current implemented behavior and operational truth sources
- Top-level `docs/*.md`: active guides, architecture notes, and operator-facing references still in use
- Plan/review documents kept at the top level must carry a status note that points back to `README.md` and `docs/current/CURRENT_STATE.md`

## Design

- `docs/design/`: forward-looking design documents, interface proposals, and design-review artifacts
- Broad adoption posture documents that do not describe the current implemented state also belong in `docs/design/`

## Run Artifacts

- `docs/reports/`: generated or session-specific reports that support audits and reviews
- `docs/manuals/`: operator runbooks, setup guides, integration guides, active checklists, and usage guides that are still intended for active use
- `docs/templates/`: reusable templates referenced by manuals and scripts

## Archive

- `docs/archive/`: historical implementation notes, child-session logs, and superseded summaries

When adding new docs:
- Put current behavior in `docs/current/` or a stable top-level guide.
- Put designs and proposals in `docs/design/`.
- Put timestamped session output in `docs/reports/` or `docs/archive/`.
