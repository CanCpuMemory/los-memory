# los-memory Database Schema Optimization

## Current Schema Analysis

### Existing Tables

```sql
-- observations: Core table for memory entries
CREATE TABLE observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    project TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    tags TEXT NOT NULL,        -- JSON array
    tags_text TEXT NOT NULL,   -- Space-separated for FTS
    raw TEXT NOT NULL,
    session_id INTEGER REFERENCES sessions(id)
);

-- sessions: Work session tracking
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,
    end_time TEXT,
    project TEXT NOT NULL,
    working_dir TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    summary TEXT DEFAULT '',
    status TEXT DEFAULT 'active'
);

-- checkpoints: Project milestones
CREATE TABLE checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    tag TEXT DEFAULT '',
    session_id INTEGER REFERENCES sessions(id),
    observation_count INTEGER DEFAULT 0,
    project TEXT DEFAULT ''
);

-- feedback_log: User feedback on observations
CREATE TABLE feedback_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    feedback_text TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

-- observation_links: Relationships between observations
CREATE TABLE observation_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    to_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    link_type TEXT NOT NULL DEFAULT 'related',
    created_at TEXT NOT NULL
);

-- meta: Schema versioning
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- observations_fts: Full-text search virtual table
CREATE VIRTUAL TABLE observations_fts
USING fts5(title, summary, tags_text, raw, content='observations', content_rowid='id');
```

### Current Indexes

```sql
-- From schema version 5
CREATE INDEX idx_feedback_target ON feedback_log(target_observation_id);

-- From schema version 6
CREATE INDEX idx_links_from_to ON observation_links(from_id, to_id);
CREATE INDEX idx_links_to_type ON observation_links(to_id, link_type);
CREATE UNIQUE INDEX idx_links_unique ON observation_links(from_id, to_id, link_type);
```

## Performance Analysis

### Query Patterns

| Query Pattern | Current Performance | Issue |
|---------------|---------------------|-------|
| Search by project + kind | O(n) table scan | Missing composite index |
| Timeline queries with date range | O(n) with sort | Missing timestamp index |
| Session observations lookup | O(n) | Missing session_id index |
| Tag filtering | O(n) with JSON parse | No tag index possible (JSON) |
| Recent observations | O(n log n) sort | Missing covering index |

### Identified Bottlenecks

1. **No index on `observations.timestamp`** - Timeline queries scan entire table
2. **No index on `observations.project`** - Project filtering is slow
3. **No index on `observations.session_id`** - Session lookups are slow
4. **No composite index for common queries** - Multi-filter queries are slow
5. **No partial index for active sessions** - Session status queries scan all rows
6. **JSON tags parsing overhead** - Tag filtering requires parsing JSON

## Optimization Recommendations

### 1. Add Core Indexes

```sql
-- Index for timeline queries (most common)
CREATE INDEX idx_observations_timestamp ON observations(timestamp DESC);

-- Index for project filtering
CREATE INDEX idx_observations_project ON observations(project);

-- Index for kind filtering
CREATE INDEX idx_observations_kind ON observations(kind);

-- Index for session lookups
CREATE INDEX idx_observations_session ON observations(session_id);

-- Composite index for common query pattern: project + timestamp
CREATE INDEX idx_observations_project_timestamp ON observations(project, timestamp DESC);

-- Composite index for kind + timestamp queries
CREATE INDEX idx_observations_kind_timestamp ON observations(kind, timestamp DESC);
```

### 2. Add Partial Indexes

```sql
-- Partial index for active sessions (small, frequently accessed)
CREATE INDEX idx_sessions_active ON sessions(id) WHERE status = 'active';

-- Partial index for recent observations (last 90 days)
-- Note: SQLite doesn't support expression-based partial indexes directly
-- Alternative: Application-level partitioning or use timestamp range queries
```

### 3. Optimize FTS Usage

```sql
-- Current FTS5 is good, but can be optimized with:
-- 1. Prefix indexing for autocomplete
-- 2. Rank function customization

-- Rebuild FTS with prefix index for better autocomplete
-- (Requires rebuilding the virtual table)
DROP TABLE IF EXISTS observations_fts;
CREATE VIRTUAL TABLE observations_fts
USING fts5(
    title, summary, tags_text, raw,
    content='observations',
    content_rowid='id',
    prefix='2 3 4'  -- Index prefixes of length 2, 3, and 4
);
```

### 4. Covering Indexes

```sql
-- Covering index for list queries (avoids table lookup)
CREATE INDEX idx_observations_list_covering
ON observations(timestamp DESC, id, project, kind, title, summary)
WHERE session_id IS NULL;

-- Covering index for search results display
CREATE INDEX idx_observations_search_covering
ON observations(id, timestamp, project, kind, title, summary);
```

