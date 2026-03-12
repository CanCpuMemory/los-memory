"""Database operations and schema management."""
from __future__ import annotations

import os
import sqlite3
from typing import TYPE_CHECKING

from .utils import ISO_FORMAT, utc_now

if TYPE_CHECKING:
    pass

SCHEMA_VERSION = 18


def connect_db(path: str) -> sqlite3.Connection:
    """Connect to SQLite database."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    optimize_connection(conn)
    return conn


def optimize_connection(conn: sqlite3.Connection) -> None:
    """Apply PRAGMA optimizations for better performance.

    These settings optimize SQLite for the los-memory workload:
    - Foreign keys enabled to enforce relational integrity/cascades
    - WAL mode for better concurrent read/write
    - Normal synchronous mode for performance/safety balance
    - Memory temp store for faster temp tables
    - 64MB cache for better query performance
    - 256MB memory-mapped I/O for faster access
    """
    conn.execute("PRAGMA foreign_keys=ON")           # Enforce FK constraints and cascades
    conn.execute("PRAGMA journal_mode=WAL")          # WAL mode for better concurrency
    conn.execute("PRAGMA synchronous=NORMAL")        # Balance performance and safety
    conn.execute("PRAGMA temp_store=MEMORY")         # Store temp tables in memory
    conn.execute("PRAGMA cache_size=-64000")         # 64MB cache (negative = KB)
    conn.execute("PRAGMA mmap_size=268435456")       # 256MB memory-mapped I/O


def ensure_meta_table(conn: sqlite3.Connection) -> None:
    """Ensure meta table exists."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version."""
    ensure_meta_table(conn)
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'",
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Set schema version."""
    ensure_meta_table(conn)
    conn.execute(
        """
        INSERT INTO meta (key, value)
        VALUES ('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(version),),
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Return whether a table exists in the current SQLite database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure database schema is up to date."""
    _ensure_observations_table(conn)
    _ensure_sessions_table(conn)
    _ensure_checkpoints_table(conn)
    migrate_schema(conn)
    conn.commit()


def _ensure_observations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            project TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            tags TEXT NOT NULL,
            tags_text TEXT NOT NULL,
            raw TEXT NOT NULL,
            session_id INTEGER REFERENCES sessions(id),
            metadata TEXT NOT NULL DEFAULT '{}'
        )
        """
    )


def _ensure_sessions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            project TEXT NOT NULL,
            working_dir TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            summary TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'ended'))
        )
        """
    )


def _ensure_checkpoints_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            tag TEXT DEFAULT '',
            session_id INTEGER REFERENCES sessions(id),
            observation_count INTEGER DEFAULT 0,
            project TEXT DEFAULT ''
        )
        """
    )


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Migrate database to current schema version."""
    version = get_schema_version(conn)
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {version} is newer than this tool supports "
            f"(max {SCHEMA_VERSION})."
        )

    for target_version, migrate in _schema_migration_steps():
        if version >= target_version:
            continue
        migrate(conn)
        set_schema_version(conn, target_version)
        version = target_version


def _schema_migration_steps():
    """Ordered migration steps from v1 through current schema version."""
    return (
        (1, _migrate_to_v1),
        (2, _migrate_to_v2),
        (3, _migrate_to_v3),
        (4, _migrate_to_v4),
        (5, _migrate_to_v5),
        (6, _migrate_to_v6),
        (7, _migrate_to_v7),
        (8, _migrate_to_v8),
        (9, _migrate_to_v9),
        (10, _migrate_to_v10),
        (11, _migrate_to_v11),
        (12, _migrate_to_v12),
        (13, _migrate_to_v13),
        (14, _migrate_to_v14),
        (15, _migrate_to_v15),
        (16, _migrate_to_v16),
        (17, _migrate_to_v17),
        (18, _migrate_to_v18),
    )


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    """Bootstrap schema version marker for legacy databases."""
    del conn


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """Add observations.tags_text and normalize tag storage."""
    from .utils import normalize_tags_list, tags_to_json, tags_to_text

    try:
        conn.execute(
            "ALTER TABLE observations ADD COLUMN tags_text TEXT NOT NULL DEFAULT ''",
        )
    except sqlite3.OperationalError:
        pass
    rows = conn.execute("SELECT id, tags FROM observations").fetchall()
    for row in rows:
        tags_list = normalize_tags_list(row["tags"])
        conn.execute(
            "UPDATE observations SET tags = ?, tags_text = ? WHERE id = ?",
            (tags_to_json(tags_list), tags_to_text(tags_list), row["id"]),
        )
    rebuild_fts(conn)


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    """Add observations.session_id reference."""
    try:
        conn.execute(
            "ALTER TABLE observations ADD COLUMN session_id INTEGER REFERENCES sessions(id)"
        )
    except sqlite3.OperationalError:
        pass


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    """Create checkpoints table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            tag TEXT DEFAULT '',
            session_id INTEGER REFERENCES sessions(id),
            observation_count INTEGER DEFAULT 0,
            project TEXT DEFAULT ''
        )
        """
    )


def _migrate_to_v5(conn: sqlite3.Connection) -> None:
    """Create feedback log table and index."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_observation_id INTEGER NOT NULL,
            action_type TEXT NOT NULL CHECK (action_type IN ('correct', 'supplement', 'delete')),
            feedback_text TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_feedback_target
        ON feedback_log(target_observation_id)
        """
    )


def _migrate_to_v6(conn: sqlite3.Connection) -> None:
    """Create observation links table and indexes."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS observation_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
            to_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
            link_type TEXT NOT NULL DEFAULT 'related' CHECK (link_type IN ('related', 'child', 'parent', 'refines')),
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_links_from_to
        ON observation_links(from_id, to_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_links_to_type
        ON observation_links(to_id, link_type)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_links_unique
        ON observation_links(from_id, to_id, link_type)
        """
    )


def _migrate_to_v7(conn: sqlite3.Connection) -> None:
    """Install baseline observations performance indexes."""
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_observations_project_timestamp
        ON observations(project, timestamp DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_observations_project_kind_timestamp
        ON observations(project, kind, timestamp DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_observations_tags_text
        ON observations(tags_text)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_observations_session_id
        ON observations(session_id) WHERE session_id IS NOT NULL
        """
    )


