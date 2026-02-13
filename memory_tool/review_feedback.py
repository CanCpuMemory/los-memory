"""Batch apply feedback from structured review outputs."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from .feedback import apply_feedback

if TYPE_CHECKING:
    import sqlite3


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid_observation_id")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return int(value.strip())
    raise ValueError("invalid_observation_id")


def _extract_item(raw: Any) -> tuple[int, str]:
    if not isinstance(raw, dict):
        raise ValueError("item_must_be_object")
    observation_id = raw.get("observation_id", raw.get("id"))
    feedback_text = raw.get("feedback", raw.get("text", raw.get("comment", "")))
    obs_id = _to_int(observation_id)
    text = str(feedback_text or "").strip()
    if not text:
        raise ValueError("feedback_required")
    return obs_id, text


def apply_review_feedback(
    conn: sqlite3.Connection,
    items: List[Any],
    auto_apply: bool = True,
) -> Dict[str, Any]:
    """Apply review feedback items and return summary report."""
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for index, raw in enumerate(items):
        try:
            observation_id, feedback_text = _extract_item(raw)
            result = apply_feedback(
                conn,
                observation_id=observation_id,
                feedback_text=feedback_text,
                auto_apply=auto_apply,
            )
            result["index"] = index
            results.append(result)
        except Exception as exc:  # pragma: no cover - defensive path
            errors.append(
                {
                    "index": index,
                    "item": raw,
                    "error": str(exc),
                }
            )

    return {
        "ok": len(errors) == 0,
        "total": len(items),
        "applied": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
        "dry_run": not auto_apply,
    }
