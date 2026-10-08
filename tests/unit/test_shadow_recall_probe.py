"""Tests for the primary recall probe.

The probe exists because the primary can answer `Search Index: Ready` while
everything written after a point is unretrievable (observed from 2026-09-24, still
true on 2026-10-08, upstream community#649). The alert we had keys off
`search_index.state`, so it cannot fire for that. These tests pin the property that
makes the probe useful and the property that keeps it honest: it must separate
"the primary lost newer content" from "the primary cannot answer at all".
"""
import json
import urllib.error

import pytest

from memory_tool.shadow import (
    connect,
    probe_primary_recall,
    put,
    select_recall_probes,
)


def build_record(source_id, created_at, title, content):
    return {"id": source_id, "space_id": "default", "title": title, "content": content,
            "created_at": created_at, "lifecycle_state": "active", "is_latest": True,
            "metadata": {}}


@pytest.fixture
def mirror(tmp_path):
    connection = connect(tmp_path / "private" / "shadow.db")
    yield connection
    connection.close()


def seed(mirror, count=8, prefix="rec"):
    """Records spread across the timeline, each with a unique literal anchor."""
    ids = []
    for index in range(count):
        source_id = f"{prefix}-{index}"
        put(mirror, "default", build_record(
            source_id,
            f"2026-09-{10 + index:02d}T00:00:00Z",
            f"release-{index}-handler notes",
            f"unique-anchor-{prefix}{index} appears only in this record."))
        ids.append(source_id)
    return ids


class FakePrimary:
    """Stands in for `nmem memories search -j`.

    Answers by *anchor*, not by guessing how anchors are derived, so the test does
    not break when anchor selection changes.
    """

    def __init__(self, answers, fails=False):
        """`answers` maps anchor -> the source_id the primary would return for it."""
        self.answers = dict(answers)
        self.fails = fails
        self.queries = []

    def __call__(self, argv, **kwargs):
        if self.fails:
            raise urllib.error.URLError("primary unreachable")
        query = argv[3]
        self.queries.append(query)
        source_id = self.answers.get(query)
        hits = [{"id": source_id}] if source_id else []
        return type("R", (), {"returncode": 0, "stderr": "",
                              "stdout": json.dumps({"memories": hits})})()


def install(monkeypatch, fake):
    from memory_tool import shadow
    monkeypatch.setattr(shadow.subprocess, "run", fake)
    return fake


def answers_for(probes, groups=("recent", "control")):
    """Every selected probe answers — i.e. a healthy primary."""
    return {probe["anchor"]: probe["source_id"]
            for probe in probes if probe["group"] in groups}


# --- probe selection -------------------------------------------------------

def test_probe_selection_splits_the_timeline_into_recent_and_control(mirror):
    seed(mirror, count=8)
    selection = select_recall_probes(mirror, recent=3, control=2, span=2)
    groups = [probe["group"] for probe in selection["probes"]]
    assert groups.count("recent") == 3
    assert groups.count("control") == 2
    assert groups.count("span") == 2

    by_group = {}
    for probe in selection["probes"]:
        by_group.setdefault(probe["group"], []).append(probe["created_at"])
    assert max(by_group["control"]) < min(by_group["recent"]), (
        "the control set must come from the other end of the timeline")


def test_span_probes_cover_the_middle_of_the_timeline(mirror):
    """Without span probes the boundary bracket is "between the first record and
    today" — three months wide on the live mirror, which answers nothing."""
    seed(mirror, count=40)
    selection = select_recall_probes(mirror, recent=4, control=3, span=3)
    groups = {}
    for probe in selection["probes"]:
        groups.setdefault(probe["group"], []).append(probe["created_at"])
    assert len(groups.get("span", [])) == 3
    middle = sorted(groups["span"])
    assert middle[0] < middle[-1], "span probes must cover a range, not one cluster"
    assert max(groups["control"]) < middle[0], "span sits after the control"
    assert middle[-1] < min(groups["recent"]), "span sits before the recent bucket"


