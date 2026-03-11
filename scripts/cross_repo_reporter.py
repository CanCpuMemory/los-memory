#!/usr/bin/env python3
"""Cross-repo integration reporter for Hub-Lite architecture.

This module generates comprehensive reports for cross-repo integration,
aggregating results from multiple child sessions and producing artifacts
for the parent epic.

Usage:
    python scripts/cross_repo_reporter.py --trace-id <id> --output-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_tool.database import connect_db, ensure_schema
from memory_tool.utils import resolve_db_path


@dataclass
class ChildSessionReport:
    """Report for a single child session."""
    child_task_id: str
    repo_name: str
    status: str  # "complete", "blocked", "in_progress"
    execution_records: int = 0
    verification_records: int = 0
    result_records: int = 0
    blocker_records: int = 0
    acceptance_state: str | None = None
    artifact_paths: list[str] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CrossRepoReport:
    """Complete cross-repo integration report."""
    trace_id: str
    parent_task_id: str
    timestamp: str
    overall_status: str  # "complete", "partial", "blocked"
    child_sessions: list[ChildSessionReport]
    aggregation: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class CrossRepoReporter:
    """Reporter for aggregating cross-repo integration results.
    
    This class queries the los-memory database for records matching a
    specific trace ID and generates comprehensive reports.
    
    Example:
        reporter = CrossRepoReporter(trace_id="trace-123")
        report = reporter.generate_report()
        reporter.write_json_report("logs/report.json")
        reporter.write_markdown_summary("logs/report.md")
    """
    
    def __init__(
        self,
        trace_id: str,
        parent_task_id: str | None = None,
        db_path: str | None = None,
    ):
        """Initialize the reporter.
        
        Args:
            trace_id: The trace ID to query
            parent_task_id: Optional parent task ID
            db_path: Custom database path
        """
        self.trace_id = trace_id
        self.parent_task_id = parent_task_id
        self.db_path = db_path or resolve_db_path("shared", None)
        
    def _get_connection(self):
        """Get database connection."""
        conn = connect_db(self.db_path)
        ensure_schema(conn)
        return conn
    
    def _query_records(self) -> list[dict[str, Any]]:
        """Query all records for this trace ID."""
        conn = self._get_connection()
        try:
            project = f"hub-lite-{self.trace_id}"
            cursor = conn.execute(
                """
                SELECT id, timestamp, project, kind, title, summary, tags
                FROM observations
                WHERE project = ?
                ORDER BY timestamp ASC
                """,
                (project,),
            )
            
            records = []
            for row in cursor.fetchall():
                record = dict(row)
                tags_str = record["tags"]
                if tags_str:
                    try:
                        parsed = json.loads(tags_str)
                        record["tags_list"] = parsed if isinstance(parsed, list) else [tags_str]
                    except json.JSONDecodeError:
                        record["tags_list"] = [t.strip() for t in tags_str.split(",")]
                else:
                    record["tags_list"] = []
                records.append(record)
            
            return records
        finally:
            conn.close()
    
    def _extract_child_task_id(self, tags: list[str]) -> str | None:
        """Extract child task ID from tags."""
        for tag in tags:
            if tag.startswith("child:"):
                # Handle both "child:id" and " child:id" (with leading space)
                return tag.replace("child:", "").strip()
        return None
    
    def _extract_repo_name(self, title: str) -> str:
        """Extract repository name from title."""
        if "[" in title and "]" in title:
            return title.split("[")[1].split("]")[0]
        return "unknown"
    
    def _extract_stage(self, tags: list[str]) -> str | None:
        """Extract stage from tags."""
        for tag in tags:
            if tag.startswith("stage:"):
                stage = tag.replace("stage:", "")
                # Handle truncated values from JSON conversion
                if stage.startswith("verific"):
                    return "verification"
                return stage
        return None
    
    def _extract_kind(self, tags: list[str]) -> str | None:
        """Extract kind from tags."""
        for tag in tags:
            if tag.startswith("kind:"):
                return tag.replace("kind:", "")
        return None
    
    def _extract_acceptance(self, tags: list[str]) -> str | None:
        """Extract acceptance state from tags."""
        for tag in tags:
            if tag.startswith("acceptance:"):
                return tag.replace("acceptance:", "")
        return None
    
    def generate_report(self) -> CrossRepoReport:
        """Generate the cross-repo integration report.
        
        Returns:
            CrossRepoReport with all aggregated data
        """
        all_records = self._query_records()
        
        # Group records by child task
        child_records: dict[str, list[dict]] = {}
        for record in all_records:
            child_id = self._extract_child_task_id(record["tags_list"])
            if child_id:
                if child_id not in child_records:
                    child_records[child_id] = []
                child_records[child_id].append(record)
        
        # Build child session reports
        child_sessions = []
        for child_id, records in child_records.items():
            repo_name = self._extract_repo_name(records[0]["title"]) if records else "unknown"
            
            execution_count = 0
            verification_count = 0
            result_count = 0
            blocker_count = 0
            acceptance_state = None
            blockers = []
            
            for record in records:
                stage = self._extract_stage(record["tags_list"])
                kind = self._extract_kind(record["tags_list"])
                
                if stage == "execution":
                    execution_count += 1
                elif stage == "verification":
                    verification_count += 1
                elif stage == "result":
                    result_count += 1
                    
                if kind == "blocker":
                    blocker_count += 1
                    blockers.append({
                        "title": record["title"],
                        "summary": record["summary"],
                        "timestamp": record["timestamp"],
                    })
                
                # Check for acceptance state
                acc = self._extract_acceptance(record["tags_list"])
                if acc:
                    acceptance_state = acc
            
            # Determine status (handle both upper and lower case acceptance states)
            if blocker_count > 0:
                status = "blocked"
            elif acceptance_state and acceptance_state.upper() in ["ACCEPTED", "ACCEPT", "ACCEPTED_WITH_NOTES"]:
                status = "complete"
            else:
                status = "in_progress"
            
            child_sessions.append(ChildSessionReport(
                child_task_id=child_id,
                repo_name=repo_name,
                status=status,
                execution_records=execution_count,
                verification_records=verification_count,
                result_records=result_count,
                blocker_records=blocker_count,
                acceptance_state=acceptance_state,
                blockers=blockers,
            ))
        
        # Calculate overall status
        if not child_sessions:
            overall_status = "blocked"
        elif any(s.status == "blocked" for s in child_sessions):
            overall_status = "blocked"
        elif all(s.status == "complete" for s in child_sessions):
            overall_status = "complete"
        else:
            overall_status = "partial"
        
        # Build aggregation
        aggregation = {
            "total_records": len(all_records),
            "total_child_sessions": len(child_sessions),
            "complete_sessions": sum(1 for s in child_sessions if s.status == "complete"),
            "blocked_sessions": sum(1 for s in child_sessions if s.status == "blocked"),
            "in_progress_sessions": sum(1 for s in child_sessions if s.status == "in_progress"),
            "total_blockers": sum(s.blocker_records for s in child_sessions),
        }
        
        return CrossRepoReport(
            trace_id=self.trace_id,
            parent_task_id=self.parent_task_id or f"epic-{self.trace_id.split('-')[-1]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            overall_status=overall_status,
            child_sessions=child_sessions,
            aggregation=aggregation,
            metadata={
                "database_path": self.db_path,
                "generated_by": "cross_repo_reporter.py",
            },
        )
    
    def write_json_report(self, output_path: Path | str) -> Path:
        """Write JSON report to file.
        
        Args:
            output_path: Path to write report
            
        Returns:
            Path to written file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        report = self.generate_report()
        
        with open(output_path, "w") as f:
            json.dump(asdict(report), f, indent=2, default=str)
        
        return output_path
    
    def write_markdown_summary(self, output_path: Path | str) -> Path:
        """Write Markdown summary to file.
        
        Args:
            output_path: Path to write summary
            
        Returns:
            Path to written file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        report = self.generate_report()
        
        with open(output_path, "w") as f:
            f.write(f"# Cross-Repo Integration Report\n\n")
            f.write(f"**Trace ID:** `{report.trace_id}`\n\n")
            f.write(f"**Parent Task:** `{report.parent_task_id}`\n\n")
            f.write(f"**Overall Status:** {report.overall_status.upper()}\n\n")
            f.write(f"**Generated:** {report.timestamp}\n\n")
            
            f.write(f"## Summary\n\n")
            f.write(f"- **Total Records:** {report.aggregation['total_records']}\n")
            f.write(f"- **Child Sessions:** {report.aggregation['total_child_sessions']}\n")
            f.write(f"- **Complete:** {report.aggregation['complete_sessions']}\n")
            f.write(f"- **Blocked:** {report.aggregation['blocked_sessions']}\n")
            f.write(f"- **In Progress:** {report.aggregation['in_progress_sessions']}\n")
            f.write(f"- **Total Blockers:** {report.aggregation['total_blockers']}\n\n")
            
            f.write(f"## Child Sessions\n\n")
            for session in report.child_sessions:
                status_icon = "✅" if session.status == "complete" else "❌" if session.status == "blocked" else "⏳"
                f.write(f"### {status_icon} {session.repo_name} (`{session.child_task_id}`)\n\n")
                f.write(f"- **Status:** {session.status}\n")
                f.write(f"- **Acceptance:** {session.acceptance_state or 'N/A'}\n")
                f.write(f"- **Execution Records:** {session.execution_records}\n")
                f.write(f"- **Verification Records:** {session.verification_records}\n")
                f.write(f"- **Result Records:** {session.result_records}\n")
                
                if session.blockers:
                    f.write(f"- **Blockers:**\n")
                    for blocker in session.blockers:
                        f.write(f"  - {blocker['title']}\n")
                
                f.write(f"\n")
            
            f.write(f"## Metadata\n\n")
            f.write(f"```json\n")
            f.write(json.dumps(report.metadata, indent=2))
            f.write(f"\n```\n")
        
        return output_path
    
    def write_dispatch_log(self, output_path: Path | str, child_sessions_config: list[dict] | None = None) -> Path:
        """Write dispatch log for the parent epic.
        
        This creates a JSON file that serves as the dispatch log for the
        hub-lite parent epic, listing all child sessions and their status.
        
        Args:
            output_path: Path to write log
            child_sessions_config: Optional configuration for child sessions
            
        Returns:
            Path to written file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        report = self.generate_report()
        
        # Build child sessions list
        if child_sessions_config:
            child_sessions = child_sessions_config
        else:
            child_sessions = [
                {
                    "childTaskId": s.child_task_id,
                    "repoName": s.repo_name,
                    "status": s.status,
                    "acceptanceState": s.acceptance_state,
                    "recordCount": s.execution_records + s.verification_records + s.result_records,
                }
                for s in report.child_sessions
            ]
        
        dispatch_log = {
            "traceId": report.trace_id,
            "parentTaskId": report.parent_task_id,
            "timestamp": report.timestamp,
            "overallStatus": report.overall_status,
            "childSessions": child_sessions,
            "aggregation": report.aggregation,
            "metadata": report.metadata,
        }
        
        with open(output_path, "w") as f:
            json.dump(dispatch_log, f, indent=2, default=str)
        
        return output_path


