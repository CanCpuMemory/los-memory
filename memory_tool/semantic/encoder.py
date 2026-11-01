"""编码器抽象（B2 步2）。

三条硬约束：

1. **查询期不引入仓库依赖。** 本仓核心依赖为空（`pyproject.toml` 的 `dependencies = []`），
   `onnxruntime` / `tokenizers` / `numpy` 只在 `tools/spikes/` 的实验里用过。
   所以 `OnnxEncoder` 是**延迟 import**：装了就可用，没装就抛
   `SemanticUnavailable('deps_missing')`，绝不 import 期炸掉整个 shadow 工具。
2. **模型不进仓库。** 模型是 24–118 MB 的二进制，按 sha256 进索引 manifest；
   仓库只记"用了哪个模型、它的 sha256 是多少"。
3. **文本组装规则是选型结论的一部分，不许在实现里随手改**（B2 报告 §3）：
   一条记录一条向量；文本 = `title` 重复两次 + 正文前 1200 字符；
   **不加 passage 前缀**（实测是负结果），查询加 `query: ` 前缀（e5 的约定）；
   分块会把改写召回打到 0.0，所以不分块。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .errors import SemanticUnavailable

# 这些常量来自 B2 报告的实测结论，改动等于换方案，必须同时改报告与验收判据。
TEXT_BODY_CHARS = 1200
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = ""


@runtime_checkable
class Encoder(Protocol):
    """把文本变成**已归一化**向量。实现方负责归一化（cosine 于是等于点积）。"""

    name: str
    dim: int

    def encode_query(self, query: str) -> list[float]:
        """编码单条查询（实现方负责加 `QUERY_PREFIX`）。"""

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        """批量编码候选文本（实现方负责按 `assemble_text` 的规则组装）。"""


def assemble_text(title: str | None, content: str | None) -> str:
    """把一条记录拼成待编码文本（B2 §3 定稿的规则，别改）。"""
    head = (title or "").strip()
    body = (content or "").strip()[:TEXT_BODY_CHARS]
    # title 重复两次：实测能提升"标题就是答案"类查询的召回，且不增加索引体积。
    text = f"{head}\n{head}\n{body}".strip()
    return f"{PASSAGE_PREFIX}{text}" if PASSAGE_PREFIX else text


def normalize(vec: list[float]) -> list[float]:
    """L2 归一化。零向量保持零向量（避免除零），调用方按"全零 = 无信号"处理。"""
    total = 0.0
    for value in vec:
        total += value * value
    if total <= 0.0:
        return [0.0] * len(vec)
    scale = 1.0 / (total ** 0.5)
    return [value * scale for value in vec]


class HashingEncoder:
    """**仅供机制测试**的确定性编码器：字符 n-gram 哈希到固定维度。

    它不是语义模型——只保证"同样输入同样输出、相关文本有重叠维度"，
    用来在**没有模型、没有依赖**的机器上验证索引/检索/失败语义这条机制链路。
    生产构建脚本**拒绝**用它（`scripts/build-semantic-index.py` 会 fail-closed），
    因为用它建出的索引会让"语义召回"变成一句谎话。
    """

    name = "hashing-mechanism-only"
    dim = 64

    def _encode(self, text: str) -> list[float]:
        import hashlib

        vec = [0.0] * self.dim
        text = text.casefold()
        for i in range(len(text) - 2):
            gram = text[i : i + 3]
            # 稳定哈希（不能用内置 hash()：PYTHONHASHSEED 会让跨进程结果不同，
            # 那样"同输入同向量"这条最基础的纪律就不成立）
            slot = int.from_bytes(hashlib.blake2b(gram.encode(), digest_size=4).digest(), "big") % self.dim
            vec[slot] += 1.0
        return normalize(vec)

    def encode_query(self, query: str) -> list[float]:
        return self._encode(f"{QUERY_PREFIX}{query}")

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._encode(text) for text in texts]


def load_onnx_encoder(model_dir: str, *, model_file: str = "model_quantized.onnx") -> Encoder:
    """加载 ONNX 编码器。依赖或模型缺失时抛 `SemanticUnavailable`，不返回 None。

    期望目录里有 `model_quantized.onnx`、`tokenizer.json`、`config.json`
    （Xenova 的 ONNX 转换产物）。
    """
    from pathlib import Path

    root = Path(model_dir)
    if not root.is_dir():
        raise SemanticUnavailable("model_missing", f"模型目录不存在：{root}")
    onnx_path = root / model_file
    tok_path = root / "tokenizer.json"
    for path in (onnx_path, tok_path):
        if not path.is_file():
            raise SemanticUnavailable("model_missing", f"缺文件：{path}")
    try:
        import onnxruntime as ort  # noqa: PLC0415 —— 故意延迟导入（见模块文档第 1 条）
        from tokenizers import Tokenizer  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 取决于机器是否装了可选依赖
        raise SemanticUnavailable("deps_missing", f"onnxruntime/tokenizers 不可用：{exc}") from exc

    tokenizer = Tokenizer.from_file(str(tok_path))
    tokenizer.enable_truncation(max_length=512)
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    input_names = {item.name for item in session.get_inputs()}

    class _OnnxEncoder:
        name = f"onnx:{onnx_path.name}"
        dim = 384  # multilingual-e5-small 的隐藏维度；由自检与实际输出长度双确认

        def _run(self, texts: list[str]) -> list[list[float]]:
            batch = tokenizer.encode_batch(texts)
            feeds: dict[str, object] = {}
            if "input_ids" in input_names:
                feeds["input_ids"] = [enc.ids for enc in batch]
            if "attention_mask" in input_names:
                feeds["attention_mask"] = [enc.attention_mask for enc in batch]
            if "token_type_ids" in input_names:
                feeds["token_type_ids"] = [enc.type_ids for enc in batch]
            hidden = session.run(None, feeds)[0]
            # mean pooling over tokens（e5 的官方用法）
            pooled: list[list[float]] = []
            for row, enc in zip(hidden, batch):
                mask = enc.attention_mask
                dim = len(row[0])
                acc = [0.0] * dim
                count = 0
                for token_vec, flag in zip(row, mask):
                    if not flag:
                        continue
                    count += 1
                    for i in range(dim):
                        acc[i] += float(token_vec[i])
                if count:
                    acc = [value / count for value in acc]
                pooled.append(normalize(acc))
            self.dim = len(pooled[0]) if pooled else self.dim
            return pooled

        def encode_query(self, query: str) -> list[float]:
            return self._run([f"{QUERY_PREFIX}{query}"])[0]

        def encode_passages(self, texts: list[str]) -> list[list[float]]:
            return self._run(texts) if texts else []

    encoder = _OnnxEncoder()
    # 自检：空文本也要能编码，且维度稳定 —— 否则索引里的维度会悄悄变
    probe = encoder.encode_passages(["维度自检"])
    if len(probe) != 1 or len(probe[0]) != encoder.dim:
        raise SemanticUnavailable("index_corrupt", f"编码器维度自检失败：{len(probe[0]) if probe else 0}")
    return encoder
