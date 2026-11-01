"""字面 + 语义的融合（B2 步2 的旁路入口）。

## 融合的第一条不变式

**语义路不得让字面类的召回下降。** 机械形态：字面结果按**原序全部保留**，
语义候选只填充剩余空位。这样"字面类 query 的 recall@k"与关掉语义时**恒等**，
不需要靠统计去证明——它就是结构性质。

反过来做（把两路分数归一化后混排）会引入一个不可复现的权重，并且在
"字面精确命中"的用例上把正确答案挤下去。B0 报告里字面类 shadow 本来就强，
这条不变式保护的就是那块优势。

## 第二条：语义不可用时仍返回字面结果

`meta["semantic"]` 是三态之一：
- `{"available": True, ...}` —— 语义路跑通（可能 0 命中）
- `{"available": False, "reason": ..., "detail": ...}` —— 不可用，**原因必给**
- 字段缺失 —— 表示调用方没请求语义路（纯字面）

## 第三条：语义结果也要能定位到原记录

语义只负责"哪些 source_id 相关"，最终结果一律用 shadow 库的 `envelope()` 取，
保证与字面路的返回形状完全一致（含 `memory`、`digest`、`verified_at`）。
"""

from __future__ import annotations

import os

from .encoder import Encoder, load_onnx_encoder
from .errors import SemanticUnavailable
from .index import VectorIndex

MODEL_DIR_ENV = "LOS_MEMORY_SEMANTIC_MODEL_DIR"
INDEX_PATH_ENV = "LOS_MEMORY_SEMANTIC_INDEX"


def resolve_paths(index_path: str | None = None, model_dir: str | None = None) -> tuple[str | None, str | None]:
    """索引与模型路径的优先级：显式参数 > 环境变量。两者都缺时由调用方决定兜底。"""
    return (
        index_path or os.environ.get(INDEX_PATH_ENV) or None,
        model_dir or os.environ.get(MODEL_DIR_ENV) or None,
    )


def _default_encoder(model_dir: str | None) -> Encoder:
    if not model_dir:
        raise SemanticUnavailable(
            "model_missing",
            f"未配置模型目录（设 {MODEL_DIR_ENV} 或传 model_dir）；语义路按设计不可用",
        )
    return load_onnx_encoder(model_dir)


def search_hybrid(
    conn,
    query: str,
    limit: int = 10,
    *,
    project=None,
    kind=None,
    space: str = "default",
    index_path: str | None = None,
    model_dir: str | None = None,
    encoder: Encoder | None = None,
    expect_generation: str | None = None,
) -> tuple[list[dict], dict]:
    """返回 `(results, meta)`。`meta["semantic"]` 见模块文档的三态。

    Args:
        conn: shadow 库连接（用于字面检索与结果取信）。
        encoder: 便于测试注入；生产用 `model_dir` 加载 ONNX 编码器。
    """
    # 延迟导入：semantic 包不能成为 shadow 模块的 import 期依赖
    from ..shadow import get, search_detailed  # noqa: PLC0415

    lexical, meta = search_detailed(conn, query, limit=limit, project=project, kind=kind, space=space)
    results = [{**item, "match_source": "lexical"} for item in lexical]

    resolved_index, resolved_model = resolve_paths(index_path, model_dir)
    semantic_meta: dict = {"available": True, "filled": 0, "index_generation": None}
    # 空白 query 不在这里特判：字面路自己会抛 ValueError（"query must contain 1..500
    # characters"），语义路跟着冒泡即可。自造一套"空 query 返回空"的语义会与
    # shadow.search 的行为分叉，而分叉就是下一个假判据的来源。
    try:
        if resolved_index is None:
            raise SemanticUnavailable("index_missing", f"未配置索引路径（设 {INDEX_PATH_ENV} 或传 index_path）")
        index = VectorIndex.open(resolved_index, expect_generation=expect_generation)
        semantic_meta["index_generation"] = index.manifest.get("generation")
        active_encoder = encoder or _default_encoder(resolved_model)
        query_vec = active_encoder.encode_query(query)
        hits = index.search(conn, query_vec, limit=limit, space=space)
        seen = {item["source_id"] for item in results}
        for hit in hits:
            if len(results) >= limit:
                break
            if hit["source_id"] in seen:
                continue  # 字面已命中的不重复占位（保持字面排序）
            envelope = get(conn, hit["source_id"], space=space)
            if envelope is None:
                continue  # 竞态：刚被删
            results.append({**envelope, "match_source": "semantic", "semantic_score": hit["score"]})
            seen.add(hit["source_id"])
            semantic_meta["filled"] += 1
    except SemanticUnavailable as exc:
        semantic_meta = exc.as_meta()
        semantic_meta["filled"] = 0
        semantic_meta["index_generation"] = None
    except Exception as exc:  # 编码器炸了也不能拖垮整次查询
        failure = SemanticUnavailable("encode_failed", f"{type(exc).__name__}: {exc}")
        semantic_meta = failure.as_meta()
        semantic_meta["filled"] = 0
        semantic_meta["index_generation"] = None

    meta["semantic"] = semantic_meta
    return results[:limit], meta
