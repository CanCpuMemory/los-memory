#!/usr/bin/env python3
"""
Built-in LLM hook for auto-summarization and tag extraction.

This hook can be used with:
    memory_tool.py add --title "..." --summary "..." --llm-hook "python3 /path/to/summarize_hook.py"

Or set as environment variable:
    export MEMORY_LLM_HOOK="python3 /path/to/summarize_hook.py"

Features:
- Generates concise titles from long summaries
- Extracts key decisions and action items
- Suggests relevant tags based on content
- Summarizes long raw text
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any


def extract_keywords(text: str, limit: int = 5) -> list[str]:
    """Extract important keywords from text."""
    # Common stop words
    stop_words = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "he", "in", "is", "it", "its", "of", "on", "that", "the",
        "to", "was", "will", "with", "the", "we", "our", "this", "have",
        "been", "had", "were", "they", "their", "been", "have", "has",
    }

    # Extract words (favor compound words and technical terms)
    words = re.findall(r'\b[a-z][a-z-]{2,}\b', text.lower())

    # Count frequencies (excluding stop words)
    counts: dict[str, int] = {}
    for word in words:
        if word in stop_words or word.isdigit():
            continue
        counts[word] = counts.get(word, 0) + 1

    # Return top keywords
    sorted_words = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [word for word, _ in sorted_words[:limit]]


def extract_decisions(text: str) -> list[str]:
    """Extract decision statements from text."""
    decisions = []

    # Patterns that indicate decisions
    patterns = [
        r'(?:decided|decision)\s+(?:to|on)\s+([^\.\n]+)',
        r'(?:we|team|i)\s+(?:will|shall|decided to|chose|opted for)\s+([^\.\n]+)',
        r'(?:going with|using|adopting)\s+([^\.\n]+?)(?:\s+(?:for|because|due to)|[\.\n])',
        r'(?:selected|chose|picked)\s+([^\.\n]+)',
    ]

    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            decision = match.group(1).strip()
            if len(decision) > 5:  # Filter out very short matches
                decisions.append(decision)

    return decisions[:3]  # Limit to top 3


def extract_action_items(text: str) -> list[str]:
    """Extract action items from text."""
    actions = []

    # Patterns that indicate action items
    patterns = [
        r'(?:TODO|FIXME|ACTION|TASK)[\s:]*(\([^\)]+\)\s*)?([^\.\n]+)',
        r'(?:need to|should|must|will)\s+([^\.\n]+)',
        r'@(\w+)\s+(?:to|will|should)\s+([^\.\n]+)',
    ]

    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            action = match.group(0).strip()
            if len(action) > 5:
                actions.append(action)

    return actions[:5]  # Limit to top 5


def generate_title(summary: str, max_length: int = 60) -> str | None:
    """Generate a concise title from summary if current title is generic."""
    # If summary is short enough, use it
    if len(summary) <= max_length:
        return summary

    # Try to extract first sentence
    sentences = re.split(r'[.!?]+', summary)
    if sentences:
        first = sentences[0].strip()
        if len(first) <= max_length:
            return first
        # Truncate with ellipsis
        return first[:max_length-3] + "..."

    return None


def suggest_tags(title: str, summary: str, existing_tags: list[str]) -> list[str]:
    """Suggest relevant tags based on content."""
    combined = f"{title} {summary}".lower()

    # Domain-specific tag mappings
    tag_patterns = {
        "database": [r'\b(sqlite|postgres|mysql|database|db|schema|table|query|index)\b'],
        "api": [r'\b(api|endpoint|rest|graphql|http|request|response)\b'],
        "ui": [r'\b(ui|ux|interface|component|frontend|react|vue|html|css)\b'],
        "backend": [r'\b(backend|server|api|endpoint|service|handler)\b'],
        "testing": [r'\b(test|spec|jest|pytest|unittest|e2e|integration)\b'],
        "deployment": [r'\b(deploy|ci/cd|pipeline|docker|kubernetes|k8s|aws|gcp)\b'],
        "performance": [r'\b(performance|optimization|speed|memory|cpu|cache)\b'],
        "security": [r'\b(security|auth|oauth|jwt|encryption|vulnerability)\b'],
        "bug": [r'\b(bug|fix|error|crash|issue|problem|broken)\b'],
        "refactor": [r'\b(refactor|cleanup|restructure|simplify|extract)\b'],
        "docs": [r'\b(documentation|docs|readme|comment|explain)\b'],
        "config": [r'\b(config|configuration|env|environment|setting|yaml|json)\b'],
        "cli": [r'\b(cli|command.?line|terminal|shell|script|bash)\b'],
        "async": [r'\b(async|await|promise|callback|event|stream|queue)\b'],
        "types": [r'\b(types|typescript|typing|interface|type.?check)\b'],
    }

    suggested = list(existing_tags)

    for tag, patterns in tag_patterns.items():
        if tag in suggested:
            continue
        for pattern in patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                suggested.append(tag)
                break

    return suggested[:8]  # Limit total tags


def process(payload: dict[str, Any]) -> dict[str, Any]:
    """Process the observation payload and return enriched fields."""
    result = {}

    title = payload.get("title", "")
    summary = payload.get("summary", "")
    raw = payload.get("raw", "")
    existing_tags = payload.get("tags", [])

    # Use combined text for analysis
    full_text = f"{title}\n{summary}\n{raw}"

    # Generate better title if current one is short/generic
    generic_titles = ["note", "update", "meeting", "discussion", "", "todo"]
    if title.lower().strip() in generic_titles or len(title) < 10:
        new_title = generate_title(summary)
        if new_title:
            result["title"] = new_title

    # Extract and enhance summary with key points
    decisions = extract_decisions(full_text)
    actions = extract_action_items(full_text)

    enhancements = []
    if decisions:
        enhancements.append(f"Decision: {'; '.join(decisions)}")
    if actions:
        enhancements.append(f"Actions: {'; '.join(actions[:2])}")

    # If summary is very long, condense it
    if len(summary) > 500:
        # Keep first sentence and any enhancements
        first_sentence = re.split(r'[.!?]+', summary)[0].strip()
        if enhancements:
            result["summary"] = f"{first_sentence}. {' | '.join(enhancements)}"
        else:
            result["summary"] = first_sentence + "."
    elif enhancements:
        # Just append key points
        result["summary"] = f"{summary} ({'; '.join(enhancements)})"

    # Suggest tags
    result["tags"] = suggest_tags(title, summary, existing_tags)

    return result


def main():
    """Read JSON from stdin, process, output JSON to stdout."""
    try:
        payload = json.load(sys.stdin)
        result = process(payload)
        print(json.dumps(result))
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON input: {e}"}), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