def _migrate_to_v8(conn: sqlite3.Connection) -> None:
    """Create incident core tables and indexes."""
    _migrate_to_v8_create_incident_tables(conn)
    _migrate_to_v8_create_incident_indexes(conn)


def _migrate_to_v8_create_incident_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_type TEXT NOT NULL CHECK (incident_type IN ('error', 'performance', 'availability')),
            severity TEXT NOT NULL CHECK (severity IN ('p0', 'p1', 'p2', 'p3')),
            status TEXT NOT NULL DEFAULT 'detected' CHECK (status IN ('detected', 'analyzing', 'recovering', 'resolved', 'closed')),
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            source_observation_id INTEGER REFERENCES observations(id),
            context_snapshot TEXT NOT NULL DEFAULT '{}',
            detected_at TEXT NOT NULL,
            resolved_at TEXT,
            project TEXT NOT NULL DEFAULT 'general'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS incident_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
            link_type TEXT NOT NULL DEFAULT 'related' CHECK (link_type IN ('related', 'source', 'documentation', 'resolution')),
            created_at TEXT NOT NULL
        )
        """
    )


def _migrate_to_v8_create_incident_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_incidents_status
        ON incidents(status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_incidents_project
        ON incidents(project)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_incidents_detected_at
        ON incidents(detected_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_incident_observations_incident
        ON incident_observations(incident_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_incident_observations_observation
        ON incident_observations(observation_id)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_incident_observations_unique
        ON incident_observations(incident_id, observation_id, link_type)
        """
    )


def _migrate_to_v9(conn: sqlite3.Connection) -> None:
    """Create recovery tables, indexes, and default actions."""
    _migrate_to_v9_create_recovery_tables(conn)
    _migrate_to_v9_create_recovery_indexes(conn)
    _migrate_to_v9_seed_default_recovery_actions(conn)


def _migrate_to_v9_create_recovery_tables(conn: sqlite3.Connection) -> None:
    _migrate_to_v9_create_recovery_actions_table(conn)
    _migrate_to_v9_create_recovery_executions_table(conn)
    _migrate_to_v9_create_recovery_policies_table(conn)


def _migrate_to_v9_create_recovery_actions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recovery_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            action_type TEXT NOT NULL CHECK (action_type IN ('shell', 'restart_service', 'clear_cache', 'webhook', 'send_alert', 'database_failover', 'switch_database')),
            config TEXT NOT NULL DEFAULT '{}',
            description TEXT DEFAULT '',
            enabled BOOLEAN DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _migrate_to_v9_create_recovery_executions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recovery_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            action_id INTEGER REFERENCES recovery_actions(id),
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'success', 'failed')),
            started_at TEXT,
            completed_at TEXT,
            output_text TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            retry_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )


