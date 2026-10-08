"""
Deterministic embedding for search re-ranking.

Mirrors lsclaw's `memory/embedding.mjs` algorithm:
  - SHA-256 based token hashing → 32-dim vectors
  - Cosine similarity scoring
  - Keyword (Jaccard-style) scoring

No external API dependencies — entirely deterministic and reproducible.

This is a hashed bag-of-words, NOT a learned embedding: it can only reward shared
tokens, never a paraphrase. Measured on 5,355 real records with a rarity-controlled
instrument (`scripts/measure_core_search.py`), the best configuration it can reach
ties the default FTS/LIKE path on Hit@1 and loses on Hit@5 while costing roughly
100x the latency. Treat `--semantic` as a lexical-overlap re-ranker, and re-run that
instrument before making any claim about it.
"""

import hashlib
import math
import re
from typing import List

# Match lsclaw's tokenizer: /[^a-z0-9_\u4e00-\u9fa5]+/
_TOKEN_RE = re.compile(r"[^a-z0-9_\u4e00-\u9fa5]+", re.IGNORECASE)

# CJK runs are not whitespace-separated, so this tokenizer keeps a whole run
# ("记忆双轨格局确立") as a single token. A query for a 4-character window of that
# run then shares no token with it at all, and the score collapses to noise:
# `--semantic` scored Hit@1 = 0.033 against the default path's 0.733 on the real
# ledger. Emitting 2-character windows ("bigrams") as extra tokens gives Chinese
# the same partial-overlap behaviour the tokenizer already gives ASCII words,
# which lifted Hit@1 to 0.700 in the same measurement. Dimensionality was also
# tested and rejected: raising 32 → 256 bought +0.033 Hit@1 for 4x the cost.
_CJK_ONLY = re.compile(r"^[\u4e00-\u9fa5]+$")


def tokenize(text: str) -> List[str]:
    """Split text into normalized tokens, with CJK bigrams for partial overlap.

    ASCII is already whitespace/punctuation separated, so its words overlap by
    token. CJK runs are not, so each run of two or more Han characters also
    contributes its 2-character windows. Deterministic and reproducible.
    """
    tokens = [t.strip() for t in _TOKEN_RE.split(str(text or "").lower()) if t.strip()]
    bigrams = []
    for token in tokens:
        if len(token) > 1 and _CJK_ONLY.match(token):
            bigrams.extend(token[index:index + 2] for index in range(len(token) - 1))
    return tokens + bigrams


def compute_embedding(text: str, dim: int = 32) -> List[float]:
    """Compute a deterministic embedding vector for text.

    Algorithm (SHA-256 based, no external API):
      1. Tokenize
      2. For each token, compute SHA-256 digest
      3. For each dimension i (0..dim-1), accumulate digest[i % 32] / 255
      4. L2-normalize

    Returns a list of `dim` floats, or a zero-vector if text is empty.
    """
    tokens = tokenize(text)
    vec = [0.0] * dim
    if not tokens:
        return vec

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for i in range(dim):
            vec[i] += digest[i % len(digest)] / 255.0

    # L2 normalization
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    length = min(len(a), len(b))
    if length == 0:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for i in range(length):
        dot += a[i] * b[i]
        norm_a += a[i] * a[i]
        norm_b += b[i] * b[i]
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def keyword_score(query_tokens: List[str], content_tokens: List[str]) -> float:
    """Jaccard-style keyword match score.

    Returns: |matching tokens| / |query tokens|
    """
    if not query_tokens or not content_tokens:
        return 0.0
    content_set = set(content_tokens)
    matched = sum(1 for t in query_tokens if t in content_set)
    return matched / len(query_tokens)


def text_for_embedding(title: str, summary: str) -> str:
    """Combine title and summary for embedding (mirrors lsclaw createTextForEmbedding)."""
    from .utils import normalize_text

    return normalize_text(title) + "\n" + normalize_text(summary)