def test_controls_stay_in_the_trusted_region(mirror):
    """A same-day record in the control set would let a stale index look like a
    dead primary, which is the confusion the control exists to prevent."""
    seed(mirror, count=40)
    selection = select_recall_probes(mirror, recent=4, control=3, span=3)
    controls = sorted(probe["created_at"] for probe in selection["probes"]
                      if probe["group"] == "control")
    recent_start = min(probe["created_at"] for probe in selection["probes"]
                       if probe["group"] == "recent")
    assert len(controls) == 3
    assert controls[-1] < recent_start
    assert controls[-1][:10] <= "2026-09-20", f"control reaches {controls[-1]}, too recent"


def test_span_probes_do_not_change_the_verdict(mirror, monkeypatch):
    """Verdict comes from recent vs control only; span only locates the boundary."""
    seed(mirror, count=20)
    probes = select_recall_probes(mirror, recent=3, control=3, span=3)["probes"]
    answers = {probe["anchor"]: probe["source_id"]
               for probe in probes if probe["group"] == "control"}
    install(monkeypatch, FakePrimary(answers))
    result = probe_primary_recall(mirror, command="nmem", probes=probes)
    assert result["verdict"] == "stale_projection_suspected"
    # The span probes all missed, yet the calibration still holds.
    assert result["control_rate"] == 1.0
    assert result["recent_rate"] == 0.0


def test_probe_selection_uses_rare_literal_anchors(mirror):
    seed(mirror, count=4)
    selection = select_recall_probes(mirror, recent=2, control=1)
    assert selection["probes"], "rare anchors exist in this fixture"
    for probe in selection["probes"]:
        assert probe["mirror_hits"] <= 2
        # The anchor must come from that record's own text (title or body).
        assert ("unique-anchor-rec" in probe["anchor"]
                or "release-" in probe["anchor"])


def test_common_anchors_are_rejected(mirror):
    """An anchor shared by many records cannot prove retrieval."""
    for index in range(4):
        put(mirror, "default", build_record(
            f"dup-{index}", f"2026-09-{10 + index:02d}T00:00:00Z",
            "shared-token-here", "the same wording in every record"))
    selection = select_recall_probes(mirror, recent=4, control=0, max_mirror_hits=2)
    assert selection["probes"] == [], (
        "a token present in every record must not be accepted as an anchor")


def test_too_few_records_is_reported_not_crashed(mirror):
    put(mirror, "default", build_record("only", "2026-09-10T00:00:00Z", "t", "body"))
    selection = select_recall_probes(mirror)
    assert selection["probes"] == []
    assert "too few records" in selection["reason"]


def test_span_can_be_disabled(mirror):
    seed(mirror, count=12)
    selection = select_recall_probes(mirror, recent=3, control=3, span=0)
    assert selection["span"] == 0
    assert all(probe["group"] != "span" for probe in selection["probes"])


# --- verdicts --------------------------------------------------------------

def test_stale_projection_is_distinguished_from_a_dead_primary(mirror, monkeypatch):
    """The whole point: these two need opposite responses."""
    seed(mirror, count=8)
    probes = select_recall_probes(mirror, recent=3, control=2)["probes"]

    # Old records answer, new ones do not -> a stale projection.
    stale = install(monkeypatch, FakePrimary(answers_for(probes, groups=("control",))))
    result = probe_primary_recall(mirror, command="nmem", probes=probes)
    assert result["verdict"] == "stale_projection_suspected"
    assert result["control_rate"] == 1.0 and result["recent_rate"] == 0.0
    assert stale.queries, "the probe must actually ask the primary"

    # The boundary is bracketed, and honestly bounded by the probed set rather than
    # presented as a solved date. Because only the controls answer, the span probes
    # are what actually locate the boundary — the bracket should be tighter than the
    # gap between the control and recent buckets.
    answered = max(probe["created_at"] for probe in probes if probe["group"] == "control")
    assert result["newest_retrievable_created_at"] == answered
    assert result["oldest_unretrievable_created_at"] == min(
        probe["created_at"] for probe in probes
        if probe["group"] != "control" and probe["created_at"] > answered)
    assert result["oldest_unretrievable_created_at"] < min(
        probe["created_at"] for probe in probes if probe["group"] == "recent"), (
        "span probes should tighten the bracket beyond the recent bucket")

    # Nothing answers -> not a freshness problem.
    dead = install(monkeypatch, FakePrimary({}))
    result = probe_primary_recall(mirror, command="nmem", probes=probes)
    assert result["verdict"] == "primary_not_answering"
    assert result["control_rate"] == 0.0
    assert result["newest_retrievable_created_at"] is None
    assert dead.queries


