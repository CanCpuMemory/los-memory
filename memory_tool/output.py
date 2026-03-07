"""Unified JSON response formatting for los-memory CLI.

This module provides:
- JSONResponse class for structured responses
- Factory functions: success() and error()
- Multiple output formats: json, table, yaml
- TTY detection with --human flag support
- Pagination metadata support
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List
from dataclasses import dataclass, field

from memory_tool.errors import ErrorCode, format_error_response
from memory_tool.schema import SCHEMA_VERSION


try:
    from rich.console import Console
    from rich.table import Table
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class ResponseMeta:
    """Metadata for all responses."""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = SCHEMA_VERSION
    profile: Optional[str] = None
    db_path: Optional[str] = None
    query_time_ms: Optional[int] = None
    pagination: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        result = {
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
        }
        if self.profile is not None:
            result["profile"] = self.profile
        if self.db_path is not None:
            result["db_path"] = self.db_path
        if self.query_time_ms is not None:
            result["query_time_ms"] = self.query_time_ms
        if self.pagination is not None:
            result["pagination"] = self.pagination
        return result


class JSONResponse:
    """Unified JSON response wrapper."""

    def __init__(
        self,
        ok: bool,
        data: Optional[Any] = None,
        error: Optional[Dict[str, Any]] = None,
        meta: Optional[ResponseMeta] = None,
    ):
        self.ok = ok
        self.data = data
        self.error = error
        self.meta = meta or ResponseMeta()

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        result: Dict[str, Any] = {
            "ok": self.ok,
            "meta": self.meta.to_dict(),
        }
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error
        return result

    def to_json(self, indent: Optional[int] = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str, ensure_ascii=False)

    def to_yaml(self) -> str:
        """Serialize to YAML string."""
        if not HAS_YAML:
            raise ImportError("PyYAML is required for YAML output")
        return yaml.dump(self.to_dict(), default_flow_style=False, allow_unicode=True)

    def to_table(self) -> str:
        """Format as human-readable table."""
        if not HAS_RICH:
            return self._simple_table()
        return self._rich_table()

    def _simple_table(self) -> str:
        """Fallback simple table format."""
        lines = []
        status = "✓ Success" if self.ok else "✗ Error"
        lines.append(f"Status: {status}")
        lines.append(f"Timestamp: {self.meta.timestamp}")

        if self.error:
            lines.append(f"\nError Code: {self.error.get('code', 'UNKNOWN')}")
            lines.append(f"Message: {self.error.get('message', 'Unknown error')}")
            if 'suggestion' in self.error:
                lines.append(f"Suggestion: {self.error['suggestion']}")

        if self.data:
            lines.append(f"\nData: {json.dumps(self.data, indent=2, ensure_ascii=False)}")

        return "\n".join(lines)

    def _rich_table(self) -> str:
        """Rich formatted table output."""
        console = Console(force_terminal=True)
        output = []

        # Status
        status_table = Table(title="Response Status", show_header=False)
        status_table.add_column("Field", style="cyan")
        status_table.add_column("Value", style="green" if self.ok else "red")

        status_table.add_row("Success", "Yes" if self.ok else "No")
        status_table.add_row("Timestamp", self.meta.timestamp)
        status_table.add_row("Schema Version", self.meta.schema_version)

        if self.meta.profile:
            status_table.add_row("Profile", self.meta.profile)
        if self.meta.db_path:
            status_table.add_row("Database", self.meta.db_path)
        if self.meta.query_time_ms:
            status_table.add_row("Query Time", f"{self.meta.query_time_ms}ms")

        output.append(status_table)

        # Error details
        if self.error:
            error_table = Table(title="Error Details", style="red")
            error_table.add_column("Field", style="red")
            error_table.add_column("Value", style="yellow")

            error_table.add_row("Code", self.error.get('code', 'UNKNOWN'))
            error_table.add_row("Message", self.error.get('message', 'Unknown error'))

            if 'suggestion' in self.error:
                error_table.add_row("Suggestion", self.error['suggestion'])
            if 'help_command' in self.error:
                error_table.add_row("Help Command", self.error['help_command'])

            output.append(error_table)

        # Data (for list responses)
        if self.data and isinstance(self.data, list) and len(self.data) > 0:
            data_table = Table(title=f"Results ({len(self.data)} items)")
            if isinstance(self.data[0], dict):
                for key in self.data[0].keys():
                    data_table.add_column(str(key)[:20])

                for item in self.data[:20]:
                    row = [str(item.get(k, ''))[:50] for k in self.data[0].keys()]
                    data_table.add_row(*row)

                if len(self.data) > 20:
                    data_table.add_row(f"... and {len(self.data) - 20} more")

            output.append(data_table)

        with console.capture() as capture:
            for table in output:
                console.print(table)
                console.print()

        return capture.get()

    def print(
        self,
        format: str = "json",
        human: bool = False,
        file: Any = None,
    ) -> None:
        """Print response to output."""
        if file is None:
            file = sys.stdout

        is_tty = hasattr(file, 'isatty') and file.isatty()

        if human or (is_tty and format == "auto"):
            text = self.to_table()
        elif format == "yaml" and HAS_YAML:
            text = self.to_yaml()
        else:
            text = self.to_json(indent=2 if is_tty else None)

        print(text, file=file)


def success(
    data: Any,
    profile: Optional[str] = None,
    db_path: Optional[str] = None,
    query_time_ms: Optional[int] = None,
    pagination: Optional[Dict[str, Any]] = None,
    **extra_meta: Any,
) -> JSONResponse:
    """Create a success response.

    Args:
        data: Response data (any JSON-serializable type)
        profile: Profile name used
        db_path: Database path
        query_time_ms: Query execution time in milliseconds
        pagination: Pagination info (total, limit, offset, has_more)
        **extra_meta: Additional metadata fields

    Returns:
        JSONResponse with ok=True
    """
    meta = ResponseMeta(
        profile=profile,
        db_path=db_path,
        query_time_ms=query_time_ms,
        pagination=pagination,
    )

    if extra_meta:
        meta_dict = meta.to_dict()
        meta_dict.update(extra_meta)

    return JSONResponse(
        ok=True,
        data=data,
        meta=meta,
    )


def error(
    error_code: ErrorCode,
    **kwargs: Any,
) -> JSONResponse:
    """Create an error response.

    Args:
        error_code: Error code definition from errors.py
        **kwargs: Values to format into error message/suggestion

    Returns:
        JSONResponse with ok=False
    """
    error_dict = format_error_response(error_code, **kwargs)

    return JSONResponse(
        ok=False,
        error=error_dict["error"],
        meta=ResponseMeta(),
    )


def is_tty() -> bool:
    """Check if stdout is a TTY."""
    return sys.stdout.isatty()


def auto_format() -> str:
    """Determine default format based on environment."""
    return "json"


def paginate(
    items: List[Any],
    total: int,
    limit: int,
    offset: int,
) -> Dict[str, Any]:
    """Create pagination metadata.

    Args:
        items: List of items for current page
        total: Total number of items
        limit: Items per page
        offset: Current offset

    Returns:
        Pagination metadata dictionary
    """
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(items)) < total,
    }


# =============================================================================
# Specialized Table Formatters
# =============================================================================

def format_observations_table(observations: List[Dict[str, Any]], verbose: bool = False) -> str:
    """Format observations as a human-readable table.

    Args:
        observations: List of observation dictionaries
        verbose: Whether to show full content or truncated

    Returns:
        Formatted table string
    """
    if not observations:
        return "No observations found."

    if HAS_RICH:
        from rich.console import Console
        from rich.table import Table

        table = Table(title=f"Observations ({len(observations)})", show_header=True)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Time", style="dim", no_wrap=True, width=19)
        table.add_column("Project", style="green", width=15)
        table.add_column("Kind", style="yellow", width=12)
        table.add_column("Title", style="white", min_width=30)
        if verbose:
            table.add_column("Tags", style="blue")
            table.add_column("Summary", style="dim")

        for obs in observations:
            ts = obs.get("timestamp", "")
            if ts and len(ts) > 19:
                ts = ts[:19].replace("T", " ")

            row = [
                str(obs.get("id", "")),
                ts,
                obs.get("project", "")[:15],
                obs.get("kind", "")[:12],
                obs.get("title", "")[:50] + ("..." if len(obs.get("title", "")) > 50 else ""),
            ]
            if verbose:
                tags = obs.get("tags", "")
                if isinstance(tags, list):
                    tags = ", ".join(tags)
                row.append(tags[:30] if tags else "")
                summary = obs.get("summary", "")
                row.append(summary[:50] + "..." if len(summary) > 50 else summary)
            table.add_row(*row)

        console = Console(force_terminal=True)
        with console.capture() as capture:
            console.print(table)
        return capture.get()
    else:
        # Simple text format
        lines = [f"Observations ({len(observations)}):", "-" * 80]
        for obs in observations:
            ts = obs.get("timestamp", "")[:19].replace("T", " ")
            lines.append(f"#{obs.get('id', '')} [{ts}] ({obs.get('project', '')}/{obs.get('kind', '')})")
            lines.append(f"  Title: {obs.get('title', '')}")
            if verbose:
                lines.append(f"  Summary: {obs.get('summary', '')[:100]}")
            lines.append("")
        return "\n".join(lines)


def format_search_results(results: List[Dict[str, Any]], query: str = "") -> str:
    """Format search results with relevance indicators.

    Args:
        results: List of search result dictionaries
        query: Original search query

    Returns:
        Formatted results string
    """
    if not results:
        return f"No results found for: {query}"

    if HAS_RICH:
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text

        table = Table(title=f"Search Results: '{query}' ({len(results)} found)", show_header=True)
        table.add_column("Rank", style="dim", justify="right", width=4)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Project", style="green", width=12)
        table.add_column("Kind", style="yellow", width=10)
        table.add_column("Title/Summary", style="white", min_width=40)
        table.add_column("Tags", style="blue")

        for i, result in enumerate(results, 1):
            rank_text = f"{i}."
            obs = result.get("observation", result)
            tags = obs.get("tags", "")
            if isinstance(tags, list):
                tags = ", ".join(tags)

            # Highlight matching terms in title/summary
            title = obs.get("title", "")
            summary = obs.get("summary", "")
            content = f"{title}\n{summary[:80]}..." if len(summary) > 80 else title

            table.add_row(
                rank_text,
                str(obs.get("id", "")),
                obs.get("project", "")[:12],
                obs.get("kind", "")[:10],
                content[:60],
                tags[:25] if tags else "",
            )

        console = Console(force_terminal=True)
        with console.capture() as capture:
            console.print(table)
        return capture.get()
    else:
        lines = [f"Search Results: '{query}' ({len(results)} found):", "-" * 80]
        for i, result in enumerate(results, 1):
            obs = result.get("observation", result)
            lines.append(f"{i}. #{obs.get('id', '')} [{obs.get('project', '')}] {obs.get('title', '')}")
        return "\n".join(lines)


def format_sessions_table(sessions: List[Dict[str, Any]]) -> str:
    """Format sessions as a human-readable table.

    Args:
        sessions: List of session dictionaries

    Returns:
        Formatted table string
    """
    if not sessions:
        return "No sessions found."

    if HAS_RICH:
        from rich.console import Console
        from rich.table import Table

        table = Table(title=f"Sessions ({len(sessions)})", show_header=True)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Status", style="green", width=10)
        table.add_column("Start Time", style="dim", width=19)
        table.add_column("Project", style="yellow", width=15)
        table.add_column("Agent", style="blue", width=10)
        table.add_column("Summary", style="white", min_width=30)

        for session in sessions:
            status = "🟢 active" if session.get("status") == "active" else "⚪ ended"
            start = session.get("start_time", "")[:19].replace("T", " ")
            summary = session.get("summary", "") or "(no summary)"
            table.add_row(
                str(session.get("id", "")),
                status,
                start,
                session.get("project", "")[:15],
                session.get("agent_type", "")[:10],
                summary[:50] + "..." if len(summary) > 50 else summary,
            )

        console = Console(force_terminal=True)
        with console.capture() as capture:
            console.print(table)
        return capture.get()
    else:
        lines = [f"Sessions ({len(sessions)}):", "-" * 80]
        for session in sessions:
            status = "[ACTIVE]" if session.get("status") == "active" else "[ENDED]"
            lines.append(f"#{session.get('id', '')} {status} {session.get('project', '')} - {session.get('summary', '')}")
        return "\n".join(lines)


def format_stats_table(stats: Dict[str, Any]) -> str:
    """Format statistics as a human-readable table.

    Args:
        stats: Statistics dictionary

    Returns:
        Formatted table string
    """
    if HAS_RICH:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel

        panels = []

        # Main stats
        if "total_observations" in stats or "observations" in stats:
            total = stats.get("total_observations", stats.get("observations", 0))
            panels.append(f"📊 Total Observations: {total}")

        if "sessions" in stats:
            panels.append(f"📅 Sessions: {stats['sessions']}")

        if "projects" in stats:
            projects = stats["projects"]
            if isinstance(projects, list):
                panels.append(f"📁 Projects: {len(projects)}")
            else:
                panels.append(f"📁 Projects: {projects}")

        # Projects breakdown
        if "project_breakdown" in stats and isinstance(stats["project_breakdown"], dict):
            table = Table(title="Project Breakdown", show_header=True)
            table.add_column("Project", style="green")
            table.add_column("Count", style="cyan", justify="right")
            for project, count in stats["project_breakdown"].items():
                table.add_row(project, str(count))
            console = Console(force_terminal=True)
            with console.capture() as capture:
                console.print(Panel("\n".join(panels)))
                console.print(table)
            return capture.get()

        console = Console(force_terminal=True)
        with console.capture() as capture:
            console.print(Panel("\n".join(panels)))
        return capture.get()
    else:
        lines = ["Statistics:", "-" * 40]
        for key, value in stats.items():
            if isinstance(value, dict):
                lines.append(f"{key}:")
                for k, v in value.items():
                    lines.append(f"  {k}: {v}")
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)


def format_timeline_visual(results: List[Dict[str, Any]], group_by: str | None = None) -> str:
    """Format timeline with visual markers.

    Args:
        results: List of timeline result dictionaries
        group_by: Grouping option (hour, day, session)

    Returns:
        Formatted timeline string
    """
    if not results:
        return "Timeline is empty."

    lines = ["Timeline View:", ""]

    if group_by == "hour":
        # Group by hour
        current_hour = None
        for item in results:
            ts = item.get("timestamp", "")
            hour = ts[:13] if len(ts) >= 13 else ts
            if hour != current_hour:
                lines.append(f"\n📅 {hour}:00")
                current_hour = hour
            lines.append(f"  [{item.get('id', '')}] {item.get('title', '')[:50]}")
    elif group_by == "day":
        # Group by day
        current_day = None
        for item in results:
            ts = item.get("timestamp", "")
            day = ts[:10] if len(ts) >= 10 else ts
            if day != current_day:
                lines.append(f"\n📅 {day}")
                current_day = day
            lines.append(f"  [{item.get('id', '')}] {item.get('title', '')[:50]}")
    else:
        # Simple list with time markers
        prev_time = None
        for item in results:
            ts = item.get("timestamp", "")[:19].replace("T", " ")
            time_only = ts[11:] if len(ts) >= 19 else ts
            lines.append(f"{time_only} │ #{item.get('id', '')} {item.get('title', '')[:45]}")

    return "\n".join(lines)


class OutputManager:
    """Context manager for handling CLI output with timing."""

    def __init__(
        self,
        profile: Optional[str] = None,
        db_path: Optional[str] = None,
        format: str = "json",
        human: bool = False,
    ):
        self.profile = profile
        self.db_path = db_path
        self.format = format
        self.human = human
        self.start_time: Optional[datetime] = None

    def __enter__(self) -> OutputManager:
        self.start_time = datetime.now(timezone.utc)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def success(
        self,
        data: Any,
        pagination: Optional[Dict[str, Any]] = None,
    ) -> JSONResponse:
        """Create success response with timing."""
        query_time = None
        if self.start_time:
            query_time = int((datetime.now(timezone.utc) - self.start_time).total_seconds() * 1000)

        return success(
            data=data,
            profile=self.profile,
            db_path=self.db_path,
            query_time_ms=query_time,
            pagination=pagination,
        )

    def error(
        self,
        error_code: ErrorCode,
        **kwargs: Any,
    ) -> JSONResponse:
        """Create error response."""
        return error(error_code, **kwargs)

    def print(self, response: JSONResponse) -> None:
        """Print response with configured format."""
        response.print(format=self.format, human=self.human)


__all__ = [
    "JSONResponse",
    "ResponseMeta",
    "success",
    "error",
    "is_tty",
    "auto_format",
    "paginate",
    "OutputManager",
    "HAS_RICH",
    "HAS_YAML",
    # Table formatters
    "format_observations_table",
    "format_search_results",
    "format_sessions_table",
    "format_stats_table",
    "format_timeline_visual",
]
