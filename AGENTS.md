# los-memory AGENTS

> **Workspace**: Part of `los-workspace` (`~/syncfolder/project/los-workspace`).
> Cross-project rules: `~/syncfolder/project/los-workspace/AGENTS.md`
> Current workspace boundary: `~/syncfolder/project/los-workspace/WORKSPACE.md`
> Historical boundary context only: `~/syncfolder/project/los-workspace/docs/archive/seven-project-boundary-spec.md`

## Scope

This repo is a local SQLite memory ledger for Codex and Claude workflows. Core records are stable; several extensions are optional or experimental; the approval module is deprecated and migrating out.

The user approved bounded Nowledge dual-track validation on 2026-09-26. `memory_tool.shadow*` owns a separate read-only mirror and SSH MCP; Nowledge remains primary. Follow `docs/design/dual-track-memory.md` and `docs/manuals/SHADOW_MEMORY.md` for this lane. Do not merge shadow storage into existing profile databases or infer authorization to switch the primary.

## Shadow memory: when to call it

`docs/manuals/SHADOW_INVOCATION_POLICY.md` is the operative policy. The short form:

- **Use the shadow first** when the query carries a literal anchor (ID, hash, path, filename, error code, version, hostname) or a short CJK term, and when completeness matters more than conceptual recall.
- **Use Nowledge first** for paraphrased/conceptual questions. The shadow is literal-only; it scores 0 on those by design.
- **Freshness precondition**: configured availability is not proof of a usable mirror. Check `shadow_status` (or a result's `verified_at`) before relying on it, treat it as an as-of snapshot, and on a degraded result fall back to Nowledge immediately instead of retrying.
- **Comparison phase is on**: call `shadow_compare` alongside a normal lookup. It records the divergence between the two backends and answers in milliseconds; it is instrumentation, **not** an answer source — keep answering from Nowledge.
- Never call the primary synchronously inside a comparison path: `nmem memories search` costs a measured ~13 s per query, which is why prompt-time recall is disabled in the DSH profile.

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
