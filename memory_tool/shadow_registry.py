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
# A record can genuinely carry more than one registered project label (measured
# 2026-10-07: 19 of 2,048 records, e.g. cantool+lot2extension). Resolving that by
# whichever label happens to come first in `label_ids` is guessing, and the order
# is not meaningful. Such records are marked `multi` instead.
MULTI = "multi"

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
#
# 2026-10-21 (B1): the original 17-entry list was written from a 2,048-record
# sample; the live corpus carries 2,079 distinct labels on unassigned records.
# The additions below are topic labels observed there (each with the record count
# that motivated it), reviewed by hand. A label belongs here when it describes
# *what a record is about*, never *which project it belongs to*.
NON_PROJECT_LABELS = {
    "label_memory-evolve", "label_daily", "label_architecture", "label_verification",
    "label_performance", "label_security", "label_audit", "label_macos", "label_surge",
    "label_agent-history", "label_jj", "label_git", "label_commit-hygiene",
    "label_single-maintainer", "label_nas", "label_z4pro",
    # topic vocabulary observed in the live corpus (B1, 2026-10-21)
    "label_workflow", "label_decision", "label_fact", "label_insight", "label_lesson",
    "label_solution", "label_procedure", "label_review", "label_validation", "label_testing",
    "label_plan", "label_prd", "label_design-review", "label_design-doc", "label_documentation",
    "label_configuration", "label_migration", "label_governance", "label_automation",
    "label_troubleshooting", "label_network", "label_proxy", "label_dns", "label_ssh",
    "label_backup", "label_quota", "label_rpa", "label_api", "label_ci", "label_rust",
    "label_sqlite", "label_extension", "label_browser-automation", "label_codex",
    "label_codex-history-distill", "label_egolite", "label_ego-browser", "label_m3",
    "label_sing-box", "label_tailscale", "label_jms", "label_grok", "label_sit",
    "label_orca", "label_socloud", "label_packycode", "label_forgejo", "label_cc-switch",
    "label_opencode", "label_reasonix", "label_cloudflared", "label_nowledge",
}

# Per-project key-memory labels: DSH stores a project's key facts under
# ``label_key-<hash>`` where the hash names a project memory directory. Until the
# hash -> project mapping is *declared* below, such a record has a project signal
# we can see but not name, so it is reported as ``key_label_unmapped`` instead of
# being guessed or silently folded into ``unassigned``.
#
# Adding an entry is a human decision: resolve the directory (``~/.dsh/memories/
# projects/<hash>/``) to the project it belongs to, then write it here.
KEY_LABEL_PREFIX = "label_key-"
KEY_LABEL_PROJECTS: dict[str, str] = {}

# Project aliases: alternate spellings that name an existing project.
PROJECT_LABEL_ALIASES = {
    "label_deepseek-harness": "deepseek-harness",  # 18 records; same project as label_dsh
}

# Reason vocabulary for "why is this record (un)assigned?". Every active record
# carries exactly one, and ``contract()`` reports the histogram, so a coverage
# number can never again be quoted without its composition.
REASON_ASSIGNED = "assigned"
REASON_MULTI = "multi"
REASON_NO_LABELS = "no_labels"
REASON_POLICY_EXCLUDED = "policy_excluded"
REASON_KEY_UNMAPPED = "key_label_unmapped"
REASON_UNCLASSIFIED = "unclassified"
REASONS = (
    REASON_ASSIGNED, REASON_MULTI, REASON_NO_LABELS,
    REASON_POLICY_EXCLUDED, REASON_KEY_UNMAPPED, REASON_UNCLASSIFIED,
)


def is_key_label(label):
    """True for a per-project key-memory label (``label_key-<hex>``)."""
    return (isinstance(label, str) and label.startswith(KEY_LABEL_PREFIX)
            and len(label) > len(KEY_LABEL_PREFIX))


def project_of_label(label):
    """Project named by a single label, or None.

    Accepts the registered project labels and the declared aliases; key labels
    resolve only when ``KEY_LABEL_PROJECTS`` declares them.
    """
    if not isinstance(label, str):
        return None
    if label in PROJECT_LABELS:
        return PROJECT_LABELS[label]
    if label in PROJECT_LABEL_ALIASES:
        return PROJECT_LABEL_ALIASES[label]
    return KEY_LABEL_PROJECTS.get(label)


def classify(label_ids):
    """Assign a project *and* the reason, for one record's labels.

    Returns ``{"project": <id|unassigned|multi>, "reason": <REASONS member>}``.
    Precedence: a declared project wins, then "no labels", then "all labels are
    known topics", then "the only project signal is an unmapped key label", else
    "at least one label nobody has classified".
    """
    labels = [item for item in (label_ids or []) if isinstance(item, str)]
    projects = sorted({p for p in (project_of_label(label) for label in labels) if p})
    if len(projects) == 1:
        return {"project": projects[0], "reason": REASON_ASSIGNED}
    if len(projects) > 1:
        return {"project": MULTI, "reason": REASON_MULTI}
    if not labels:
        return {"project": UNASSIGNED, "reason": REASON_NO_LABELS}
    if all(label in NON_PROJECT_LABELS for label in labels):
        return {"project": UNASSIGNED, "reason": REASON_POLICY_EXCLUDED}
    if all(label in NON_PROJECT_LABELS or is_key_label(label) for label in labels):
        return {"project": UNASSIGNED, "reason": REASON_KEY_UNMAPPED}
    return {"project": UNASSIGNED, "reason": REASON_UNCLASSIFIED}


def project_for(label_ids, source_app="", thread_source=""):
    """Return the registered project id for a record.

    ``source_app`` and ``thread_source`` are accepted for signature stability and
    forward compatibility, but they are deliberately **not** used to infer a
    project: they identify the host app, not the project.

    Returns ``MULTI`` when the record carries two or more distinct registered
    projects — that is a real property of the record, not something to resolve by
    label order. Retrieval still finds such a record under *each* of its projects
    because filtering goes through label membership (see ``labels_for``), so
    marking it ``multi`` costs no recall.
    """
    return classify(label_ids)["project"]


def projects_for(label_ids):
    """All distinct registered projects a record's labels map to, sorted."""
    if not isinstance(label_ids, (list, tuple)):
        return []
    return sorted({p for p in (project_of_label(label) for label in label_ids) if p})


def labels_for(project):
    """The label set that makes a record belong to ``project``.

    This is what makes a project filter a set membership test rather than an
    equality test on one projected column: a cantool+lot2extension record is
    findable under either project.
    """
    labels = {label for label, value in PROJECT_LABELS.items() if value == project}
    labels |= {label for label, value in PROJECT_LABEL_ALIASES.items() if value == project}
    labels |= {label for label, value in KEY_LABEL_PROJECTS.items() if value == project}
    return sorted(labels)


def known_projects():
    return sorted(set(PROJECT_LABELS.values()))