def _migrate_to_v9_create_recovery_policies_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recovery_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_id TEXT NOT NULL,
            trigger_type TEXT NOT NULL CHECK (trigger_type IN ('threshold', 'event', 'manual', 'composite')),
            action_ids TEXT NOT NULL,
            execution_strategy TEXT NOT NULL DEFAULT 'sequential' CHECK (execution_strategy IN ('sequential', 'parallel')),
            timeout_seconds INTEGER DEFAULT 300,
            enabled BOOLEAN DEFAULT 1,
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _migrate_to_v9_create_recovery_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recovery_executions_incident
        ON recovery_executions(incident_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recovery_executions_status
        ON recovery_executions(status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recovery_policies_trigger
        ON recovery_policies(trigger_id)
        """
    )


def _migrate_to_v9_seed_default_recovery_actions(conn: sqlite3.Connection) -> None:
    from .utils import utc_now

    now = utc_now()
    default_actions = [
        ('restart_service', 'shell', '{"command": "systemctl restart {{service}}"}', 'Restart a system service', now, now),
        ('clear_cache', 'shell', '{"command": "rm -rf {{cache_path}}/*"}', 'Clear application cache', now, now),
        ('send_alert', 'webhook', '{"method": "POST", "url": "{{webhook_url}}"}', 'Send alert notification', now, now),
        ('switch_database', 'switch_database', '{"action": "failover", "target": "{{backup_db}}"}', 'Switch to backup database', now, now),
    ]
    conn.executemany(
        """
        INSERT OR IGNORE INTO recovery_actions
        (name, action_type, config, description, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        default_actions
    )


def _migrate_to_v10(conn: sqlite3.Connection) -> None:
    """Create approval system tables and indexes."""
    _migrate_to_v10_create_approval_tables(conn)
    _migrate_to_v10_create_approval_indexes(conn)


def _migrate_to_v10_create_approval_tables(conn: sqlite3.Connection) -> None:
    _migrate_to_v10_create_approval_requests_table(conn)
    _migrate_to_v10_create_approval_audit_log_table(conn)
    _migrate_to_v10_create_approval_events_table(conn)
    _migrate_to_v10_create_approval_nonces_table(conn)


def _migrate_to_v10_create_approval_requests_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL UNIQUE,
            command TEXT NOT NULL,
            risk_level TEXT NOT NULL DEFAULT 'medium' CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'timeout')),
            version INTEGER NOT NULL DEFAULT 1,
            requested_by TEXT,
            approved_by TEXT,
            reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            context TEXT DEFAULT '{}'
        )
        """
    )


def _migrate_to_v10_create_approval_audit_log_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('created', 'approved', 'rejected', 'timeout')),
            actor_id TEXT,
            previous_status TEXT CHECK (previous_status IS NULL OR previous_status IN ('pending', 'approved', 'rejected', 'timeout')),
            new_status TEXT NOT NULL CHECK (new_status IN ('pending', 'approved', 'rejected', 'timeout')),
            version INTEGER NOT NULL,
            reason TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (request_id) REFERENCES approval_requests(id)
        )
        """
    )


def _migrate_to_v10_create_approval_events_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            job_id TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def _migrate_to_v10_create_approval_nonces_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_nonces (
            nonce TEXT PRIMARY KEY,
            expires_at TEXT NOT NULL
        )
        """
    )


def _migrate_to_v10_create_approval_indexes(conn: sqlite3.Connection) -> None:
    _migrate_to_v10_create_approval_request_indexes(conn)
    _migrate_to_v10_create_approval_audit_indexes(conn)
    _migrate_to_v10_create_approval_event_indexes(conn)
    _migrate_to_v10_create_approval_nonce_indexes(conn)


def _migrate_to_v10_create_approval_request_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_approval_status
        ON approval_requests(status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_approval_expires
        ON approval_requests(expires_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_approval_job
        ON approval_requests(job_id)
        """
    )


def _migrate_to_v10_create_approval_audit_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_request
        ON approval_audit_log(request_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp
        ON approval_audit_log(timestamp)
        """
    )


def _migrate_to_v10_create_approval_event_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_approval_events_job
        ON approval_events(job_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_approval_events_created
        ON approval_events(created_at)
        """
    )


def _migrate_to_v10_create_approval_nonce_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_nonce_expires
        ON approval_nonces(expires_at)
        """
    )


def _migrate_to_v11(conn: sqlite3.Connection) -> None:
    """Create attribution tables and indexes."""
    _migrate_to_v11_create_attribution_reports_table(conn)
    _migrate_to_v11_create_incident_attributions_table(conn)
    _migrate_to_v11_create_attribution_indexes(conn)


def _migrate_to_v11_create_attribution_reports_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attribution_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            root_cause_category TEXT NOT NULL,
            root_cause_description TEXT NOT NULL,
            confidence_score REAL NOT NULL DEFAULT 0.0,
            contributing_factors TEXT NOT NULL DEFAULT '[]',
            evidence_observation_ids TEXT NOT NULL DEFAULT '[]',
            recommended_prevention TEXT NOT NULL DEFAULT '[]',
            time_window_minutes INTEGER DEFAULT 30,
            created_at TEXT NOT NULL
        )
        """
    )


def _migrate_to_v11_create_incident_attributions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS incident_attributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            attribution_report_id INTEGER NOT NULL REFERENCES attribution_reports(id) ON DELETE CASCADE,
            factor_type TEXT NOT NULL,
            factor_description TEXT NOT NULL,
            confidence REAL NOT NULL,
            observation_id INTEGER REFERENCES observations(id),
            created_at TEXT NOT NULL
        )
        """
    )


def _migrate_to_v11_create_attribution_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attribution_incident
        ON attribution_reports(incident_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attribution_category
        ON attribution_reports(root_cause_category)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_incident_attributions_report
        ON incident_attributions(attribution_report_id)
        """
    )


