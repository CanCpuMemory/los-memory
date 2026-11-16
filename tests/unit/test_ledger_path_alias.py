"""cwd alias canonicalization rules (mirrors evolve's ``lib/path-alias.js``).

The ledger's project identity is ``sha1(<literal cwd>)[:12]``, so a moved
repository silently becomes a second project directory. These tests pin the four
resolution rules and, just as importantly, the *failure* policy: a missing or
broken table must yield the identity mapping plus a named reason rather than a
guess.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_tool.ledger.path_alias import canonical_cwd, normalize_path, parse_alias_map  # noqa: E402


def write_map(tmp_path: Path, declared: list[dict]) -> str:
    path = tmp_path / "path-alias-map.json"
    path.write_text(json.dumps({"version": 2, "declared": declared}), encoding="utf-8")
    return str(path)


def test_normalize_path_trims_trailing_slashes_but_keeps_root() -> None:
    assert normalize_path("/a/b/") == "/a/b"
    assert normalize_path("/a/b") == "/a/b"
    assert normalize_path("/") == "/"
    assert normalize_path("") is None
    assert normalize_path(None) is None


def test_parse_drops_self_mappings_and_orders_longest_first() -> None:
    pairs = parse_alias_map({"declared": [
        {"from": "/a", "to": "/x"},
        {"from": "/a/b/c", "to": "/y"},
        {"from": "/same", "to": "/same"},
        {"from": "/bad"},
        "not-an-object",
    ]})
    assert pairs[0] == ("/a/b/c", "/y"), "longest prefix must win"
    assert ("/same", "/same") not in pairs
    assert len(pairs) == 2


def test_exact_match_wins_over_prefix(tmp_path: Path) -> None:
    path = write_map(tmp_path, [
        {"from": "/old/proj", "to": "/new/proj"},
        {"from": "/old", "to": "/elsewhere"},
    ])
    canonical, applied, reason = canonical_cwd("/old/proj", path)
    assert (canonical, applied, reason) == ("/new/proj", "/old/proj", None)


def test_prefix_maps_onto_the_matching_subpath(tmp_path: Path) -> None:
    path = write_map(tmp_path, [{"from": "/old", "to": "/new"}])
    canonical, applied, _ = canonical_cwd("/old/proj/sub/", path)
    assert canonical == "/new/proj/sub"
    assert applied == "/old"


def test_prefix_never_matches_on_a_partial_segment(tmp_path: Path) -> None:
    """/a/b must not rewrite /a/bc — a boundary-less prefix match moves data silently."""
    path = write_map(tmp_path, [{"from": "/a/b", "to": "/x"}])
    canonical, applied, _ = canonical_cwd("/a/bc/thing", path)
    assert canonical == "/a/bc/thing"
    assert applied is None


def test_only_one_hop_is_taken(tmp_path: Path) -> None:
    """Chasing a chain turns a bad table into silent data movement."""
    path = write_map(tmp_path, [
        {"from": "/a", "to": "/b"},
        {"from": "/b", "to": "/c"},
    ])
    canonical, applied, _ = canonical_cwd("/a", path)
    assert (canonical, applied) == ("/b", "/a")


def test_unmapped_cwd_is_identity_without_a_reason(tmp_path: Path) -> None:
    path = write_map(tmp_path, [{"from": "/old", "to": "/new"}])
    canonical, applied, reason = canonical_cwd("/somewhere/else", path)
    assert (canonical, applied, reason) == ("/somewhere/else", None, None)


def test_missing_table_is_identity_with_a_named_reason(tmp_path: Path) -> None:
    canonical, applied, reason = canonical_cwd("/a", str(tmp_path / "nope.json"))
    assert (canonical, applied, reason) == ("/a", None, "alias_map_missing")


def test_malformed_table_is_identity_with_a_named_reason(tmp_path: Path) -> None:
    broken = tmp_path / "path-alias-map.json"
    broken.write_text("{not json", encoding="utf-8")
    canonical, applied, reason = canonical_cwd("/a", str(broken))
    assert (canonical, applied, reason) == ("/a", None, "alias_map_malformed")


def test_disabled_canonicalization_is_identity_without_a_reason(tmp_path: Path) -> None:
    """Disabling is an explicit choice, not a degraded table — do not report it as one."""
    path = write_map(tmp_path, [{"from": "/a", "to": "/b"}])
    canonical, applied, reason = canonical_cwd("/a", None)
    assert (canonical, applied, reason) == ("/a", None, None)
    assert Path(path).exists(), "the fixture must still be on disk; the point is that it was not read"
