"""Hub-Lite integration module for cross-repo coordination.

This module provides APIs for the Hub-Lite Parent Epic pattern, enabling
los-memory to function as a control plane for coordinating multi-repo
child sessions with proper record keeping.

Usage:
    from memory_tool.hub_lite import HubLiteSession, create_execution_record
    
    session = HubLiteSession(
        trace_id="trace-parent-epic-20260309063153",
        parent_task_id="epic-20260309063153",
        child_task_id="los-memory-20260309063153",
    )
    session.create_execution_record("Task started", "Initializing...")
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import connect_db, ensure_schema
from .utils import resolve_db_path, tags_to_json, utc_now


@dataclass
class HubLiteContext:
    """Context for a Hub-Lite child session.
    
    Attributes:
        trace_id: Shared trace ID across all child sessions
        parent_task_id: Parent epic task identifier
        child_task_id: Unique child task identifier for this repo
        session_id: Optional session identifier
        repo_name: Repository name
        repo_path: Absolute path to the repository
    """
    trace_id: str
    parent_task_id: str
    child_task_id: str
    session_id: str | None = None
    repo_name: str = "los-memory"
    repo_path: str | None = None
    
    def __post_init__(self):
        if self.repo_path is None:
            self.repo_path = str(Path(__file__).parent.parent.absolute())


@dataclass
class ExecutionRecord:
    """A structured execution record for lsclaw integration.
    
    Attributes:
        stage: Record stage (execution, verification, result)
        kind: Record kind (progress, checkpoint, artifact, blocker)
        title: Short title describing the record
        summary: Detailed summary
        tags: List of tags for categorization
        observation_id: Database observation ID (set after creation)
        timestamp: ISO format timestamp
        metadata: Additional metadata
    """
    stage: str
    kind: str
    title: str
    summary: str
    tags: list[str] = field(default_factory=list)
    observation_id: int | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)


class HubLiteSession:
    """Session manager for Hub-Lite integration.
    
    This class manages the lifecycle of a child session in the Hub-Lite
    architecture, handling record creation and cross-repo coordination.
    
    Example:
        session = HubLiteSession(
            trace_id="trace-parent-epic-20260309063153",
            parent_task_id="epic-20260309063153",
            child_task_id="los-memory-20260309063153",
        )
        
        # Create execution record
        session.create_execution_record(
            "Validation started",
            "Beginning ledger boundary validation..."
        )
        
        # Create verification checkpoint
        session.create_verification_checkpoint(
            {"database": "PASS", "schema": "PASS"}
        )
        
        # Create result artifact
        session.create_result_artifact("ACCEPTED")
    """
    
    def __init__(
        self,
        trace_id: str,
        parent_task_id: str,
        child_task_id: str,
        session_id: str | None = None,
        repo_name: str = "los-memory",
        repo_path: str | None = None,
        db_path: str | None = None,
        agent: str = "kimi-k2p5",
        role: str = "writer",
    ):
        """Initialize a Hub-Lite session.
        
        Args:
            trace_id: Shared trace ID for distributed tracing
            parent_task_id: Parent epic task identifier
            child_task_id: Unique child task identifier
            session_id: Optional session identifier
            repo_name: Repository name
            repo_path: Path to repository root
            db_path: Custom database path (optional)
            agent: Agent identifier for records
            role: Role identifier for records
        """
        self.context = HubLiteContext(
            trace_id=trace_id,
            parent_task_id=parent_task_id,
            child_task_id=child_task_id,
            session_id=session_id,
            repo_name=repo_name,
            repo_path=repo_path,
        )
        self.db_path = db_path or resolve_db_path("shared", None)
        self.agent = agent
        self.role = role
        self.records: list[ExecutionRecord] = []
        
    def _get_connection(self):
        """Get database connection."""
        conn = connect_db(self.db_path)
        ensure_schema(conn)
        return conn
    
    def _build_tags(
        self,
        stage: str,
        kind: str,
        extra_tags: list[str] | None = None,
        result: str | None = None,
    ) -> list[str]:
        """Build standardized tags for records."""
        tags = [
            f"stage:{stage}",
            f"kind:{kind}",
            f"trace:{self.context.trace_id}",
            f"parent:{self.context.parent_task_id}",
            f"child:{self.context.child_task_id}",
        ]
        
        if self.context.session_id:
            tags.append(f"session:{self.context.session_id}")
            
        tags.append(f"repo:{self.context.repo_name}")
        tags.append(f"agent:{self.agent}")
        tags.append(f"role:{self.role}")
        
        if result:
            tags.append(f"result:{result}")
            
        if extra_tags:
            tags.extend(extra_tags)
            
        return tags
    
    def _create_observation(
        self,
        title: str,
        summary: str,
        tags: list[str],
        kind: str = "note",
    ) -> int:
        """Create observation in database.
        
        Args:
            title: Observation title
            summary: Observation summary
            tags: List of tags
            kind: Observation kind
            
        Returns:
            Observation ID
        """
        conn = self._get_connection()
        try:
            tags_text = " ".join(tags)
            raw = f"{title}\n\n{summary}"
            timestamp = utc_now()
            project = f"hub-lite-{self.context.trace_id}"
            
            cursor = conn.execute(
                """
                INSERT INTO observations 
                (timestamp, project, kind, title, summary, tags, tags_text, raw, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    project,
                    kind,
                    title,
                    summary,
                    tags_to_json(tags),
                    tags_text,
                    raw,
                    None,  # session_id
                ),
            )
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()
    
    def create_execution_record(
        self,
        title: str,
        summary: str,
        extra_tags: list[str] | None = None,
    ) -> ExecutionRecord:
        """Create an execution/progress record.
        
        Args:
            title: Record title
            summary: Record summary
            extra_tags: Additional tags
            
        Returns:
            ExecutionRecord with observation_id set
        """
        tags = self._build_tags("execution", "progress", extra_tags)
        
        obs_id = self._create_observation(
            title=f"[{self.context.repo_name}] {title}",
            summary=summary,
            tags=tags,
            kind="progress",
        )
        
        record = ExecutionRecord(
            stage="execution",
            kind="progress",
            title=title,
            summary=summary,
            tags=tags,
            observation_id=obs_id,
            metadata={
                "trace_id": self.context.trace_id,
                "parent_task_id": self.context.parent_task_id,
                "child_task_id": self.context.child_task_id,
            },
        )
        
        self.records.append(record)
        return record
    
    def create_verification_checkpoint(
        self,
        checkpoint_data: dict[str, str],
        title: str | None = None,
        extra_tags: list[str] | None = None,
    ) -> ExecutionRecord:
        """Create a verification/checkpoint record.
        
        Args:
            checkpoint_data: Dictionary of check names to results
            title: Optional custom title
            extra_tags: Additional tags
            
        Returns:
            ExecutionRecord with observation_id set
        """
        # Determine overall result
        if any(v.upper() == "FAIL" for v in checkpoint_data.values()):
            result = "FAIL"
        elif any(v.upper() == "WARN" for v in checkpoint_data.values()):
            result = "WARNING"
        else:
            result = "PASS"
        
        tags = self._build_tags("verification", "checkpoint", extra_tags, result=result)
        
        check_details = ", ".join(
            f"{k}: {v}" for k, v in checkpoint_data.items()
        )
        
        title = title or f"[{self.context.repo_name}] Core validation checks {result}"
        summary = (
            f"Verification checkpoint: {check_details}. "
            f"Overall result: {result}"
        )
        
        obs_id = self._create_observation(
            title=title,
            summary=summary,
            tags=tags,
            kind="checkpoint",
        )
        
        record = ExecutionRecord(
            stage="verification",
            kind="checkpoint",
            title=title,
            summary=summary,
            tags=tags,
            observation_id=obs_id,
            metadata={
                "trace_id": self.context.trace_id,
                "parent_task_id": self.context.parent_task_id,
                "child_task_id": self.context.child_task_id,
                "checkpoint_data": checkpoint_data,
                "result": result,
            },
        )
        
        self.records.append(record)
        return record
    
    def create_result_artifact(
        self,
        acceptance_state: str,
        title: str | None = None,
        summary: str | None = None,
        extra_tags: list[str] | None = None,
    ) -> ExecutionRecord:
        """Create a result/artifact record.
        
        Args:
            acceptance_state: ACCEPTED, ACCEPTED_WITH_NOTES, or BLOCKED
            title: Optional custom title
            summary: Optional custom summary
            extra_tags: Additional tags
            
        Returns:
            ExecutionRecord with observation_id set
        """
        tags = self._build_tags("result", "artifact", extra_tags)
        tags.append(f"acceptance:{acceptance_state}")
        
        title = title or f"[{self.context.repo_name}] Validation complete"
        summary = summary or (
            f"Repository validation complete. "
            f"Acceptance State: {acceptance_state}."
        )
        
        obs_id = self._create_observation(
            title=title,
            summary=summary,
            tags=tags,
            kind="result",
        )
        
        record = ExecutionRecord(
            stage="result",
            kind="artifact",
            title=title,
            summary=summary,
            tags=tags,
            observation_id=obs_id,
            metadata={
                "trace_id": self.context.trace_id,
                "parent_task_id": self.context.parent_task_id,
                "child_task_id": self.context.child_task_id,
                "acceptance_state": acceptance_state,
            },
        )
        
        self.records.append(record)
        return record
    
    def create_blocker_record(
        self,
        blocker_type: str,
        summary: str,
        escalation_target: str = "parent",
        extra_tags: list[str] | None = None,
    ) -> ExecutionRecord:
        """Create a blocker/escalation record.
        
        Args:
            blocker_type: Type of blocker (e.g., PATH_UNRESOLVED, CONTRACT_UNRESOLVED)
            summary: Blocker description
            escalation_target: Where to escalate (default: parent)
            extra_tags: Additional tags
            
        Returns:
            ExecutionRecord with observation_id set
        """
        tags = self._build_tags("result", "blocker", extra_tags)
        tags.extend([
            f"blockerType:{blocker_type}",
            "acceptance:BLOCKED",
            f"escalation:{escalation_target}",
        ])
        
        title = f"[{self.context.repo_name}] Blocker: {blocker_type}"
        full_summary = f"Blocker: {blocker_type} - {summary}. Escalation target: {escalation_target}"
        
        obs_id = self._create_observation(
            title=title,
            summary=full_summary,
            tags=tags,
            kind="blocker",
        )
        
        record = ExecutionRecord(
            stage="result",
            kind="blocker",
            title=title,
            summary=full_summary,
            tags=tags,
            observation_id=obs_id,
            metadata={
                "trace_id": self.context.trace_id,
                "parent_task_id": self.context.parent_task_id,
                "child_task_id": self.context.child_task_id,
                "blocker_type": blocker_type,
                "escalation_target": escalation_target,
            },
        )
        
        self.records.append(record)
        return record
    
    def generate_report(self) -> dict[str, Any]:
        """Generate a summary report of all records.
        
        Returns:
            Dictionary containing report data
        """
        return {
            "trace_id": self.context.trace_id,
            "parent_task_id": self.context.parent_task_id,
            "child_task_id": self.context.child_task_id,
            "session_id": self.context.session_id,
            "repo_name": self.context.repo_name,
            "repo_path": self.context.repo_path,
            "agent": self.agent,
            "role": self.role,
            "record_count": len(self.records),
            "records": [r.to_dict() for r in self.records],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    
    def write_report(self, output_path: Path | str) -> Path:
        """Write report to JSON file.
        
        Args:
            output_path: Path to write report
            
        Returns:
            Path to written file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        report = self.generate_report()
        
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        
        return output_path


def create_execution_record(
    trace_id: str,
    parent_task_id: str,
    child_task_id: str,
    title: str,
    summary: str,
    db_path: str | None = None,
    **kwargs,
) -> ExecutionRecord:
    """Convenience function to create an execution record.
    
    Args:
        trace_id: Shared trace ID
        parent_task_id: Parent task ID
        child_task_id: Child task ID
        title: Record title
        summary: Record summary
        db_path: Custom database path
        **kwargs: Additional arguments for HubLiteSession
        
    Returns:
        ExecutionRecord
    """
    session = HubLiteSession(
        trace_id=trace_id,
        parent_task_id=parent_task_id,
        child_task_id=child_task_id,
        db_path=db_path,
        **kwargs,
    )
    return session.create_execution_record(title, summary)


def create_verification_checkpoint(
    trace_id: str,
    parent_task_id: str,
    child_task_id: str,
    checkpoint_data: dict[str, str],
    db_path: str | None = None,
    **kwargs,
) -> ExecutionRecord:
    """Convenience function to create a verification checkpoint.
    
    Args:
        trace_id: Shared trace ID
        parent_task_id: Parent task ID
        child_task_id: Child task ID
        checkpoint_data: Dictionary of check results
        db_path: Custom database path
        **kwargs: Additional arguments for HubLiteSession
        
    Returns:
        ExecutionRecord
    """
    session = HubLiteSession(
        trace_id=trace_id,
        parent_task_id=parent_task_id,
        child_task_id=child_task_id,
        db_path=db_path,
        **kwargs,
    )
    return session.create_verification_checkpoint(checkpoint_data)


def create_result_artifact(
    trace_id: str,
    parent_task_id: str,
    child_task_id: str,
    acceptance_state: str,
    db_path: str | None = None,
    **kwargs,
) -> ExecutionRecord:
    """Convenience function to create a result artifact.
    
    Args:
        trace_id: Shared trace ID
        parent_task_id: Parent task ID
        child_task_id: Child task ID
        acceptance_state: ACCEPTED, ACCEPTED_WITH_NOTES, or BLOCKED
        db_path: Custom database path
        **kwargs: Additional arguments for HubLiteSession
        
    Returns:
        ExecutionRecord
    """
    session = HubLiteSession(
        trace_id=trace_id,
        parent_task_id=parent_task_id,
        child_task_id=child_task_id,
        db_path=db_path,
        **kwargs,
    )
    return session.create_result_artifact(acceptance_state)
