"""B2 步2 语义旁路的**机制门禁**（不是质量门禁）。

范围与边界（必须先读，否则会误以为语义质量已经验收）：

- 这里验证的是**机制**：索引构建、generation 内容寻址、查询期与库对账、
  不可用三态、融合不变式。用 `HashingEncoder`（确定性字符 n-gram，**不是语义模型**），
  所以任何一条"命中"只说明链路通，**不说明语义质量**。
- **质量门禁**（语义类 MRR@10 ≥ 0.7、best-rank@20 ≥ 0.85、改写 recall@5 ≥ 0.8）
  需要真模型 + 私有语料，跑法见 SKIPPED 段末尾的说明；未跑就是未验收，不许写成通过。

每条负向控制都在注释里写清"如果这条失效，会出现什么症状"。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from memory_tool import shadow
from memory_tool.semantic import (
    HashingEncoder,
    SemanticUnavailable,
    VectorIndex,
    assemble_text,
    compute_generation,
    normalize,
    search_hybrid,
)
from memory_tool.semantic.encoder import TEXT_BODY_CHARS


# ── 夹具 ────────────────────────────────────────────────────────────
def _record(rid: str, title: str, content: str, *, active: bool = True) -> dict:
    return {
        "id": rid,
        "space_id": "default",
        "title": title,
        "content": content,
        "lifecycle_state": "active" if active else "deprecated",
        "is_latest": True,
    }


@pytest.fixture()
def conn(tmp_path):
    connection = shadow.connect(str(tmp_path / "shadow.sqlite3"))
    yield connection
    connection.close()


@pytest.fixture()
def seeded(conn):
    shadow.put(conn, "default", _record("m1", "forgejo SSH 端口是 3022", "forgejo 的 git ssh 走 3022 端口，HTTP 走 3022 的 api"))
    shadow.put(conn, "default", _record("m2", "restic 仓库位于 Synology", "restic 仓库路径 /volume1/restic，口令在纸卡与 Vaultwarden"))
    shadow.put(conn, "default", _record("m3", "记忆生命周期三个标记", "slot / supersedes / deprecated 三个头部标记的语义"))
    return conn


def _build(seeded, tmp_path, **kwargs):
    return VectorIndex.build(
        str(tmp_path / "semantic.sqlite3"),
        conn=seeded,
        encoder=HashingEncoder(),
        **kwargs,
    )


# ── 规则守卫 ────────────────────────────────────────────────────────
def test_assemble_text_把标题重复两次并截断正文():
    long_body = "x" * (TEXT_BODY_CHARS + 50)
    text = assemble_text("标题", long_body)
    assert text.count("标题") == 2, "title 必须重复两次（B2 §3 的实测结论）"
    assert len(text) < TEXT_BODY_CHARS + len("标题") * 2 + 8, "正文必须被截断"
    assert assemble_text(None, None) == ""


def test_normalize_零向量不炸且保持零():
    assert normalize([0.0, 0.0]) == [0.0, 0.0]
    unit = normalize([3.0, 4.0])
    assert abs(sum(v * v for v in unit) - 1.0) < 1e-9


def test_generation_是内容寻址():
    entries = [("a", "d1"), ("b", "d2")]
    base = compute_generation(model="m", model_sha256="s", dim=8, text_mode="t", entries=entries)
    assert base == compute_generation(model="m", model_sha256="s", dim=8, text_mode="t", entries=entries)
    # 记录顺序无关
    assert base == compute_generation(model="m", model_sha256="s", dim=8, text_mode="t", entries=list(reversed(entries)))
    # 任何一项变了都必须变
    assert base != compute_generation(model="m", model_sha256="s", dim=8, text_mode="t", entries=[("a", "d1"), ("b", "dX")])
    assert base != compute_generation(model="m2", model_sha256="s", dim=8, text_mode="t", entries=entries)
    assert base != compute_generation(model="m", model_sha256="s", dim=8, text_mode="t2", entries=entries)


# ── 正向：链路通 ─────────────────────────────────────────────────────
def test_索引构建后可检索且命中相关记录(seeded, tmp_path):
    index = _build(seeded, tmp_path)
    try:
        assert index.stats()["vectors"] == 3
        qv = HashingEncoder().encode_query("forgejo 端口")
        hits = index.search(seeded, qv, limit=3)
        assert hits, "同语料下应有命中"
        assert hits[0]["source_id"] == "m1", "标题含 forgejo/端口 的那条应排第一"
        assert hits[0]["match_source"] == "semantic"
    finally:
        index.close()


def test_零向量查询返回空(seeded, tmp_path):
    index = _build(seeded, tmp_path)
    try:
        assert index.search(seeded, [0.0] * index.manifest["dim"], limit=3) == []
    finally:
        index.close()


def test_空白查询与字面路行为一致_都拒绝且不自造第二套语义(seeded, tmp_path):
    """失效症状：语义路自己发明一套"空 query 返回空"，与 shadow.search 分叉。"""
    with pytest.raises(ValueError):
        search_hybrid(
            seeded, "   ", limit=3,
            index_path=str(tmp_path / "semantic.sqlite3"),
            encoder=HashingEncoder(),
        )


# ── 负向控制（每条都对应一种真实故障形态）────────────────────────────
def test_负控_索引缺失时不可用但字面结果仍在(seeded, tmp_path):
    """失效症状：语义不可用被静默当成"没有结果"，用户以为库里没这条。"""
    results, meta = search_hybrid(
        seeded, "forgejo 端口", limit=3,
        index_path=str(tmp_path / "does-not-exist.sqlite3"),
        encoder=HashingEncoder(),
    )
    assert meta["semantic"]["available"] is False
    assert meta["semantic"]["reason"] == "index_missing"
    assert results, "语义不可用时仍必须返回字面结果"
    assert all(item["match_source"] == "lexical" for item in results)


def test_负控_generation_不符判过期(seeded, tmp_path):
    """失效症状：换了模型/改了记录却继续用旧索引，召回悄悄说谎。"""
    index = _build(seeded, tmp_path)
    index.close()
    with pytest.raises(SemanticUnavailable) as excinfo:
        VectorIndex.open(str(tmp_path / "semantic.sqlite3"), expect_generation="deadbeef")
    assert excinfo.value.reason == "index_stale"


def test_负控_已删记录不得召回(seeded, tmp_path):
    """失效症状：用户删掉的记忆还能被搜出来（隐私与正确性双输）。"""
    index = _build(seeded, tmp_path)
    try:
        shadow.mark_missing(seeded, "default", "m1")
        hits = index.search(seeded, HashingEncoder().encode_query("forgejo 端口"), limit=3)
        assert all(hit["source_id"] != "m1" for hit in hits), "mark_missing 之后不得召回"
    finally:
        index.close()


def test_负控_记录被改过则其向量作废(seeded, tmp_path):
    """失效症状：条目已更新，语义仍按旧文本召回（且分数与新内容无关）。"""
    index = _build(seeded, tmp_path)
    try:
        shadow.put(seeded, "default", _record("m1", "forgejo SSH 端口已改为 3222", "端口变了"))
        hits = index.search(seeded, HashingEncoder().encode_query("forgejo 端口"), limit=3)
        assert all(hit["source_id"] != "m1" for hit in hits), "digest 变了的记录必须被跳过"
    finally:
        index.close()


def test_负控_过期候选超过阈值判不可用而不是给残缺结果(seeded, tmp_path):
    """失效症状：索引大面积过期，返回一两条"还能用"的，读起来像正常结果。"""
    index = _build(seeded, tmp_path)
    try:
        for rid in ("m1", "m2", "m3"):
            shadow.put(seeded, "default", _record(rid, f"{rid} 已改", "全部换掉"))
        with pytest.raises(SemanticUnavailable) as excinfo:
            index.search(seeded, HashingEncoder().encode_query("forgejo 端口"), limit=3)
        assert excinfo.value.reason == "index_stale"
    finally:
        index.close()


def test_负控_维度不符判索引损坏(seeded, tmp_path):
    """失效症状：换了个维度的模型却复用旧索引，点积算出来的分数毫无意义。"""
    index = _build(seeded, tmp_path)
    try:
        with pytest.raises(SemanticUnavailable) as excinfo:
            index.search(seeded, [0.1] * 7, limit=3)
        assert excinfo.value.reason == "index_corrupt"
    finally:
        index.close()


# ── 融合不变式 ───────────────────────────────────────────────────────
def test_融合_字面结果原序保留_语义只填空位(seeded, tmp_path):
    """失效症状：混排把字面精确命中挤下去 —— B0 里字面类的优势就没了。"""
    index = _build(seeded, tmp_path)
    try:
        lexical, _ = shadow.search_detailed(seeded, "forgejo", limit=2)
        results, meta = search_hybrid(
            seeded, "forgejo", limit=5,
            index_path=str(tmp_path / "semantic.sqlite3"),
            encoder=HashingEncoder(),
        )
        assert meta["semantic"]["available"] is True
        assert [item["source_id"] for item in results[: len(lexical)]] == [item["source_id"] for item in lexical], \
            "字面命中必须原序保留"
        assert all(item["match_source"] == "lexical" for item in results[: len(lexical)])
        # 语义只补空位：总条数不超过 limit，且补充项不与字面项重复
        assert len(results) <= 5
        assert len({item["source_id"] for item in results}) == len(results)
    finally:
        index.close()


def test_融合_语义可用但零命中时available为真(seeded, tmp_path):
    """失效症状：把"没命中"报成"不可用"，运维会去修一个没坏的东西。"""
    index = _build(seeded, tmp_path)
    try:
        results, meta = search_hybrid(
            seeded, "zzzzz-完全不存在的词-ξ", limit=3,
            index_path=str(tmp_path / "semantic.sqlite3"),
            encoder=HashingEncoder(),
        )
        assert meta["semantic"]["available"] is True
        # 不断言 filled==0：HashingEncoder 是字符 n-gram，任意两条中文都可能有权重重叠；
        # 这里只要求"可用且形状正确"。真正的零命中判据属于质量门禁（真模型 + 私有语料）。
        assert "filled" in meta["semantic"]
    finally:
        index.close()
