"""语义召回旁路（B2 步2）。

**这是旁路，不是主入口**：字面检索（`shadow.search_detailed`）的排序与语义
保持不变；语义只在 `hybrid.search_hybrid` 里补充字面未命中的空位。

模块地图：

| 文件 | 职责 |
| --- | --- |
| `errors.py` | `SemanticUnavailable` 与六个原因码（不可用必须报原因） |
| `encoder.py` | 编码器协议；`HashingEncoder`（仅机制测试）、`load_onnx_encoder`（可选依赖） |
| `index.py` | 纯 Python 向量索引：构建、generation 内容寻址、查询期与库对账 |
| `hybrid.py` | 融合：字面原序保留，语义只填空位 |

零依赖纪律：本包 import 期不碰 numpy/onnxruntime/tokenizers；缺依赖只会在
`load_onnx_encoder` 时抛 `SemanticUnavailable('deps_missing')`。
"""

from .encoder import HashingEncoder, assemble_text, load_onnx_encoder, normalize
from .errors import REASONS, SemanticUnavailable
from .hybrid import search_hybrid
from .index import VectorIndex, compute_generation

__all__ = [
    "HashingEncoder",
    "REASONS",
    "SemanticUnavailable",
    "VectorIndex",
    "assemble_text",
    "compute_generation",
    "load_onnx_encoder",
    "normalize",
    "search_hybrid",
]
