# B2 语义召回模型选型的实验脚本（不是生产代码）

这两个脚本产出了 `docs/reports/2026-10-21-b2-model-selection.md` 的全部数字。
它们**不被任何代码引用**，只作为"结论可复跑"的证据留在这里。

## 为什么不进 `memory_tool/`

`onnxruntime` + `tokenizers` 不是这个仓库的依赖，模型本身是 24–118 MB 的二进制。
把实验留在 `tools/spikes/` 意味着：仓库不背这两个依赖、也不背模型文件；
结论和脚本仍然可追溯。

## 复跑

```bash
python3 -m venv /tmp/b2env
/tmp/b2env/bin/pip install onnxruntime tokenizers numpy

# 模型（Xenova 的 ONNX 转换，每个仓库三个文件：onnx/model_quantized.onnx、tokenizer.json、config.json）
#   https://huggingface.co/Xenova/multilingual-e5-small
#   https://huggingface.co/Xenova/bge-small-zh-v1.5
#   https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2

/tmp/b2env/bin/python tools/spikes/b2-model-selection/embed_ab.py \
  --model e5-title-repeat --onnx /path/e5.onnx --tokenizer /path/e5-tokenizer.json \
  --query-prefix "query: " --text-mode title_repeat

/tmp/b2env/bin/python tools/spikes/b2-model-selection/rss_probe.py   # 只看模型自身的 RSS
```

`embed_ab.py` 读的是**私有评测快照** `~/.local/share/los-memory-shadow/eval/snap.sqlite3`
与 40 例留出集 `cases.jsonl`（都不在仓库里）。没有这两个文件时脚本按路径报错，不会伪造数据。

## 两个必须记住的读数

- **分块（`--chunk-size`）会把改写召回打到 0.0**，比不分块差一个数量级（报告 §4）。
- **耗时不可用**：同一配置三次测得 82 / 164 / 264 s，机器负载波动主导，排序别靠它。
