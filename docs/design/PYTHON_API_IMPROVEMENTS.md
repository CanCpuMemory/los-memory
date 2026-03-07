# los-memory Python API Improvements

## Current Python API Analysis

### Issues with Current API

1. **Inconsistent Return Types**
   - Some functions return `dict`, others return `List[Observation]`
   - No standardized error handling
   - Missing metadata (duration, row count)

2. **Poor Type Hints**
   - Missing return type annotations
   - Optional parameters not properly typed
   - No generic types for pagination

3. **No Context Manager Support**
   - Database connection management is manual
   - No automatic cleanup
   - Resource leaks possible

4. **Limited Error Handling**
   - Uses generic `ValueError` for all errors
   - No custom exception hierarchy
   - Error messages not standardized

5. **Missing Async Support**
   - No async/await API
   - Blocking I/O only
   - Not suitable for async applications

6. **No Streaming Support**
   - Large result sets loaded entirely into memory
   - No generator-based iteration
   - Memory inefficient for large databases

## Improved API Design

### Module Structure

```
memory_tool/
├── __init__.py          # Public API exports
├── client.py            # MemoryClient class
├── async_client.py      # AsyncMemoryClient class
├── exceptions.py        # Custom exceptions
├── models.py            # Data models (enhanced)
├── types.py             # Type definitions
├── pagination.py        # Pagination utilities
└── _internal/           # Internal implementation
    ├── database.py
    ├── operations.py
    └── utils.py
```

### Custom Exceptions

```python
# exceptions.py
"""Custom exceptions for los-memory."""


class MemoryToolError(Exception):
    """Base exception for all memory tool errors."""

    def __init__(self, message: str, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code or "UNKNOWN_ERROR"
        self.details = details or {}


class ValidationError(MemoryToolError):
    """Raised when input validation fails."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message, code="VAL_INVALID_INPUT", details={"field": field})
        self.field = field


class ObservationNotFoundError(MemoryToolError):
    """Raised when an observation is not found."""

    def __init__(self, observation_id: int):
        super().__init__(
            f"Observation {observation_id} not found",
            code="NF_OBSERVATION",
            details={"id": observation_id}
        )
        self.observation_id = observation_id


class SessionNotFoundError(MemoryToolError):
    """Raised when a session is not found."""

    def __init__(self, session_id: int):
        super().__init__(
            f"Session {session_id} not found",
            code="NF_SESSION",
            details={"id": session_id}
        )
        self.session_id = session_id


class DatabaseError(MemoryToolError):
    """Raised when a database operation fails."""

    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message, code="DB_ERROR")
        self.original_error = original_error


class SchemaVersionError(DatabaseError):
    """Raised when schema version mismatch occurs."""

    def __init__(self, current: int, expected: int):
        super().__init__(
            f"Schema version {current} does not match expected {expected}",
            code="DB_SCHEMA_VERSION"
        )
        self.current = current
        self.expected = expected


class PermissionError(MemoryToolError):
    """Raised when permission is denied."""

    def __init__(self, path: str, operation: str):
        super().__init__(
            f"Permission denied for {operation} on {path}",
            code="SYS_PERMISSION",
            details={"path": path, "operation": operation}
        )
```

### Enhanced Models

