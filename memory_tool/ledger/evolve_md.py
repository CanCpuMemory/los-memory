"""Reading the ``dsh-memory-evolve`` markdown corpus.

The corpus under ``~/.dsh/memories`` is the only copy of its own content, so the
parser here is deliberately conservative and mirrors the vocabulary that
``scripts/hygiene_scan.py`` already owns (``[id:…] [slot:…] [supersedes:…]
[deprecated…]`` plus date/git/branch tokens). Two behaviours are contract, not
style:

* **``§`` splits only as a standalone line.** ``evolve`` once cut a 5000-character
  record into six fragments because it split on every ``§``; the real corpus has
  867 lines where ``§`` appears *inside* an entry (chapter references such as
  ``§7``). Those must survive verbatim.
* **File identity is ``(st_dev, st_ino)``, not path.** ``evolve`` reaches some
  projects through a symlinked directory (measured 2026-11-13: ``projects/
  faab4b6033b2`` is a symlink to ``2d4994d462af``), and an earlier pass counted
  such aliases as 31.3% "redundancy" that did not exist. An alias is the same
  file, so it is read once.

Track mapping is fixed by the caller-facing contract in
``docs/design/m1-writable-ledger.md``.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

#: A line containing only ``§`` (whitespace tolerated, no other text) separates
#: entries. Everything else — including ``§7`` inside a sentence — is content.
SEPARATOR_LINE = "§"

HEAD_TOKEN_RE = re.compile(r"^\[([^\[\]]{1,120})\]\s*")

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}(?::\d{2})?))?$")
TIME_RE = re.compile(r"^(\d{1,2}:\d{2}(?::\d{2})?)$")

DAILY_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

#: ``TODOS*.md`` uses a different schema and belongs to the host plugin, so it is
#: counted and skipped rather than mis-parsed.
TODOS_GLOB = "TODOS*.md"


@dataclass
class Entry:
    """One parsed corpus entry.

    ``text`` is the entry exactly as stored (head tokens included) so a round trip
    cannot lose information; ``body`` is what remains after the program tokens.
    """

    text: str
    body: str
    source_id: str | None = None
    slot: str | None = None
    supersedes: str | None = None
    deprecated: bool = False
    deprecated_by: str | None = None
    day: str | None = None
    time: str | None = None
    neutral: list[str] = field(default_factory=list)

    @property
    def searchable_text(self) -> str:
        """Text the search index should carry.

        The body plus the order-preserving tokens that are human-facing — notably
        ``[summary:…]`` (evolve's title line) and ``[<project>]`` — while
        identity/date tokens stay out so a query cannot match on metadata.
        """
        parts = [self.body]
        parts.extend(self.neutral)
        return "\n".join(part for part in parts if part)


@dataclass
class TrackFile:
    """A corpus file together with the track it maps onto."""

    path: Path
    rel_path: str
    track: str
    project: str | None = None
    day: str | None = None

    @property
    def is_daily(self) -> bool:
        """Whether the entry date is the file's date rather than a head token."""
        return self.track == "daily" and self.day is not None


@dataclass
class Discovery:
    """Result of walking a corpus root."""

    files: list[TrackFile] = field(default_factory=list)
    aliases: list[dict[str, object]] = field(default_factory=list)
    skipped_todos_files: int = 0
    skipped_todos_bytes: int = 0
    skipped_daily_other: list[str] = field(default_factory=list)


def split_entries(text: str) -> list[str]:
    """Split a corpus file into entries on standalone ``§`` lines.

    Args:
        text: File content decoded as UTF-8.

    Returns:
        Stripped, non-empty entries in file order. A line that merely *contains*
        ``§`` does not split.
    """
    text = text.lstrip("\ufeff")
    entries: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip() == SEPARATOR_LINE:
            chunk = "\n".join(current).strip()
            if chunk:
                entries.append(chunk)
            current = []
            continue
        current.append(line)
    chunk = "\n".join(current).strip()
    if chunk:
        entries.append(chunk)
    return entries


def parse_entry(text: str) -> Entry:
    """Parse the leading program-metadata tokens of one entry.

    Unknown bracket tokens are kept as ``neutral``: they must not become part of
    the body's identity, but dropping them would silently lose information
    (``[git main]``, ``[summary:…]``, ``[dsh-only]``).
    """
    stripped = text.strip()
    rest = stripped
    entry = Entry(text=stripped, body=stripped)
    while True:
        match = HEAD_TOKEN_RE.match(rest)
        if match is None:
            break
        token = match.group(1).strip()
        rest = rest[match.end():]
        lowered = token.lower()
        if lowered.startswith("id:"):
            entry.source_id = token[3:].strip()
        elif lowered.startswith("slot:"):
            entry.slot = token[5:].strip()
        elif lowered.startswith("supersedes:"):
            entry.supersedes = token[11:].strip()
        elif lowered.startswith("deprecated"):
            entry.deprecated = True
            entry.deprecated_by = token.split(":", 1)[1].strip() if ":" in token else None
        elif DATE_RE.match(token):
            parsed = DATE_RE.match(token)
            entry.day = parsed.group(1)
            entry.time = parsed.group(2)
        elif TIME_RE.match(token):
            entry.time = token
        else:
            entry.neutral.append(token)
    entry.body = rest.strip()
    return entry


def _track_for(root: Path, path: Path, project_map: dict[str, str] | None = None):
    """Map a discovered file onto ``(track, project, day)``.

    Returns ``None`` for files that are intentionally not imported.
    """
    name = path.name
    parent = path.parent
    if parent == root:
        if name == "MEMORY.md":
            return "memory", None, None
        if name == "USER.md":
            return "user", None, None
        return None
    if parent.parent == root and parent.name == "daily":
        match = DAILY_NAME_RE.match(name)
        if match:
            return "daily", None, match.group(1)
        return None
    if parent.parent.parent == root and parent.parent.name == "projects":
        directory = parent.name
        project = (project_map or {}).get(directory, directory)
        if name == "KEY.md":
            return "key", project, None
        if name == "MEMORY.md":
            return "project", project, None
        return None
    return None


def discover(
    root: str | os.PathLike[str],
    only: tuple[str, ...] | None = None,
    project_map: dict[str, str] | None = None,
) -> Discovery:
    """Walk a corpus root and return the files to import, de-aliased.

    Args:
        root: ``~/.dsh/memories`` (or a fixture).
        only: Optional track filter; ``None`` imports every track.
        project_map: Optional ``{directory_hash: display_slug}`` mapping. Without
            it the project identifier is the evolve directory name, which is
            ``sha1(<literal cwd>)[:12]`` — reproducible by hashing a candidate
            path, never guessed here.

    Returns:
        A :class:`Discovery` with the selected files, alias groups and skip counts.
    """
    base = Path(root).expanduser()
    if not base.is_dir():
        return Discovery()
    resolved_base = base.resolve()
    found: list[tuple[Path, str, str | None, str | None]] = []
    skipped_todos: list[Path] = []
    skipped_daily_other: list[str] = []

    def add(path: Path) -> None:
        mapped = _track_for(base, path, project_map)
        if mapped is None:
            return
        track, project, day = mapped
        if only is not None and track not in only:
            return
        found.append((path, track, project, day))

    # Root-level tracks, skipping TODOS* (host-plugin schema).
    for name in ("MEMORY.md", "USER.md"):
        candidate = base / name
        if candidate.is_file():
            add(candidate)
    for todo in sorted(base.glob(TODOS_GLOB)):
        if todo.is_file():
            skipped_todos.append(todo)

    daily = base / "daily"
    if daily.is_dir():
        for candidate in sorted(daily.glob("*.md")):
            if not candidate.is_file():
                continue
            if candidate.name.endswith(".todo.md"):
                skipped_todos.append(candidate)
                continue
            if DAILY_NAME_RE.match(candidate.name):
                add(candidate)
            else:
                skipped_daily_other.append(str(candidate.relative_to(base)))

    projects = base / "projects"
    if projects.is_dir():
        for directory in sorted(projects.iterdir()):
            if not directory.is_dir():
                continue
            for name in ("KEY.md", "MEMORY.md"):
                candidate = directory / name
                if candidate.is_file():
                    add(candidate)
            todo = directory / "TODOS.md"
            if todo.is_file():
                skipped_todos.append(todo)

    # De-alias: one inode is one file. Two shapes occur in the real corpus
    # (measured 2026-11-13): symlinked project directories, and hard-linked
    # KEY.md/MEMORY.md pairs across different project hashes. Reading them twice
    # would double the corpus; reading them as "different files" would make the
    # import count unverifiable.
    by_inode: dict[tuple[int, int], list[tuple[Path, str, str | None, str | None]]] = {}
    order: list[tuple[int, int]] = []
    for item in found:
        path = item[0]
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - vanishes mid-walk
            continue
        key = (stat.st_dev, stat.st_ino)
        if key not in by_inode:
            by_inode[key] = []
            order.append(key)
        by_inode[key].append(item)

    discovery = Discovery()
    for key in order:
        group = by_inode[key]

        def rel(item: tuple[Path, str, str | None, str | None]) -> str:
            return str(item[0].relative_to(base))

        # A real path beats a symlinked one (resolves to itself); among real paths
        # — i.e. hard links, where neither is "more real" — the lexicographic
        # minimum wins so the choice is deterministic across runs.
        real = [item for item in group if item[0].resolve() == resolved_base / rel(item)]
        preferred = sorted(real or group, key=rel)
        chosen = preferred[0]
        path, track, project, day = chosen
        discovery.files.append(
            TrackFile(
                path=path,
                rel_path=rel(chosen),
                track=track,
                project=project,
                day=day,
            )
        )
        others = sorted(rel(item) for item in group if item is not chosen)
        if others:
            discovery.aliases.append({"canonical": rel(chosen), "aliases": others})
    discovery.skipped_todos_files = len(skipped_todos)
    discovery.skipped_todos_bytes = sum(_safe_size(path) for path in skipped_todos)
    discovery.skipped_daily_other = skipped_daily_other
    return discovery


def _safe_size(path: Path) -> int:
    """Return a file size, or 0 when it cannot be read (never raise mid-walk)."""
    try:
        return path.stat().st_size
    except OSError:  # pragma: no cover - unreadable file
        return 0