def _migrate_to_v12(conn: sqlite3.Connection) -> None:
    """Create knowledge base table and indexes."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            symptoms_pattern TEXT NOT NULL,
            root_cause_summary TEXT NOT NULL,
            solution_steps TEXT NOT NULL DEFAULT '[]',
            prerequisites TEXT DEFAULT '[]',
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            source_incident_ids TEXT DEFAULT '[]',
            tags TEXT DEFAULT '[]',
            last_used_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_type_severity
        ON knowledge_entries(incident_type, severity)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_success_rate
        ON knowledge_entries(success_count, failure_count)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_last_used
        ON knowledge_entries(last_used_at)
        """
    )


def _migrate_to_v13(conn: sqlite3.Connection) -> None:
    """Add and normalize observations.metadata for legacy databases."""
    try:
        conn.execute(
            "ALTER TABLE observations ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
        )
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "UPDATE observations SET metadata = '{}' WHERE metadata IS NULL OR trim(metadata) = ''"
    )


def _migrate_to_v14(conn: sqlite3.Connection) -> None:
    """Add and normalize feedback_log.metadata for legacy databases."""
    try:
        conn.execute(
            "ALTER TABLE feedback_log ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
        )
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "UPDATE feedback_log SET metadata = '{}' WHERE metadata IS NULL OR trim(metadata) = ''"
    )


def _migrate_to_v15(conn: sqlite3.Connection) -> None:
    """Normalize v15 enum values and install legacy enum guards."""
    conn.execute(
        """
        UPDATE sessions
        SET status = CASE
            WHEN end_time IS NULL THEN 'active'
            ELSE 'completed'
        END
        WHERE status IS NULL
           OR trim(status) = ''
           OR status NOT IN ('active', 'completed', 'ended')
        """
    )
    conn.execute(
        """
        UPDATE observation_links
        SET link_type = 'related'
        WHERE link_type IS NULL
           OR trim(link_type) = ''
           OR link_type NOT IN ('related', 'child', 'parent', 'refines')
        """
    )
    conn.execute(
        """
        UPDATE feedback_log
        SET action_type = 'supplement'
        WHERE action_type IS NULL
           OR trim(action_type) = ''
           OR action_type NOT IN ('correct', 'supplement', 'delete')
        """
    )

    _ensure_enum_guard_triggers(conn)


def _migrate_to_v16(conn: sqlite3.Connection) -> None:
    """Normalize v16 status values and install legacy status guards."""
    _migrate_to_v16_normalize_incident_status(conn)
    _migrate_to_v16_normalize_recovery_execution_status(conn)
    _migrate_to_v16_normalize_approval_status(conn)
    _ensure_status_guard_triggers(conn)


def _migrate_to_v17(conn: sqlite3.Connection) -> None:
    """Normalize v17 non-status enums and install legacy non-status guards."""
    _migrate_to_v17_normalize_incident_enums(conn)
    _migrate_to_v17_normalize_recovery_policy_strategy(conn)
    _migrate_to_v17_normalize_approval_non_status_enums(conn)
    _ensure_non_status_enum_guard_triggers(conn)


def _migrate_to_v16_normalize_incident_status(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "incidents"):
        conn.execute(
            """
            UPDATE incidents
            SET status = 'detected'
            WHERE status IS NULL
               OR trim(status) = ''
               OR status NOT IN ('detected', 'analyzing', 'recovering', 'resolved', 'closed')
            """
        )


def _migrate_to_v16_normalize_recovery_execution_status(
    conn: sqlite3.Connection,
) -> None:
    if _table_exists(conn, "recovery_executions"):
        conn.execute(
            """
            UPDATE recovery_executions
            SET status = CASE
                WHEN completed_at IS NULL THEN 'pending'
                WHEN error_message IS NOT NULL AND trim(error_message) != '' THEN 'failed'
                ELSE 'success'
            END
            WHERE status IS NULL
               OR trim(status) = ''
               OR status NOT IN ('pending', 'success', 'failed')
            """
        )


def _migrate_to_v16_normalize_approval_status(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "approval_requests"):
        conn.execute(
            """
            UPDATE approval_requests
            SET status = 'pending'
            WHERE status IS NULL
               OR trim(status) = ''
               OR status NOT IN ('pending', 'approved', 'rejected', 'timeout')
            """
        )
    if _table_exists(conn, "approval_audit_log"):
        conn.execute(
            """
            UPDATE approval_audit_log
            SET previous_status = NULL
            WHERE previous_status IS NOT NULL
              AND trim(previous_status) != ''
              AND previous_status NOT IN ('pending', 'approved', 'rejected', 'timeout')
            """
        )
        conn.execute(
            """
            UPDATE approval_audit_log
            SET new_status = CASE
                WHEN action = 'approved' THEN 'approved'
                WHEN action = 'rejected' THEN 'rejected'
                WHEN action = 'timeout' THEN 'timeout'
                ELSE 'pending'
            END
            WHERE new_status IS NULL
               OR trim(new_status) = ''
               OR new_status NOT IN ('pending', 'approved', 'rejected', 'timeout')
            """
        )


