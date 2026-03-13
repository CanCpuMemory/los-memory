# VPS Agent Web Writeback Contract

This document defines the current `los-memory` writeback contract for `VPS Agent Web` and other controlled integrators.

## Scope

This contract covers:

- `observation add`
- `observation bulk`
- `observation edit`
- `observation feedback`
- `review apply`
- `admin manage stats`
- `memory get`
- `memory search`
- `memory list`
- `memory export`

## Stable Smoke Contract

The following commands can be treated as stable smoke contract targets for upstream runtime verification:

- `review apply --file <json> --dry-run`
- `admin manage stats`
- `observation delete <id> --dry-run`

Rationale:

- `review apply --dry-run` validates the batch review/correction path without mutating stored observations.
- `admin manage stats` is a read-only aggregate check with a small, stable JSON surface.
- `observation delete --dry-run` validates identifier-based delete routing and match accounting without mutating data.

## Profile Boundary

`--profile {claude,codex,shared}` selects the storage partition only.

It must not be used as a business identity field.

Business identity and workflow context belong in structured metadata, for example:

- `tenant_id`
- `project_id`
- `actor_id`
- `user_id`
- `role`
- `trace_id`
- `request_id`
- `session_id`
- `idempotency_key`
- `job_id`
- `approval_id`
- `event_type`
- `source`

`profile` is rejected inside `--metadata`.

## Observation Write Path

### CLI

Inline JSON:

```bash
los-memory --profile shared observation add \
  --project "ops" \
  --kind "decision" \
  --title "Writeback baseline" \
  --summary "Structured metadata round-trip" \
  --metadata '{"tenant_id":"tenant-a","trace_id":"trace-123","request_id":"req-456","source":"vps-agent-web"}'
```

From stdin:

```bash
printf '%s' '{"tenant_id":"tenant-a","trace_id":"trace-stdin","source":"vps-agent-web"}' | \
los-memory --profile shared observation add \
  --title "Writeback from stdin" \
  --summary "stdin metadata path" \
  --metadata @-
```

### Readback

`memory get` returns `metadata` as a JSON object:

```bash
los-memory --profile shared memory get 123
```

Expected shape:

```json
{
  "ok": true,
  "results": [
    {
      "id": 123,
      "title": "Writeback baseline",
      "metadata": {
        "tenant_id": "tenant-a",
        "trace_id": "trace-123",
        "request_id": "req-456",
        "source": "vps-agent-web"
      }
    }
  ]
}
```

### Bulk Write Path

The primary JSON writeback path is `observation bulk`.

From stdin:

```bash
printf '%s' '{"items":[{"project":"ops","kind":"decision","title":"Bulk writeback baseline","summary":"stdin bulk path","tags":["tenant:a","user:alice"],"metadata":{"tenant_id":"tenant-a","trace_id":"trace-bulk-1","source":"vps-agent-web"}}]}' | \
los-memory --profile shared observation bulk \
  --input @-
```

From file:

```bash
los-memory --profile shared observation bulk \
  --input @review-items.json
```

Expected shape:

```json
{
  "ok": true,
  "total": 1,
  "created": 1,
  "ids": [123],
  "results": [
    {
      "id": 123,
      "title": "Bulk writeback baseline",
      "metadata": {
        "tenant_id": "tenant-a",
        "trace_id": "trace-bulk-1",
        "source": "vps-agent-web"
      }
    }
  ],
  "dry_run": false
}
```

## Observation Edit Path

`observation edit --metadata` replaces the stored metadata object.

Example:

```bash
los-memory --profile shared observation edit \
  --id 123 \
  --metadata '{"tenant_id":"tenant-a","trace_id":"trace-999","event_type":"memory.corrected","source":"vps-agent-web"}'
```

## Feedback / Correction Path

The current correction model is:

- use `observation feedback` for structured correction/supplement/delete actions
- use `review apply` for batch correction from reviewer output

This contract does not introduce a second correction object model.

### Feedback CLI

```bash
los-memory --profile shared observation feedback \
  --id 123 \
  --metadata '{"trace_id":"trace-feedback-1","approval_id":"approval-1","source":"vps-agent-web"}' \
  '修正: Updated value'
```

Expected result includes the feedback metadata:

```json
{
  "ok": true,
  "observation_id": 123,
  "action": "correct",
  "metadata": {
    "trace_id": "trace-feedback-1",
    "approval_id": "approval-1",
    "source": "vps-agent-web"
  }
}
```

### Feedback History

```bash
los-memory --profile shared observation feedback --id 123 --history history
```

Expected shape:

```json
{
  "ok": true,
  "observation_id": 123,
  "history": [
    {
      "action_type": "correct",
      "feedback_text": "修正: Updated value",
      "metadata": {
        "trace_id": "trace-feedback-1",
        "approval_id": "approval-1",
        "source": "vps-agent-web"
      }
    }
  ]
}
```

## Review Apply Path

`review apply` accepts item-level metadata and propagates it into feedback history.

Input file example:

```json
{
  "items": [
    {
      "observation_id": 123,
      "feedback": "补充: Reviewed note",
      "metadata": {
        "trace_id": "trace-review-1",
        "job_id": "job-review-1",
        "source": "vps-agent-web"
      }
    }
  ]
}
```

Invocation:

```bash
los-memory --profile shared review apply --file review-apply-input.json
```

Stable smoke invocation:

```bash
los-memory --profile shared review apply --file review-apply-input.json --dry-run
```

Expected smoke fields:

```json
{
  "ok": true,
  "total": 1,
  "applied": 0,
  "failed": 0,
  "errors": [],
  "dry_run": true
}
```

## Admin Manage Stats Path

`admin manage stats` is a stable read-only smoke path for adapter/runtime verification.

Invocation:

```bash
los-memory --profile shared admin manage stats
```

Expected smoke fields:

```json
{
  "ok": true,
  "action": "stats",
  "total": 1,
  "earliest": "2026-03-12T10:00:00Z",
  "latest": "2026-03-12T10:00:00Z",
  "projects": [
    {
      "project": "ops",
      "count": 1
    }
  ],
  "kinds": [
    {
      "kind": "decision",
      "count": 1
    }
  ]
}
```

## Export Contract

`memory export` now preserves `metadata` in JSON output and includes a `metadata` column in CSV output.

Examples:

```bash
los-memory --profile shared memory export --format json --output export.json
los-memory --profile shared memory export --format csv --output export.csv
```

## Metadata Query Filters

`memory search` and `memory list` support metadata-native equality filters.

Search example:

```bash
los-memory --profile shared memory search "baseline" \
  --metadata-filter '{"tenant_id":"tenant-a","source":"vps-agent-web"}'
```

List example:

```bash
los-memory --profile shared memory list \
  --metadata-filter @metadata-filter.json
```

The filter uses AND semantics across the provided metadata keys.
