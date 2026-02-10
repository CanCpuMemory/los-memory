"""Memory tool package."""
from __future__ import annotations

from .models import Checkpoint, Observation, Session
from .database import connect_db, ensure_fts, ensure_schema, init_db, SCHEMA_VERSION
from .utils import (
    auto_tags_from_text,
    normalize_tags_list,
    normalize_text,
    parse_ids,
    resolve_db_path,
    tags_to_json,
    tags_to_text,
    utc_now,
    DEFAULT_PROFILE,
    PROFILE_CHOICES,
    PROFILE_DB_PATHS,
)
from .operations import (
    add_observation,
    normalize_rows,
    run_search,
    run_timeline,
    run_get,
    run_list,
    run_export,
    run_edit,
    run_delete,
    run_clean,
    run_manage,
    generate_visual_timeline,
)
from .sessions import (
    get_active_session,
    set_active_session,
    clear_active_session,
    start_session,
    end_session,
    get_session,
    list_sessions,
    get_session_observations,
    generate_session_summary,
)
from .checkpoints import (
    get_checkpoint,
    list_checkpoints,
    get_checkpoint_observations,
    resume_from_checkpoint,
)
from .projects import list_projects
from .share import run_share, run_import

__all__ = [
    # Models
    "Checkpoint",
    "Observation",
    "Session",
    # Database
    "connect_db",
    "ensure_fts",
    "ensure_schema",
    "init_db",
    "SCHEMA_VERSION",
    # Utils
    "auto_tags_from_text",
    "normalize_tags_list",
    "normalize_text",
    "parse_ids",
    "resolve_db_path",
    "tags_to_json",
    "tags_to_text",
    "utc_now",
    "DEFAULT_PROFILE",
    "PROFILE_CHOICES",
    "PROFILE_DB_PATHS",
    # Operations
    "add_observation",
    "normalize_rows",
    "run_search",
    "run_timeline",
    "run_get",
    "run_list",
    "run_export",
    "run_edit",
    "run_delete",
    "run_clean",
    "run_manage",
    "generate_visual_timeline",
    # Sessions
    "get_active_session",
    "set_active_session",
    "clear_active_session",
    "start_session",
    "end_session",
    "get_session",
    "list_sessions",
    "get_session_observations",
    "generate_session_summary",
    # Checkpoints
    "get_checkpoint",
    "list_checkpoints",
    "get_checkpoint_observations",
    "resume_from_checkpoint",
    # Projects
    "list_projects",
    # Share
    "run_share",
    "run_import",
]
