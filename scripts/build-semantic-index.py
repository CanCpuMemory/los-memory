#!/usr/bin/env python3
"""构建语义向量索引（B2 步2 的离线构建步骤）。

## 为什么是离线脚本而不是启动时自建

编码 6000+ 条要几十秒到几分钟（真模型）；放进 MCP server 启动路径会让每次
重连都卡住，而 `serve` 是每次工具调用都走 stdio 的短生命周期进程。

## 退出码（三态，`2` 不是通过）

- `0` 索引已生成（打印 manifest 摘要）
- `1` 构建失败但**可判**（库里没有记录、条数不符等）
- `2` **判不了**：模型/依赖缺失、库文件读不到 —— 绝不能折成 1

## 零依赖纪律

本脚本不 import numpy/onnxruntime；它调用 `memory_tool.semantic.load_onnx_encoder`，
依赖缺失时那里抛 `SemanticUnavailable('deps_missing')`，本脚本转成 exit 2。

## 拒绝机制编码器

`HashingEncoder` 只用于单测。用它建出的索引会让"语义召回"变成一句谎话，
所以默认**拒绝**；只有显式 `--allow-mechanism-encoder` 才允许，且输出文件名强制带
`.mechanism` 后缀（避免被误当成生产索引）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 与 scripts/ 下其余脚本一致：让 `python3 scripts/xxx.py` 直接可跑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory_tool import shadow  # noqa: E402  (路径注入必须在导入之前)
from memory_tool.semantic import HashingEncoder, SemanticUnavailable, VectorIndex, load_onnx_encoder
from memory_tool.semantic.encoder import Encoder

MODEL_FILE = "model_quantized.onnx"


def sha256_of(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 los-memory 语义向量索引")
    parser.add_argument("--db", default=str(shadow.DEFAULT_DB), help="影子库路径")
    parser.add_argument("--out", required=True, help="输出索引路径（sqlite 文件）")
    parser.add_argument("--space", default="default")
    parser.add_argument("--model-dir", default="", help="ONNX 模型目录（含 model_quantized.onnx 与 tokenizer.json）")
    parser.add_argument("--model-file", default=MODEL_FILE)
    parser.add_argument("--allow-mechanism-encoder", action="store_true",
                        help="仅用于本地验证机制：允许用 HashingEncoder 建索引（非语义）")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    if not db_path.is_file():
        print(f"判定不了：影子库不存在 {db_path}", file=sys.stderr)
        return 2

    encoder: Encoder
    model_sha = ""
    if args.allow_mechanism_encoder:
        encoder = HashingEncoder()
        out = Path(args.out)
        if ".mechanism" not in out.name:
            out = out.with_name(f"{out.stem}.mechanism{out.suffix}")
        args.out = str(out)
        print(f"⚠️  使用机制编码器（非语义），输出 {args.out}", file=sys.stderr)
    else:
        if not args.model_dir:
            print("判定不了：未给 --model-dir（语义索引必须用真模型）", file=sys.stderr)
            return 2
        model_path = Path(args.model_dir).expanduser() / args.model_file
        if not model_path.is_file():
            print(f"判定不了：模型文件不存在 {model_path}", file=sys.stderr)
            return 2
        model_sha = sha256_of(model_path)
        try:
            encoder = load_onnx_encoder(str(Path(args.model_dir).expanduser()), model_file=args.model_file)
        except SemanticUnavailable as exc:
            print(f"判定不了：{exc.reason} — {exc.detail}", file=sys.stderr)
            return 2

    conn = shadow.connect(str(db_path))
    try:
        try:
            index = VectorIndex.build(args.out, conn=conn, encoder=encoder, space=args.space,
                                      model_sha256=model_sha)
        except SemanticUnavailable as exc:
            print(f"构建失败（可判）：{exc.reason} — {exc.detail}", file=sys.stderr)
            return 1
    finally:
        conn.close()
    try:
        print(json.dumps(index.stats(), ensure_ascii=False, indent=2))
    finally:
        index.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