def main():
    """Main entry point for the reporter."""
    parser = argparse.ArgumentParser(
        description="Generate cross-repo integration reports for Hub-Lite"
    )
    parser.add_argument(
        "--trace-id",
        required=True,
        help="Trace ID to query records for"
    )
    parser.add_argument(
        "--parent-task-id",
        help="Parent task ID (optional)"
    )
    parser.add_argument(
        "--output-dir",
        default="logs",
        help="Directory to write reports"
    )
    parser.add_argument(
        "--db-path",
        help="Custom database path"
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Cross-Repo Integration Reporter")
    print("=" * 60)
    print(f"Trace ID: {args.trace_id}")
    print(f"Output Directory: {args.output_dir}")
    print("-" * 60)
    
    # Create reporter
    reporter = CrossRepoReporter(
        trace_id=args.trace_id,
        parent_task_id=args.parent_task_id,
        db_path=args.db_path,
    )
    
    # Generate reports
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # JSON report
    json_path = output_dir / f"hub-lite-cross-repo-report-{args.trace_id}.json"
    reporter.write_json_report(json_path)
    print(f"✓ JSON Report: {json_path}")
    
    # Markdown summary
    md_path = output_dir / f"hub-lite-cross-repo-summary-{args.trace_id}.md"
    reporter.write_markdown_summary(md_path)
    print(f"✓ Markdown Summary: {md_path}")
    
    # Dispatch log
    dispatch_path = output_dir / f"hub-lite-parent-epic-dispatch-{args.trace_id.split('-')[-1]}.json"
    reporter.write_dispatch_log(dispatch_path)
    print(f"✓ Dispatch Log: {dispatch_path}")
    
    # Print summary
    report = reporter.generate_report()
    print("-" * 60)
    print("Report Summary:")
    print(f"  Overall Status: {report.overall_status.upper()}")
    print(f"  Child Sessions: {report.aggregation['total_child_sessions']}")
    print(f"  Complete: {report.aggregation['complete_sessions']}")
    print(f"  Blocked: {report.aggregation['blocked_sessions']}")
    print(f"  Total Blockers: {report.aggregation['total_blockers']}")
    print("=" * 60)
    
    return 0 if report.overall_status != "blocked" else 1


if __name__ == "__main__":
    sys.exit(main())