def _migrate_to_v17_normalize_incident_enums(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "incidents"):
        conn.execute(
            """
            UPDATE incidents
            SET incident_type = 'error'
            WHERE incident_type IS NULL
               OR trim(incident_type) = ''
               OR incident_type NOT IN ('error', 'performance', 'availability')
            """
        )
        conn.execute(
            """
            UPDATE incidents
            SET severity = 'p2'
            WHERE severity IS NULL
               OR trim(severity) = ''
               OR severity NOT IN ('p0', 'p1', 'p2', 'p3')
            """
        )


def _migrate_to_v17_normalize_recovery_policy_strategy(
    conn: sqlite3.Connection,
) -> None:
    if _table_exists(conn, "recovery_policies"):
        conn.execute(
            """
            UPDATE recovery_policies
            SET execution_strategy = 'sequential'
            WHERE execution_strategy IS NULL
               OR trim(execution_strategy) = ''
               OR execution_strategy NOT IN ('sequential', 'parallel')
            """
        )


def _migrate_to_v17_normalize_approval_non_status_enums(
    conn: sqlite3.Connection,
) -> None:
    if _table_exists(conn, "approval_requests"):
        conn.execute(
            """
            UPDATE approval_requests
            SET risk_level = 'medium'
            WHERE risk_level IS NULL
               OR trim(risk_level) = ''
               OR risk_level NOT IN ('low', 'medium', 'high', 'critical')
            """
        )
    if _table_exists(conn, "approval_audit_log"):
        conn.execute(
            """
            UPDATE approval_audit_log
            SET action = CASE
                WHEN new_status = 'approved' THEN 'approved'
                WHEN new_status = 'rejected' THEN 'rejected'
                WHEN new_status = 'timeout' THEN 'timeout'
                ELSE 'created'
            END
            WHERE action IS NULL
               OR trim(action) = ''
               OR action NOT IN ('created', 'approved', 'rejected', 'timeout')
            """
        )


def _migrate_to_v18(conn: sqlite3.Connection) -> None:
    """Normalize v18 follow-up enums and install legacy guards."""
    if _table_exists(conn, "incident_observations"):
        conn.execute(
            """
            UPDATE incident_observations
            SET link_type = 'related'
            WHERE link_type IS NULL
               OR trim(link_type) = ''
               OR link_type NOT IN ('related', 'source', 'documentation', 'resolution')
            """
        )
    if _table_exists(conn, "recovery_actions"):
        conn.execute(
            """
            UPDATE recovery_actions
            SET action_type = CASE
                WHEN action_type = 'database' THEN 'switch_database'
                WHEN name IN ('restart_service', 'clear_cache', 'send_alert', 'database_failover', 'switch_database') THEN name
                ELSE 'shell'
            END
            WHERE action_type IS NULL
               OR trim(action_type) = ''
               OR action_type NOT IN ('shell', 'restart_service', 'clear_cache', 'webhook', 'send_alert', 'database_failover', 'switch_database')
            """
        )
    if _table_exists(conn, "recovery_policies"):
        conn.execute(
            """
            UPDATE recovery_policies
            SET trigger_type = 'threshold'
            WHERE trigger_type IS NULL
               OR trim(trigger_type) = ''
               OR trigger_type NOT IN ('threshold', 'event', 'manual', 'composite')
            """
        )

    _ensure_followup_enum_guard_triggers(conn)


def _ensure_enum_guard_triggers(conn: sqlite3.Connection) -> None:
    """Create enum guard triggers for mutable legacy tables.

    Existing databases created before CHECK constraints were introduced may keep
    older table definitions. These triggers enforce equivalent constraints for
    new writes without requiring destructive table rebuilds.
    """
    _ensure_sessions_status_guards(conn)
    _ensure_observation_link_type_guards(conn)
    _ensure_feedback_action_type_guards(conn)


