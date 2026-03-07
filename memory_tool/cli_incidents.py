"""CLI commands for incident management."""
from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from .incidents import Incident, IncidentManager
from .cli_attribution import add_incident_attribution_subcommands, handle_attribution_command

if TYPE_CHECKING:
    import sqlite3


def add_incident_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Add incident management subcommands."""
    incident_parser = subparsers.add_parser(
        "incident",
        help="Incident management commands"
    )
    incident_subparsers = incident_parser.add_subparsers(
        dest="incident_action",
        help="Incident actions"
    )

    # Create incident
    create_parser = incident_subparsers.add_parser(
        "create",
        help="Create a new incident"
    )
    create_parser.add_argument(
        "--type", "-t",
        required=True,
        choices=[Incident.TYPE_ERROR, Incident.TYPE_PERFORMANCE, Incident.TYPE_AVAILABILITY],
        help="Incident type"
    )
    create_parser.add_argument(
        "--severity", "-s",
        required=True,
        choices=[Incident.SEVERITY_P0, Incident.SEVERITY_P1, Incident.SEVERITY_P2, Incident.SEVERITY_P3],
        help="Incident severity"
    )
    create_parser.add_argument(
        "--title",
        required=True,
        help="Incident title"
    )
    create_parser.add_argument(
        "--description", "-d",
        required=True,
        help="Incident description"
    )
    create_parser.add_argument(
        "--project", "-p",
        default="general",
        help="Project name (default: general)"
    )
    create_parser.add_argument(
        "--observation-id", "-o",
        type=int,
        help="Source observation ID"
    )
    create_parser.add_argument(
        "--context",
        help="Context snapshot as JSON string"
    )

    # List incidents
    list_parser = incident_subparsers.add_parser(
        "list",
        help="List incidents"
    )
    list_parser.add_argument(
        "--status",
        choices=[Incident.STATUS_DETECTED, Incident.STATUS_ANALYZING, Incident.STATUS_RECOVERING,
                 Incident.STATUS_RESOLVED, Incident.STATUS_CLOSED],
        help="Filter by status"
    )
    list_parser.add_argument(
        "--type",
        choices=[Incident.TYPE_ERROR, Incident.TYPE_PERFORMANCE, Incident.TYPE_AVAILABILITY],
        help="Filter by type"
    )
    list_parser.add_argument(
        "--severity",
        choices=[Incident.SEVERITY_P0, Incident.SEVERITY_P1, Incident.SEVERITY_P2, Incident.SEVERITY_P3],
        help="Filter by severity"
    )
    list_parser.add_argument(
        "--project", "-p",
        help="Filter by project"
    )
    list_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        help="Maximum number of incidents (default: 20)"
    )

    # Get incident
    get_parser = incident_subparsers.add_parser(
        "get",
        help="Get incident details"
    )
    get_parser.add_argument(
        "id",
        type=int,
        help="Incident ID"
    )

    # Update incident status
    status_parser = incident_subparsers.add_parser(
        "status",
        help="Update incident status"
    )
    status_parser.add_argument(
        "id",
        type=int,
        help="Incident ID"
    )
    status_parser.add_argument(
        "new_status",
        choices=[Incident.STATUS_DETECTED, Incident.STATUS_ANALYZING, Incident.STATUS_RECOVERING,
                 Incident.STATUS_RESOLVED, Incident.STATUS_CLOSED],
        help="New status"
    )
    status_parser.add_argument(
        "--notes",
        help="Resolution notes"
    )

    # Attribution analysis commands (Phase 3)
    add_incident_attribution_subcommands(incident_subparsers)

    # Link observation to incident
    link_parser = incident_subparsers.add_parser(
        "link",
        help="Link observation to incident"
    )
    link_parser.add_argument(
        "incident_id",
        type=int,
        help="Incident ID"
    )
    link_parser.add_argument(
        "observation_id",
        type=int,
        help="Observation ID"
    )
    link_parser.add_argument(
        "--type",
        default="related",
        help="Link type (default: related)"
    )

    # Get linked observations
    links_parser = incident_subparsers.add_parser(
        "links",
        help="Get observations linked to incident"
    )
    links_parser.add_argument(
        "id",
        type=int,
        help="Incident ID"
    )


def handle_incident_command(
    conn: sqlite3.Connection,
    args: argparse.Namespace
) -> dict:
    """Handle incident subcommands."""
    manager = IncidentManager(conn)

    if args.incident_action == "create":
        context = {}
        if args.context:
            try:
                context = json.loads(args.context)
            except json.JSONDecodeError as e:
                return {
                    "success": False,
                    "error": f"Invalid JSON context: {e}"
                }

        incident = manager.create(
            incident_type=args.type,
            severity=args.severity,
            title=args.title,
            description=args.description,
            project=args.project,
            source_observation_id=args.observation_id,
            context_snapshot=context
        )
        return {
            "success": True,
            "message": f"Created incident #{incident.id}",
            "incident": {
                "id": incident.id,
                "type": incident.incident_type,
                "severity": incident.severity,
                "status": incident.status,
                "title": incident.title,
                "detected_at": incident.detected_at,
                "project": incident.project
            }
        }

    elif args.incident_action == "list":
        incidents = manager.list(
            status=args.status,
            incident_type=args.type,
            severity=args.severity,
            project=args.project,
            limit=args.limit
        )
        return {
            "success": True,
            "count": len(incidents),
            "incidents": [
                {
                    "id": i.id,
                    "type": i.incident_type,
                    "severity": i.severity,
                    "status": i.status,
                    "title": i.title,
                    "detected_at": i.detected_at,
                    "resolved_at": i.resolved_at,
                    "project": i.project
                }
                for i in incidents
            ]
        }

    elif args.incident_action == "get":
        incident = manager.get(args.id)
        if not incident:
            return {
                "success": False,
                "error": f"Incident #{args.id} not found"
            }
        return {
            "success": True,
            "incident": {
                "id": incident.id,
                "type": incident.incident_type,
                "severity": incident.severity,
                "status": incident.status,
                "title": incident.title,
                "description": incident.description,
                "source_observation_id": incident.source_observation_id,
                "context_snapshot": incident.context_snapshot,
                "detected_at": incident.detected_at,
                "resolved_at": incident.resolved_at,
                "project": incident.project
            }
        }

    elif args.incident_action == "status":
        try:
            incident = manager.update_status(args.id, args.new_status, args.notes)
            if not incident:
                return {
                    "success": False,
                    "error": f"Incident #{args.id} not found"
                }
            return {
                "success": True,
                "message": f"Updated incident #{incident.id} status to {incident.status}",
                "incident": {
                    "id": incident.id,
                    "status": incident.status,
                    "resolved_at": incident.resolved_at
                }
            }
        except ValueError as e:
            return {
                "success": False,
                "error": str(e)
            }

    elif args.incident_action == "link":
        success = manager.link_observation(
            args.incident_id,
            args.observation_id,
            args.type
        )
        if success:
            return {
                "success": True,
                "message": f"Linked observation #{args.observation_id} to incident #{args.incident_id}"
            }
        else:
            return {
                "success": False,
                "error": "Link already exists or invalid IDs"
            }

    elif args.incident_action == "links":
        observations = manager.get_linked_observations(args.id)
        return {
            "success": True,
            "incident_id": args.id,
            "count": len(observations),
            "observations": observations
        }

    # Attribution analysis commands
    elif args.incident_action in ("analyze", "report", "reports", "attribution-stats"):
        return handle_attribution_command(conn, args, args.incident_action)

    else:
        return {
            "success": False,
            "error": "No incident action specified. Use: create, list, get, status, link, links, analyze, report"
        }
