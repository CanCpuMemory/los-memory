#!/usr/bin/env python3
"""Model-selection spike for B2 (semantic recall): encode the shadow corpus with
each candidate ONNX model and score the *semantic* categories of the 40-case set.

Read-only. Nothing here becomes production code; the point is to answer two
questions with numbers:
  1. does a small in-process model actually retrieve the paraphrase cases the
     primary store misses (B0: chinese_short 0/5, exact_identifier 1/5)?
  2. which candidate is worth wiring — and what does it cost (index time, RSS,
     model bytes)?

Categories are separated on purpose: `exact_identifier`/`chinese_short` are
literal queries (semantic embeddings are not expected to win those; the hybrid
design keeps FTS as the literal path), while `semantic_paraphrase` /
`relational_multihop` / `cross_tool_continuation` / `fact_revision` are the ones
a bi-encoder is supposed to help with.
"""

from __future__ import annotations

import argparse
import json
import resource
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

SHADOW = Path.home() / ".local/share/los-memory-shadow"
DB = SHADOW / "eval/snap.sqlite3"
CASES = SHADOW / "eval/cases.jsonl"

SEMANTIC_CATS = {"semantic_paraphrase", "relational_multihop", "cross_tool_continuation",
                 "fact_revision"}
LITERAL_CATS = {"exact_identifier", "chinese_short"}
MAX_CHARS = 1200
BATCH = 32


class Embedder:
    def __init__(self, onnx_path: Path, tokenizer_path: Path, *, query_prefix: str,
                 max_len: int = 256):
        self.session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=max_len)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.query_prefix = query_prefix
        self.input_names = {i.name for i in self.session.get_inputs()}

    def encode(self, texts: list[str]) -> np.ndarray:
        enc = self.tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feed["token_type_ids"] = np.zeros_like(ids)
        out = self.session.run(None, feed)[0]          # [B, T, H]
        m = mask[..., None].astype(np.float32)
        pooled = (out * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-6, None)
        return pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        return self.encode([self.query_prefix + t for t in texts])