```python
# models.py
"""Enhanced data models with validation and serialization."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar


class ObservationKind(str, Enum):
    """Observation kinds."""
    NOTE = "note"
    DECISION = "decision"
    FIX = "fix"
    INCIDENT = "incident"


class SessionStatus(str, Enum):
    """Session statuses."""
    ACTIVE = "active"
    COMPLETED = "completed"


class LinkType(str, Enum):
    """Observation link types."""
    RELATED = "related"
    CHILD = "child"
    PARENT = "parent"
    REFINES = "refines"


@dataclass(frozen=True, slots=True)
class Observation:
    """Observation data model."""
    id: int
    timestamp: datetime
    project: str
    kind: ObservationKind
    title: str
    summary: str
    tags: tuple[str, ...]
    raw: str
    session_id: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Observation:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            timestamp=datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00")),
            project=data["project"],
            kind=ObservationKind(data["kind"]),
            title=data["title"],
            summary=data["summary"],
            tags=tuple(data.get("tags", [])),
            raw=data["raw"],
            session_id=data.get("session_id")
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "project": self.project,
            "kind": self.kind.value,
            "title": self.title,
            "summary": self.summary,
            "tags": list(self.tags),
            "raw": self.raw,
            "session_id": self.session_id
        }


@dataclass(frozen=True, slots=True)
class Session:
    """Session data model."""
    id: int
    start_time: datetime
    end_time: datetime | None
    project: str
    working_dir: str
    agent_type: str
    summary: str
    status: SessionStatus

    @property
    def is_active(self) -> bool:
        """Check if session is active."""
        return self.status == SessionStatus.ACTIVE

    @property
    def duration(self) -> float | None:
        """Get session duration in seconds."""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Checkpoint data model."""
    id: int
    timestamp: datetime
    name: str
    description: str
    tag: str
    session_id: int | None
    observation_count: int
    project: str


@dataclass(frozen=True, slots=True)
class ObservationLink:
    """Observation link data model."""
    id: int
    from_id: int
    to_id: int
    link_type: LinkType
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Search result with relevance score."""
    observation: Observation
    score: float | None


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class PaginatedResult(Generic[T]):
    """Paginated result container."""
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        """Check if more results available."""
        return self.offset + len(self.items) < self.total

    @property
    def next_offset(self) -> int | None:
        """Get next offset for pagination."""
        if self.has_more:
            return self.offset + self.limit
        return None

    def __iter__(self):
        """Iterate over items."""
        return iter(self.items)

    def __len__(self) -> int:
        """Get item count."""
        return len(self.items)

    def __getitem__(self, index: int) -> T:
        """Get item by index."""
        return self.items[index]


@dataclass(frozen=True, slots=True)
class DatabaseStats:
    """Database statistics."""
    total_observations: int
    earliest_timestamp: datetime | None
    latest_timestamp: datetime | None
    projects: list[tuple[str, int]]
    kinds: list[tuple[str, int]]
    database_size_bytes: int | None = None
```

### Type Definitions

```python
# types.py
"""Type definitions for los-memory."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict


Profile = Literal["codex", "claude", "shared"]
SearchMode = Literal["auto", "fts", "like"]


class ObservationInput(TypedDict, total=False):
    """Input for creating observation."""
    title: str
    summary: str
    project: str
    kind: str
    tags: list[str]
    raw: str
    timestamp: datetime | str | None
    auto_tags: bool


class SearchOptions(TypedDict, total=False):
    """Options for search operation."""
    limit: int
    offset: int
    mode: SearchMode
    quote: bool
    required_tags: list[str]


class TimelineOptions(TypedDict, total=False):
    """Options for timeline operation."""
    start: datetime | str | None
    end: datetime | str | None
    around_id: int | None
    window_minutes: int
    limit: int
    offset: int


class CleanOptions(TypedDict, total=False):
    """Options for clean operation."""
    before: datetime | str | None
    older_than_days: int | None
    project: str | None
    kind: str | None
    tag: str | None
    delete_all: bool
    dry_run: bool
    vacuum: bool
```

### Synchronous Client