def test_a_control_miss_does_not_corrupt_the_boundary_bracket(mirror, monkeypatch):
    """A control probe that misses sits before the newest retrievable record.

    Taking the raw minimum of missed dates would report an interval running
    backwards; only misses after the newest retrievable record may bound it.
    """
    seed(mirror, count=8)
    probes = select_recall_probes(mirror, recent=3, control=3)["probes"]
    control = [probe for probe in probes if probe["group"] == "control"]
    # Two of three controls answer; the oldest one does not.
    answers = {probe["anchor"]: probe["source_id"] for probe in control[1:]}
    install(monkeypatch, FakePrimary(answers))

    result = probe_primary_recall(mirror, command="nmem", probes=probes)
    assert result["verdict"] == "stale_projection_suspected"
    assert result["control_misses"] == [control[0]["anchor"]]
    answered = max(probe["created_at"] for probe in control[1:])
    assert result["newest_retrievable_created_at"] == answered
    assert result["oldest_unretrievable_created_at"] == min(
        probe["created_at"] for probe in probes
        if probe["created_at"] > answered
        and probe["anchor"] not in {item["anchor"] for item in control[1:]})
    assert (result["newest_retrievable_created_at"]
            < result["oldest_unretrievable_created_at"]), "the bracket must not run backwards"


def test_healthy_primary_is_reported_ok(mirror, monkeypatch):
    seed(mirror, count=8)
    probes = select_recall_probes(mirror, recent=3, control=2)["probes"]
    install(monkeypatch, FakePrimary(answers_for(probes)))
    result = probe_primary_recall(mirror, command="nmem", probes=probes)
    assert result["verdict"] == "ok"
    assert result["recent_rate"] == 1.0 and result["control_rate"] == 1.0


def test_no_recent_probes_is_not_silently_ok(mirror, monkeypatch):
    seed(mirror, count=4)
    selection = select_recall_probes(mirror, recent=2, control=2, span=0)["probes"]
    only_control = [probe for probe in selection if probe["group"] == "control"]
    install(monkeypatch, FakePrimary({}))
    result = probe_primary_recall(mirror, command="nmem", probes=only_control)
    assert result["verdict"] == "no_recent_probes"


def test_no_probes_returns_a_named_outcome(mirror):
    result = probe_primary_recall(mirror, command="nmem", probes=[])
    assert result["verdict"] == "no_probes"
    assert "rare enough anchor" in result["reading"]


def test_primary_errors_are_surfaced_per_probe(mirror, monkeypatch):
    seed(mirror, count=6)
    probes = select_recall_probes(mirror, recent=2, control=1)["probes"]

    def failing(argv, **kwargs):
        raise urllib.error.URLError("timeout")

    install(monkeypatch, failing)
    result = probe_primary_recall(mirror, command="nmem", probes=probes)
    assert len(result["errors"]) == len(probes)
    assert result["verdict"] in ("primary_not_answering", "stale_projection_suspected")
    assert all(probe["retrieved"] is False for probe in result["probes"])


def test_probe_is_read_only_on_the_mirror(mirror, monkeypatch):
    seed(mirror, count=6)
    before = mirror.execute("SELECT count(*), sum(active) FROM records").fetchone()[:]
    before_revisions = mirror.execute("SELECT count(*) FROM revisions").fetchone()[0]
    probes = select_recall_probes(mirror, recent=2, control=1)["probes"]
    install(monkeypatch, FakePrimary(answers_for(probes)))
    probe_primary_recall(mirror, command="nmem", probes=probes)
    after = mirror.execute("SELECT count(*), sum(active) FROM records").fetchone()[:]
    assert before == after
    assert mirror.execute("SELECT count(*) FROM revisions").fetchone()[0] == before_revisions


# --- anchor extraction -----------------------------------------------------

