"""High-level Python API for los-memory.

This module provides MemoryClient - a user-friendly Python API for interacting
with the memory ledger. It wraps low-level database operations with a clean,
intuitive interface suitable for use in AI agents and other Python applications.

Example:
    from memory_tool.client import MemoryClient

    # Using context manager (recommended)
    with MemoryClient(profile="claude") as memory:
        # Add an observation
        obs = memory.add(
            title="Found a bug",
            summary="The auth module has a race condition",
            tags=["bug", "auth"]
        )

        # Search observations
        results = memory.search("auth bug", limit=5)

        # Get active session
        session = memory.get_active_session()

    # Using explicit lifecycle management
    memory = MemoryClient(db_path="/path/to/memory.db")
    memory.connect()
    try:
        obs = memory.add(title="Note", summary="Content")
    finally:
        memory.close()
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

from .database import connect_db, ensure_fts, ensure_schema, init_db
from .models import Observation, Session, Checkpoint
from .operations import (
    add_observation,
    run_search,
    run_timeline,
    run_get,
    run_edit,
    run_delete,
    run_list,
    run_clean,
)
from .sessions import (
    start_session,
    end_session,
    get_active_session,
    set_active_session,
    clear_active_session,
    list_sessions,
    get_session,
    get_session_observations,
    generate_session_summary,
)
from .checkpoints import (
    create_checkpoint,
    list_checkpoints,
    get_checkpoint,
    resume_from_checkpoint,
)
from .projects import (
    get_active_project,
    set_active_project,
    list_projects,
    get_project_stats,
)
from .analytics import (
    log_tool_call,
    log_agent_transition,
    get_tool_stats,
    suggest_tools_for_task,
)
from .feedback import apply_feedback, get_feedback_history
from .links import create_link, delete_link, get_related_observations, find_similar_observations
from .utils import (
    resolve_db_path,
    normalize_tags_list,
    tags_to_json,
    tags_to_text,
    utc_now,
)


class MemoryError(Exception):
    """Base exception for memory client errors."""
    pass


class ConnectionError(MemoryError):
    """Raised when database connection fails."""
    pass


class NotFoundError(MemoryError):
    """Raised when requested resource is not found."""
    pass


class ObservationData:
    """Data class for observation results.

    Attributes:
        id: Observation ID
        timestamp: ISO format timestamp
        project: Project name
        kind: Observation kind
        title: Short title
        summary: Detailed summary
        tags: List of tags
        raw: Raw original data
        session_id: Optional session ID
    """

    def __init__(self, obs: Observation | Dict[str, Any]):
        if isinstance(obs, Observation):
            self.id = obs.id
            self.timestamp = obs.timestamp
            self.project = obs.project
            self.kind = obs.kind
            self.title = obs.title
            self.summary = obs.summary
            self.tags = obs.tags if isinstance(obs.tags, list) else json.loads(obs.tags or "[]")
            self.raw = obs.raw
            self.session_id = obs.session_id
        else:
            self.id = obs.get("id")
            self.timestamp = obs.get("timestamp")
            self.project = obs.get("project")
            self.kind = obs.get("kind")
            self.title = obs.get("title")
            self.summary = obs.get("summary")
            tags = obs.get("tags", "[]")
            self.tags = tags if isinstance(tags, list) else json.loads(tags)
            self.raw = obs.get("raw")
            self.session_id = obs.get("session_id")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "project": self.project,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "tags": self.tags,
            "raw": self.raw,
            "session_id": self.session_id,
        }

    def __repr__(self) -> str:
        return f"ObservationData(id={self.id}, title={self.title!r})"


class SessionData:
    """Data class for session results.

    Attributes:
        id: Session ID
        start_time: Session start timestamp
        end_time: Session end timestamp (None if active)
        project: Project name
        working_dir: Working directory
        agent_type: Agent type (claude/codex)
        summary: Session summary
        status: active or completed
    """

    def __init__(self, session: Session | Dict[str, Any]):
        if isinstance(session, Session):
            self.id = session.id
            self.start_time = session.start_time
            self.end_time = session.end_time
            self.project = session.project
            self.working_dir = session.working_dir
            self.agent_type = session.agent_type
            self.summary = session.summary
            self.status = session.status
        else:
            self.id = session.get("id")
            self.start_time = session.get("start_time")
            self.end_time = session.get("end_time")
            self.project = session.get("project")
            self.working_dir = session.get("working_dir")
            self.agent_type = session.get("agent_type")
            self.summary = session.get("summary")
            self.status = session.get("status")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "project": self.project,
            "working_dir": self.working_dir,
            "agent_type": self.agent_type,
            "summary": self.summary,
            "status": self.status,
        }

    def __repr__(self) -> str:
        return f"SessionData(id={self.id}, status={self.status})"


class MemoryClient:
    """High-level client for los-memory operations.

    Provides a clean Python API for managing observations, sessions,
    projects, and checkpoints. Can be used as a context manager or
    with explicit connect/close calls.

    Args:
        profile: Memory profile (claude, codex, shared)
        db_path: Direct database path (overrides profile)
        auto_connect: Whether to auto-connect on first use

    Example:
        # Context manager (auto-connect/close)
        with MemoryClient(profile="claude") as m:
            m.add(title="Note", summary="Content")

        # Explicit management
        client = MemoryClient(db_path="/path/to/db")
        client.connect()
        try:
            client.add(title="Note", summary="Content")
        finally:
            client.close()
    """

    def __init__(
        self,
        profile: str = "claude",
        db_path: Optional[str] = None,
        auto_connect: bool = False,
    ):
        self.profile = profile
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._auto_connect = auto_connect

    def _resolve_db_path(self) -> str:
        """Resolve database path from profile or explicit path."""
        if self.db_path:
            return self.db_path
        return resolve_db_path(self.profile, None)

    def connect(self) -> "MemoryClient":
        """Connect to the database.

        Returns:
            Self for method chaining

        Raises:
            ConnectionError: If connection fails
        """
        try:
            db_path = self._resolve_db_path()
            self._conn = connect_db(db_path)
            ensure_schema(self._conn)
            ensure_fts(self._conn)
            return self
        except sqlite3.Error as e:
            raise ConnectionError(f"Failed to connect to database: {e}")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def init_database(self) -> "MemoryClient":
        """Initialize a new database.

        Returns:
            Self for method chaining
        """
        from .database import init_db
        db_path = self._resolve_db_path()
        init_db(db_path)
        return self

    def _ensure_connected(self) -> sqlite3.Connection:
        """Ensure connection exists, auto-connect if enabled."""
        if self._conn is None:
            if self._auto_connect:
                self.connect()
            else:
                raise ConnectionError("Not connected. Call connect() or use context manager.")
        return self._conn

    # ========================================================================
    # Context Manager
    # ========================================================================

    def __enter__(self) -> "MemoryClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ========================================================================
    # Observation Operations
    # ========================================================================

    def add(
        self,
        title: str,
        summary: str,
        project: Optional[str] = None,
        kind: str = "note",
        tags: Optional[List[str]] = None,
        raw: Optional[str] = None,
        auto_tags: bool = False,
    ) -> ObservationData:
        """Add a new observation.

        Args:
            title: Short title (required)
            summary: Detailed summary (required)
            project: Project name (defaults to active project or "general")
            kind: Observation kind (default: note)
            tags: List of tags
            raw: Raw original data
            auto_tags: Auto-generate tags from content

        Returns:
            Created observation data
        """
        conn = self._ensure_connected()

        # Use active project if not specified
        if project is None:
            project = get_active_project(self.profile) or "general"

        # Process tags
        tags_list = tags or []
        if auto_tags and not tags_list:
            from .utils import auto_tags_from_text
            tags_list = auto_tags_from_text(title, summary)

        # Get active session
        active_session = get_active_session(self.profile)
        session_id = active_session["session_id"] if active_session else None

        obs_id = add_observation(
            conn,
            utc_now(),
            project,
            kind,
            title,
            summary,
            tags_to_json(tags_list),
            tags_to_text(tags_list),
            raw or "",
            session_id,
        )

        # Fetch the created observation
        results = run_get(conn, [obs_id])
        if not results:
            raise MemoryError(f"Failed to fetch created observation {obs_id}")

        return ObservationData(results[0])

    def get(self, obs_id: int) -> ObservationData:
        """Get an observation by ID.

        Args:
            obs_id: Observation ID

        Returns:
            Observation data

        Raises:
            NotFoundError: If observation not found
        """
        conn = self._ensure_connected()
        results = run_get(conn, [obs_id])
        if not results:
            raise NotFoundError(f"Observation {obs_id} not found")
        return ObservationData(results[0])

    def get_many(self, obs_ids: List[int]) -> List[ObservationData]:
        """Get multiple observations by ID.

        Args:
            obs_ids: List of observation IDs

        Returns:
            List of observation data
        """
        conn = self._ensure_connected()
        results = run_get(conn, obs_ids)
        return [ObservationData(r) for r in results]

    def list(
        self,
        limit: int = 20,
        offset: int = 0,
        require_tags: Optional[List[str]] = None,
    ) -> List[ObservationData]:
        """List observations.

        Args:
            limit: Maximum results
            offset: Result offset
            require_tags: Tags that results must have

        Returns:
            List of observation data
        """
        conn = self._ensure_connected()
        results = run_list(conn, limit, offset=offset, required_tags=require_tags)
        return [ObservationData(r) for r in results]

    def search(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        mode: str = "auto",
        require_tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search observations.

        Args:
            query: Search query
            limit: Maximum results
            offset: Result offset
            mode: Search mode (auto, fts, like)
            require_tags: Tags that results must have

        Returns:
            List of search results with observation and rank
        """
        conn = self._ensure_connected()
        return run_search(
            conn,
            query,
            limit,
            offset=offset,
            mode=mode,
            required_tags=require_tags,
        )

    def timeline(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        around_id: Optional[int] = None,
        window_minutes: int = 120,
        limit: int = 20,
        offset: int = 0,
    ) -> List[ObservationData]:
        """Query timeline of observations.

        Args:
            start: Start timestamp (ISO format)
            end: End timestamp (ISO format)
            around_id: Get observations around this ID
            window_minutes: Window size when using around_id
            limit: Maximum results
            offset: Result offset

        Returns:
            List of observation data
        """
        conn = self._ensure_connected()
        results = run_timeline(conn, start, end, around_id, window_minutes, limit, offset=offset)
        return [ObservationData(r) for r in results]

    def edit(
        self,
        obs_id: int,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        project: Optional[str] = None,
        kind: Optional[str] = None,
        tags: Optional[List[str]] = None,
        raw: Optional[str] = None,
    ) -> ObservationData:
        """Edit an observation.

        Args:
            obs_id: Observation ID
            title: New title (optional)
            summary: New summary (optional)
            project: New project (optional)
            kind: New kind (optional)
            tags: New tags (optional)
            raw: New raw data (optional)

        Returns:
            Updated observation data

        Raises:
            NotFoundError: If observation not found
        """
        conn = self._ensure_connected()

        tags_str = None
        if tags is not None:
            tags_str = tags_to_json(tags)

        result = run_edit(
            conn,
            obs_id,
            project,
            kind,
            title,
            summary,
            tags_str,
            raw,
            timestamp=None,
            auto_tags=False,
        )

        if not result.get("ok"):
            raise NotFoundError(f"Observation {obs_id} not found")

        return self.get(obs_id)

    def delete(self, obs_ids: Union[int, List[int]], dry_run: bool = False) -> Dict[str, Any]:
        """Delete observations.

        Args:
            obs_ids: Single ID or list of IDs to delete
            dry_run: Preview without deleting

        Returns:
            Deletion result with count
        """
        conn = self._ensure_connected()

        if isinstance(obs_ids, int):
            obs_ids = [obs_ids]

        return run_delete(conn, obs_ids, dry_run)

    def clean(
        self,
        before: Optional[str] = None,
        older_than_days: Optional[int] = None,
        project: Optional[str] = None,
        kind: Optional[str] = None,
        tag: Optional[str] = None,
        all_obs: bool = False,
        dry_run: bool = False,
        vacuum: bool = False,
    ) -> Dict[str, Any]:
        """Clean old observations.

        Args:
            before: Delete observations before this timestamp
            older_than_days: Delete observations older than N days
            project: Filter by project
            kind: Filter by kind
            tag: Filter by tag
            all_obs: Delete all observations (use with caution)
            dry_run: Preview without deleting
            vacuum: Run VACUUM after deletion

        Returns:
            Cleanup result with deleted count
        """
        conn = self._ensure_connected()
        return run_clean(
            conn,
            before,
            older_than_days,
            project,
            kind,
            tag,
            all_obs,
            dry_run,
            vacuum,
        )

    def capture(
        self,
        text: str,
        project: Optional[str] = None,
        kind: str = "note",
        tags: Optional[List[str]] = None,
        auto_tags: bool = False,
    ) -> ObservationData:
        """Quick capture from text.

        Automatically extracts title from first sentence or first 80 chars.

        Args:
            text: Full text content
            project: Project name
            kind: Observation kind
            tags: List of tags
            auto_tags: Auto-generate tags

        Returns:
            Created observation data
        """
        # Extract title from text
        sentences = text.replace("! ", "!|").replace("? ", "?|").replace(". ", ".|").split("|")
        if len(sentences) > 1 and len(sentences[0]) < 100:
            title = sentences[0].strip()
            summary = " ".join(s.strip() for s in sentences[1:]).strip()
            if not summary:
                summary = title
        else:
            if len(text) <= 80:
                title = text
                summary = text
            else:
                break_point = text.rfind(" ", 0, 80)
                if break_point == -1:
                    break_point = 80
                title = text[:break_point].strip()
                summary = text.strip()

        return self.add(
            title=title,
            summary=summary,
            project=project,
            kind=kind,
            tags=tags,
            raw=text,
            auto_tags=auto_tags,
        )

    # ========================================================================
    # Session Operations
    # ========================================================================

    def start_session(
        self,
        project: str = "general",
        working_dir: Optional[str] = None,
        agent_type: Optional[str] = None,
        summary: str = "",
    ) -> SessionData:
        """Start a new session.

        Args:
            project: Project name
            working_dir: Working directory
            agent_type: Agent type (defaults to profile)
            summary: Session summary

        Returns:
            Created session data
        """
        conn = self._ensure_connected()

        if working_dir is None:
            import os
            working_dir = os.getcwd()

        if agent_type is None:
            agent_type = self.profile

        session_id = start_session(conn, project, working_dir, agent_type, summary)
        set_active_session(self.profile, session_id, "")

        session = get_session(conn, session_id)
        return SessionData(session)

    def end_session(self, summary: Optional[str] = None) -> SessionData:
        """End the active session.

        Args:
            summary: Session summary (auto-generated if not provided)

        Returns:
            Ended session data

        Raises:
            NotFoundError: If no active session
        """
        conn = self._ensure_connected()

        active = get_active_session(self.profile)
        if not active:
            raise NotFoundError("No active session")

        if summary is None:
            summary = generate_session_summary(conn, active["session_id"])

        end_session(conn, active["session_id"], summary)
        clear_active_session(self.profile)

        session = get_session(conn, active["session_id"])
        return SessionData(session)

    def get_active_session(self) -> Optional[Dict[str, Any]]:
        """Get the currently active session info.

        Returns:
            Active session dict or None
        """
        return get_active_session(self.profile)

    def list_sessions(
        self,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[SessionData]:
        """List sessions.

        Args:
            status: Filter by status (active/completed)
            limit: Maximum results

        Returns:
            List of session data
        """
        conn = self._ensure_connected()
        sessions = list_sessions(conn, status=status, limit=limit)
        return [SessionData(s) for s in sessions]

    def get_session(self, session_id: int) -> SessionData:
        """Get session by ID.

        Args:
            session_id: Session ID

        Returns:
            Session data

        Raises:
            NotFoundError: If session not found
        """
        conn = self._ensure_connected()
        session = get_session(conn, session_id)
        if not session:
            raise NotFoundError(f"Session {session_id} not found")
        return SessionData(session)

    def get_session_observations(self, session_id: int) -> List[ObservationData]:
        """Get all observations in a session.

        Args:
            session_id: Session ID

        Returns:
            List of observation data
        """
        conn = self._ensure_connected()
        results = get_session_observations(conn, session_id)
        return [ObservationData(r) for r in results]

    # ========================================================================
    # Project Operations
    # ========================================================================

    def set_active_project(self, project_name: str) -> None:
        """Set the active project.

        Args:
            project_name: Project name
        """
        set_active_project(self.profile, project_name)

    def get_active_project(self) -> Optional[str]:
        """Get the currently active project.

        Returns:
            Active project name or None
        """
        return get_active_project(self.profile)

    def list_projects(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List all projects.

        Args:
            limit: Maximum results

        Returns:
            List of project info dicts
        """
        conn = self._ensure_connected()
        return list_projects(conn, limit)

    def get_project_stats(self, project_name: Optional[str] = None) -> Dict[str, Any]:
        """Get project statistics.

        Args:
            project_name: Project name (defaults to active)

        Returns:
            Project statistics
        """
        conn = self._ensure_connected()
        if project_name is None:
            project_name = get_active_project(self.profile) or "general"
        return get_project_stats(conn, project_name)

    # ========================================================================
    # Checkpoint Operations
    # ========================================================================

    def create_checkpoint(
        self,
        name: str,
        description: str = "",
        tag: str = "",
    ) -> Dict[str, Any]:
        """Create a checkpoint.

        Args:
            name: Checkpoint name
            description: Checkpoint description
            tag: Checkpoint tag

        Returns:
            Checkpoint info with ID
        """
        conn = self._ensure_connected()

        active_session = get_active_session(self.profile)
        session_id = active_session["session_id"] if active_session else None
        project = get_active_project(self.profile) or "general"

        checkpoint_id = create_checkpoint(conn, name, description, tag, session_id, project)
        return {"id": checkpoint_id, "name": name, "project": project}

    def list_checkpoints(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List checkpoints.

        Args:
            limit: Maximum results

        Returns:
            List of checkpoint info
        """
        conn = self._ensure_connected()
        checkpoints = list_checkpoints(conn, limit=limit)
        return [asdict(c) for c in checkpoints]

    def get_checkpoint(self, checkpoint_id: int) -> Dict[str, Any]:
        """Get checkpoint by ID.

        Args:
            checkpoint_id: Checkpoint ID

        Returns:
            Checkpoint info with observations

        Raises:
            NotFoundError: If checkpoint not found
        """
        conn = self._ensure_connected()

        checkpoint = get_checkpoint(conn, checkpoint_id)
        if not checkpoint:
            raise NotFoundError(f"Checkpoint {checkpoint_id} not found")

        observations = get_checkpoint_observations(conn, checkpoint_id)
        return {
            "checkpoint": asdict(checkpoint),
            "observations": [asdict(o) for o in observations],
        }

    def resume_from_checkpoint(self, checkpoint_id: int) -> Dict[str, Any]:
        """Resume from a checkpoint.

        Args:
            checkpoint_id: Checkpoint ID

        Returns:
            Resume result with restored session info
        """
        conn = self._ensure_connected()
        return resume_from_checkpoint(conn, checkpoint_id, self.profile)

    # ========================================================================
    # Feedback Operations
    # ========================================================================

    def add_feedback(
        self,
        obs_id: int,
        feedback_text: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Add feedback to an observation.

        Args:
            obs_id: Target observation ID
            feedback_text: Feedback text
            dry_run: Preview without applying

        Returns:
            Feedback result with changes
        """
        conn = self._ensure_connected()
        result = apply_feedback(conn, obs_id, feedback_text, auto_apply=not dry_run)
        result["dry_run"] = dry_run
        return result

    def get_feedback_history(self, obs_id: int) -> List[Dict[str, Any]]:
        """Get feedback history for an observation.

        Args:
            obs_id: Observation ID

        Returns:
            List of feedback entries
        """
        conn = self._ensure_connected()
        return get_feedback_history(conn, obs_id)

    # ========================================================================
    # Link Operations
    # ========================================================================

    def link(
        self,
        from_id: int,
        to_id: int,
        link_type: str = "related",
    ) -> Dict[str, Any]:
        """Create a link between observations.

        Args:
            from_id: Source observation ID
            to_id: Target observation ID
            link_type: Link type (related, child, parent, refines)

        Returns:
            Link info with ID
        """
        conn = self._ensure_connected()
        link_id = create_link(conn, from_id, to_id, link_type)
        return {
            "id": link_id,
            "from_id": from_id,
            "to_id": to_id,
            "type": link_type,
        }

    def unlink(
        self,
        from_id: int,
        to_id: int,
        link_type: Optional[str] = None,
    ) -> bool:
        """Remove a link between observations.

        Args:
            from_id: Source observation ID
            to_id: Target observation ID
            link_type: Specific link type to remove (optional)

        Returns:
            True if link was deleted
        """
        conn = self._ensure_connected()
        return delete_link(conn, from_id, to_id, link_type)

    def get_related(
        self,
        obs_id: int,
        link_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get related observations.

        Args:
            obs_id: Observation ID
            link_type: Filter by link type
            limit: Maximum results

        Returns:
            List of related observations
        """
        conn = self._ensure_connected()
        return get_related_observations(conn, obs_id, link_type, limit)

    def find_similar(
        self,
        obs_id: int,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Find potentially similar observations.

        Args:
            obs_id: Observation ID
            limit: Maximum results

        Returns:
            List of similar observation suggestions
        """
        conn = self._ensure_connected()
        return find_similar_observations(conn, obs_id, limit)

    # ========================================================================
    # Analytics Operations
    # ========================================================================

    def log_tool(
        self,
        tool: str,
        tool_input: Dict[str, Any],
        tool_output: Dict[str, Any],
        status: str = "success",
        duration: Optional[int] = None,
        project: Optional[str] = None,
    ) -> ObservationData:
        """Log a tool call.

        Args:
            tool: Tool name
            tool_input: Tool input data
            tool_output: Tool output data
            status: Call status (success/error)
            duration: Duration in milliseconds
            project: Project name

        Returns:
            Created observation
        """
        conn = self._ensure_connected()

        if project is None:
            project = get_active_project(self.profile) or "general"

        active_session = get_active_session(self.profile)
        session_id = active_session["session_id"] if active_session else None

        obs_id = log_tool_call(
            conn,
            tool,
            tool_input,
            tool_output,
            status,
            duration,
            project,
            session_id,
        )

        return self.get(obs_id)

    def log_transition(
        self,
        phase: str,
        action: str,
        transition_input: Dict[str, Any],
        transition_output: Dict[str, Any],
        status: str = "success",
        reward: Optional[float] = None,
        project: Optional[str] = None,
    ) -> ObservationData:
        """Log an agent transition.

        Args:
            phase: Transition phase (plan/act/review)
            action: Transition action name
            transition_input: Input data
            transition_output: Output data
            status: Transition status
            reward: Optional reward score
            project: Project name

        Returns:
            Created observation
        """
        conn = self._ensure_connected()

        if project is None:
            project = get_active_project(self.profile) or "general"

        active_session = get_active_session(self.profile)
        session_id = active_session["session_id"] if active_session else None

        obs_id = log_agent_transition(
            conn,
            phase,
            action,
            transition_input,
            transition_output,
            status,
            reward,
            project,
            session_id,
        )

        return self.get(obs_id)

    def get_tool_stats(
        self,
        project: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Get tool usage statistics.

        Args:
            project: Filter by project
            limit: Maximum tools to show

        Returns:
            Tool statistics
        """
        conn = self._ensure_connected()
        return get_tool_stats(conn, project, limit)

    def suggest_tools(
        self,
        task: str,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Suggest tools for a task.

        Args:
            task: Task description
            limit: Maximum suggestions

        Returns:
            Tool suggestions
        """
        conn = self._ensure_connected()
        return suggest_tools_for_task(conn, task, limit)


# Convenience function for quick operations
@contextmanager
def memory(
    profile: str = "claude",
    db_path: Optional[str] = None,
) -> Iterator[MemoryClient]:
    """Context manager for quick memory operations.

    Args:
        profile: Memory profile
        db_path: Database path override

    Example:
        with memory() as m:
            m.add(title="Note", summary="Content")
            results = m.search("keyword")
    """
    client = MemoryClient(profile=profile, db_path=db_path)
    client.connect()
    try:
        yield client
    finally:
        client.close()


__all__ = [
    "MemoryClient",
    "ObservationData",
    "SessionData",
    "MemoryError",
    "ConnectionError",
    "NotFoundError",
    "memory",
]