def _ensure_sessions_status_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_sessions_status_insert_guard
        BEFORE INSERT ON sessions
        FOR EACH ROW
        WHEN NEW.status IS NULL
          OR trim(NEW.status) = ''
          OR NEW.status NOT IN ('active', 'completed', 'ended')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_sessions_status');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_sessions_status_update_guard
        BEFORE UPDATE OF status ON sessions
        FOR EACH ROW
        WHEN NEW.status IS NULL
          OR trim(NEW.status) = ''
          OR NEW.status NOT IN ('active', 'completed', 'ended')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_sessions_status');
        END
        """
    )


def _ensure_observation_link_type_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_observation_links_type_insert_guard
        BEFORE INSERT ON observation_links
        FOR EACH ROW
        WHEN NEW.link_type IS NULL
          OR trim(NEW.link_type) = ''
          OR NEW.link_type NOT IN ('related', 'child', 'parent', 'refines')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_observation_link_type');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_observation_links_type_update_guard
        BEFORE UPDATE OF link_type ON observation_links
        FOR EACH ROW
        WHEN NEW.link_type IS NULL
          OR trim(NEW.link_type) = ''
          OR NEW.link_type NOT IN ('related', 'child', 'parent', 'refines')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_observation_link_type');
        END
        """
    )


def _ensure_feedback_action_type_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_feedback_action_insert_guard
        BEFORE INSERT ON feedback_log
        FOR EACH ROW
        WHEN NEW.action_type IS NULL
          OR trim(NEW.action_type) = ''
          OR NEW.action_type NOT IN ('correct', 'supplement', 'delete')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_feedback_action_type');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_feedback_action_update_guard
        BEFORE UPDATE OF action_type ON feedback_log
        FOR EACH ROW
        WHEN NEW.action_type IS NULL
          OR trim(NEW.action_type) = ''
          OR NEW.action_type NOT IN ('correct', 'supplement', 'delete')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_feedback_action_type');
        END
        """
    )


def _ensure_status_guard_triggers(conn: sqlite3.Connection) -> None:
    """Create status guard triggers for legacy tables lacking CHECK constraints."""
    if _table_exists(conn, "incidents"):
        _ensure_incidents_status_guards(conn)
    if _table_exists(conn, "recovery_executions"):
        _ensure_recovery_execution_status_guards(conn)
    if _table_exists(conn, "approval_requests"):
        _ensure_approval_request_status_guards(conn)
    if _table_exists(conn, "approval_audit_log"):
        _ensure_approval_audit_status_guards(conn)


def _ensure_incidents_status_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_incidents_status_insert_guard
        BEFORE INSERT ON incidents
        FOR EACH ROW
        WHEN NEW.status IS NULL
          OR trim(NEW.status) = ''
          OR NEW.status NOT IN ('detected', 'analyzing', 'recovering', 'resolved', 'closed')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_incident_status');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_incidents_status_update_guard
        BEFORE UPDATE OF status ON incidents
        FOR EACH ROW
        WHEN NEW.status IS NULL
          OR trim(NEW.status) = ''
          OR NEW.status NOT IN ('detected', 'analyzing', 'recovering', 'resolved', 'closed')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_incident_status');
        END
        """
    )


def _ensure_recovery_execution_status_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_recovery_executions_status_insert_guard
        BEFORE INSERT ON recovery_executions
        FOR EACH ROW
        WHEN NEW.status IS NULL
          OR trim(NEW.status) = ''
          OR NEW.status NOT IN ('pending', 'success', 'failed')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_recovery_execution_status');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_recovery_executions_status_update_guard
        BEFORE UPDATE OF status ON recovery_executions
        FOR EACH ROW
        WHEN NEW.status IS NULL
          OR trim(NEW.status) = ''
          OR NEW.status NOT IN ('pending', 'success', 'failed')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_recovery_execution_status');
        END
        """
    )


def _ensure_approval_request_status_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_approval_requests_status_insert_guard
        BEFORE INSERT ON approval_requests
        FOR EACH ROW
        WHEN NEW.status IS NULL
          OR trim(NEW.status) = ''
          OR NEW.status NOT IN ('pending', 'approved', 'rejected', 'timeout')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_approval_request_status');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_approval_requests_status_update_guard
        BEFORE UPDATE OF status ON approval_requests
        FOR EACH ROW
        WHEN NEW.status IS NULL
          OR trim(NEW.status) = ''
          OR NEW.status NOT IN ('pending', 'approved', 'rejected', 'timeout')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_approval_request_status');
        END
        """
    )


def _ensure_approval_audit_status_guards(conn: sqlite3.Connection) -> None:
    _ensure_approval_audit_previous_status_guards(conn)
    _ensure_approval_audit_new_status_guards(conn)


