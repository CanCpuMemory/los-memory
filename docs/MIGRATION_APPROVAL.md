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
  - [x] API compatibility layer design ✅
  - [x] HMAC signature compatibility verification ✅
  - [x] Dual-write mechanism implementation ✅

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

## Phase 4: Adapter Layer (NEW)

The adapter layer provides seamless migration with configurable operation modes.

### Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Application   │────▶│  Adapter Layer   │────▶│  VPS Agent Web  │
│                 │     │ (Migration Phase)│     │   (Target)      │
└─────────────────┘     └────────┬─────────┘     └─────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
            ┌──────────────┐        ┌──────────────┐
            │ los-memory   │        │ Dual-Write   │
            │ (SQLite)     │        │ Coordination │
            └──────────────┘        └──────────────┘
```

### Migration Phases

| Phase | Behavior | Use Case |
|-------|----------|----------|
| `local-only` | los-memory only | Phase 1 (current) |
| `dual-write` | Write to both systems | Phase 2 transition |
| `remote-only` | VPS Agent Web only | Phase 3 completion |
| `removed` | Feature disabled | Phase 4 cleanup |

### Configuration

#### Environment Variables

```bash
# Phase control
export APPROVAL_MIGRATION_PHASE=dual-write  # local-only, dual-write, remote-only, removed
export APPROVAL_MIGRATION_MODE=strict       # strict, local_preferred, remote_preferred, read_only

# Connection settings
export VPS_AGENT_WEB_URL=https://vps-agent-web.example.com
export APPROVAL_MIGRATION_TIMEOUT=30

# HMAC secrets
export APPROVAL_HMAC_SECRET=<legacy-secret>
export VPS_AGENT_HMAC_SECRET=<vps-secret>
export VPS_AGENT_KEY_ID=v1

# Feature flags
export APPROVAL_ENABLE_LOCAL=true
export APPROVAL_ENABLE_REMOTE=true
export MEMORY_APPROVAL_SILENCE_WARNING=1  # Silence deprecation warnings
```

#### Dual-Write Modes

| Mode | Local Success | Remote Success | Overall Result | Use Case |
|------|---------------|----------------|----------------|----------|
| `strict` | ✅ | ✅ | ✅ | Data consistency critical |
| `strict` | ✅ | ❌ | ❌ | Fail if VPS unavailable |
| `local_preferred` | ✅ | ❌ | ✅ | Graceful degradation |
| `remote_preferred` | ❌ | ✅ | ✅ | VPS is source of truth |
| `read_only` | N/A | N/A | ❌ | Maintenance windows |

### Python API Usage

```python
from memory_tool.migrate_out.approval import (
    MigrationConfig, MigrationPhase, ApprovalMigrationAdapter
)

# Configure for dual-write mode
config = MigrationConfig(phase=MigrationPhase.DUAL_WRITE)
adapter = ApprovalMigrationAdapter(config, sqlite_conn)

# Create request - writes to both systems
result = adapter.create_request(
    job_id="deploy-123",
    command="deploy prod",
    risk_level="high"
)

# Check health of both backends
health = adapter.health_check()
print(f"Local: {health['local']['healthy']}")
print(f"Remote: {health['remote']['healthy']}")

# Get migration status
status = adapter.get_migration_status()
print(f"Phase: {status['phase']}")
```

### Security: HMAC Verification

The adapter includes HMAC signature verification with replay protection:

```python
from memory_tool.migrate_out.approval import HMACBridge, HMACConfig

config = HMACConfig(
    legacy_active_secret="legacy-secret",
    vps_active_secret="vps-secret"
)
bridge = HMACBridge(config)

# Verify local signature (includes nonce replay check)
headers = {"X-Signature": "...", "X-Timestamp": "...", "X-Nonce": "..."}
if bridge.verify_local(headers, payload):
    # Re-sign for VPS Agent Web
    remote_headers = bridge.resign_for_remote(headers, payload)
```

**Security Features:**
- Timestamp validation (reject if >60s future or >5min past)
- Nonce replay prevention (5-minute window)
- Dual-key rotation support (24-hour overlap)
- Automatic signature re-signing for remote forwarding

### Troubleshooting

#### HMAC Verification Failed
```
HMACVerificationError: Nonce has already been used (replay attack detected)
```
**Cause**: The nonce was already used within the 5-minute window.
**Solution**: Ensure each request uses a unique nonce (UUID recommended).

#### Connection Timeout
```
VPSAgentWebError: Request failed after 3 attempts
```
**Cause**: Cannot connect to VPS Agent Web.
**Solution**:
- Check `VPS_AGENT_WEB_URL` is correct
- Verify network connectivity
- Increase `APPROVAL_MIGRATION_TIMEOUT`
- Use `local_preferred` mode during outages

#### Data Inconsistency
```
DualWriteResult: local_success=True, remote_success=False
```
**Cause**: Write succeeded locally but failed remotely.
**Solution**:
- Check remote system health
- Use `strict` mode to ensure consistency
- Review logs for error details

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

---

## Changelog

### 2026-03-07 - Phase 4 Adapter Layer Released
- ✅ Adapter layer implementation complete
- ✅ Dual-write mechanism with 4 modes
- ✅ HMAC bridge with nonce replay prevention
- ✅ Thread-safe SQLite connection handling
- ✅ Comprehensive configuration system
