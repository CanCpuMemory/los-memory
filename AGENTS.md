# los-memory AGENTS

> **Workspace**: Part of `los-workspace` (`~/projects/los-workspace`).
> Cross-project rules: `~/projects/los-workspace/AGENTS.md`
> Authority boundary spec: `~/projects/los-workspace/docs/architecture/five-project-boundary-spec.md`

## Scope

This repo is a local SQLite memory ledger for Codex and Claude workflows. Core records are stable; several extensions are optional or experimental; the approval module is deprecated and migrating out.

## Read Order

1. `README.md`
2. `TODO.md`
3. `docs/current/CURRENT_STATE.md`
4. `docs/manuals/AI_USAGE_GUIDE.md`
5. `docs/manuals/VPSAGENTWEB_WRITEBACK_CONTRACT.md` for controlled writeback work

## Stability Rules

- Core surfaces: observation, session, checkpoint, feedback, link.
- Extensions: incident, recovery, knowledge, attribution.
- Deprecated path: approval. Do not extend it unless the task explicitly targets migration or compatibility.

## Key Commands

```bash
los-memory --profile codex init
los-memory admin doctor
make help
make init-codex
make init-claude
make smoke-contract
pytest -q
```

## Change Rules

- Preserve profile isolation. `codex`, `claude`, and `shared` defaults must not bleed into each other.
- Keep CLI contract, structured output, and dry-run behavior stable for maintenance commands.
- Do not move transient runtime scratch data into durable memory semantics.
- For deprecated approval-related behavior, prefer migration or compatibility containment rather than new feature growth.

## Validation

- CLI changes: run the narrowest matching command and at least one dry-run contract path.
- Schema or storage changes: verify against a real SQLite db path without breaking profile scoping.
- Extension changes: confirm they can still be disabled cleanly when `MEMORY_DISABLE_EXTENSIONS` is set.
