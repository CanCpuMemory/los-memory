from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"


RULES: list[tuple[re.Pattern[str], str]] = [
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


ALLOW_CONTEXT = ("兼容", "旧命令", "deprecated", "alias", "别名", "历史", "迁移说明")
SKIP_PATH_PARTS = ("/docs/design/", "/docs/ARCHITECTURE_CONVERGENCE_REVIEW_")


def main() -> int:
    violations: list[tuple[Path, int, str, str]] = []
    for path in sorted(DOCS_DIR.rglob("*.md")):
        norm = str(path).replace("\\", "/")
        if any(part in norm for part in SKIP_PATH_PARTS):
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern, hint in RULES:
                if not pattern.search(line):
                    continue
                lowered = line.lower()
                if any(ctx in line for ctx in ALLOW_CONTEXT) or any(ctx in lowered for ctx in ALLOW_CONTEXT):
                    continue
                violations.append((path, lineno, line.strip(), hint))

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