def load_records(path: Path, mode: str = "title_body") -> tuple[list[str], list[str], list[str]]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT source_id, snapshot FROM records WHERE space='default' AND active=1").fetchall()
    ids, texts = [], []
    for row in rows:
        snap = json.loads(row["snapshot"])
        meta = snap.get("metadata") or {}
        title = snap.get("title") or ""
        content = snap.get("content") or snap.get("summary") or ""
        if not content:
            content = json.dumps(meta, ensure_ascii=False)[:MAX_CHARS]
        ids.append(row["source_id"])
        if mode == "title_only":
            texts.append(title)
        elif mode == "title_repeat":
            texts.append((title + "\n" + title + "\n" + content)[:MAX_CHARS])
        else:
            texts.append((title + "\n" + content)[:MAX_CHARS])
    conn.close()
    return ids, texts, [t for t in texts]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="label for the report")
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--tokenizer", required=True)
    ap.add_argument("--query-prefix", default="")
    ap.add_argument("--doc-prefix", default="")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS)
    ap.add_argument("--chunk-size", type=int, default=0, help="0 = one vector per record")
    ap.add_argument("--chunk-overlap", type=int, default=40)
    ap.add_argument("--text-mode", default="title_body",
                    choices=["title_body", "title_only", "title_repeat"])
    args = ap.parse_args()

    ids, texts, _ = load_records(DB, args.text_mode)
    texts = [args.doc_prefix + t[:args.max_chars] for t in texts]
    cases = [json.loads(line) for line in open(CASES)]
    t0 = time.time()
    emb = Embedder(Path(args.onnx), Path(args.tokenizer), query_prefix=args.query_prefix)
    load_s = time.time() - t0

    t0 = time.time()
    if args.chunk_size:
        # Chunked index: a record is scored by its best chunk. Long Chinese
        # records bury the one sentence that answers an abstract question, so the
        # per-record mean vector dilutes exactly the signal we are looking for.
        chunk_texts, owner = [], []
        for idx, text in enumerate(texts):
            start = 0
            if len(text) <= args.chunk_size:
                chunk_texts.append(text)
                owner.append(idx)
                continue
            step = max(args.chunk_size - args.chunk_overlap, 1)
            while start < len(text):
                piece = text[start:start + args.chunk_size]
                if piece.strip():
                    chunk_texts.append(piece)
                    owner.append(idx)
                if start + args.chunk_size >= len(text):
                    break
                start += step
        vectors = []
        for start in range(0, len(chunk_texts), BATCH):
            vectors.append(emb.encode(chunk_texts[start:start + BATCH]))
        chunk_matrix = np.vstack(vectors)
        owner_arr = np.array(owner)
        index_s = time.time() - t0
    else:
        chunk_matrix = None
        owner_arr = None
        vectors = []
        for start in range(0, len(texts), BATCH):
            vectors.append(emb.encode(texts[start:start + BATCH]))
        matrix = np.vstack(vectors)
        index_s = time.time() - t0

    t0 = time.time()
    queries = [c["query"] for c in cases]
    qvec = emb.encode_queries(queries)
    query_s = (time.time() - t0) / max(len(queries), 1)

    if chunk_matrix is not None:
        chunk_scores = qvec @ chunk_matrix.T          # [Q, C]
        # max over the chunks of each record
        order = np.argsort(owner_arr, kind="stable")
        sorted_owner = owner_arr[order]
        bounds = np.searchsorted(sorted_owner, np.arange(len(ids)))
        bounds = np.append(bounds, len(sorted_owner))
        scores = np.full((len(cases), len(ids)), -1.0, dtype=np.float32)
        for rec in range(len(ids)):
            lo, hi = bounds[rec], bounds[rec + 1]
            if hi > lo:
                scores[:, rec] = chunk_scores[:, order[lo:hi]].max(axis=1)
    else:
        scores = qvec @ matrix.T

    def evaluate(cats: set[str], depth: int = 5) -> dict:
        per_cat: dict[str, list[float]] = {}
        for case, row in zip(cases, scores):
            if case["category"] not in cats:
                continue
            top = np.argsort(-row)[:depth]
            got = {ids[i] for i in top}
            expected = [e["source_id"] for e in case["expected"]]
            if not expected:
                continue
            per_cat.setdefault(case["category"], []).append(
                len(got & set(expected)) / len(expected))
        flat = [v for vals in per_cat.values() for v in vals]
        return {
            "recall@5_by_category": {k: round(sum(v) / len(v), 3) for k, v in per_cat.items()},
            "recall@5_overall": round(sum(flat) / len(flat), 3) if flat else None,
            "cases": len(flat),
        }

    def rank_metrics(cats: set[str]) -> dict:
        """MRR@10 and recall@20 over the expected ids.

        Binary recall@5 on 5 cases moves in steps of 0.2, which cannot separate two
        models. Ranks can: a case that went 18 -> 3 and one that went 3 -> 18 both
        read as "miss" at depth 5.
        """
        rr, hit20, n = [], 0, 0
        for case, row in zip(cases, scores):
            if case["category"] not in cats:
                continue
            expected = [e["source_id"] for e in case["expected"]]
            if not expected:
                continue
            ranks = rank_of(case, row)
            n += 1
            valid = [r for r in ranks if r > 0]
            if not valid:
                rr.append(0.0)
                continue
            best = min(valid)
            rr.append(1.0 / best if best <= 10 else 0.0)
            hit20 += int(best <= 20)
        return {"mrr@10": round(sum(rr) / n, 3) if n else None,
                "best_rank@20": round(hit20 / n, 3) if n else None, "cases": n}

    def rank_of(case, row):
        expected = [e["source_id"] for e in case["expected"]]
        order = list(np.argsort(-row))
        ranks = []
        for eid in expected:
            ranks.append(order.index(ids.index(eid)) + 1 if eid in ids else -1)
        return ranks

    detail = []
    for case, row in zip(cases, scores):
        if case["category"] in SEMANTIC_CATS:
            detail.append({"id": case["id"], "cat": case["category"],
                           "ranks": rank_of(case, row)})

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    try:
        import os
        steady = int(open(f"/proc/{os.getpid()}/status").read().split("VmRSS:")[1].split()[0]) // 1024
    except Exception:
        import subprocess
        steady = int(subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                                    capture_output=True, text=True).stdout.strip() or 0) // 1024
    print(json.dumps({
        "model": args.model,
        "model_bytes": Path(args.onnx).stat().st_size,
        "records": len(ids),
        "load_s": round(load_s, 2),
        "index_s": round(index_s, 1),
        "index_ms_per_record": round(index_s * 1000 / len(ids), 1),
        "query_ms": round(query_s * 1000, 1),
        "peak_rss_mib": round(rss, 1),
        "steady_rss_mib": steady,
        "max_chars": args.max_chars,
        "text_mode": args.text_mode,
        "chunk_size": args.chunk_size,
        "chunk_count": int(len(owner_arr)) if owner_arr is not None else len(ids),
        "semantic": evaluate(SEMANTIC_CATS),
        "literal_reference": evaluate(LITERAL_CATS),
        "semantic_rank": rank_metrics(SEMANTIC_CATS),
        "paraphrase_rank": rank_metrics({"semantic_paraphrase"}),
        "ranking_detail": detail,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
