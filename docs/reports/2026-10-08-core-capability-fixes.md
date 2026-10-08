# Core capability fixes (2026-10-08)

Status: **implemented and verified**. Read-only investigation first; four fixes and one
measurement instrument landed. All state changes are inside this repository except one
quarantined artifact file described in §3.

Base revision `545f0e7` on `feature/nowledge-readiness`. Test suite: **744 passed**
(was 714). The exact CI Ruff command still exits 0; no new lint findings
(`memory_tool/shadow.py` 3 → 3, `memory_tool/share.py` 8 → 8, both pre-existing and
neither on the CI Ruff surface before this change).

Why now: the project's only previously "completed" capability (deterministic
`--semantic` search, commit `0ab9f9c`) had never been measured or touched again, and it
turned out to be a net negative. Three defects were live in daily use. The Nowledge-side
index freeze is a separate, upstream-owned problem
(`docs/reports/2026-10-07-shadow-14day-preclose.md` §8, upstream community#649, open) and
is explicitly out of scope here.

---

## 1. LIKE wildcard escaping (`memory_tool/operations.py`, `share.py`, `knowledge_base.py`)

**Defect.** `_run_search_like` built `f"%{query}%"` and interpolated it into
`title LIKE ? OR summary LIKE ? OR tags_text LIKE ? OR raw LIKE ?` with no `ESCAPE`
clause. SQLite's `%` and `_` are wildcards, so user text containing them over-matched.

**Measured on the real `shared` ledger (5,355 observations):**

| query | before | after | literal truth |
| --- | --- | --- | --- |
| `los_memory` | **895** rows | **0** | 0 |
| `%` | whole table | 0 | 0 |
| `_` | whole table | 0 | 0 |
| `记忆双轨` | 1 (correct) | 1 (correct) | 1 |

`_` matched the hyphen in `los-memory`; 31.5% (1,688/5,355) of the ledger contains an
underscore, and this corpus is full of filenames, identifiers and paths. The LIKE path is
the one Chinese queries actually use (FTS misses them), and it is reached from
`memory_tool/cli.py`, `memory_tool/client.py` and `memory_tool/viewer.py`, including the
external writeback contract.

**Fix.** `memory_tool/utils.py` gains `escape_like()` and `like_pattern()`; the SQL
statements carry the matching `ESCAPE '\'` clause. Backslash is escaped first so the
wildcard escapes are not double-escaped. All five user-input LIKE sites now use
`like_pattern()`: the four in `_run_search_like`, the tag filter in
`_append_clean_tag_filters`, `share._build_share_query`, and
`knowledge_base._search_rows_by_like` / the knowledge tag filter.

**Not fixed here, because the actual magnitude was unknown.** In `mode="auto"`, FTS runs
first and the FTS5 tokenizer splits `los_memory` into `los` + `memory`, so an identifier
query can over-match. That was carried as a "documented limitation". See the follow-up
section at the end of this report: sampling the real ledger showed the over-match is far
smaller than that one example suggested, and that the real defect in this area is
different.

## 2. `--semantic` was worse than useless on Chinese (`memory_tool/embedding.py`)

**Defect.** `tokenize()` kept a whole CJK run as one token (`记忆双轨格局确立` → one
token), so a 4-character window of that run shared no token with its own record, while
`dim=32` with `digest[i % 32]` is a random projection. Measured with the §4 instrument
(30 rarity-controlled cases, each query a literal window of the target record's own
summary, appearing in ≤2 records corpus-wide):

| path | Hit@1 | Hit@5 | Hit@10 | p50 |
| --- | --- | --- | --- | --- |
| `auto` (FTS/LIKE, reference) | 0.733 | **1.000** | 1.000 | 13 ms |
| `--semantic` before | **0.033** | 0.100 | 0.100 | 824 ms |
| `--semantic` after | 0.767 | 1.000 | 1.000 | 946 ms |

**Variant experiment** (same protocol; other dimensions rejected on data):

| variant | Hit@1 | Hit@5 | p50 |
| --- | --- | --- | --- |
| dim=32, no bigrams (= before) | 0.033 | 0.100 | 824 ms |
| **dim=32, CJK bigrams (= shipped)** | 0.700 | 0.900 | 1,186 ms |
| dim=256, no bigrams | 0.700 | 0.900 | 5,108 ms |
| dim=256, CJK bigrams | 0.733 | 0.900 | 6,740 ms |

The tokenizer bug is the whole effect; raising 32 → 256 buys +0.033 Hit@1 for 4–6× the
cost, so it was rejected.

**Fix.** `tokenize()` now also emits 2-character windows for runs of ≥2 Han characters,
giving Chinese the partial-overlap behaviour ASCII already had. Blast radius is the
`--semantic` path only (`compute_embedding` and `keyword_score`).

**What is NOT claimed.** `--semantic` still does not beat the default path. Across three
seeds at 40 cases each on the real ledger: default Hit@1 0.725/0.625/0.700 and Hit@5
0.975/1.000/1.000; `--semantic` Hit@1 0.725/0.675/0.575 and Hit@5 0.925/0.925/0.875. It
trails Hit@5 by ~8 points at ~70× the latency. This is a hashed bag-of-words: it can
reward shared tokens, never a paraphrase. `--mode` still defaults to `auto`, and
`tests/unit/test_core_search_quality.py` now fails if that default changes.

**Migration cost: zero.** 0 of 5,355 records store `metadata.embedding`, so no stored
vector is in the old space. The embedding module docstring and the measurement are the
guard against re-claiming "semantic".

## 3. Compare ledger wrote into the operator's production state directory (`memory_tool/shadow.py`)

**Defect.** `compare_paths()` resolved `DEFAULT_DB.parent` — the *process's* default
directory — regardless of which database the caller was serving. The CLI passed
`state_dir` explicitly and was correct; the MCP path called
`record_compare(conn, query, limit=...)` with no `state_dir`, so **every run of
`tests/unit/test_shadow.py::test_mcp_compare_queues_and_says_answer_from_the_primary`
appended an entry to the production directory of whatever machine ran pytest.**

**Evidence.** `~/.local/share/los-memory-shadow/compare-pending.jsonl` held exactly 10
entries, all `{query: "冷层 热数据", shadow_ids: ["a"], limit: 10}` — the fixture's
signature (`indexed_record("a", ...)`) — timestamped 2026-10-07 13:39–13:46 and
2026-10-08 20:58, matching the number of test runs. There is no drain job on this host, so
they would never have been consumed.

**Fix.** `compare_paths(state_dir=None, conn=None)` resolves the directory from the
connection's own database via `PRAGMA database_list` (positional indexing, so a
connection without a `row_factory` still works), falling back to the historical default
only for in-memory databases. `record_compare` passes its connection through.

