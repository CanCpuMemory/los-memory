"""
Deterministic embedding for semantic search.

Mirrors lsclaw's `memory/embedding.mjs` algorithm:
  - SHA-256 based token hashing → 32-dim vectors
  - Cosine similarity scoring
  - Keyword (Jaccard-style) scoring

No external API dependencies — entirely deterministic and reproducible.
"""

import hashlib
import math
import re
from typing import List

# Match lsclaw's tokenizer: /[^a-z0-9_\u4e00-\u9fa5]+/
_TOKEN_RE = re.compile(r"[^a-z0-9_\u4e00-\u9fa5]+", re.IGNORECASE)


def tokenize(text: str) -> List[str]:
    """Split text into normalized tokens (mirrors lsclaw tokenize)."""
    return [t.strip() for t in _TOKEN_RE.split(str(text or "").lower()) if t.strip()]


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