```python
# client.py
"""Synchronous MemoryClient for los-memory."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator, Literal

from .exceptions import (
    DatabaseError,
    ObservationNotFoundError,
    PermissionError,
    SchemaVersionError,
    ValidationError,
)
from .models import (
    Checkpoint,
    DatabaseStats,
    Observation,
    ObservationKind,
    ObservationLink,
    PaginatedResult,
    SearchResult,
    Session,
    SessionStatus,
)
from .types import CleanOptions, ObservationInput, Profile, SearchOptions, TimelineOptions


class MemoryClient:
    """Synchronous client for los-memory operations.

    Usage:
        # Context manager (recommended)
        with MemoryClient() as client:
            obs = client.add_observation(title="Test", summary="Test summary")
            results = client.search("test")

        # Manual management
        client = MemoryClient(profile="claude")
        try:
            results = client.search("query")
        finally:
            client.close()
    """

    def __init__(
        self,
        profile: Profile | None = None,
        db_path: str | Path | None = None,
        auto_init: bool = True
    ):
        """Initialize client.

        Args:
            profile: Memory profile (codex, claude, shared)
            db_path: Explicit database path (overrides profile)
            auto_init: Auto-initialize database if needed
        """
        self._profile = profile or self._get_default_profile()
        self._db_path = self._resolve_db_path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._auto_init = auto_init
        self._closed = False

    def __enter__(self) -> MemoryClient:
        """Enter context manager."""
        self._ensure_connection()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager."""
        self.close()

    def _get_default_profile(self) -> Profile:
        """Get default profile from environment."""
        profile = os.environ.get("MEMORY_PROFILE", "codex").lower()
        if profile not in ("codex", "claude", "shared"):
            return "codex"
        return profile  # type: ignore

    def _resolve_db_path(self, explicit_path: str | Path | None) -> Path:
        """Resolve database path."""
        if explicit_path:
            return Path(explicit_path).expanduser()

        paths = {
            "codex": "~/.codex_memory/memory.db",
            "claude": "~/.claude_memory/memory.db",
            "shared": "~/.local/share/llm-memory/memory.db",
        }
        return Path(paths[self._profile]).expanduser()

    def _ensure_connection(self) -> None:
        """Ensure database connection exists."""
        if self._closed:
            raise RuntimeError("Client has been closed")

        if self._conn is None:
            try:
                self._conn = sqlite3.connect(self._db_path)
                self._conn.row_factory = sqlite3.Row

                if self._auto_init:
                    from .database import ensure_schema, ensure_fts
                    ensure_schema(self._conn)
                    ensure_fts(self._conn)

            except sqlite3.Error as e:
                raise DatabaseError(f"Failed to connect to database: {e}", e)
            except PermissionError as e:
                raise PermissionError(str(self._db_path), "connect")

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
        self._closed = True

    @property
    def is_closed(self) -> bool:
        """Check if client is closed."""
        return self._closed

    @property
    def db_path(self) -> Path:
        """Get database path."""
        return self._db_path

    @property
    def profile(self) -> Profile:
        """Get profile."""
        return self._profile

    # Observation Operations

    def add_observation(
        self,
        title: str,
        summary: str,
        *,
        project: str | None = None,
        kind: str | ObservationKind = ObservationKind.NOTE,
        tags: list[str] | None = None,
        raw: str = "",
        timestamp: datetime | str | None = None,
        auto_tags: bool = False,
        session_id: int | None = None
    ) -> Observation:
        """Add a new observation.

        Args:
            title: Observation title
            summary: Observation summary
            project: Project name (defaults to active project)
            kind: Observation kind
            tags: List of tags
            raw: Raw content
            timestamp: Custom timestamp (defaults to now)
            auto_tags: Auto-generate tags from content
            session_id: Associate with session

        Returns:
            Created observation

        Raises:
            ValidationError: If title or summary is empty
            DatabaseError: If database operation fails
        """
        if not title or not title.strip():
            raise ValidationError("Title is required", field="title")
        if not summary or not summary.strip():
            raise ValidationError("Summary is required", field="summary")

        self._ensure_connection()

        # Resolve project
        if project is None:
            project = self.get_active_project() or "general"

        # Resolve timestamp
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        elif isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

        # Auto-generate tags
        tag_list = tags or []
        if auto_tags and not tag_list:
            from .utils import auto_tags_from_text
            tag_list = auto_tags_from_text(title, summary)

        # Insert
        from .utils import tags_to_json, tags_to_text, normalize_text
        cursor = self._conn.execute(
            """
            INSERT INTO observations (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp.isoformat(),
                project,
                kind.value if isinstance(kind, ObservationKind) else kind,
                normalize_text(title),
                normalize_text(summary),
                tags_to_json(tag_list),
                tags_to_text(tag_list),
                raw,
                session_id
            )
        )
        self._conn.commit()

        obs_id = cursor.lastrowid
        return self.get_observation(obs_id)

    def get_observation(self, observation_id: int) -> Observation:
        """Get observation by ID.

        Args:
            observation_id: Observation ID

        Returns:
            Observation

        Raises:
            ObservationNotFoundError: If observation not found
        """
        self._ensure_connection()

        row = self._conn.execute(
            "SELECT * FROM observations WHERE id = ?",
            (observation_id,)
        ).fetchone()

        if row is None:
            raise ObservationNotFoundError(observation_id)

        return self._row_to_observation(row)

    def get_observations(self, observation_ids: list[int]) -> list[Observation]:
        """Get multiple observations by ID.

        Args:
            observation_ids: List of observation IDs

        Returns:
            List of observations (may be shorter than input if some not found)
        """
        if not observation_ids:
            return []

        self._ensure_connection()

        placeholders = ",".join("?" * len(observation_ids))
        rows = self._conn.execute(
            f"SELECT * FROM observations WHERE id IN ({placeholders}) ORDER BY timestamp DESC",
            observation_ids
        ).fetchall()

        return [self._row_to_observation(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
        mode: Literal["auto", "fts", "like"] = "auto",
        quote: bool = False,
        required_tags: list[str] | None = None
    ) -> PaginatedResult[SearchResult]:
        """Search observations.

        Args:
            query: Search query
            limit: Maximum results
            offset: Result offset
            mode: Search mode (auto, fts, like)
            quote: Quote query for FTS safety
            required_tags: Tags that all results must have

        Returns:
            Paginated search results
        """
        self._ensure_connection()

        from .operations import run_search
        from .utils import parse_tags_json, quote_fts_query

        if not query.strip():
            return PaginatedResult(items=[], total=0, limit=limit, offset=offset)

        search_query = quote_fts_query(query) if quote else query

        results = run_search(
            self._conn,
            search_query,
            limit=limit + 1,  # Fetch one extra to check has_more
            offset=offset,
            mode=mode,
            quote=False,  # Already quoted if needed
            required_tags=required_tags
        )

        has_more = len(results) > limit
        if has_more:
            results = results[:limit]

        search_results = [
            SearchResult(
                observation=Observation(
                    id=r["id"],
                    timestamp=datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")),
                    project=r["project"],
                    kind=ObservationKind(r["kind"]),
                    title=r["title"],
                    summary=r["summary"],
                    tags=tuple(r.get("tags", [])),
                    raw=r.get("raw", ""),
                    session_id=r.get("session_id")
                ),
                score=r.get("score")
            )
            for r in results
        ]

        # Get total count (approximate for FTS)
        total = offset + len(search_results) + (1 if has_more else 0)

        return PaginatedResult(
            items=search_results,
            total=total,
            limit=limit,
            offset=offset
        )

    def list_observations(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        project: str | None = None,
        kind: str | None = None,
        required_tags: list[str] | None = None
    ) -> PaginatedResult[Observation]:
        """List recent observations.

        Args:
            limit: Maximum results
            offset: Result offset
            project: Filter by project
            kind: Filter by kind
            required_tags: Filter by tags

        Returns:
            Paginated observations
        """
        self._ensure_connection()

        query = "SELECT * FROM observations WHERE 1=1"
        params: list[Any] = []

        if project:
            query += " AND project = ?"
            params.append(project)

        if kind:
            query += " AND kind = ?"
            params.append(kind)

        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit + 1, offset])

        rows = self._conn.execute(query, params).fetchall()

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        observations = [self._row_to_observation(row) for row in rows]

        # Filter by tags in Python (SQLite doesn't have array contains)
        if required_tags:
            observations = [
                o for o in observations
                if all(t in o.tags for t in required_tags)
            ]

        return PaginatedResult(
            items=observations,
            total=offset + len(observations) + (1 if has_more else 0),
            limit=limit,
            offset=offset
        )

    def update_observation(
        self,
        observation_id: int,
        **kwargs: Any
    ) -> Observation:
        """Update an observation.

        Args:
            observation_id: Observation ID
            **kwargs: Fields to update (title, summary, project, kind, tags, raw)

        Returns:
            Updated observation

        Raises:
            ObservationNotFoundError: If observation not found
            ValidationError: If no changes provided
        """
        self._ensure_connection()

        # Check exists
        self.get_observation(observation_id)

        if not kwargs:
            raise ValidationError("No changes provided")

        # Build update
        updates: dict[str, Any] = {}

        if "title" in kwargs:
            from .utils import normalize_text
            updates["title"] = normalize_text(kwargs["title"])

        if "summary" in kwargs:
            from .utils import normalize_text
            updates["summary"] = normalize_text(kwargs["summary"])

        if "project" in kwargs:
            updates["project"] = kwargs["project"]

        if "kind" in kwargs:
            kind = kwargs["kind"]
            updates["kind"] = kind.value if isinstance(kind, ObservationKind) else kind

        if "raw" in kwargs:
            updates["raw"] = kwargs["raw"]

        if "tags" in kwargs:
            from .utils import tags_to_json, tags_to_text, normalize_tags_list
            tags = normalize_tags_list(kwargs["tags"])
            updates["tags"] = tags_to_json(tags)
            updates["tags_text"] = tags_to_text(tags)

        if not updates:
            raise ValidationError("No valid changes provided")

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        params = list(updates.values()) + [observation_id]

        self._conn.execute(
            f"UPDATE observations SET {set_clause} WHERE id = ?",
            params
        )
        self._conn.commit()

        return self.get_observation(observation_id)

    def delete_observations(self, observation_ids: list[int]) -> int:
        """Delete observations.

        Args:
            observation_ids: List of observation IDs

        Returns:
            Number of observations deleted
        """
        if not observation_ids:
            return 0

        self._ensure_connection()

        placeholders = ",".join("?" * len(observation_ids))
        cursor = self._conn.execute(
            f"DELETE FROM observations WHERE id IN ({placeholders})",
            observation_ids
        )
        self._conn.commit()

        return cursor.rowcount

    # Session Operations

    def start_session(
        self,
        project: str = "general",
        working_dir: str | None = None,
        agent_type: str | None = None,
        summary: str = ""
    ) -> Session:
        """Start a new session.

        Args:
            project: Project name
            working_dir: Working directory
            agent_type: Agent type
            summary: Session summary

        Returns:
            Created session
        """
        self._ensure_connection()

        if working_dir is None:
            working_dir = os.getcwd()

        if agent_type is None:
            agent_type = self._profile

        from .utils import utc_now
        cursor = self._conn.execute(
            """
            INSERT INTO sessions (start_time, project, working_dir, agent_type, summary, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (utc_now(), project, working_dir, agent_type, summary, "active")
        )
        self._conn.commit()

        session_id = cursor.lastrowid

        # Set as active
        from .sessions import set_active_session
        set_active_session(self._profile, session_id, working_dir)

        return self.get_session(session_id)

    def get_session(self, session_id: int) -> Session:
        """Get session by ID.

        Args:
            session_id: Session ID

        Returns:
            Session

        Raises:
            SessionNotFoundError: If session not found
        """
        self._ensure_connection()

        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,)
        ).fetchone()

        if row is None:
            from .exceptions import SessionNotFoundError
            raise SessionNotFoundError(session_id)

        return self._row_to_session(row)

    def get_active_session(self) -> Session | None:
        """Get currently active session.

        Returns:
            Active session or None
        """
        from .sessions import get_active_session
        active = get_active_session(self._profile)

        if active:
            try:
                return self.get_session(active["session_id"])
            except Exception:
                return None
        return None

    def end_session(self, session_id: int | None = None, summary: str | None = None) -> Session:
        """End a session.

        Args:
            session_id: Session ID (defaults to active)
            summary: Final summary

        Returns:
            Ended session
        """
        self._ensure_connection()

        if session_id is None:
            active = self.get_active_session()
            if active is None:
                raise ValidationError("No active session")
            session_id = active.id

        from .utils import utc_now
        from .sessions import generate_session_summary, clear_active_session

        if summary is None:
            summary = generate_session_summary(self._conn, session_id)

        self._conn.execute(
            """
            UPDATE sessions
            SET end_time = ?, summary = ?, status = ?
            WHERE id = ?
            """,
            (utc_now(), summary, "completed", session_id)
        )
        self._conn.commit()

        clear_active_session(self._profile)

        return self.get_session(session_id)

    # Project Operations

    def get_active_project(self) -> str | None:
        """Get active project."""
        from .projects import get_active_project
        return get_active_project(self._profile)

    def set_active_project(self, project: str) -> None:
        """Set active project."""
        from .projects import set_active_project
        set_active_project(self._profile, project)

    def list_projects(self, limit: int = 50) -> list[tuple[str, int]]:
        """List projects with observation counts."""
        self._ensure_connection()

        rows = self._conn.execute(
            """
            SELECT project, COUNT(*) as count
            FROM observations
            GROUP BY project
            ORDER BY count DESC, project ASC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()

        return [(row["project"], row["count"]) for row in rows]

    # Statistics

    def get_stats(self) -> DatabaseStats:
        """Get database statistics."""
        self._ensure_connection()

        row = self._conn.execute(
            """
            SELECT COUNT(*) as total, MIN(timestamp) as earliest, MAX(timestamp) as latest
            FROM observations
            """
        ).fetchone()

        projects = self.list_projects()

        kinds_rows = self._conn.execute(
            """
            SELECT kind, COUNT(*) as count
            FROM observations
            GROUP BY kind
            ORDER BY count DESC
            """
        ).fetchall()
        kinds = [(row["kind"], row["count"]) for row in kinds_rows]

        # Get database size
        size = None
        try:
            size = self._db_path.stat().st_size
        except OSError:
            pass

        return DatabaseStats(
            total_observations=row["total"],
            earliest_timestamp=datetime.fromisoformat(row["earliest"]) if row["earliest"] else None,
            latest_timestamp=datetime.fromisoformat(row["latest"]) if row["latest"] else None,
            projects=projects,
            kinds=kinds,
            database_size_bytes=size
        )

    # Helper Methods

    def _row_to_observation(self, row: sqlite3.Row) -> Observation:
        """Convert database row to Observation."""
        from .utils import parse_tags_json
        return Observation(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")),
            project=row["project"],
            kind=ObservationKind(row["kind"]),
            title=row["title"],
            summary=row["summary"],
            tags=tuple(parse_tags_json(row["tags"])),
            raw=row["raw"],
            session_id=row.get("session_id")
        )

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        """Convert database row to Session."""
        return Session(
            id=row["id"],
            start_time=datetime.fromisoformat(row["start_time"].replace("Z", "+00:00")),
            end_time=datetime.fromisoformat(row["end_time"].replace("Z", "+00:00")) if row["end_time"] else None,
            project=row["project"],
            working_dir=row["working_dir"],
            agent_type=row["agent_type"],
            summary=row["summary"],
            status=SessionStatus(row["status"])
        )
```