def _ensure_approval_audit_previous_status_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_approval_audit_prev_status_insert_guard
        BEFORE INSERT ON approval_audit_log
        FOR EACH ROW
        WHEN NEW.previous_status IS NOT NULL
          AND trim(NEW.previous_status) != ''
          AND NEW.previous_status NOT IN ('pending', 'approved', 'rejected', 'timeout')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_approval_audit_previous_status');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_approval_audit_prev_status_update_guard
        BEFORE UPDATE OF previous_status ON approval_audit_log
        FOR EACH ROW
        WHEN NEW.previous_status IS NOT NULL
          AND trim(NEW.previous_status) != ''
          AND NEW.previous_status NOT IN ('pending', 'approved', 'rejected', 'timeout')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_approval_audit_previous_status');
        END
        """
    )


def _ensure_approval_audit_new_status_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_approval_audit_new_status_insert_guard
        BEFORE INSERT ON approval_audit_log
        FOR EACH ROW
        WHEN NEW.new_status IS NULL
          OR trim(NEW.new_status) = ''
          OR NEW.new_status NOT IN ('pending', 'approved', 'rejected', 'timeout')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_approval_audit_new_status');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_approval_audit_new_status_update_guard
        BEFORE UPDATE OF new_status ON approval_audit_log
        FOR EACH ROW
        WHEN NEW.new_status IS NULL
          OR trim(NEW.new_status) = ''
          OR NEW.new_status NOT IN ('pending', 'approved', 'rejected', 'timeout')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_approval_audit_new_status');
        END
        """
    )


def _ensure_non_status_enum_guard_triggers(conn: sqlite3.Connection) -> None:
    """Create non-status enum guard triggers for legacy tables lacking CHECK."""
    if _table_exists(conn, "incidents"):
        _ensure_incident_non_status_guards(conn)
    if _table_exists(conn, "recovery_policies"):
        _ensure_recovery_strategy_guards(conn)
    if _table_exists(conn, "approval_requests"):
        _ensure_approval_risk_guards(conn)
    if _table_exists(conn, "approval_audit_log"):
        _ensure_approval_audit_action_guards(conn)


def _ensure_incident_non_status_guards(conn: sqlite3.Connection) -> None:
    _ensure_incident_type_guards(conn)
    _ensure_incident_severity_guards(conn)


def _ensure_incident_type_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_incidents_type_insert_guard
        BEFORE INSERT ON incidents
        FOR EACH ROW
        WHEN NEW.incident_type IS NULL
          OR trim(NEW.incident_type) = ''
          OR NEW.incident_type NOT IN ('error', 'performance', 'availability')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_incident_type');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_incidents_type_update_guard
        BEFORE UPDATE OF incident_type ON incidents
        FOR EACH ROW
        WHEN NEW.incident_type IS NULL
          OR trim(NEW.incident_type) = ''
          OR NEW.incident_type NOT IN ('error', 'performance', 'availability')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_incident_type');
        END
        """
    )


def _ensure_incident_severity_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_incidents_severity_insert_guard
        BEFORE INSERT ON incidents
        FOR EACH ROW
        WHEN NEW.severity IS NULL
          OR trim(NEW.severity) = ''
          OR NEW.severity NOT IN ('p0', 'p1', 'p2', 'p3')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_incident_severity');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_incidents_severity_update_guard
        BEFORE UPDATE OF severity ON incidents
        FOR EACH ROW
        WHEN NEW.severity IS NULL
          OR trim(NEW.severity) = ''
          OR NEW.severity NOT IN ('p0', 'p1', 'p2', 'p3')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_incident_severity');
        END
        """
    )


def _ensure_recovery_strategy_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_recovery_policies_strategy_insert_guard
        BEFORE INSERT ON recovery_policies
        FOR EACH ROW
        WHEN NEW.execution_strategy IS NULL
          OR trim(NEW.execution_strategy) = ''
          OR NEW.execution_strategy NOT IN ('sequential', 'parallel')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_recovery_execution_strategy');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_recovery_policies_strategy_update_guard
        BEFORE UPDATE OF execution_strategy ON recovery_policies
        FOR EACH ROW
        WHEN NEW.execution_strategy IS NULL
          OR trim(NEW.execution_strategy) = ''
          OR NEW.execution_strategy NOT IN ('sequential', 'parallel')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_recovery_execution_strategy');
        END
        """
    )


def _ensure_approval_risk_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_approval_requests_risk_insert_guard
        BEFORE INSERT ON approval_requests
        FOR EACH ROW
        WHEN NEW.risk_level IS NULL
          OR trim(NEW.risk_level) = ''
          OR NEW.risk_level NOT IN ('low', 'medium', 'high', 'critical')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_approval_risk_level');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_approval_requests_risk_update_guard
        BEFORE UPDATE OF risk_level ON approval_requests
        FOR EACH ROW
        WHEN NEW.risk_level IS NULL
          OR trim(NEW.risk_level) = ''
          OR NEW.risk_level NOT IN ('low', 'medium', 'high', 'critical')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_approval_risk_level');
        END
        """
    )