**Verification.** Production file byte count is unchanged (2,088 → 2,088) across a full
test run. The 10 artifacts were **quarantined, not deleted**, to
`~/.local/share/los-memory-shadow/compare-pending.jsonl.test-artifacts-20261008`
(outside this repository).

## 4. Deleted records were re-probed on every rotation (`memory_tool/shadow.py`)

**Defect.** The rotation candidate set was `manifest | every record`, including
`active=0` rows, so a 404'd record was re-fetched forever and each 404 incremented
`missing` — the metric the operation report reads as deletion activity. The live mirror
holds 12 inactive rows, and the report showed `missing` 0 → 33 (10-07) → 116 (10-08),
consistent with continuous re-probing rather than a wave of deletions.

**Fix.** Tombstones are candidates only once their last attempt is older than
`TOMBSTONE_RECHECK_SECONDS` (24 h); deletions are still discovered by the manifest, so
this is only a safety net for an upstream restore. The run report gains
`tombstones_deferred` so the deferral is visible rather than implicit.

**Rollback knob.** `TOMBSTONE_RECHECK_SECONDS = 0` restores the previous behaviour
exactly, because every stored attempt timestamp is in the past.

## 5. Core retrieval measurement instrument (`scripts/measure_core_search.py`)

New, and the reason the above could be decided on data rather than opinion. It samples
real observations, cuts a short CJK window from each record's own summary, and reports
Hit@K plus p50/p95 per path.

Two guards keep it trustworthy:

- **Rarity control.** Windows appearing in more than `--max-corpus-hits` records are
  discarded, so a low rank measures the ranker and not corpus ambiguity. Without it the
  first run reported the default path at Hit@1 0.300 purely because `title[:4]` was
  `code` for many records.
- **`--self-check`**, now a CI step: builds a throwaway database with known answers and
  asserts the harness scores a correct ranker at Hit@1 1.0 and a deliberately broken one
  at 0.0. The instrument is validated before its numbers are trusted. It needs no private
  corpus, so it runs in CI; the real measurement is an operator action against the
  private ledger.

`--min-hit1` / `--min-hit5` make it usable as a gate. The database is opened
`mode=ro`; it writes nothing.

## 6. De-scoped, with reasons

