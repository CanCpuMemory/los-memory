"""Explicit project registry for the read-only Nowledge shadow.

Why this file exists (2026-10-07 evidence, 2,048 active default-space records):

- `metadata.project` appears on **0** records; there is no top-level `project`
  field either.
- Thread ids encode only the host app, never the project:
  `codex-<uuid>`, `deepseek-harness-session-<uuid>`.
- No metadata key carries `project` / `repo` / `path` / `workspace` / `cwd`.
- The only project signal is `label_ids` (1,994 of 2,048 records carry labels),
  and it **mixes project names with topic labels** in one flat namespace.

Therefore project assignment is explicit and allowlisted: a record is assigned a
project only when it carries a registered label. Everything else stays
`unassigned`. Guessing from directory names, thread titles or `source_app` is
forbidden by `docs/design/memory-service-architecture.md` §4.

Adding a project means adding a label here after human review. The registry is
intentionally a Python module (not JSON) because `scripts/deploy_shadow.py`
packages `memory_tool/**/*.py` only.
"""

UNASSIGNED = "unassigned"

# label -> project_id. Observed record counts on 2026-10-07 are in comments so a
# reviewer can see how much coverage each entry actually buys.
PROJECT_LABELS = {
    "label_cankey": "cankey",                    # 125
    "label_los": "los",                          # 100
    "label_wechatdp": "wechatdp",                # 92
    "label_cantool": "cantool",                  # 91
    "label_dsh": "deepseek-harness",             # 59
    "label_lzlyx": "lzlyx",                      # 48
    "label_lot2extension": "lot2extension",      # 44
    "label_canpad": "canpad",                    # 35
    "label_los-memory": "los-memory",            # 0 observed yet; registered intent
    "label_dsfolder": "dsfolder",                # 0 observed yet; registered intent
}

# Labels observed in the same corpus that are deliberately NOT projects. Kept
# here as an explicit exclusion list so "why is this unassigned?" is answerable
# without re-deriving it from the data.
NON_PROJECT_LABELS = {
    "label_memory-evolve", "label_daily", "label_architecture", "label_verification",
    "label_performance", "label_security", "label_audit", "label_macos", "label_surge",
    "label_agent-history", "label_jj", "label_git", "label_commit-hygiene",
    "label_single-maintainer", "label_nas", "label_z4pro",
}


def project_for(label_ids, source_app="", thread_source=""):
    """Return the registered project id for a record, else ``unassigned``.

    ``source_app`` and ``thread_source`` are accepted for signature stability and
    forward compatibility, but they are deliberately **not** used to infer a
    project: they identify the host app, not the project.
    """
    if not isinstance(label_ids, (list, tuple)):
        return UNASSIGNED
    for label in label_ids:
        if isinstance(label, str) and label in PROJECT_LABELS:
            return PROJECT_LABELS[label]
    return UNASSIGNED


def known_projects():
    return sorted(set(PROJECT_LABELS.values()))
