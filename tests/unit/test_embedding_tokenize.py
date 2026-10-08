"""Tests for the deterministic tokenizer/embedding used by `--semantic`.

The CJK bigram behaviour is a bug fix with a measured effect, so it is pinned
here: `--semantic` scored Hit@1 = 0.033 on the real ledger because a whole CJK run
was a single token, so a 4-character window of that run shared no token with it.
"""
import math

from memory_tool.embedding import compute_embedding, cosine_similarity, tokenize


def test_cjk_run_emits_bigrams():
    tokens = tokenize("记忆双轨")
    assert tokens[0] == "记忆双轨"
    assert {"记忆", "忆双", "双轨"} <= set(tokens)


def test_ascii_words_are_not_bigrammed():
    assert tokenize("writeback contract") == ["writeback", "contract"]


def test_single_cjk_character_has_no_bigram():
    assert tokenize("记") == ["记"]


def test_mixed_text_keeps_ascii_and_bigrams_the_cjk_run():
    tokens = tokenize("M3 代理设置 update-sing-box-native")
    assert "m3" in tokens and "update" in tokens and "native" in tokens
    assert "代理" in tokens and "设置" in tokens


def test_a_window_of_a_longer_run_now_shares_tokens():
    """The regression this fix addresses."""
    document = tokenize("记忆双轨格局确立")
    query = tokenize("记忆双轨")
    assert set(query) & set(document), "a substring query shares no token with its own record"


def test_embedding_is_deterministic_and_normalised():
    first = compute_embedding("记忆双轨格局确立")
    second = compute_embedding("记忆双轨格局确立")
    assert first == second
    assert first != compute_embedding("完全不同的另一段文字内容")
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0, rel_tol=1e-9)


def test_empty_text_yields_a_zero_vector():
    assert compute_embedding("") == [0.0] * 32
    assert compute_embedding("   ") == [0.0] * 32
    assert cosine_similarity([0.0] * 32, [0.0] * 32) == 0.0


def test_vector_length_follows_the_requested_dimension():
    assert len(compute_embedding("记忆", dim=32)) == 32
    assert len(compute_embedding("记忆", dim=16)) == 16
