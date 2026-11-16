"""The evolve importer: separators, track mapping, lifecycle tokens, idempotency.

The corpus under ``~/.dsh/memories`` is the only copy of its own content, so the
two failures that would do real damage are asserted explicitly:

* an **inline ``§``** must not split an entry (``evolve`` once cut a 5000-character
  record into six fragments this way, and the live corpus has 867 lines where
  ``§`` appears inside an entry), and
* a **second run must create nothing** — the importer is add/update only and never
  deletes.

Fixtures are built under ``tmp_path``; nothing here reads ``~/.dsh/memories``.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_tool.ledger import schema  # noqa: E402
from memory_tool.ledger.evolve_md import discover, parse_entry, split_entries  # noqa: E402
from memory_tool.ledger.import_evolve import import_evolve  # noqa: E402
from memory_tool.ledger.store import Ledger  # noqa: E402

SEPARATOR = "\n§\n"


def build_corpus(root: Path) -> Path:
    """Create a small corpus that mirrors every shape the importer maps."""
    (root / "daily").mkdir(parents=True)
    project = root / "projects" / "abc123def456"
    project.mkdir(parents=True)
    (root / "MEMORY.md").write_text(
        "[2026-01-01] 全局条目一：§7 是章节引用，不能被切开" + SEPARATOR
        + "[2026-01-02] 全局条目二" + "\n",
        encoding="utf-8",
    )
    (root / "USER.md").write_text("[2026-01-03] 用户偏好：先给证据\n", encoding="utf-8")
    (project / "KEY.md").write_text(
        "[id:aaaa1111] [2026-01-04] 关键事实：端口 9090" + SEPARATOR
        + "[id:bbbb2222] [2026-01-05] 关键事实：端口 10090" + SEPARATOR
        + "[id:cccc3333] [supersedes:bbbb2222] [2026-01-06] 关键事实：端口 10091" + SEPARATOR
        + "[id:dddd4444] [deprecated] [2026-01-07] 已废弃的旧事实" + "\n",
        encoding="utf-8",
    )
    (project / "MEMORY.md").write_text(
        "[2026-01-08 10:00] [git main] [dsfolder] 项目日志条目\n", encoding="utf-8"
    )
    (project / "TODOS.md").write_text("[id: xxxx] 待办不导入\n", encoding="utf-8")
    (root / "TODOS-work.md").write_text("[id: yyyy] 待办不导入\n", encoding="utf-8")
    (root / "daily" / "2026-01-09.md").write_text(
        "[09:00] [git main] [dsfolder] 今天的日志" + SEPARATOR
        + "[10:00] [git main] 第二条日志\n",
        encoding="utf-8",
    )
    (root / "daily" / "2026-01-10.todo.md").write_text("[09:00] 待办文件不导入\n", encoding="utf-8")
    return root


def open_ledger(path: Path) -> tuple[object, Ledger]:
    """Open an initialised ledger in a temp file."""
    conn = schema.connect(str(path), create=True)
    return conn, Ledger(conn)


def run_import(ledger: Ledger, root: Path, **kwargs) -> dict:
    """Run one import inside its own transaction."""
    with ledger.transaction():
        return import_evolve(ledger, root, **kwargs)


def test_split_entries_keeps_inline_section_marks() -> None:
    """Only a standalone ``§`` line separates entries; ``§7`` inside text does not."""
    text = "第一段提到 §7 与 §3.2" + SEPARATOR + "第二段" + "\n"
    entries = split_entries(text)
    assert entries == ["第一段提到 §7 与 §3.2", "第二段"]
    # Negative control: the naive split this guard exists to prevent.
    assert len(text.split("§")) == 4
    assert len(entries) == 2


def test_split_entries_tolerates_padded_separator_lines() -> None:
    """A separator line with trailing whitespace still separates (no lost entries)."""
    assert split_entries("a\n§   \nb") == ["a", "b"]


def test_parse_entry_reads_the_lifecycle_vocabulary() -> None:
    """Head tokens are metadata: id/slot/supersedes/deprecated/date, not body text."""
    entry = parse_entry(
        "[id:ab12cd34] [slot:gateway-port] [supersedes:ff00ff00] [2026-02-03 08:09] 正文"
    )
    assert entry.source_id == "ab12cd34"
    assert entry.slot == "gateway-port"
    assert entry.supersedes == "ff00ff00"
    assert entry.day == "2026-02-03"
    assert entry.time == "08:09"
    assert entry.body == "正文"
    assert entry.text.startswith("[id:ab12cd34]")

    deprecated = parse_entry("[deprecated:ab12cd34] 旧事实")
    assert deprecated.deprecated is True
    assert deprecated.deprecated_by == "ab12cd34"


def test_parser_agrees_with_the_hygiene_ruler() -> None:
    """Drift guard: the shared corpus vocabulary is parsed the same way twice.

    ``scripts/hygiene_scan.py`` owns the same vocabulary for read-only scanning.
    Two parsers that disagree would let the ruler and the writer describe different
    corpora, so the fields they both expose are compared here.
    """
    path = REPO_ROOT / "scripts" / "hygiene_scan.py"
    spec = importlib.util.spec_from_file_location("hygiene_scan_parity", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    samples = [
        "[id:ab12cd34] [2026-02-03] 正文一",
        "[id:ff00ff00] [slot:port] [supersedes:ab12cd34] [deprecated] [2026-03-04 10:11] 正文二",
        "[2026-04-05] [git main] [summary:标题] 正文三",
        "没有头标记的正文 §7",
    ]
    for raw in samples:
        ours = parse_entry(raw)
        theirs = module.parse_entry(raw)
        assert ours.source_id == theirs.id or (raw.startswith("没有") and theirs.id is None)
        assert ours.slot == theirs.slot
        assert ours.supersedes == theirs.supersedes
        assert ours.deprecated == theirs.deprecated
        assert ours.body.strip() == theirs.body.strip()


def test_import_maps_every_track_and_skips_todos(tmp_path: Path) -> None:
    """One import: per-track counts match the corpus, and todos are counted separately."""
    root = build_corpus(tmp_path / "memories")
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        report = run_import(ledger, root)
        tracks = report["tracks"]
        assert tracks["memory"]["entries"] == 2
        assert tracks["user"]["entries"] == 1
        assert tracks["key"]["entries"] == 4
        assert tracks["project"]["entries"] == 1
        assert tracks["daily"]["entries"] == 2
        assert report["totals"]["entries"] == 10
        assert report["totals"]["created"] == 10

        # TODOS files are counted and skipped; their content never becomes a unit.
        assert report["skipped"]["todos_files"] == 3
        assert report["skipped"]["todos_bytes"] > 0
        assert conn.execute(
            "SELECT COUNT(*) FROM revisions WHERE content LIKE '%待办不导入%'"
        ).fetchone()[0] == 0

        # Project identity is the evolve directory (sha1(cwd)[:12]), never guessed.
        projects = {
            row[0] for row in conn.execute("SELECT DISTINCT project FROM units WHERE track='key'")
        }
        assert projects == {"abc123def456"}
    finally:
        conn.close()


def test_import_is_idempotent(tmp_path: Path) -> None:
    """A second run creates nothing and appends no revision."""
    root = build_corpus(tmp_path / "memories")
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        first = run_import(ledger, root)
        after_first = conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
        second = run_import(ledger, root)

        assert first["totals"]["created"] > 0
        assert second["totals"]["created"] == 0
        assert second["totals"]["updated"] == 0
        assert second["totals"]["unchanged"] == second["totals"]["entries"]
        assert conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == after_first
    finally:
        conn.close()


def test_import_applies_supersede_and_deprecated_tokens(tmp_path: Path) -> None:
    """``[supersedes:]`` and ``[deprecated]`` become real lifecycle state."""
    root = build_corpus(tmp_path / "memories")
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        report = run_import(ledger, root)
        bucket = report["tracks"]["key"]
        assert bucket["supersede_edges"] == 1
        assert bucket["deprecated_imported"] == 1
        assert bucket["supersede_unresolved"] == 0

        retired = conn.execute(
            "SELECT u.id, u.state, u.superseded_by FROM units u JOIN revisions r "
            "ON r.unit_id=u.id AND r.revision=(SELECT MAX(revision) FROM revisions r2 "
            "WHERE r2.unit_id=u.id) WHERE r.content LIKE '%端口 10090%'"
        ).fetchone()
        assert retired is not None
        assert retired["state"] == "deprecated"
        head = conn.execute(
            "SELECT u.id FROM units u JOIN revisions r ON r.unit_id=u.id AND "
            "r.revision=(SELECT MAX(revision) FROM revisions r2 WHERE r2.unit_id=u.id) "
            "WHERE r.content LIKE '%端口 10091%'"
        ).fetchone()
        assert retired["superseded_by"] == head["id"]

        marked = conn.execute(
            "SELECT u.state FROM units u JOIN revisions r ON r.unit_id=u.id AND "
            "r.revision=(SELECT MAX(revision) FROM revisions r2 WHERE r2.unit_id=u.id) "
            "WHERE r.content LIKE '%已废弃的旧事实%'"
        ).fetchone()
        assert marked["state"] == "deprecated"
    finally:
        conn.close()


def test_import_records_the_source_id_for_traceability(tmp_path: Path) -> None:
    """The original ``[id:8hex]`` survives in the event payload."""
    root = build_corpus(tmp_path / "memories")
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        run_import(ledger, root)
        payloads = [
            row[0]
            for row in conn.execute("SELECT payload_json FROM events WHERE kind='add'")
        ]
        assert any('"source_id": "aaaa1111"' in payload for payload in payloads)
        assert any(
            '"source_file": "projects/abc123def456/KEY.md"' in payload for payload in payloads
        )
    finally:
        conn.close()


def test_import_updates_a_changed_source_entry_in_place(tmp_path: Path) -> None:
    """An upstream edit of an id-carrying entry becomes a new revision, not a new unit."""
    root = build_corpus(tmp_path / "memories")
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        run_import(ledger, root)
        key_file = root / "projects" / "abc123def456" / "KEY.md"
        key_file.write_text(
            key_file.read_text(encoding="utf-8").replace("端口 9090", "端口 19090"),
            encoding="utf-8",
        )
        second = run_import(ledger, root)

        assert second["tracks"]["key"]["updated"] == 1
        assert second["tracks"]["key"]["created"] == 0
        unit = ledger.find_by_origin_key("evolve:id:key:abc123def456:aaaa1111")
        assert unit is not None, "the edited entry keeps its unit identity"
        assert "端口 19090" in unit.content
        revisions = conn.execute(
            "SELECT COUNT(*) FROM revisions WHERE unit_id=?", (unit.id,)
        ).fetchone()[0]
        assert revisions == 2, "the edit is history, not an overwrite"
        assert conn.execute(
            "SELECT COUNT(*) FROM units WHERE origin_key LIKE 'evolve:id:%aaaa1111'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_discover_collapses_symlinked_project_aliases(tmp_path: Path) -> None:
    """One inode is one file: a symlinked project directory is read once.

    Negative control: without alias handling this corpus reports a phantom
    duplicate (the measured 2026-11-04 false "31.3% redundancy" shape).
    """
    root = tmp_path / "memories"
    real = root / "projects" / "real00000001"
    real.mkdir(parents=True)
    (real / "KEY.md").write_text("[id:eeee5555] [2026-01-01] 别名测试\n", encoding="utf-8")
    os.symlink("real00000001", str(root / "projects" / "alias0000002"))

    discovery = discover(root)
    key_files = [item for item in discovery.files if item.track == "key"]
    assert len(key_files) == 1
    assert key_files[0].rel_path.endswith("real00000001/KEY.md")
    assert discovery.aliases == [
        {
            "canonical": "projects/real00000001/KEY.md",
            "aliases": ["projects/alias0000002/KEY.md"],
        }
    ]


def test_import_honours_the_only_filter(tmp_path: Path) -> None:
    """``--only`` restricts the import without changing the mapping."""
    root = build_corpus(tmp_path / "memories")
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        report = run_import(ledger, root, only=("user",))
        assert report["tracks"]["user"]["entries"] == 1
        assert report["tracks"]["memory"]["entries"] == 0
        assert conn.execute("SELECT COUNT(*) FROM units WHERE track != 'user'").fetchone()[0] == 0
        with pytest.raises(ValueError):
            run_import(ledger, root, only=("nope",))
    finally:
        conn.close()


def test_import_keeps_true_duplicates_inside_one_file(tmp_path: Path) -> None:
    """A genuine duplicate line in one file keeps both rows (and is reported)."""
    root = tmp_path / "memories"
    root.mkdir()
    (root / "MEMORY.md").write_text(
        "[2026-01-01] 重复的事实" + SEPARATOR + "[2026-01-01] 重复的事实\n", encoding="utf-8"
    )
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        report = run_import(ledger, root)
        assert report["tracks"]["memory"]["created"] == 2
        assert report["duplicates_kept"] == 1
        assert conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 2
    finally:
        conn.close()


def test_import_of_an_empty_root_reports_zero_without_error(tmp_path: Path) -> None:
    """An existing but empty corpus is a judged zero, not an error."""
    root = tmp_path / "memories"
    root.mkdir()
    conn, ledger = open_ledger(tmp_path / "s.sqlite3")
    try:
        report = run_import(ledger, root)
        assert report["totals"]["entries"] == 0
        assert report["files"] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------- archive files (2026-11-16)

def add_archive_files(root: Path) -> None:
    """Add the ``*-archive.md`` shapes the corpus carries next to the live files."""
    project = root / "projects" / "abc123def456"
    (root / "MEMORY-archive.md").write_text(
        "[2026-01-01] 已归档的全局事实\n", encoding="utf-8"
    )
    (root / "USER-archive.md").write_text(
        "[2026-01-02] 已归档的用户偏好\n", encoding="utf-8"
    )
    (project / "KEY-archive.md").write_text(
        "[id:eeee5555] [2026-01-03] 已归档的关键事实\n", encoding="utf-8"
    )
    # TODOS-archive.md belongs to the host plugin: it must be counted and skipped,
    # not imported as memory history.
    (project / "TODOS-archive.md").write_text("[id:zzzz] 归档待办不导入\n", encoding="utf-8")


def test_archive_files_import_as_archived_and_never_go_live(tmp_path: Path) -> None:
    """``*-archive.md`` is history: imported (readable by id) but never injected.

    The failure this pins: importing archives as active would resurrect retired facts
    into the injection surface, and *skipping* them entirely would make the ledger an
    incomplete mirror that cannot answer `expand` or survive a rollback.
    """
    from memory_tool.ledger.snapshot import SnapshotOptions, build_snapshot

    root = build_corpus(tmp_path / "corpus")
    add_archive_files(root)
    conn, ledger = open_ledger(tmp_path / "arc.sqlite3")
    try:
        report = run_import(ledger, root)
        assert report["totals"]["archived_imported"] == 3, report["totals"]
        assert report["skipped"]["todos_files"] >= 4, "TODOS-archive.md 必须计入跳过"
        rels = {f.rel_path for f in discover(root).files}
        assert "projects/abc123def456/TODOS-archive.md" not in rels

        states = {}
        for row in conn.execute("SELECT track, state, COUNT(*) FROM units GROUP BY track, state"):
            states[(row[0], row[1])] = row[2]
        assert states.get(("key", "archived")) == 1
        assert states.get(("memory", "archived")) == 1
        assert states.get(("user", "archived")) == 1

        # Injection surface sees only live facts.
        snapshot = build_snapshot(
            ledger, SnapshotOptions(cwd=str(tmp_path / "cwd"), project="abc123def456")
        )
        archived_ids = {
            row[0]
            for row in conn.execute("SELECT id FROM units WHERE state = 'archived'")
        }
        emitted = {
            entry["id"]
            for track in ("memory", "user", "key")
            for entry in snapshot["tracks"][track]["entries"]
        }
        assert not (emitted & archived_ids), "归档条目不得出现在注入 payload 里"

        # Idempotent: a second run creates nothing and re-counts the same archives.
        second = run_import(ledger, root)
        assert second["totals"]["created"] == 0
        assert second["totals"]["archived_imported"] == 3
    finally:
        conn.close()
