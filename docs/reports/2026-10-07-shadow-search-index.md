# 影子检索加 trigram 索引：实测与迁移

实测时间：2026-10-07 12:05–12:10 CST。范围：M3 影子库的**只读副本**（`VACUUM INTO` 导出后 scp 到 M1，22.6 MB / 2048 条）。没有修改 M3 上的现网库、没有部署。

## 结论

影子检索从「每次全表拉取 + Python 子串匹配」改为「trigram FTS5 预筛 + 原 Python 子串过滤」：

| | median | p95 | 库体积 |
|---|---|---|---|
| 未建索引（全表扫描，原实现） | **39.27 ms** | 43.64 ms | 22.6 MB |
| trigram 索引 + 过滤 | **6.61 ms** | 39.66 ms | 46.0 MB |

- **语义未变**：索引只决定「哪些记录被加载并做子串检查」，权威过滤仍是原来的 Python 逐词子串 AND。5 个查询（含中文、单词、短语）在「索引路径」与「删掉索引后全表扫描」下结果**逐条一致**（0 处不一致）。
- **中位数 6× 提升；p95 基本不变**（39.66 vs 43.64 ms）。原因诚实记录：短于 3 字符的词（trigram 无法索引）与命中候选过多的查询仍走全表扫描，所以尾部延迟不会一起降。要压 p95 得改语料或换分词方案，不在本次范围。
- 重建索引：2048 条 **1.02 s**，体积 +23 MB。索引是派生结构，可随时删了重建。

## 为什么是 trigram 而不是默认 unicode61

影子的既有语义是**子串**（`tests/unit/test_shadow.py` 钉住的：`all(term in title or term in body)`，含中文单字/词）。默认分词器只匹配整词，会把 `uthentication` 这类中段子串查空——那是**语义退化**，不是优化。trigram 支持子串匹配，行为与原实现同构，因此能作为预筛而不改变结果。

## 实现要点

1. `records_fts` = `fts5(source_id UNINDEXED, space UNINDEXED, text, tokenize='trigram')`，内容是 `title + "\n" + content`（与原搜索读的两个字段一致），单条上限 `FTS_TEXT_CAP=32000`。
2. **稳定 rowid**：`fts_rowid(space, source_id) = sha256(space\0source_id)[:8] & (2^63-1)`。记录更新时按 rowid 删除重写是 O(1)，否则每次 upsert 都要扫全表索引行（同步每轮 100 条时尤其明显）。
3. `put()` 在**同一个事务**里写 records 与 records_fts，索引不会与记录失配。
4. `search_detailed()` 返回 `(results, meta)`；`search()` 是其薄包装，签名不变（既有调用方与 MCP 工具不受影响）。`meta.mode ∈ {index+filter, scan}`，索引没用上时看得见，而不是让人猜。
5. **回退永不丢召回**：任一词 < 3 字符 / 候选集为空 / FTS 抛错 → 回退全表扫描。
6. `reindex` action（CLI：`python3 -m memory_tool.shadow reindex`），复用 sync 的 `flock` 单写者锁——重建与同步并发会交错写索引行。

## 静默失败的堵口（这一条是照搬 session-index 的教训）

**external-content FTS5 表的 `count(*)` 读的是内容表**：索引被清空后它照样报满行数。本实现用 `records_fts_docsize` 取真实索引文档数（实测：删空索引后 `records_fts` 报 2048 行而 docsize = 0）。

`status()` 因此新增：

```json
"search_index": {"indexed": 0, "records": 2048, "state": "not_built",
                 "hint": "run `python3 -m memory_tool.shadow reindex`"}
```

没有这条，一个从未建过索引的库会安静地把所有查询答成"没有相关记忆"——正是迁移门槛 1 要禁止的静默失败。

## 测试

- `tests/unit/test_shadow.py` +5 条：索引随 put 建立与更新、中段子串走索引、短词回退、**索引未建时仍能召回且被上报**、AND 与 project 语义不变。
- 全量：`python3 -m pytest tests/ -q` → **687 passed**（含既有 682 条，无回归）。
- 真实副本：5 查询索引/扫描一致性抽检 0 处不一致。

## 迁移（尚未执行）

代码向后兼容：新库 `connect()` 时自动建 `records_fts`；旧库在重建索引前 `search()` 自动回退全表扫描，`status()` 报 `not_built`。因此顺序是安全的：

1. 部署新代码到 M3（发布目录 `~/.local/share/los-memory-shadow/releases/<sha>`，沿用既有发布方式）；
2. 在 M3 上跑一次 `python3 -m memory_tool.shadow reindex`（约 1 s / 2048 条）；
3. `status` 确认 `search_index.state == "ready"`，`shadow_status` MCP 输出同步可见。

注意：重建要在 sync 停下或至少不并发时进行（CLI 已用 flock 拒绝并发）。