### 5. Normalized Tags Table (Optional Major Change)

Current JSON tags have limitations. Consider normalized approach:

```sql
-- New table for normalized tags
CREATE TABLE tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- Junction table
CREATE TABLE observation_tags (
    observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (observation_id, tag_id)
);

-- Index for tag lookups
CREATE INDEX idx_observation_tags_tag ON observation_tags(tag_id);

-- Index for observation tag lookups
CREATE INDEX idx_observation_tags_obs ON observation_tags(observation_id);
```

**Pros:**
- Fast tag-based queries
- Tag statistics and autocomplete
- No JSON parsing overhead

**Cons:**
- Migration complexity
- More storage for junction table
- Insert/delete overhead

### 6. Query Optimization

#### Before (Slow)
```sql
-- Full table scan with sort
SELECT * FROM observations
WHERE project = 'myapp'
ORDER BY timestamp DESC
LIMIT 20;
```

#### After (Fast with index)
```sql
-- Uses idx_observations_project_timestamp
SELECT * FROM observations
WHERE project = 'myapp'
ORDER BY timestamp DESC
LIMIT 20;
```

### 7. Connection Optimizations

```python
# In database.py, add PRAGMA optimizations
def connect_db(path: str) -> sqlite3.Connection:
    """Connect to SQLite database with optimizations."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    # Performance optimizations
    conn.execute("PRAGMA journal_mode = WAL")          # Better concurrency
    conn.execute("PRAGMA synchronous = NORMAL")        # Balance safety/speed
    conn.execute("PRAGMA cache_size = -64000")         # 64MB cache
    conn.execute("PRAGMA temp_store = MEMORY")         # Temp tables in memory
    conn.execute("PRAGMA mmap_size = 268435456")       # 256MB memory map
    conn.execute("PRAGMA foreign_keys = ON")           # Enforce FK constraints

    return conn
```

## Schema Migration Plan

### Version 7: Index Optimization

```python
def migrate_v7(conn: sqlite3.Connection) -> None:
    """Add performance indexes."""

    # Core indexes
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_observations_timestamp
        ON observations(timestamp DESC)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_observations_project
        ON observations(project)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_observations_kind
        ON observations(kind)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_observations_session
        ON observations(session_id)
    """)

    # Composite indexes
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_observations_project_timestamp
        ON observations(project, timestamp DESC)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_observations_kind_timestamp
        ON observations(kind, timestamp DESC)
    """)

    # Partial index for active sessions
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_active
        ON sessions(id) WHERE status = 'active'
    """)

    # Analyze for query planner
    conn.execute("ANALYZE")
```

### Version 8: FTS Optimization (Optional)

```python
def migrate_v8(conn: sqlite3.Connection) -> None:
    """Rebuild FTS with prefix indexing."""

    # Backup existing FTS data
    rows = conn.execute("SELECT rowid, title, summary, tags_text, raw FROM observations").fetchall()

    # Drop and recreate FTS with prefix support
    conn.execute("DROP TABLE IF EXISTS observations_fts")
    conn.execute("""
        CREATE VIRTUAL TABLE observations_fts
        USING fts5(
            title, summary, tags_text, raw,
            content='observations',
            content_rowid='id',
            prefix='2 3 4'
        )
    """)

    # Rebuild triggers
    conn.execute("DROP TRIGGER IF EXISTS observations_ai")
    conn.execute("DROP TRIGGER IF EXISTS observations_ad")
    conn.execute("DROP TRIGGER IF EXISTS observations_au")

    # Recreate triggers (same as original)
    conn.execute("""
        CREATE TRIGGER observations_ai
        AFTER INSERT ON observations BEGIN
            INSERT INTO observations_fts(rowid, title, summary, tags_text, raw)
            VALUES (new.id, new.title, new.summary, new.tags_text, new.raw);
        END
    """)

    conn.execute("""
        CREATE TRIGGER observations_ad
        AFTER DELETE ON observations BEGIN
            INSERT INTO observations_fts(observations_fts, rowid, title, summary, tags_text, raw)
            VALUES ('delete', old.id, old.title, old.summary, old.tags_text, old.raw);
        END
    """)

    conn.execute("""
        CREATE TRIGGER observations_au
        AFTER UPDATE ON observations BEGIN
            INSERT INTO observations_fts(observations_fts, rowid, title, summary, tags_text, raw)
            VALUES ('delete', old.id, old.title, old.summary, old.tags_text, old.raw);
            INSERT INTO observations_fts(rowid, title, summary, tags_text, raw)
            VALUES (new.id, new.title, new.summary, new.tags_text, new.raw);
        END
    """)

    # Repopulate FTS
    for row in rows:
        conn.execute("""
            INSERT INTO observations_fts(rowid, title, summary, tags_text, raw)
            VALUES (?, ?, ?, ?, ?)
        """, (row["rowid"], row["title"], row["summary"], row["tags_text"], row["raw"]))
```

