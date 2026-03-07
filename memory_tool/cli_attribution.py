"""CLI commands for incident attribution analysis."""
from __future__ import annotations

import argparse
import sqlite3
from typing import Any, Dict

from .attribution_engine import AttributionEngine, RootCauseCategory


def add_attribution_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Add attribution analysis subcommands."""
    # Add to incident subparser
    # Note: These are added as subcommands under 'incident' command
    pass  # Handled in cli_incidents.py


def add_incident_attribution_subcommands(
    incident_subparsers: argparse._SubParsersAction,
) -> None:
    """Add attribution subcommands under incident."""
    # Analyze incident
    analyze_parser = incident_subparsers.add_parser(
        "analyze",
        help="Perform attribution analysis on an incident"
    )
    analyze_parser.add_argument(
        "incident_id",
        type=int,
        help="Incident ID to analyze"
    )
    analyze_parser.add_argument(
        "--window", "-w",
        type=int,
        default=30,
        help="Time window in minutes (default: 30)"
    )
    analyze_parser.add_argument(
        "--save", "-s",
        action="store_true",
        help="Save report to database"
    )

    # View report
    report_parser = incident_subparsers.add_parser(
        "report",
        help="View attribution report for an incident"
    )
    report_parser.add_argument(
        "incident_id",
        type=int,
        help="Incident ID to view report for"
    )

    # List reports
    list_parser = incident_subparsers.add_parser(
        "reports",
        help="List attribution reports"
    )
    list_parser.add_argument(
        "--category", "-c",
        choices=[c.value for c in RootCauseCategory],
        help="Filter by category"
    )
    list_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        help="Maximum results"
    )

    # Statistics
    stats_parser = incident_subparsers.add_parser(
        "attribution-stats",
        help="Show attribution statistics"
    )


def handle_attribution_command(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    action: str,
) -> Dict[str, Any]:
    """Handle attribution subcommands."""
    engine = AttributionEngine(conn)

    if action == "analyze":
        return _handle_analyze(conn, args, engine)
    elif action == "report":
        return _handle_report(conn, args, engine)
    elif action == "reports":
        return _handle_reports(conn, args, engine)
    elif action == "attribution-stats":
        return _handle_stats(conn, args, engine)
    else:
        return {"success": False, "error": f"Unknown action: {action}"}


def _handle_analyze(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    engine: AttributionEngine,
) -> Dict[str, Any]:
    """Handle incident analyze command."""
    try:
        report = engine.analyze_incident(
            incident_id=args.incident_id,
            time_window_minutes=args.window,
        )

        result = {
            "success": True,
            "incident_id": args.incident_id,
            "analysis": report.to_dict(),
        }

        if args.save:
            report_id = engine.save_report(report)
            result["saved_report_id"] = report_id

        return result

    except ValueError as e:
        return {"success": False, "error": str(e)}


def _handle_report(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    engine: AttributionEngine,
) -> Dict[str, Any]:
    """Handle incident report command."""
    report = engine.get_report_for_incident(args.incident_id)

    if not report:
        return {
            "success": False,
            "error": f"No attribution report found for incident {args.incident_id}"
        }

    return {
        "success": True,
        "report": report.to_dict(),
    }


def _handle_reports(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    engine: AttributionEngine,
) -> Dict[str, Any]:
    """Handle incident reports command."""
    category = None
    if args.category:
        category = RootCauseCategory(args.category)

    reports = engine.list_reports(category=category, limit=args.limit)

    return {
        "success": True,
        "count": len(reports),
        "reports": [r.to_dict() for r in reports],
    }


def _handle_stats(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    engine: AttributionEngine,
) -> Dict[str, Any]:
    """Handle attribution stats command."""
    stats = engine.get_statistics()

    return {
        "success": True,
        "statistics": stats,
    }