def test_anchor_candidates_prefer_identifiers_and_dedupe():
    from memory_tool.shadow import _anchor_candidates
    record = {"title": "mcp-los-memory-shadow rollout",
              "content": "see mcp-los-memory-shadow and 记忆双轨格局 for details"}
    candidates = _anchor_candidates(record)
    assert "mcp-los-memory-shadow" in candidates
    assert candidates.count("mcp-los-memory-shadow") == 1, "anchors must be deduplicated"
    assert any(len(item) == 4 for item in candidates), "a CJK window should be offered"


def test_anchor_candidates_enforces_a_floor_length():
    from memory_tool.shadow import _anchor_candidates
    assert _anchor_candidates({"title": "a b", "content": ""}) == []


# --- the shared primary-lookup contract ------------------------------------
#
# `_nowledge_ids` used to raise on a missing binary, a timeout or unparseable
# output while also returning errors in its third element — two error channels for
# one job. A scheduler needs one: an exception takes down the whole run instead of
# recording a single failed lookup. Every case below must come back as a triple.

def make_runner(exc=None, returncode=0, stdout="", stderr=""):
    def run(argv, **kwargs):
        if exc is not None:
            raise exc
        return type("R", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()
    return run


@pytest.mark.parametrize("exception,expected", [
    (FileNotFoundError("nmem"), "FileNotFoundError"),
    (OSError("boom"), "OSError"),
])
def test_nowledge_ids_returns_os_errors_instead_of_raising(monkeypatch, exception, expected):
    from memory_tool import shadow
    monkeypatch.setattr(shadow.subprocess, "run", make_runner(exc=exception))
    ids, elapsed, error = shadow._nowledge_ids("q", 5, "nmem", 180)
    assert ids is None and error == expected and elapsed is not None


def test_nowledge_ids_returns_a_timeout_instead_of_raising(monkeypatch):
    import subprocess as subprocess_module
    from memory_tool import shadow
    monkeypatch.setattr(shadow.subprocess, "run",
                        make_runner(exc=subprocess_module.TimeoutExpired("nmem", 180)))
    ids, _elapsed, error = shadow._nowledge_ids("q", 5, "nmem", 180)
    assert ids is None and error == "TimeoutExpired"


def test_nowledge_ids_returns_unparseable_output_as_an_error(monkeypatch):
    from memory_tool import shadow
    monkeypatch.setattr(shadow.subprocess, "run", make_runner(stdout="not json"))
    ids, _elapsed, error = shadow._nowledge_ids("q", 5, "nmem", 180)
    assert ids is None and error == "JSONDecodeError"


def test_nowledge_ids_reports_a_nonzero_exit_with_stderr(monkeypatch):
    from memory_tool import shadow
    monkeypatch.setattr(shadow.subprocess, "run",
                        make_runner(returncode=2, stderr="  boom  "))
    ids, _elapsed, error = shadow._nowledge_ids("q", 5, "nmem", 180)
    assert ids is None and error == "boom"


def test_nowledge_ids_returns_ids_on_success(monkeypatch):
    from memory_tool import shadow
    payload = json.dumps({"memories": [{"id": "a"}, {"id": "b"}, {"no_id": 1}]})
    monkeypatch.setattr(shadow.subprocess, "run", make_runner(stdout=payload))
    ids, _elapsed, error = shadow._nowledge_ids("q", 5, "nmem", 180)
    assert ids == ["a", "b"] and error is None


# --- CLI guards -------------------------------------------------------------
#
# A negative count would slice the timeline backwards (`entries[-(-1):]`), and an
# unbounded total would hammer the primary at ~13 s per lookup.

def run_cli(*arguments):
    import subprocess
    import sys
    return subprocess.run(
        [sys.executable, "-m", "memory_tool.shadow", "recall-probe", *arguments],
        capture_output=True, text=True)


def test_cli_rejects_negative_probe_counts():
    result = run_cli("--recent", "-1", "--db", "/tmp/does-not-exist.db")
    assert result.returncode != 0
    assert "must not be negative" in result.stderr


def test_cli_rejects_an_unbounded_probe_total():
    result = run_cli("--recent", "30", "--control", "10", "--span", "10",
                     "--db", "/tmp/does-not-exist.db")
    assert result.returncode != 0
    assert "13 s per lookup" in result.stderr