## Monitoring Queries

### Index Usage Analysis

```sql
-- Check index usage (SQLite 3.16+)
SELECT * FROM sqlite_stat1;

-- Analyze query plan
EXPLAIN QUERY PLAN
SELECT * FROM observations
WHERE project = 'myapp'
ORDER BY timestamp DESC
LIMIT 20;

-- Expected: USING INDEX idx_observations_project_timestamp
```

### Performance Metrics

```python
def analyze_performance(conn: sqlite3.Connection) -> dict:
    """Analyze database performance metrics."""

    # Table sizes
    tables = conn.execute("""
        SELECT name, SUM(pgsize) as size
        FROM dbstat
        GROUP BY name
        ORDER BY size DESC
    """).fetchall()

    # Index sizes
    indexes = conn.execute("""
        SELECT name, SUM(pgsize) as size
        FROM dbstat
        WHERE name LIKE 'idx_%'
        GROUP BY name
        ORDER BY size DESC
    """).fetchall()

    # Query performance (if sqlite3_stmt enabled)
    # Requires compile-time option SQLITE_ENABLE_STMTVTAB

    return {
        "tables": [{"name": r["name"], "size_bytes": r["size"]} for r in tables],
        "indexes": [{"name": r["name"], "size_bytes": r["size"]} for r in indexes],
    }
```

## Storage Estimates

### Current Schema (per 1000 observations)

| Component | Estimated Size |
|-----------|---------------|
| observations table | ~500 KB |
| observations_fts | ~300 KB |
| indexes | ~200 KB |
| **Total** | **~1 MB** |

### After Optimization (per 1000 observations)

| Component | Estimated Size |
|-----------|---------------|
| observations table | ~500 KB |
| observations_fts | ~300 KB |
| indexes (current) | ~200 KB |
| indexes (new) | ~400 KB |
| **Total** | **~1.4 MB** |

**Trade-off:** 40% more storage for significantly better query performance.

## Maintenance Recommendations

### Regular Maintenance

```python
def perform_maintenance(conn: sqlite3.Connection) -> None:
    """Perform regular database maintenance."""

    # Update statistics for query planner
    conn.execute("ANALYZE")

    # Rebuild FTS if fragmented
    conn.execute("INSERT INTO observations_fts(observations_fts) VALUES('rebuild')")

    # Vacuum to reclaim space (optional, can be slow)
    # conn.execute("VACUUM")

    conn.commit()
```

### Recommended Schedule

| Task | Frequency | Command |
|------|-----------|---------|
| ANALYZE | Weekly | `ANALYZE` |
| FTS rebuild | Monthly | `INSERT INTO observations_fts(observations_fts) VALUES('rebuild')` |
| VACUUM | Quarterly | `VACUUM` |
| Integrity check | Monthly | `PRAGMA integrity_check` |

## Backward Compatibility

### Migration Safety

1. All new indexes use `IF NOT EXISTS`
2. Indexes can be added without data migration
3. No breaking changes to table schema
4. Existing queries continue to work

### Rollback Plan

```sql
-- If issues arise, indexes can be safely dropped
DROP INDEX IF EXISTS idx_observations_timestamp;
DROP INDEX IF EXISTS idx_observations_project;
DROP INDEX IF EXISTS idx_observations_kind;
DROP INDEX IF EXISTS idx_observations_session;
DROP INDEX IF EXISTS idx_observations_project_timestamp;
DROP INDEX IF EXISTS idx_observations_kind_timestamp;
DROP INDEX IF EXISTS idx_sessions_active;
```

## Summary

### Immediate Actions (High Impact, Low Risk)

1. Add `idx_observations_timestamp` - Improves timeline queries
2. Add `idx_observations_project` - Improves project filtering
3. Add `idx_observations_session` - Improves session lookups
4. Enable WAL mode - Improves concurrency

### Medium-Term Actions

1. Add composite indexes for common query patterns
2. Add partial index for active sessions
3. Implement connection optimizations (PRAGMAs)

### Long-Term Considerations

1. Evaluate normalized tags table (major change)
2. Consider table partitioning for very large databases (>100K observations)
3. Implement query caching layer

### Expected Performance Improvements

| Query Type | Before | After | Improvement |
|------------|--------|-------|-------------|
| Timeline (project filter) | O(n) | O(log n) | 10-100x |
| Session observations | O(n) | O(log n) | 10-50x |
| Recent list | O(n log n) | O(log n) | 10-100x |
| Active session lookup | O(n) | O(1) | 100-1000x |