### Async Client

```python
# async_client.py
"""AsyncMemoryClient for asynchronous operations."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .client import MemoryClient
from .models import Observation, PaginatedResult, SearchResult, Session
from .types import Profile


class AsyncMemoryClient:
    """Asynchronous client for los-memory.

    Wraps synchronous MemoryClient in executor for async operations.

    Usage:
        async with AsyncMemoryClient() as client:
            obs = await client.add_observation(title="Test", summary="Test")
            results = await client.search("test")
    """

    def __init__(
        self,
        profile: Profile | None = None,
        db_path: str | None = None,
        max_workers: int = 4
    ):
        self._client = MemoryClient(profile, db_path)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    async def __aenter__(self) -> AsyncMemoryClient:
        """Enter async context."""
        await asyncio.get_event_loop().run_in_executor(
            self._executor, self._client._ensure_connection
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context."""
        await self.close()

    async def close(self) -> None:
        """Close client and executor."""
        await asyncio.get_event_loop().run_in_executor(
            self._executor, self._client.close
        )
        self._executor.shutdown(wait=True)

    # Async wrappers for sync methods

    async def add_observation(self, **kwargs: Any) -> Observation:
        """Add observation asynchronously."""
        return await asyncio.get_event_loop().run_in_executor(
            self._executor, lambda: self._client.add_observation(**kwargs)
        )

    async def get_observation(self, observation_id: int) -> Observation:
        """Get observation asynchronously."""
        return await asyncio.get_event_loop().run_in_executor(
            self._executor, self._client.get_observation, observation_id
        )

    async def search(self, query: str, **kwargs: Any) -> PaginatedResult[SearchResult]:
        """Search asynchronously."""
        return await asyncio.get_event_loop().run_in_executor(
            self._executor, lambda: self._client.search(query, **kwargs)
        )

    async def list_observations(self, **kwargs: Any) -> PaginatedResult[Observation]:
        """List observations asynchronously."""
        return await asyncio.get_event_loop().run_in_executor(
            self._executor, lambda: self._client.list_observations(**kwargs)
        )

    async def start_session(self, **kwargs: Any) -> Session:
        """Start session asynchronously."""
        return await asyncio.get_event_loop().run_in_executor(
            self._executor, lambda: self._client.start_session(**kwargs)
        )

    async def get_active_session(self) -> Session | None:
        """Get active session asynchronously."""
        return await asyncio.get_event_loop().run_in_executor(
            self._executor, self._client.get_active_session
        )
```

