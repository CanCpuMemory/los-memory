# Hub-Lite Parent Epic First Run Manual

**Version**: 1.0.0  
**Date**: 2026-03-09  
**Trace ID**: `trace-parent-epic-20260309000327`  
**Parent Task ID**: `epic-20260309000327`

## Overview

This manual describes the first-run procedure for establishing los-memory as the hub-lite control plane in a multi-repo parent epic. This pattern enables centralized orchestration of child sessions while maintaining strict repo boundaries.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Hub-Lite Control Plane                    │
│                      (los-memory)                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Parent     │  │  Dispatch   │  │  Aggregation        │  │
│  │  Session    │  │  Registry   │  │  & Reporting        │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
└─────────┼────────────────┼────────────────────┼─────────────┘
          │                │                    │
          ▼                ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│                      Child Sessions                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ los-     │  │ lsclaw   │  │ hapi     │  │ other    │    │
│  │ memory   │  │          │  │          │  │ repos    │    │
│  │ (self)   │  │          │  │          │  │          │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Prerequisites

1. **los-memory Repository**: Must be initialized and validated
2. **Child Repositories**: Must have defined local paths
3. **Trace Context**: Shared `traceId` and `parentTaskId` for all sessions

## First Run Procedure

### Step 1: Initialize Parent Session

```bash
# Start a parent session for the epic
los-memory session start \
  --summary "Hub-Lite Parent Epic: trace-parent-epic-20260309000327" \
  --tags "type:parent-epic,trace:trace-parent-epic-20260309000327,task:epic-20260309000327"
```

### Step 2: Validate Repository Boundaries

Each child repository must validate its own boundary:

```bash
# Validate los-memory (self)
los-memory observation add \
  --title "los-memory boundary validation" \
  --summary "Validated repo-local memory/ledger boundary for cross-repo integration" \
  --tags "stage:validation,repo:los-memory,status:accepted"
```

### Step 3: Dispatch Child Sessions

Use the dispatch template to coordinate child sessions:

```bash
# Load dispatch configuration
export EPIC_CONFIG="docs/templates/hub-lite-parent-epic-integration.json"

# Dispatch to child repos (example pattern)
# Each child receives:
# - traceId: trace-parent-epic-20260309000327
# - parentTaskId: epic-20260309000327  
# - unique childTaskId per repo
# - targetProjectPath: local repo path
```

### Step 4: Monitor Child Progress

Child sessions report progress back to los-memory:

```bash
# Query child session status
los-memory memory search \
  "trace:trace-parent-epic-20260309000327" \
  --tags "stage:execution,kind:progress"

# List all records for this epic
los-memory memory list \
  --tags "trace:trace-parent-epic-20260309000327" \
  --limit 50
```

## Record Structure

### Execution/Progress Records

```json
{
  "traceId": "trace-parent-epic-20260309000327",
  "parentTaskId": "epic-20260309000327",
  "childTaskId": "{repo}-20260309000327",
  "sessionId": "child-{repo}-20260309000327",
  "stage": "execution",
  "kind": "progress",
  "summary": "Validated {repo} repo path and structure"
}
```

### Verification/Checkpoint Records

```json
{
  "traceId": "trace-parent-epic-20260309000327",
  "parentTaskId": "epic-20260309000327",
  "childTaskId": "{repo}-20260309000327",
  "sessionId": "child-{repo}-20260309000327",
  "stage": "verification",
  "kind": "checkpoint",
  "summary": "Core Python API imports successful",
  "result": "PASS"
}
```

### Result/Artifact Records

```json
{
  "traceId": "trace-parent-epic-20260309000327",
  "parentTaskId": "epic-20260309000327",
  "childTaskId": "{repo}-20260309000327",
  "sessionId": "child-{repo}-20260309000327",
  "stage": "result",
  "kind": "artifact",
  "summary": "{repo} repo validation complete",
  "acceptanceState": "ACCEPTED|ACCEPTED_WITH_NOTES|BLOCKED",
  "artifactPath": "logs/{filename}"
}
```

## Acceptance Criteria

The first run is considered successful when:

1. ✅ **Parent session created** with trace metadata
2. ✅ **All child sessions visible** in los-memory with stable ordering
3. ✅ **Linked task metadata** recorded for each child
4. ✅ **Artifact counts** documented per child
5. ✅ **Each child emitted** at least one execution/progress record
6. ✅ **Each child emitted** at least one result or blocker record
7. ✅ **Verification commands** recorded as checkpoints
8. ✅ **Parent summary** records acceptance state, blockers, and next steps

## Handling Blockers

If a child encounters a blocker:

1. **Stop immediately** - Do not patch around the blocker
2. **Record blocker** - Create explicit blocker record
3. **Escalate to parent** - Parent session aggregates all blockers
4. **Await resolution** - Parent decides on next steps

### Blocker Record Format

```json
{
  "traceId": "trace-parent-epic-20260309000327",
  "parentTaskId": "epic-20260309000327",
  "childTaskId": "{repo}-20260309000327",
  "sessionId": "child-{repo}-20260309000327",
  "stage": "result",
  "kind": "blocker",
  "summary": "Repository path not found: /path/to/{repo}",
  "acceptanceState": "BLOCKED",
  "blockerType": "PATH_UNRESOLVED|CONTRACT_UNRESOLVED|DEPENDENCY_MISSING",
  "escalationTarget": "parent"
}
```

## Verification Commands

### For los-memory (Self)

```bash
# Test core imports
python3 -c "from memory_tool import connect_db, run_search; print('OK')"

# Test CLI
los-memory memory list --limit 5

# Test admin doctor
los-memory admin doctor --output json
```

### For Integration Testing

```bash
# Run hub-lite integration tests
python3 -m pytest tests/integration/test_hub_lite_integration.py -v

# Run contract tests
python3 -m pytest tests/contract/ -v -m contract
```

## Related Artifacts

- **Template**: `docs/templates/hub-lite-parent-epic-integration.json`
- **Control Plane Logs**: `control-plane/logs/hub-lite-lsclaw-round1.md`
- **Dispatch Log**: `logs/hub-lite-parent-epic-dispatch-20260309000327.json`

## Next Steps

After successful first run:

1. Review aggregated results in parent session
2. Address any blockers or warnings
3. Proceed to implementation phase for accepted repos
4. Schedule periodic boundary re-validation
5. Archive completed epic records per retention policy

---

**Maintained by**: los-memory hub-lite control plane  
**Last Updated**: 2026-03-09
