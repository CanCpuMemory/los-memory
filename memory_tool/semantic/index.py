"""纯 Python 向量索引（B2 步2）——零依赖，可用单测机械验证。

## 为什么不用 numpy / faiss

本仓核心依赖为空。6650 条 × 384 维 ≈ 2.5 M 次乘加，纯 Python 全扫描在本机约 1–3 s；
影子检索是离线/低频工具，这个代价换"任何机器都能跑、门禁不依赖安装环境"是划算的。
真要提速，第一步是把编码搬进常驻进程，而不是先引 numpy（见 B2 §7 的后续项）。

## 索引里存什么、为什么

| 字段 | 为什么必须有 |
| --- | --- |
| `source_id` + `digest` | 查询期要和库里对账：**已删/撤回的不得召回**，被改过的条目其向量已过期 |
| 向量（float32 BLOB，已 L2 归一化） | cosine 退化成点积，省掉查询期一次遍历 |
| `generation` | 内容寻址：模型、维度、切分规则、记录集合任何一项变了都会变 |
| `model` + `model_sha256` | 模型不进仓库，但"用的哪个模型"必须可追溯 |
| `built_at` + `record_count` | 人读新鲜度；**判定不用它**（判定只认 generation） |

## 过期判据（机械，不看时间）

`expected_generation` 与索引里的 generation 不等 ⇒ `index_stale`。
**不用 mtime、不用 built_at**：那些是"多久没建"，不是"内容对不对"。
另有一条查询期判据：top 候选的 `digest` 与库里不符 ⇒ 该条向量过期，跳过；
跳过数超过 `stale_tolerance`（默认 25% 的候选）⇒ 整体判 `index_stale`，
因为此时"跳到只剩几条"会让语义召回悄悄失真，比直接报不可用更坏。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from array import array
from typing import Iterable

from .encoder import assemble_text, normalize
from .errors import SemanticUnavailable

SCHEMA_VERSION = 1
DEFAULT_STALE_TOLERANCE = 0.25
# 少于这么多条过期时，一律"跳过该条"而不是"整体判过期"（小库友好，见 search() 注释）
STALE_MIN_COUNT = 3

_MANIFEST_DDL = """
CREATE TABLE IF NOT EXISTS manifest (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_VECTORS_DDL = """
CREATE TABLE IF NOT EXISTS vectors (
    space     TEXT NOT NULL,
    source_id TEXT NOT NULL,
    digest    TEXT NOT NULL,
    vec       BLOB NOT NULL,
    PRIMARY KEY (space, source_id)
)
"""


def compute_generation(*, model: str, model_sha256: str, dim: int, text_mode: str,
                       entries: Iterable[tuple[str, str]]) -> str:
    """内容寻址的 generation：模型 + 维度 + 切分规则 + (source_id, digest) 集合。"""
    digest = hashlib.sha256()
    digest.update(f"{SCHEMA_VERSION}|{model}|{model_sha256}|{dim}|{text_mode}".encode())
    for source_id, record_digest in sorted(entries):
        digest.update(f"\n{source_id}:{record_digest}".encode())
    return digest.hexdigest()


def _pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def _unpack(blob: bytes) -> array:
    out = array("f")
    out.frombytes(blob)
    return out


class VectorIndex:
    """一条索引 = 一个 sqlite 文件（与 shadow 主库分开，便于整体替换/回滚）。"""

    def __init__(self, path: str, conn: sqlite3.Connection, manifest: dict) -> None:
        self.path = path
        self._conn = conn
        self.manifest = manifest

    # ── 构建 ─────────────────────────────────────────────────────────
    @classmethod
    def build(cls, path: str, *, conn: sqlite3.Connection, encoder, space: str = "default",
              model_sha256: str = "", text_mode: str = "title_repeat_1200",
              built_at: str | None = None) -> "VectorIndex":
        """从 shadow 库的 active 记录建索引（**构建期**就要排除已删/撤回）。"""
        rows = conn.execute(
            "SELECT source_id, digest, snapshot FROM records WHERE space=? AND active=1",
            (space,),
        ).fetchall()
        if not rows:
            raise SemanticUnavailable("index_corrupt", "库里没有可索引的 active 记录")

        entries = [(row["source_id"], row["digest"]) for row in rows]
        texts: list[str] = []
        for row in rows:
            record = json.loads(row["snapshot"])
            texts.append(assemble_text(record.get("title"), record.get("content")))
        vectors = encoder.encode_passages(texts)
        if len(vectors) != len(rows):
            raise SemanticUnavailable("index_corrupt", f"编码条数不符：{len(vectors)} != {len(rows)}")

        dim = len(vectors[0])
        for vec in vectors:
            if len(vec) != dim:
                raise SemanticUnavailable("index_corrupt", "向量维度不一致")

        generation = compute_generation(
            model=encoder.name, model_sha256=model_sha256, dim=dim,
            text_mode=text_mode, entries=entries,
        )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "generation": generation,
            "model": encoder.name,
            "model_sha256": model_sha256,
            "dim": dim,
            "text_mode": text_mode,
            "space": space,
            "record_count": len(rows),
            "built_at": built_at or "",
        }

        out = sqlite3.connect(path)
        try:
            out.execute("PRAGMA journal_mode=WAL")
            out.execute(_MANIFEST_DDL)
            out.execute(_VECTORS_DDL)
            out.execute("DELETE FROM vectors")
            out.execute("DELETE FROM manifest")
            out.executemany(
                "INSERT INTO vectors (space, source_id, digest, vec) VALUES (?, ?, ?, ?)",
                [(space, row["source_id"], row["digest"], _pack(vec))
                 for row, vec in zip(rows, vectors)],
            )
            out.executemany(
                "INSERT INTO manifest (key, value) VALUES (?, ?)",
                [(key, json.dumps(value)) for key, value in manifest.items()],
            )
            out.commit()
        finally:
            out.close()
        return cls.open(path, expect_generation=generation)

    # ── 打开 ─────────────────────────────────────────────────────────
    @classmethod
    def open(cls, path: str, *, expect_generation: str | None = None) -> "VectorIndex":
        import os

        if not os.path.isfile(path):
            raise SemanticUnavailable("index_missing", f"索引不存在：{path}")
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            manifest = {row["key"]: json.loads(row["value"])
                        for row in conn.execute("SELECT key, value FROM manifest")}
            count = conn.execute("SELECT COUNT(*) AS n FROM vectors").fetchone()["n"]
        except sqlite3.DatabaseError as exc:
            raise SemanticUnavailable("index_corrupt", f"索引不可读：{exc}") from exc

        if manifest.get("schema_version") != SCHEMA_VERSION:
            conn.close()
            raise SemanticUnavailable(
                "index_corrupt",
                f"schema 版本不符：{manifest.get('schema_version')} != {SCHEMA_VERSION}",
            )
        if count != manifest.get("record_count"):
            conn.close()
            raise SemanticUnavailable("index_corrupt", f"条数不符：{count} != {manifest.get('record_count')}")
        if expect_generation is not None and expect_generation != manifest.get("generation"):
            conn.close()
            raise SemanticUnavailable(
                "index_stale",
                f"generation 不符：索引 {str(manifest.get('generation'))[:12]}… != 期望 {expect_generation[:12]}…",
            )
        return cls(path, conn, manifest)

    def close(self) -> None:
        self._conn.close()

    # ── 查询 ─────────────────────────────────────────────────────────
    def search(self, shadow_conn: sqlite3.Connection, query_vec: list[float], *, limit: int = 10,
               space: str = "default", stale_tolerance: float = DEFAULT_STALE_TOLERANCE) -> list[dict]:
        """与查询向量最近邻；**已删/撤回/被改过的候选一律丢弃**。"""
        dim = int(self.manifest.get("dim") or 0)
        if len(query_vec) != dim:
            raise SemanticUnavailable("index_corrupt", f"查询维度不符：{len(query_vec)} != {dim}")
        query_vec = normalize(list(query_vec))
        if not any(query_vec):
            return []

        scored: list[tuple[float, str, str]] = []
        for row in self._conn.execute(
            "SELECT source_id, digest, vec FROM vectors WHERE space=?", (space,)
        ):
            vec = _unpack(row["vec"])
            if len(vec) != dim:
                raise SemanticUnavailable("index_corrupt", "索引内向量维度不一致")
            total = 0.0
            for a, b in zip(query_vec, vec):
                total += a * b
            scored.append((total, row["source_id"], row["digest"]))
        scored.sort(key=lambda item: item[0], reverse=True)

        # 先取 4×limit 的候选，再和库对账 —— 对账要查库，不能对全表做
        candidate_n = max(limit * 4, 16)
        checked = 0
        stale = 0
        out: list[dict] = []
        for score, source_id, index_digest in scored[:candidate_n]:
            checked += 1
            row = shadow_conn.execute(
                "SELECT active, digest FROM records WHERE space=? AND source_id=?",
                (space, source_id),
            ).fetchone()
            if row is None:
                continue  # 库里已无此记录
            if not row["active"]:
                # 已删/撤回：正常滞后，跳过即可。**不计入 stale** —— 用删除去触发
                # "索引过期"的告警会让每次删除都报一次需要重建，最后没人看这个告警。
                continue
            if row["digest"] != index_digest:
                stale += 1  # 内容已变但向量还是旧的 ⇒ 该条向量失真
                continue
            out.append({
                "source_id": source_id,
                "score": round(float(score), 6),
                "match_source": "semantic",
            })
            if len(out) >= limit:
                break
        # 绝对量下限：候选很少时 1/3 就超 25%，那样小库永远"需要重建"。
        # 只有过期条数达到 STALE_MIN_COUNT 且占比超过阈值，才判整体过期。
        if checked and stale >= STALE_MIN_COUNT and stale / checked > stale_tolerance:
            # 过度跳过会让语义召回悄悄失真 —— 直接判过期，比给出残缺结果好
            raise SemanticUnavailable(
                "index_stale",
                f"{stale}/{checked} 条候选的 digest 与库不符（阈值 {stale_tolerance:.0%}），需要重建索引",
            )
        return out

    def stats(self) -> dict:
        rows = self._conn.execute("SELECT COUNT(*) AS n FROM vectors").fetchone()["n"]
        return {**self.manifest, "vectors": rows}