### Public API Exports

```python
# __init__.py
"""Public API for los-memory."""
from __future__ import annotations

# Clients
from .client import MemoryClient
from .async_client import AsyncMemoryClient

# Exceptions
from .exceptions import (
    MemoryToolError,
    ValidationError,
    ObservationNotFoundError,
    SessionNotFoundError,
    DatabaseError,
    SchemaVersionError,
    PermissionError,
)

# Models
from .models import (
    Observation,
    Session,
    Checkpoint,
    ObservationLink,
    SearchResult,
    PaginatedResult,
    DatabaseStats,
    ObservationKind,
    SessionStatus,
    LinkType,
)

# Types
from .types import (
    Profile,
    SearchMode,
    ObservationInput,
    SearchOptions,
    TimelineOptions,
    CleanOptions,
)

__version__ = "1.0.0"

__all__ = [
    # Clients
    "MemoryClient",
    "AsyncMemoryClient",
    # Exceptions
    "MemoryToolError",
    "ValidationError",
    "ObservationNotFoundError",
    "SessionNotFoundError",
    "DatabaseError",
    "SchemaVersionError",
    "PermissionError",
    # Models
    "Observation",
    "Session",
    "Checkpoint",
    "ObservationLink",
    "SearchResult",
    "PaginatedResult",
    "DatabaseStats",
    "ObservationKind",
    "SessionStatus",
    "LinkType",
    # Types
    "Profile",
    "SearchMode",
    "ObservationInput",
    "SearchOptions",
    "TimelineOptions",
    "CleanOptions",
    # Version
    "__version__",
]
```

