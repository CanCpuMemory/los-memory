"""语义召回旁路的失败语义（B2 步2）。

设计纪律（来自 B2 报告与仓库的通用纪律，别改）：

1. **不可用必须带上原因，不许静默降级成一个空列表。**
   空列表和"没有语义能力"在调用方看来长得一样，那会让上层把"没有能力"
   误判成"没有结果"——同族事故见 2026-10-28 的三态混淆。
2. **`reason` 是枚举，不是自然语言。** 上层要按它分支（是否重试、是否重建索引、
   要不要提示运维），自然语言会让每个调用方各写一套字符串匹配。
3. **异常只在"这条查询拿不到语义结果"时抛；能返回部分结果时返回部分结果。**

原因码（`REASONS`）：

| reason | 含义 | 典型处置 |
| --- | --- | --- |
| `deps_missing` | 查询期所需的编码器依赖不可用（onnxruntime/tokenizers 未安装） | 装依赖或退回纯字面 |
| `model_missing` | 模型文件不在约定目录 | 下载模型 |
| `index_missing` | 没有索引文件 | 跑 `scripts/build-semantic-index.py` |
| `index_stale` | 索引 generation 与当前库不符（记录增删、模型或切分规则变了） | 重建索引 |
| `index_corrupt` | 索引可读但自检失败（维度/数量/校验和对不上） | 重建索引 |
| `encode_failed` | 编码器可用但本次编码抛错 | 看 detail；通常退回纯字面 |
"""

from __future__ import annotations

REASONS = (
    "deps_missing",
    "model_missing",
    "index_missing",
    "index_stale",
    "index_corrupt",
    "encode_failed",
)


class SemanticUnavailable(RuntimeError):
    """语义路不可用。`reason` 必须是 REASONS 之一，`detail` 供人读。

    Attributes:
        reason: 枚举原因码（见模块文档）。
        detail: 人类可读的补充说明（可为空串）。
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown SemanticUnavailable reason: {reason!r}")
        self.reason = reason
        self.detail = detail
        super().__init__(f"semantic_unavailable:{reason}" + (f" ({detail})" if detail else ""))

    def as_meta(self) -> dict:
        """给查询结果 `meta.semantic` 用的形状（调用方直接放进响应）。"""
        return {"available": False, "reason": self.reason, "detail": self.detail}
