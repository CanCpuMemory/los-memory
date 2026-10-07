# Docs Index

This repository separates documentation by usage instead of keeping every note at the top level.

## Current

- `docs/current/`: current implemented behavior and operational truth sources
- Top-level `docs/*.md`: active guides, architecture notes, and operator-facing references still in use
- Plan/review documents kept at the top level must carry a status note that points back to `README.md` and `docs/current/CURRENT_STATE.md`

## Design

- [Memory service architecture](design/memory-service-architecture.md): proposed identities, scopes, APIs, consistency and topology
- [Recording and retrieval pipeline](design/memory-retrieval-pipeline.md): proposed full-text, vector, temporal graph and context assembly
- [Phased optimization roadmap](design/memory-roadmap.md): P0–P6 goals, quality/resource gates and rollback
- [Nowledge replacement readiness review (2026-10-07)](design/nowledge-replacement-readiness.md): executed-state evidence, gap list G1–G12, work packages W-00–W-11
- [P2 write-path minimal design review](design/p2-write-path-minimal-loop.md): identity/event/revision/retraction/outbox contracts and the negative test list (design only)
- [Upstream memory tool research](reports/2026-09-26-memory-landscape.md): verified source links and adoption boundaries

- [Dual-track memory and migration gates](design/dual-track-memory.md)
- [Shadow operation manual](manuals/SHADOW_MEMORY.md)
- [2026-09-26 validation](reports/2026-09-26-dual-track.md)
- [2026-09-26 resource assessment: M3 and NAS34](reports/2026-09-26-resource-assessment.md)
- [2026-10-07 operation report and alerting](reports/2026-10-07-shadow-operation-report.md)
- [2026-10-07 off-host backup and restore drill](reports/2026-10-07-offhost-backup.md)
- [2026-10-07 client retrieval evidence](reports/2026-10-07-client-retrieval-evidence.md)
- [2026-10-07 source API increment probe](reports/2026-10-07-source-api-increment.md)
- [2026-10-07 thread coverage probe](reports/2026-10-07-thread-coverage.md)

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
