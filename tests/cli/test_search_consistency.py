import json
import sqlite3
import subprocess
import sys


def run(db, *arguments):
    result = subprocess.run([sys.executable, "-m", "memory_tool", "--db", str(db),
                             "--output", "json", *arguments], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_search_filter_dedup_and_edit_consistency(tmp_path):
    db = tmp_path / "memory.db"
    run(db, "init")
    for project in ("alpha", "beta"):
        run(db, "observation", "add", "--project", project, "--kind", "note",
            "--title", "中文记忆检索", "--summary", "shared wording", "--dedup-mode", "skip",
            "--metadata", json.dumps({"tenant": project}))
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
    result = run(db, "memory", "search", "记忆")
    assert len(result["results"]) == 2
    run(db, "observation", "add", "--project", "gamma", "--kind", "note", "--title", "embedded",
        "--summary", "shared wording", "--metadata", json.dumps({"embedding": [0.1] * 32, "tenant": "gamma"}))
    result = run(db, "memory", "search", "shared", "--semantic", "--metadata-filter", '{"tenant":"alpha"}')
    assert len(result["results"]) == 1
    result = run(db, "memory", "search", "shared", "--semantic")
    assert len(result["results"]) == 3
    run(db, "observation", "edit", "--id", "3", "--title", "updated")
    with sqlite3.connect(db) as conn:
        metadata = json.loads(conn.execute("SELECT metadata FROM observations WHERE id=3").fetchone()[0])
        assert "embedding" not in metadata
        assert metadata["contentHash"]
