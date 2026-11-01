# B2 步2：语义召回旁路落地（2026-11-01）

> 上游：`docs/reports/2026-10-21-b2-model-selection.md`（模型选型与三个负结果）。
> 本文只记**步2 的落地形态、判据与未做项**；选型理由不重复。

## 1. 形态：旁路，不是主入口

```
shadow.search_detailed()   ← 字面检索（未改一行）
        │
        ├── query 为空 / 字面命中 → 原样返回
        │
        └── semantic.search_hybrid()   ← 新入口
                ├── 字面结果按原序全部保留（不变式 1）
                ├── 语义候选只填充剩余空位
                └── 不可用 → meta.semantic = {available:false, reason, detail}
```

**为什么不做成"两路混排"**：混排要引入一个不可复现的权重，且在"字面精确命中"的
用例上会把正确答案挤下去。B0 里字面类的优势恰好就在这块。把语义做成"只填空位"，
"字面类 recall@k 不下降"是**结构性质**，不需要靠统计去证明。

## 2. 模块与零依赖纪律

| 文件 | 职责 |
| --- | --- |
| `memory_tool/semantic/errors.py` | `SemanticUnavailable` + 六个原因码 |
| `memory_tool/semantic/encoder.py` | 编码器协议、`HashingEncoder`（**仅机制测试**）、`load_onnx_encoder`（延迟 import） |
| `memory_tool/semantic/index.py` | 纯 Python 向量索引：构建、generation 内容寻址、查询期与库对账 |
| `memory_tool/semantic/hybrid.py` | 融合（见 §1 不变式） |
| `scripts/build-semantic-index.py` | 离线构建，三态退出码 |

**本仓 `dependencies = []` 没变**：`numpy` / `onnxruntime` / `tokenizers` 一律不进口，
`load_onnx_encoder` 里延迟 import，缺失时抛 `SemanticUnavailable('deps_missing')`。
索引与检索是纯 Python（`array` + 点积），6650 条 × 384 维的全扫描换"任何机器都能跑、
门禁不依赖安装环境"。

## 3. 失败语义（不可用必须报原因，不是空列表）

| reason | 触发 |
| --- | --- |
| `deps_missing` | 查询期 onnxruntime/tokenizers 不可用 |
| `model_missing` | 模型目录/文件不在约定位置 |
| `index_missing` | 没有索引文件 |
| `index_stale` | generation 不符；或候选里 digest 失配的比例超阈值（且有绝对量下限 3 条） |
| `index_corrupt` | schema 版本/条数/维度/自检不符 |
| `encode_failed` | 编码器可用但本次编码抛错 |

两条容易写错、已经固化的细节：

1. **已删/撤回不计入 stale。** `active=0` 的候选直接跳过——索引滞后于删除是正常的；
   若把它计入"索引过期"，每次删除都会触发一次"需要重建"的告警，最后没人看这个告警。
   只有 `digest` 变（内容改了但向量还是旧的）才是"向量失真"。
2. **阈值要带绝对量下限。** 候选只有 3 条时，1 条过期就占 33%，小库会永远"需要重建"。
   现在要求 `stale >= 3` **且** 占比超 25% 才整体判过期，否则逐条跳过。

## 4. 判据：机制已验收，质量未验收

`tests/test_semantic_recall.py`（14 例，全绿）验证的是**机制**，用 `HashingEncoder`：

| # | 判据 | 类型 |
| --- | --- | --- |
| 1 | 索引构建后可检索、相关记录排第一 | 正向 |
| 2 | `assemble_text` 标题重复两次 + 正文截断 | 规则守卫 |
| 3 | `generation` 内容寻址（顺序无关、模型/维度/切分/记录变化必变） | 正向 |
| 4 | 索引缺失 ⇒ 不可用但**字面结果仍在** | 负向控制 |
| 5 | generation 不符 ⇒ `index_stale` | 负向控制 |
| 6 | `mark_missing` 之后不得召回 | 负向控制 |
| 7 | 记录被改（digest 变）⇒ 该条向量作废 | 负向控制 |
| 8 | 大面积过期 ⇒ 判不可用而不是给残缺结果 | 负向控制 |
| 9 | 维度不符 ⇒ `index_corrupt` | 负向控制 |
| 10 | 字面结果原序保留、语义只填空位 | 不变式 |
| 11 | 空白 query 与 `shadow.search` 行为一致（都拒绝） | 一致性 |
| 12 | 零向量查询返回空 | 边界 |

**质量门禁没跑**（`MRR@10 ≥ 0.7`、`best-rank@20 ≥ 0.85`、改写 `recall@5 ≥ 0.8`）：
需要真模型 + 私有语料（`~/.local/share/los-memory-shadow/eval/`）。
**未跑 = 未验收**，不许在任何汇报里写成通过。

## 5. 构建与部署（尚未执行）

```bash
# ① 依赖与模型（不进仓库；模型按 sha256 记进 manifest）
python3 -m venv /tmp/b2env && /tmp/b2env/bin/pip install onnxruntime tokenizers numpy
#   https://huggingface.co/Xenova/multilingual-e5-small → onnx/model_quantized.onnx + tokenizer.json

# ② 建索引（必须用真模型；--allow-mechanism-encoder 只用于本地验证机制，输出带 .mechanism）
/tmp/b2env/bin/python scripts/build-semantic-index.py \
  --db ~/.local/share/los-memory-shadow/shadow.sqlite3 \
  --model-dir /path/to/e5 --out ~/.local/share/los-memory-shadow/semantic.sqlite3

# ③ 查询期只需要"索引 + 编码器"，把它指向已建好的东西
export LOS_MEMORY_SEMANTIC_INDEX=~/.local/share/los-memory-shadow/semantic.sqlite3
export LOS_MEMORY_SEMANTIC_MODEL_DIR=/path/to/e5

# ④ 部署面：MCP server 跑在 M3（web profile: ssh m3-t …/serve），
#    所以本仓改动要在 M3 上同步并重启该进程，才可能生效。
```

**当前状态（诚实版）**：

- 本机仓：代码 + 14 例机制门禁 + 构建脚本三态（空库 exit 1、缺模型 exit 2）**已验证**。
- 索引：**未生成**（本机 `~/.local/share/los-memory-shadow/shadow.sqlite3` 只有 98 KB
  / 0 条 active，是 10-10 的旧壳；真库 338 MB 在 M3）。
- M3 部署：**未做**。所以 `shadow_search` 的行为**没有任何变化**——
  这正是仓库纪律里的"改了代码 ≠ 改了运行的进程"。
- 规模与耗时：**未测**。B2 已记过"同一配置三次测得 82/164/264 s，负载主导"，
  所以即便测了也不该当判据；要测的是**查询期 p95**，属于质量门禁那一批。

## 6. 下一步（按门槛排序）

1. M3 上装 venv + 下模型 + 建索引（≈ 一次 3–5 分钟），跑一次 `stale` 自检。
2. 用私有语料跑质量门禁（语义类 40 例 + 改写探针扩到 ≥20 例），把三个数字落进 B2 报告。
3. 只有质量门禁过了，才谈把 `shadow_search` 的默认路径改成 hybrid；
   在此之前它只能是一个显式开启的旁路。
