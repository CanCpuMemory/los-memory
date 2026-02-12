"""Observation link management for graph relationships."""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional

from .utils import utc_now

if TYPE_CHECKING:
    import sqlite3

LinkType = Literal["related", "child", "parent", "refines"]


def create_link(
    conn: sqlite3.Connection,
    from_id: int,
    to_id: int,
    link_type: LinkType = "related",
) -> int:
    """Create a link between two observations.

    Args:
        conn: Database connection
        from_id: Source observation ID
        to_id: Target observation ID
        link_type: Type of link (related, child, parent, refines)

    Returns:
        The ID of the created link

    Raises:
        ValueError: If either observation doesn't exist
    """
    # Verify both observations exist
    row = conn.execute(
        "SELECT COUNT(*) as count FROM observations WHERE id IN (?, ?)",
        (from_id, to_id),
    ).fetchone()
    if row["count"] != 2:
        raise ValueError(f"One or both observations ({from_id}, {to_id}) not found")

    cursor = conn.execute(
        """
        INSERT INTO observation_links (from_id, to_id, link_type, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(from_id, to_id, link_type) DO UPDATE SET
            link_type = excluded.link_type,
            created_at = excluded.created_at
        """,
        (from_id, to_id, link_type, utc_now()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def delete_link(
    conn: sqlite3.Connection,
    from_id: int,
    to_id: int,
    link_type: Optional[LinkType] = None,
) -> bool:
    """Delete a link between observations.

    Args:
        conn: Database connection
        from_id: Source observation ID
        to_id: Target observation ID
        link_type: Optional specific link type to delete

    Returns:
        True if a link was deleted
    """
    if link_type:
        cursor = conn.execute(
            """
            DELETE FROM observation_links
            WHERE from_id = ? AND to_id = ? AND link_type = ?
            """,
            (from_id, to_id, link_type),
        )
    else:
        cursor = conn.execute(
            """
            DELETE FROM observation_links
            WHERE from_id = ? AND to_id = ?
            """,
            (from_id, to_id),
        )
    conn.commit()
    return cursor.rowcount > 0


def get_related_observations(
    conn: sqlite3.Connection,
    observation_id: int,
    link_type: Optional[LinkType] = None,
    limit: int = 20,
) -> list[dict]:
    """Get observations related to the given observation.

    Args:
        conn: Database connection
        observation_id: The observation to find relations for
        link_type: Optional filter by link type
        limit: Maximum number of results

    Returns:
        List of related observation dicts with link info
    """
    from .operations import normalize_rows

    if link_type:
        rows = conn.execute(
            """
            SELECT o.*, l.link_type, l.from_id as link_source
            FROM observation_links l
            JOIN observations o ON (l.to_id = o.id OR l.from_id = o.id)
            WHERE (l.from_id = ? OR l.to_id = ?)
              AND l.link_type = ?
              AND o.id != ?
            ORDER BY l.created_at DESC
            LIMIT ?
            """,
            (observation_id, observation_id, link_type, observation_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT o.*, l.link_type, l.from_id as link_source
            FROM observation_links l
            JOIN observations o ON (l.to_id = o.id OR l.from_id = o.id)
            WHERE (l.from_id = ? OR l.to_id = ?)
              AND o.id != ?
            ORDER BY l.created_at DESC
            LIMIT ?
            """,
            (observation_id, observation_id, observation_id, limit),
        ).fetchall()

    results = []
    seen_ids = set()

    for row in rows:
        obs_id = row["id"]
        if obs_id in seen_ids:
            continue
        seen_ids.add(obs_id)

        results.append({
            "id": obs_id,
            "timestamp": row["timestamp"],
            "project": row["project"],
            "kind": row["kind"],
            "title": row["title"],
            "summary": row["summary"],
            "tags": row["tags"],
            "link_type": row["link_type"],
            "direction": "outgoing" if row["link_source"] == observation_id else "incoming",
        })

    return results


def find_similar_observations(
    conn: sqlite3.Connection,
    observation_id: int,
    limit: int = 5,
) -> list[dict]:
    """Find potentially related observations based on similar titles/tags.

    This is used for suggesting automatic links.

    Args:
        conn: Database connection
        observation_id: The observation to find similar observations for
        limit: Maximum number of results

    Returns:
        List of similar observations with similarity score
    """
    # Get the source observation
    row = conn.execute(
        "SELECT title, summary, tags, tags_text FROM observations WHERE id = ?",
        (observation_id,),
    ).fetchone()

    if not row:
        return []

    source_title = row["title"].lower()
    source_summary = row["summary"].lower()
    source_tags_text = row["tags_text"].lower() if row["tags_text"] else ""
    source_tags = set(source_tags_text.split()) if source_tags_text else set()

    # Find candidates using broader criteria
    candidates = conn.execute(
        """
        SELECT id, title, summary, tags, tags_text, timestamp, project, kind
        FROM observations
        WHERE id != ?
        LIMIT 100
        """,
        (observation_id,),
    ).fetchall()

    scored = []
    for cand in candidates:
        score = 0.0

        cand_tags_text = (cand["tags_text"] or "").lower()
        cand_tags = set(cand_tags_text.split())

        # Tag similarity (weighted heavily)
        if source_tags and cand_tags:
            shared_tags = source_tags & cand_tags
            score += len(shared_tags) * 25  # 25 points per shared tag

        # Title word overlap
        source_words = set(source_title.split())
        cand_words = set(cand["title"].lower().split())
        shared_words = source_words & cand_words
        if source_words:
            score += len(shared_words) * 10  # 10 points per shared word

        # Summary keyword overlap (simpler approach)
        cand_summary_lower = cand["summary"].lower()
        for word in source_summary.split():
            if len(word) > 4 and word in cand_summary_lower:  # Only longer, meaningful words
                score += 3

        if score >= 20:  # Minimum threshold
            scored.append((score, {
                "id": cand["id"],
                "title": cand["title"],
                "summary": cand["summary"],
                "project": cand["project"],
                "kind": cand["kind"],
                "similarity_score": round(score, 1),
            }))

    scored.sort(key=lambda x: -x[0])
    return [s[1] for s in scored[:limit]]


def get_links_for_observations(
    conn: sqlite3.Connection,
    observation_ids: list[int],
) -> dict[int, list[dict]]:
    """Get all links for a list of observation IDs.

    This is useful for enriching search results with related observations.

    Args:
        conn: Database connection
        observation_ids: List of observation IDs

    Returns:
        Dict mapping observation ID to list of related observation summaries
    """
    if not observation_ids:
        return {}

    placeholders = ",".join("?" for _ in observation_ids)
    rows = conn.execute(
        f"""
        SELECT l.from_id, l.to_id, l.link_type, o.title, o.id as related_id
        FROM observation_links l
        JOIN observations o ON (
            (l.from_id IN ({placeholders}) AND o.id = l.to_id)
            OR (l.to_id IN ({placeholders}) AND o.id = l.from_id)
        )
        WHERE l.from_id IN ({placeholders}) OR l.to_id IN ({placeholders})
        """,
        observation_ids * 4,
    ).fetchall()

    result: dict[int, list[dict]] = {obs_id: [] for obs_id in observation_ids}

    for row in rows:
        from_id = row["from_id"]
        to_id = row["to_id"]
        related_id = row["related_id"]

        # Determine which observation this link belongs to
        if from_id in result:
            result[from_id].append({
                "id": related_id,
                "title": row["title"],
                "link_type": row["link_type"],
                "direction": "outgoing",
            })
        if to_id in result:
            result[to_id].append({
                "id": related_id,
                "title": row["title"],
                "link_type": row["link_type"],
                "direction": "incoming",
            })

    return result
