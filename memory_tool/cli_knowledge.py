"""CLI commands for knowledge base management."""
from __future__ import annotations

import argparse
import sqlite3
from typing import Any, Dict

from .knowledge_base import KnowledgeBase, KnowledgeEntry


def add_knowledge_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Add knowledge base subcommands."""
    knowledge_parser = subparsers.add_parser(
        "knowledge",
        help="Knowledge base management [EXT]"
    )
    knowledge_subparsers = knowledge_parser.add_subparsers(
        dest="knowledge_action",
        help="Knowledge actions"
    )

    # Search knowledge
    search_parser = knowledge_subparsers.add_parser(
        "search",
        help="Search knowledge base"
    )
    search_parser.add_argument(
        "query",
        help="Search query"
    )
    search_parser.add_argument(
        "--type", "-t",
        choices=["error", "performance", "availability"],
        help="Filter by incident type"
    )
    search_parser.add_argument(
        "--min-success",
        type=float,
        default=0.0,
        help="Minimum success rate (0.0-1.0)"
    )
    search_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=10,
        help="Maximum results"
    )

    # Add from incident
    add_parser = knowledge_subparsers.add_parser(
        "add",
        help="Add knowledge from incident"
    )
    add_parser.add_argument(
        "--from-incident", "-i",
        type=int,
        required=True,
        help="Incident ID to extract from"
    )

    # List entries
    list_parser = knowledge_subparsers.add_parser(
        "list",
        help="List knowledge entries"
    )
    list_parser.add_argument(
        "--type", "-t",
        help="Filter by incident type"
    )
    list_parser.add_argument(
        "--tag",
        help="Filter by tag"
    )
    list_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        help="Maximum results"
    )

    # Get entry details
    get_parser = knowledge_subparsers.add_parser(
        "get",
        help="Get knowledge entry details"
    )
    get_parser.add_argument(
        "entry_id",
        type=int,
        help="Entry ID"
    )

    # Record outcome
    record_parser = knowledge_subparsers.add_parser(
        "record",
        help="Record solution outcome"
    )
    record_parser.add_argument(
        "entry_id",
        type=int,
        help="Entry ID"
    )
    record_parser.add_argument(
        "--success", "-s",
        action="store_true",
        help="Record success"
    )
    record_parser.add_argument(
        "--failure", "-f",
        action="store_true",
        help="Record failure"
    )

    # Statistics
    stats_parser = knowledge_subparsers.add_parser(
        "stats",
        help="Show knowledge base statistics"
    )

    # Find similar
    similar_parser = knowledge_subparsers.add_parser(
        "similar",
        help="Find similar solutions"
    )
    similar_parser.add_argument(
        "symptoms",
        help="Symptoms description"
    )
    similar_parser.add_argument(
        "--type", "-t",
        help="Incident type"
    )
    similar_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=5,
        help="Maximum results"
    )

    # Cleanup unused
    cleanup_parser = knowledge_subparsers.add_parser(
        "cleanup",
        help="List unused entries for cleanup"
    )
    cleanup_parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Days since last use"
    )


def handle_knowledge_command(
    conn: sqlite3.Connection,
    args: argparse.Namespace
) -> Dict[str, Any]:
    """Handle knowledge subcommands."""
    kb = KnowledgeBase(conn)

    action = getattr(args, "knowledge_action", None)

    if action == "search":
        return _handle_search(conn, args, kb)
    elif action == "add":
        return _handle_add(conn, args, kb)
    elif action == "list":
        return _handle_list(conn, args, kb)
    elif action == "get":
        return _handle_get(conn, args, kb)
    elif action == "record":
        return _handle_record(conn, args, kb)
    elif action == "stats":
        return _handle_stats(conn, args, kb)
    elif action == "similar":
        return _handle_similar(conn, args, kb)
    elif action == "cleanup":
        return _handle_cleanup(conn, args, kb)
    else:
        return {
            "success": False,
            "error": "No knowledge action specified. Use: search, add, list, get, record, stats, similar, cleanup"
        }


def _handle_search(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    kb: KnowledgeBase
) -> Dict[str, Any]:
    """Handle knowledge search."""
    results = kb.search(
        query=args.query,
        incident_type=args.type,
        min_success_rate=args.min_success,
        limit=args.limit,
    )

    return {
        "success": True,
        "count": len(results),
        "results": [
            {
                "entry": entry.to_dict(),
                "match_score": round(score, 3),
            }
            for entry, score in results
        ]
    }


def _handle_add(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    kb: KnowledgeBase
) -> Dict[str, Any]:
    """Handle add from incident."""
    entry_id = kb.extract_and_add(args.from_incident)

    if entry_id:
        return {
            "success": True,
            "message": f"Knowledge entry #{entry_id} created",
            "entry_id": entry_id,
        }
    else:
        return {
            "success": False,
            "error": "Failed to extract knowledge from incident (may not be resolved)"
        }


def _handle_list(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    kb: KnowledgeBase
) -> Dict[str, Any]:
    """Handle list entries."""
    entries = kb.list_entries(
        incident_type=args.type,
        tag=args.tag,
        limit=args.limit,
    )

    return {
        "success": True,
        "count": len(entries),
        "entries": [e.to_dict() for e in entries]
    }


def _handle_get(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    kb: KnowledgeBase
) -> Dict[str, Any]:
    """Handle get entry details."""
    entry = kb.get_entry(args.entry_id)

    if not entry:
        return {
            "success": False,
            "error": f"Knowledge entry #{args.entry_id} not found"
        }

    return {
        "success": True,
        "entry": entry.to_dict(),
    }


def _handle_record(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    kb: KnowledgeBase
) -> Dict[str, Any]:
    """Handle record outcome."""
    if args.success:
        success = kb.record_success(args.entry_id)
        action = "success"
    elif args.failure:
        success = kb.record_failure(args.entry_id)
        action = "failure"
    else:
        return {
            "success": False,
            "error": "Specify --success or --failure"
        }

    if success:
        return {
            "success": True,
            "message": f"Recorded {action} for entry #{args.entry_id}"
        }
    else:
        return {
            "success": False,
            "error": f"Entry #{args.entry_id} not found"
        }


def _handle_stats(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    kb: KnowledgeBase
) -> Dict[str, Any]:
    """Handle statistics."""
    stats = kb.get_statistics()

    return {
        "success": True,
        "statistics": stats,
    }


def _handle_similar(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    kb: KnowledgeBase
) -> Dict[str, Any]:
    """Handle find similar."""
    entries = kb.find_similar(
        symptoms=args.symptoms,
        incident_type=args.type,
        limit=args.limit,
    )

    return {
        "success": True,
        "count": len(entries),
        "entries": [e.to_dict() for e in entries]
    }


def _handle_cleanup(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    kb: KnowledgeBase
) -> Dict[str, Any]:
    """Handle cleanup list."""
    entries = kb.get_unused_entries(days=args.days)

    return {
        "success": True,
        "message": f"{len(entries)} unused entries (>{args.days} days)",
        "entries": [
            {
                "id": e.id,
                "symptoms": e.symptoms_pattern[:50],
                "success_count": e.success_count,
                "last_used": e.last_used_at,
            }
            for e in entries
        ]
    }
