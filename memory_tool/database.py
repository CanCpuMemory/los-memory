"""Database operations and schema management."""
from __future__ import annotations

import os
import sqlite3
from typing import TYPE_CHECKING

from .utils import ISO_FORMAT, utc_now

if TYPE_CHECKING:
    pass

SCHEMA_VERSION = 12


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
    - WAL mode for better concurrent read/write
    - Normal synchronous mode for performance/safety balance
    - Memory temp store for faster temp tables
    - 64MB cache for better query performance
    - 256MB memory-mapped I/O for faster access
    """
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


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure database schema is up to date."""
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
            session_id INTEGER REFERENCES sessions(id)
        )
        """
    )
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
            status TEXT DEFAULT 'active'
        )
        """
    )
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
    migrate_schema(conn)
    conn.commit()


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Migrate database to current schema version."""
    from .utils import normalize_tags_list, tags_to_json, tags_to_text

    version = get_schema_version(conn)
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {version} is newer than this tool supports "
            f"(max {SCHEMA_VERSION})."
        )

    if version < 1:
        set_schema_version(conn, 1)
        version = 1

    if version < 2:
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
        set_schema_version(conn, 2)
        version = 2

    if version < 3:
        try:
            conn.execute(
                "ALTER TABLE observations ADD COLUMN session_id INTEGER REFERENCES sessions(id)"
            )
        except sqlite3.OperationalError:
            pass
        set_schema_version(conn, 3)
        version = 3

    if version < 4:
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
        set_schema_version(conn, 4)
        version = 4

    if version < 5:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
                action_type TEXT NOT NULL,
                feedback_text TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feedback_target
            ON feedback_log(target_observation_id)
            """
        )
        set_schema_version(conn, 5)
        version = 5

    if version < 6:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS observation_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
                to_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
                link_type TEXT NOT NULL DEFAULT 'related',
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
        set_schema_version(conn, 6)
        version = 6

    if version < 7:
        # Performance indexes for T009-T010
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
        set_schema_version(conn, 7)
        version = 7

    if version < 8:
        # Phase 1: Incident management tables
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'detected',
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
                link_type TEXT NOT NULL DEFAULT 'related',
                created_at TEXT NOT NULL
            )
            """
        )
        # Indexes for incident queries
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
        set_schema_version(conn, 8)
        version = 8

    if version < 9:
        # Phase 2: L1自动恢复系统表
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                action_type TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                description TEXT DEFAULT '',
                enabled BOOLEAN DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                action_id INTEGER REFERENCES recovery_actions(id),
                status TEXT NOT NULL DEFAULT 'pending',
                started_at TEXT,
                completed_at TEXT,
                output_text TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                retry_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_policies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_id TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                action_ids TEXT NOT NULL,
                execution_strategy TEXT DEFAULT 'sequential',
                timeout_seconds INTEGER DEFAULT 300,
                enabled BOOLEAN DEFAULT 1,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Indexes for recovery queries
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
        # Pre-insert default recovery actions
        from .utils import utc_now
        now = utc_now()
        default_actions = [
            ('restart_service', 'shell', '{"command": "systemctl restart {{service}}"}', 'Restart a system service', now, now),
            ('clear_cache', 'shell', '{"command": "rm -rf {{cache_path}}/*"}', 'Clear application cache', now, now),
            ('send_alert', 'webhook', '{"method": "POST", "url": "{{webhook_url}}"}', 'Send alert notification', now, now),
            ('switch_database', 'database', '{"action": "failover", "target": "{{backup_db}}"}', 'Switch to backup database', now, now),
        ]
        conn.executemany(
            """
            INSERT OR IGNORE INTO recovery_actions
            (name, action_type, config, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            default_actions
        )
        set_schema_version(conn, 9)
        version = 9

    if version < 10:
        # Phase 3: L2审批恢复系统表
        # Approval requests with optimistic locking
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                command TEXT NOT NULL,
                risk_level TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'pending',
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
        # Approval audit log
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                actor_id TEXT,
                previous_status TEXT,
                new_status TEXT NOT NULL,
                version INTEGER NOT NULL,
                reason TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (request_id) REFERENCES approval_requests(id)
            )
            """
        )
        # Approval events for SSE
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
        # Nonce storage for replay attack prevention
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_nonces (
                nonce TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL
            )
            """
        )
        # Indexes for approval queries
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
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nonce_expires
            ON approval_nonces(expires_at)
            """
        )
        set_schema_version(conn, 10)
        version = 10

    if version < 11:
        # Phase 3: 归因分析系统表
        # Attribution reports for incident root cause analysis
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
        # Incident attributions linking table
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
        # Indexes for attribution queries
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
        set_schema_version(conn, 11)
        version = 11

    if version < 12:
        # Phase 4: 经验沉淀系统 - Knowledge Base
        # Knowledge entries for reusable solutions
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
        # Indexes for knowledge queries
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
        set_schema_version(conn, 12)


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
    """Rebuild FTS index."""
    conn.execute("DROP TRIGGER IF EXISTS observations_ai")
    conn.execute("DROP TRIGGER IF EXISTS observations_ad")
    conn.execute("DROP TRIGGER IF EXISTS observations_au")
    conn.execute("DROP TABLE IF EXISTS observations_fts")
    ensure_fts(conn)


def init_db(path: str) -> None:
    """Initialize database."""
    conn = connect_db(path)
    ensure_schema(conn)
    ensure_fts(conn)
    conn.close()
