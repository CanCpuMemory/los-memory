# Approval System Migration Guide

**Status**: In Progress
**Target Completion**: 12 months (2027-03-07)
**Destination**: VPS Agent Web

---

## Overview

The approval system is being migrated out of `los-memory` to [VPS Agent Web](https://docs.vps-agent-web.example.com/approval), where it more appropriately belongs as part of the governance/execution domain.

## Migration Timeline

### Phase 1: Freeze (Months 1-3) - CURRENT
- **Status**: ✅ Active
- **Actions**:
  - [x] Freeze new approval feature development in los-memory
  - [x] Add deprecation warnings to all approval commands
  - [x] Document migration timeline
  - [ ] Notify users via release notes

### Phase 2: Parallel Build (Months 4-8)
- **Status**: ⏳ Pending
- **Actions**:
  - [ ] VPS Agent Web implements approval workflow
  - [ ] API compatibility layer design
  - [ ] HMAC signature compatibility verification
  - [ ] Dual-write mechanism implementation

### Phase 3: Migration (Months 9-12)
- **Status**: ⏳ Pending
- **Actions**:
  - [ ] Data migration scripts
  - [ ] Dual system operation period (30 days)
  - [ ] User migration guide
  - [ ] Complete removal from los-memory

## Current State

### What Works Now
```bash
# Approval commands still work but show deprecation warnings
los-memory approval create --title "Deploy to production" --risk-level high
los-memory approval list
los-memory approve <id>
```

### Deprecation Warnings
All approval commands now emit:
```
DeprecationWarning: Approval command is deprecated and will be removed.
Migrate to VPS Agent Web's approval workflow.
```

## Migration Steps for Users

### Step 1: Identify Usage
Search your scripts and workflows for approval commands:
```bash
grep -r "los-memory approval" .
grep -r "handle_approval_command" .
```

### Step 2: Plan Migration
| los-memory Command | VPS Agent Web Equivalent |
|-------------------|-------------------------|
| `approval create` | `vps-agent approval create` |
| `approval list` | `vps-agent approval list` |
| `approval approve` | `vps-agent approval approve` |
| `approval reject` | `vps-agent approval reject` |

### Step 3: Update Integrations
The HMAC-signed callbacks will continue to work during the migration period. VPS Agent Web will maintain compatible signatures.

### Step 4: Data Export
When ready, export approval history:
```bash
# Coming in Phase 3
los-memory approval export --format json > approvals-backup.json
```

## Technical Details

### Database Schema
Approval tables will be migrated:
- `approval_requests` → VPS Agent Web
- `approval_audit_log` → VPS Agent Web
- `approval_events` → VPS Agent Web
- `approval_nonces` → VPS Agent Web

### API Compatibility
The HMAC-signed POST endpoint `/api/v1/jobs/approval` will remain functional with redirects to VPS Agent Web.

### SSE Event Stream
Event stream `/api/v1/events/stream` will proxy to VPS Agent Web during transition.

## Disabling Now

If you want to disable approval commands immediately:
```bash
export MEMORY_DISABLE_EXTENSIONS=approval
los-memory approval create  # Will show disabled message
```

## Getting Help

- **Issues**: [GitHub Issues](https://github.com/yourusername/los-memory/issues)
- **VPS Agent Web Docs**: https://docs.vps-agent-web.example.com/approval
- **Migration Support**: migration@example.com

---

**Last Updated**: 2026-03-07
**Next Review**: 2026-04-07 (Monthly during Phase 1)