def _ensure_approval_audit_action_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_approval_audit_action_insert_guard
        BEFORE INSERT ON approval_audit_log
        FOR EACH ROW
        WHEN NEW.action IS NULL
          OR trim(NEW.action) = ''
          OR NEW.action NOT IN ('created', 'approved', 'rejected', 'timeout')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_approval_audit_action');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_approval_audit_action_update_guard
        BEFORE UPDATE OF action ON approval_audit_log
        FOR EACH ROW
        WHEN NEW.action IS NULL
          OR trim(NEW.action) = ''
          OR NEW.action NOT IN ('created', 'approved', 'rejected', 'timeout')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_approval_audit_action');
        END
        """
    )


def _ensure_followup_enum_guard_triggers(conn: sqlite3.Connection) -> None:
    """Create additional enum guard triggers for legacy tables lacking CHECK."""
    if _table_exists(conn, "incident_observations"):
        _ensure_incident_observation_link_guards(conn)
    if _table_exists(conn, "recovery_actions"):
        _ensure_recovery_action_type_guards(conn)
    if _table_exists(conn, "recovery_policies"):
        _ensure_recovery_policy_trigger_type_guards(conn)


def _ensure_incident_observation_link_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_incident_observations_type_insert_guard
        BEFORE INSERT ON incident_observations
        FOR EACH ROW
        WHEN NEW.link_type IS NULL
          OR trim(NEW.link_type) = ''
          OR NEW.link_type NOT IN ('related', 'source', 'documentation', 'resolution')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_incident_observation_link_type');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_incident_observations_type_update_guard
        BEFORE UPDATE OF link_type ON incident_observations
        FOR EACH ROW
        WHEN NEW.link_type IS NULL
          OR trim(NEW.link_type) = ''
          OR NEW.link_type NOT IN ('related', 'source', 'documentation', 'resolution')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_incident_observation_link_type');
        END
        """
    )


def _ensure_recovery_action_type_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_recovery_actions_type_insert_guard
        BEFORE INSERT ON recovery_actions
        FOR EACH ROW
        WHEN NEW.action_type IS NULL
          OR trim(NEW.action_type) = ''
          OR NEW.action_type NOT IN ('shell', 'restart_service', 'clear_cache', 'webhook', 'send_alert', 'database_failover', 'switch_database')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_recovery_action_type');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_recovery_actions_type_update_guard
        BEFORE UPDATE OF action_type ON recovery_actions
        FOR EACH ROW
        WHEN NEW.action_type IS NULL
          OR trim(NEW.action_type) = ''
          OR NEW.action_type NOT IN ('shell', 'restart_service', 'clear_cache', 'webhook', 'send_alert', 'database_failover', 'switch_database')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_recovery_action_type');
        END
        """
    )


def _ensure_recovery_policy_trigger_type_guards(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_recovery_policies_type_insert_guard
        BEFORE INSERT ON recovery_policies
        FOR EACH ROW
        WHEN NEW.trigger_type IS NULL
          OR trim(NEW.trigger_type) = ''
          OR NEW.trigger_type NOT IN ('threshold', 'event', 'manual', 'composite')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_recovery_policy_trigger_type');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_recovery_policies_type_update_guard
        BEFORE UPDATE OF trigger_type ON recovery_policies
        FOR EACH ROW
        WHEN NEW.trigger_type IS NULL
          OR trim(NEW.trigger_type) = ''
          OR NEW.trigger_type NOT IN ('threshold', 'event', 'manual', 'composite')
        BEGIN
            SELECT RAISE(ABORT, 'invalid_recovery_policy_trigger_type');
        END
        """
    )


def ensure_fts(conn: sqlite3.Connection) -> bool:
    """Ensure FTS5 virtual table and triggers exist."""
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts
            USING fts5(title, summary, tags_text, raw, content='observations', content_rowid='id')
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_ai
            AFTER INSERT ON observations BEGIN
                INSERT INTO observations_fts(rowid, title, summary, tags_text, raw)
                VALUES (new.id, new.title, new.summary, new.tags_text, new.raw);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_ad
            AFTER DELETE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, title, summary, tags_text, raw)
                VALUES ('delete', old.id, old.title, old.summary, old.tags_text, old.raw);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS observations_au
            AFTER UPDATE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, title, summary, tags_text, raw)
                VALUES ('delete', old.id, old.title, old.summary, old.tags_text, old.raw);
                INSERT INTO observations_fts(rowid, title, summary, tags_text, raw)
                VALUES (new.id, new.title, new.summary, new.tags_text, new.raw);
            END;
            """
        )
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild FTS index.

    Drops and recreates the FTS table and triggers, then repopulates
    with all existing observations.
    """
    conn.execute("DROP TRIGGER IF EXISTS observations_ai")
    conn.execute("DROP TRIGGER IF EXISTS observations_ad")
    conn.execute("DROP TRIGGER IF EXISTS observations_au")
    conn.execute("DROP TABLE IF EXISTS observations_fts")
    ensure_fts(conn)

    # Repopulate FTS index with existing observations
    conn.execute(
        """
        INSERT INTO observations_fts (rowid, title, summary, tags_text, raw)
        SELECT id, title, summary, tags_text, raw FROM observations
        """
    )
    conn.commit()


def init_db(path: str) -> None:
    """Initialize database."""
    conn = connect_db(path)
    ensure_schema(conn)
    ensure_fts(conn)
    conn.close()