| Item | Why not done |
| --- | --- |
| Renaming `status.unresolved_errors` | Verified **accurate**: it counts records whose latest attempt failed, and it self-clears on the next success (measured 1 → 0). Renaming a structured-output field would break the stability rule for no benefit. |
| Alert delivery channel | `scripts/shadow_report.py alert` records to `alerts.jsonl` and exits 1, but there is no push channel. Choosing one (IM vs webhook vs mail) is a user decision, not a code decision — and a threshold that fires hourly with nowhere to go is still worth fixing, so this stays on the list. |
| Row/`revisions` retention policy | Needs a retention decision (how long a superseded snapshot is worth keeping); `revisions` was 3,867 and grows only on content change (~21 revisions per 11 days), so it is slow growth, not an incident. |
| `--semantic` O(N) full scan (946 ms) | Restricting ranking to literal candidates is a design change that turns it into a re-ranker; it should be measured with §5 before landing, not bundled with a bug fix. |
| Primary recall canary | Proposed (use the mirror's frozen-era records as read-only anchors to detect "index reports Ready but recent content is unsearchable"). Now the highest-value unimplemented item, since the current alert `index_not_ready` keys off `search_index.state == "ready"` and therefore cannot fire for this failure mode. |

## 7. Rollback

Every change is isolated and independent:

| Change | Rollback |
| --- | --- |
| LIKE escaping | Revert `memory_tool/utils.py` helpers and the five call sites. |
| CJK bigrams | Revert `tokenize()` to the plain split; no stored data is affected (0 stored embeddings). |
| Compare ledger location | Passing `state_dir` explicitly still overrides, so old behaviour is one argument away. |
| Tombstone recheck | `TOMBSTONE_RECHECK_SECONDS = 0`. |
| Instrument / CI step | Remove the CI step; the script is additive. |

## 8. Reproduce

```sh
# suite (744 tests)
.venv/bin/python -m pytest -q

# CI's Ruff surface, including the files this change added
ruff check --select F,B memory_tool/operations.py scripts/measure_core_search.py \
  tests/unit/test_core_search_quality.py tests/unit/test_embedding_tokenize.py \
  tests/unit/test_search_like_escaping.py

# instrument self-check (also a CI step)
python3 scripts/measure_core_search.py --self-check

# real measurement (read-only against the private ledger)
python3 scripts/measure_core_search.py --samples 40 --seed 7
python3 scripts/measure_core_search.py --samples 30 --json

# the LIKE regression, end to end
python3 -m memory_tool --profile shared memory search "los_memory" --mode like --limit 500
```

---

## 9. Follow-up (2026-10-08, later): the identifier claim was overstated; a different defect was real

§1 left "identifier queries over-match in `mode="auto"`" as a known limitation, on the
strength of `memory search "los_memory"` returning 500 rows. Sampling the real ledger
showed that example is a **tail case**, not the norm. Queries were built from
**identifiers that occur in exactly one record** (1,695 available), so the answer is
unambiguous, and grouped by which separator they carry — because the separator is what
decides how FTS5 parses the query.

Within-snapshot A/B, 80 queries per group (the ledger is live, so cross-run comparison is
not valid — an earlier attempt produced two different `like` baselines for an unmodified
path and was discarded):

| group | path | Hit@1 | mean rows | p50 | p95 |
| --- | --- | --- | --- | --- | --- |
| underscore | before | 0.775 | 2.6 | 0.2 ms | **7.7 ms** |
| underscore | after | 0.812 | 2.3 | 0.2 ms | **0.5 ms** |
| other separators | before | 0.887 | 1.9 | **7.6 ms** | 9.1 ms |
| other separators | after | 0.863 | 1.6 | **0.1 ms** | **0.4 ms** |

**The real defect.** FTS5's query syntax gives `-`, `/`, `.` and `:` operator meaning, so
those queries raised `sqlite3.OperationalError`. `mode="auto"` swallowed it and silently
fell through to a **full-table LIKE scan** — 100% of identifier queries in that group, at
~20x the indexed latency. Under an explicit `--mode fts` the same query surfaced a raw
SQLite error to the user:

```
$ los-memory memory search "los-memory" --mode fts
{"ok": false, "error": "SQLite error: no such column: memory",
 "suggestion": "Run 'los-memory admin diagnose' ..."}
```

**Fix.** `_fts_query_candidates()` retries a single-term query as a quoted phrase, which
FTS5 matches as adjacent tokens: it stays indexed and is separator-agnostic (`foo/bar`
also finds `foo-bar` — verified). Multi-word queries are deliberately **not** quoted,
because that would turn FTS5's implicit AND into an adjacency requirement — a different
decision, not one to make implicitly. `mode="fts"` still raises when *every* form fails
(`a AND`), preserving the old contract. 12 tests in
`tests/unit/test_fts_query_fallback.py`.

**What is not claimed.** The ranking effect is **mixed and inside noise** at n=80: one
group gained 3.7 points of Hit@1, the other lost 2.4. Do not read this as a ranking
improvement. It is a latency and error-handling fix; the ranking question — should literal
containment outrank a separator-variant token match — is still open and needs a larger
frozen set.

**Instrument.** `scripts/measure_core_search.py` gained an `identifier` family
(`--family cjk|identifier|both`) precisely because the existing CJK-window family cannot
see FTS-parsing changes. Measured on the shared ledger (30 cases each):

| family | path | Hit@1 | Hit@5 | p50 |
| --- | --- | --- | --- | --- |
| cjk | auto | 0.733 | 1.000 | 13.4 ms |
| cjk | semantic | 0.767 | 1.000 | 933.9 ms |
| identifier | auto | 0.767 | 0.900 | **0.4 ms** |
| identifier | semantic | 0.233 | 0.367 | 950.1 ms |