## Usage Examples

### Basic Usage

```python
from memory_tool import MemoryClient

# Context manager (recommended)
with MemoryClient(profile="codex") as client:
    # Add observation
    obs = client.add_observation(
        title="API Design Decision",
        summary="Decided to use REST over GraphQL",
        kind="decision",
        tags=["api", "rest", "graphql"]
    )
    print(f"Created observation {obs.id}")

    # Search
    results = client.search("API design", limit=10)
    for result in results:
        print(f"{result.observation.title}: {result.score}")
```

### Error Handling

```python
from memory_tool import (
    MemoryClient,
    ObservationNotFoundError,
    ValidationError,
    DatabaseError
)

with MemoryClient() as client:
    try:
        obs = client.get_observation(99999)
    except ObservationNotFoundError as e:
        print(f"Observation not found: {e.observation_id}")

    try:
        obs = client.add_observation(title="", summary="Test")
    except ValidationError as e:
        print(f"Validation error: {e.message}")
```

### Pagination

```python
with MemoryClient() as client:
    offset = 0
    limit = 20

    while True:
        results = client.list_observations(limit=limit, offset=offset)

        for obs in results:
            print(f"{obs.id}: {obs.title}")

        if not results.has_more:
            break

        offset = results.next_offset
```

### Async Usage

