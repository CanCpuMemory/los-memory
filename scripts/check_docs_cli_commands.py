from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"


LINE_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bmemory_tool\s+doctor\b"),
        "请使用 `memory_tool admin doctor`。",
    ),
    (
        re.compile(
            r"\bmemory_tool\s+(?!import\b)(add|search|list|get|edit|delete|clean|manage|feedback|link|unlink|related|share|import)\b"
        ),
        "请使用分组命令（如 `observation add`、`memory search`、`admin manage`）。",
    ),
    (
        re.compile(r"\b(memory_tool|los-memory)(?:\.py)?\b[^\n]*\breview-feedback\b"),
        "请使用 `review apply`，`review-feedback` 仅允许作为兼容说明出现。",
    ),
    (
        re.compile(r"\b(memory_tool|los-memory)(?:\.py)?\b[^\n]*\btransition-log\b"),
        "请使用 `tool transition`，`transition-log` 仅允许作为兼容说明出现。",
    ),
]

TEXT_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(memory_tool|los-memory)(?:\.py)?\b[\s\\\r\n-]*session\s+start\b[\s\S]{0,200}?--description\b",
            re.MULTILINE,
        ),
        "请使用 `session start --summary`，不要再使用旧参数 `--description`。",
    ),
]


ALLOW_CONTEXT = ("兼容", "旧命令", "deprecated", "alias", "别名", "历史", "迁移说明")
SKIP_PATH_PARTS = ("/docs/design/", "/docs/ARCHITECTURE_CONVERGENCE_REVIEW_")


def _find_violations(path: Path, text: str) -> list[tuple[Path, int, str, str]]:
    violations: list[tuple[Path, int, str, str]] = []
    lines = text.splitlines()
    for lineno, line in enumerate(lines, start=1):
        for pattern, hint in LINE_RULES:
            if not pattern.search(line):
                continue
            lowered = line.lower()
            if any(ctx in line for ctx in ALLOW_CONTEXT) or any(ctx in lowered for ctx in ALLOW_CONTEXT):
                continue
            violations.append((path, lineno, line.strip(), hint))

    for pattern, hint in TEXT_RULES:
        for match in pattern.finditer(text):
            start = match.start()
            lineno = text.count("\n", 0, start) + 1
            snippet = match.group(0).strip().splitlines()[0]
            segment = text[max(0, start - 160) : min(len(text), match.end() + 160)]
            lowered = segment.lower()
            if any(ctx in segment for ctx in ALLOW_CONTEXT) or any(ctx in lowered for ctx in ALLOW_CONTEXT):
                continue
            violations.append((path, lineno, snippet, hint))

    return violations


def main() -> int:
    violations: list[tuple[Path, int, str, str]] = []
    for path in sorted(DOCS_DIR.rglob("*.md")):
        norm = str(path).replace("\\", "/")
        if any(part in norm for part in SKIP_PATH_PARTS):
            continue
        text = path.read_text(encoding="utf-8")
        violations.extend(_find_violations(path, text))

    if not violations:
        print("docs-command-lint: ok")
        return 0

    print("docs-command-lint: found outdated command examples")
    for path, lineno, line, hint in violations:
        rel = path.relative_to(ROOT)
        print(f"- {rel}:{lineno}")
        print(f"  line: {line}")
        print(f"  hint: {hint}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