```python
import asyncio
from memory_tool import AsyncMemoryClient

async def main():
    async with AsyncMemoryClient() as client:
        # Add observation
        obs = await client.add_observation(
            title="Async Test",
            summary="Testing async client"
        )

        # Search
        results = await client.search("test")
        for result in results:
            print(result.observation.title)

asyncio.run(main())
```

### Sessions

```python
with MemoryClient() as client:
    # Start session
    session = client.start_session(
        project="myapp",
        working_dir="/home/user/myapp"
    )
    print(f"Started session {session.id}")

    # Add observation in session
    obs = client.add_observation(
        title="Session observation",
        summary="This is associated with the session",
        session_id=session.id
    )

    # End session
    ended = client.end_session(summary="Completed feature X")
    print(f"Session lasted {ended.duration} seconds")
```

## Migration Guide

### From Old API

```python
# Old API
from memory_tool import add_observation, connect_db

conn = connect_db("~/.codex_memory/memory.db")
obs_id = add_observation(conn, "timestamp", "project", "kind", "title", "summary", "tags", "tags_text", "raw")

# New API
from memory_tool import MemoryClient

with MemoryClient(db_path="~/.codex_memory/memory.db") as client:
    obs = client.add_observation(
        title="title",
        summary="summary",
        project="project",
        kind="kind"
    )
    obs_id = obs.id
```
