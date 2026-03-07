# los-memory 实现方案

**版本**: v1.0
**日期**: 2026-03-07
**编制**: 多角色协作分析（产品经理、技术架构师、后端工程师、CLI专家、测试工程师）

---

## 1. 执行摘要

本方案基于对 los-memory 项目的多角色协作分析，制定了从当前 CLI 工具向稳定化、易用化演进的详细实施计划。

### 核心结论

| 维度 | 当前状态 | 目标状态 |
|------|----------|----------|
| **定位** | 个人/代理级 CLI 工具 | 稳定可靠的开发者工具 |
| **集成方式** | 直接 Python 调用/子进程 | 官方 launcher + 标准化 SDK |
| **输出格式** | 混合格式，不稳定 | JSON-first + 人类可读可选 |
| **可靠性** | 缺少健康检查 | doctor 命令 + 降级策略 |
| **跨项目支持** | 各项目自行封装 | 官方多语言 SDK |

### 关键决策

1. **保持 CLI-first**：服务化条件未满足（5个触发条件需满足2-3个），维持 CLI 工具定位
2. **向后兼容优先**：所有变更不得破坏现有 CLI 接口
3. **JSON-first**：所有命令默认 JSON 输出，可选人类可读格式
4. **分层演进**：P0（稳定基础）→ P1（易用性）→ P2（高级功能）

---

## 2. 功能需求与优先级

### 2.1 P0 - 稳定基础（必须实现）

| # | 功能 | 用户价值 | 技术风险 | 交付物 |
|---|------|----------|----------|--------|
| 1 | **doctor 命令** | 快速诊断环境问题，降低支持成本 | 低 | `los-memory admin doctor` |
| 2 | **官方 launcher** | 统一入口，消除环境差异 | 低 | `bin/los-memory` 脚本 |
| 3 | **JSON 输出标准化** | 机器可读，消除解析错误 | 低 | JSON Schema v1.0 |
| 4 | **健康检查契约** | 支持降级策略，提高可靠性 | 低 | 退出码规范 |
| 5 | **配置文件支持** | 统一配置管理，减少参数 | 中 | `~/.config/los-memory/config.yaml` |

### 2.2 P1 - 易用性增强（强烈建议）

| # | 功能 | 用户价值 | 技术风险 | 交付物 |
|---|------|----------|----------|--------|
| 6 | **多语言 SDK 模板** (Go/Rust/Node) | 降低接入门槛 | 中 | `sdk/{go,rust,node}/` |
| 7 | **人类可读输出** | 提升交互体验 | 中 | 表格/列表格式化 |
| 8 | **标签策略助手** | 提高数据质量 | 中 | auto-tags 改进 |
| 9 | **Shell 补全** | 提升使用效率 | 低 | bash/zsh/fish 补全脚本 |
| 10 | **交互式模式** | 降低学习成本 | 中 | `-i` 交互式提示 |

### 2.3 P2 - 高级功能（可选）

| # | 功能 | 用户价值 | 技术风险 |
|---|------|----------|----------|
| 11 | **跨项目对比报表** | 量化使用效果 | 中 |
| 12 | **推荐清理与归并** | 数据治理 | 中 |
| 13 | **TUI 界面** | 可视化浏览 | 高 |

### 2.4 不做清单（当前阶段明确不做）

| 功能 | 不做原因 |
|------|----------|
| HTTP/gRPC 服务化 | CLI 调用延迟可接受，服务化条件未满足 |
| PostgreSQL 后端 | SQLite 足以支持当前规模 |
| 多租户正式隔离 | Profile 机制足够 |
| 审批工作流集成 | 属于 VPS Agent Web 职责 |
| 实时协作同步 | 当前单机场景不需要 |

---

## 3. 架构设计

### 3.1 当前架构（CLI 形态）

```
┌─────────────────────────────────────────────────────────────────────┐
│                         调用方层                                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐  │
│  │ Claude Code │  │    Codex    │  │  lsclaw     │  │  Scripts  │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────┬─────┘  │
└─────────┼────────────────┼────────────────┼───────────────┼────────┘
          │                │                │               │
          └────────────────┴────────────────┴───────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   CLI Interface     │  ← argparse + JSON 输出
                    │   (memory_tool/cli) │
                    └──────────┬──────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
┌─────────▼─────────┐ ┌────────▼────────┐ ┌────────▼────────┐
│   Python API      │ │   State Files   │ │   SQLite DB     │
│  (operations.py)  │ │  (session/proj) │ │  (observations) │
│  - add_observation│ │                 │ │  - FTS5 索引    │
│  - run_search     │ │  ~/.claude_mem/ │ │  - 关系表       │
│  - run_timeline   │ │  ~/.codex_mem/  │ │  - 反馈日志     │
└───────────────────┘ └─────────────────┘ └─────────────────┘
```

### 3.2 关键组件

| 组件 | 当前文件 | 职责 | 变更计划 |
|------|----------|------|----------|
| CLI 层 | `cli.py` | 命令解析、参数验证、输出格式化 | 新增命令分组、--json 全局选项 |
| Python API | `operations.py` | 业务逻辑、数据操作 | 新增 MemoryClient 类 |
| 数据模型 | `models.py` | 类型定义 | 保持 |
| 存储层 | `database.py` | SQLite 连接、Schema | 新增索引优化 |
| 配置管理 | *新增* | 配置加载、Profile 管理 | `config.py` 新增 |
| 健康检查 | *新增* | 环境诊断 | `doctor.py` 新增 |
| 输出格式化 | *新增* | JSON/表格/颜色 | `output.py` 新增 |

### 3.3 服务化演进条件

当以下 5 个条件满足 **2-3 条**时，启动服务化演进：

| 条件 | 当前状态 | 阈值 |
|------|----------|------|
| 多个系统需要并发共享同一记忆库 | ❌ 否 | 3+ 项目同时依赖 |
| 需要正式 proposal/commit 审批流程 | ❌ 否 | 合规/审批需求出现 |
| 需要项目级权限与治理 | ❌ 否 | 多租户隔离需求 |
| 需要跨机器共享记忆 | ❌ 否 | 分布式团队场景 |
| CLI 调用延迟/可靠性成为瓶颈 | ❌ 否 | P99 > 500ms 或失败率 > 1% |

---

## 4. 详细设计方案

### 4.1 Doctor 命令

#### 检查项清单

| 检查类别 | 检查项 | 优先级 | 自动修复 |
|----------|--------|--------|----------|
| **Python 环境** | Python 版本 >= 3.8 | P0 | 否 |
| | 标准库可用 (sqlite3, json) | P0 | 否 |
| | pip 包安装状态 | P1 | 提示命令 |
| **SQLite** | SQLite 版本 >= 3.25 | P0 | 否 |
| | FTS5 扩展可用 | P0 | 否 |
| | WAL 模式支持 | P1 | 是 |
| **数据库** | 数据库文件存在 | P0 | 提示 init |
| | 数据库可读写 | P0 | 提示权限 |
| | Schema 版本正确 | P0 | 是（迁移） |
| | 数据库完整性 | P1 | 否 |
| **Profile** | Profile 配置存在 | P0 | 是（创建默认） |
| | 数据库路径可解析 | P0 | 否 |
| | 磁盘空间充足 | P1 | 否 |
| **功能** | FTS 索引正常 | P1 | 是（重建） |
| | 关键表存在 | P0 | 是（init） |

#### 输出格式

**人类可读**:
```
$ los-memory admin doctor

✓ Python 3.11.4
✓ SQLite 3.39.5
✓ Database: ~/.claude_memory/memory.db
✓ Profile: claude
✓ Schema version: 6
✓ Disk space: 45.2 GB available

ℹ 1 warning:
  FTS index size is large (1.2 GB), consider running 'vacuum'

All checks passed!
```

**JSON 输出**:
```json
{
  "ok": true,
  "status": "healthy",
  "capabilities": {
    "can_read": true,
    "can_write": true,
    "can_search": true,
    "can_migrate": true
  },
  "checks": {
    "python": {"ok": true, "version": "3.11.4"},
    "sqlite": {"ok": true, "version": "3.39.5", "fts5": true},
    "database": {"ok": true, "path": "...", "writable": true, "readable": true},
    "profile": {"ok": true, "name": "claude"},
    "schema": {"ok": true, "version": 6, "current": true}
  },
  "warnings": [{"code": "LARGE_FTS", "message": "..."}],
  "suggestions": ["Run 'vacuum' to optimize"]
}
```

**Machine-Readable Exit Summary**:

doctor 命令提供 `capabilities` 字段供调用方快速判断能力：

| 字段 | 类型 | 说明 | 使用场景 |
|------|------|------|----------|
| `can_read` | bool | 能否读取数据 | 判断是否可执行检索操作 |
| `can_write` | bool | 能否写入数据 | 判断是否可执行写入操作 |
| `can_search` | bool | 搜索功能是否正常 | FTS 索引是否可用 |
| `can_migrate` | bool | 能否执行迁移 | Schema 是否需要升级 |

**状态映射**:

| `status` | `ok` | 退出码 | 含义 | 建议动作 |
|----------|------|--------|------|----------|
| `healthy` | true | 0 | 完全健康 | 正常运行 |
| `degraded` | true | 0 | 降级运行（有警告）| 可运行，建议关注警告 |
| `unhealthy` | false | 2 | 不健康 | 需要修复后才能运行 |

### 4.2 JSON Schema 规范

#### 统一响应格式

**成功响应**:
```json
{
  "ok": true,
  "data": { ... },
  "meta": {
    "profile": "claude",
    "db_path": "~/.claude_memory/memory.db",
    "timestamp": "2026-03-07T10:00:00Z",
    "schema_version": 6,
    "query_time_ms": 23
  }
}
```

**错误响应**:
```json
{
  "ok": false,
  "error": {
    "code": "DB_NOT_FOUND",
    "message": "Database file not found: ~/.claude_memory/memory.db",
    "suggestion": "Run 'los-memory admin init' to create a new database",
    "help_command": "los-memory admin init --help",
    "docs_url": "https://..."
  },
  "meta": { ... }
}
```

> **注意**: `docs_url` 为可选字段，CLI 工具可能运行在离线环境。优先提供 `suggestion` 和 `help_command`。

#### 错误码与退出码映射矩阵

| JSON 错误码 | 退出码 | 含义 | 可重试 | 建议动作 |
|-------------|--------|------|--------|----------|
| `VAL_*` | 4 | 验证错误 | 否 | 检查参数，查看用法 |
| `NF_*` | 5 | 未找到 | 否 | 确认资源存在，尝试搜索 |
| `DB_NOT_FOUND` | 2 | 数据库不存在 | 否 | 运行 `admin init` |
| `DB_LOCKED` | 3 | 数据库锁定 | 是 | 等待重试或检查其他进程 |
| `DB_ERROR` | 3 | 数据库错误 | 否 | 检查权限，运行 `doctor` |
| `CFG_*` | 2 | 配置错误 | 否 | 检查配置，运行 `doctor` |
| `SYS_*` | 1 | 系统错误 | 否 | 查看日志，报告问题 |

#### 错误码分类

| 类别 | 前缀 | 示例 | HTTP 映射 |
|------|------|------|-----------|
| 验证错误 | `VAL_*` | VAL_MISSING_PARAM | 400 |
| 未找到 | `NF_*` | NF_OBSERVATION | 404 |
| 数据库 | `DB_*` | DB_LOCKED | 500 |
| 配置 | `CFG_*` | CFG_INVALID_PROFILE | 400 |
| 系统 | `SYS_*` | SYS_PYTHON_ERROR | 500 |

### 4.3 CLI 接口改进

#### 全局选项

```bash
los-memory [全局选项] <命令> [子命令] [选项]

全局选项:
  -p, --profile {claude,codex,shared}  Profile 选择
  -o, --output {json,table,yaml}       输出格式 (默认: json)
      --human, --table                 人类可读格式（表格）
      --color {auto,always,never}      颜色模式
      --config PATH                    配置文件路径
  -v, --verbose                        详细日志
      --debug                          调试模式
  -h, --help                           帮助
      --version                        版本
```

> **默认输出策略**: 为保持机器集成稳定，**默认始终输出 JSON**。人类可读格式通过 `--human` 或 `--table` 显式开启。

#### 配置优先级

配置项优先级（高到低）：

1. **命令行参数** (`--profile`, `--db`, `--output`)
2. **环境变量** (`MEMORY_PROFILE`, `MEMORY_DB_PATH`, `MEMORY_OUTPUT`)
3. **项目本地配置** (`./.memory/config.yaml`)
4. **用户全局配置** (`~/.config/los-memory/config.yaml`)
5. **系统默认配置** (`/etc/los-memory/config.yaml`)
6. **硬编码默认值**

> 这个优先级确保：临时覆盖 > 环境配置 > 项目配置 > 用户配置 > 系统配置 > 默认

#### 退出码规范

| 退出码 | 含义 | 处理建议 |
|--------|------|----------|
| 0 | 成功 | - |
| 1 | 业务错误 | 查看 error.message |
| 2 | 配置错误 | 运行 doctor 命令 |
| 3 | 数据库错误 | 检查 DB 路径和权限 |
| 4 | 验证错误 | 检查参数 |
| 5 | 未找到 | 确认资源存在 |
| 127 | 命令未找到 | 检查安装 |

#### 命令分组

```
los-memory
├── memory (默认)       - 核心记忆操作
│   ├── add, search, list, get, edit, delete
│   ├── timeline, feedback, link, related
│   └── export, import
├── session             - 会话管理
│   └── start, stop, list, show, resume
├── checkpoint          - 检查点管理
│   └── create, list, show, resume
├── tool                - 工具追踪
│   └── log, stats, suggest
├── project             - 项目管理
│   └── list, switch, stats, active
└── admin               - 管理维护
    ├── init, doctor, stats
    ├── vacuum, clean
    └── config
```

### 4.4 Python API 改进

#### MemoryClient 类

```python
from memory_tool import MemoryClient, MemoryError

# 同步客户端
with MemoryClient(profile="claude") as client:
    # 添加观察
    obs_id = client.add(
        title="架构决策",
        summary="选择了 PostgreSQL",
        kind="decision",
        project="myapp",
        tags=["db", "postgres"]
    )

    # 搜索
    results = client.search("数据库", limit=10)
    for obs in results.items:
        print(f"{obs.id}: {obs.title}")

    # 上下文管理自动关闭连接

# 异步客户端
async with AsyncMemoryClient(profile="claude") as client:
    obs = await client.get(123)
```

#### 异常层次

```python
class MemoryError(Exception):
    """Base exception for all memory errors."""
    code: str
    message: str
    suggestion: str

class ValidationError(MemoryError):
    """Invalid input or parameters."""
    code = "VAL_ERROR"

class NotFoundError(MemoryError):
    """Resource not found."""
    code = "NF_ERROR"

class DatabaseError(MemoryError):
    """Database operation failed."""
    code = "DB_ERROR"

class ConfigurationError(MemoryError):
    """Invalid configuration."""
    code = "CFG_ERROR"
```

### 4.5 多语言 SDK 设计

#### 通用设计原则

所有 SDK 遵循统一模式：
1. **CLI 包装**：调用 `los-memory` 命令，解析 JSON 输出
2. **类型安全**：完整的响应类型定义
3. **错误处理**：统一的错误类型和消息
4. **重试策略**：可配置的重试和超时

#### SDK 统一配置选项

所有语言 SDK 支持以下配置项：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `launcher_path` | string | `los-memory` | launcher 可执行文件路径 |
| `profile` | string | `claude` | Profile 名称 |
| `db_path` | string | - | 数据库路径（覆盖 profile） |
| `timeout` | duration | 30s | 命令执行超时 |
| `retry_count` | int | 3 | 重试次数 |
| `retry_delay` | duration | 1s | 重试间隔 |
| `working_dir` | string | - | 工作目录 |
| `env` | map[string]string | {} | 额外环境变量 |

> `launcher_path` 支持绝对路径、相对路径或 PATH 中的命令名

#### Go SDK 示例

```go
package memory

import (
    "context"
    "encoding/json"
    "os/exec"
)

type Client struct {
    Profile     string
    LauncherPath string
    Timeout     time.Duration
}

type Observation struct {
    ID        int64    `json:"id"`
    Title     string   `json:"title"`
    Summary   string   `json:"summary"`
    Tags      []string `json:"tags"`
    // ...
}

func (c *Client) Add(ctx context.Context, req AddRequest) (*Observation, error) {
    args := []string{"memory", "add", "--json", "--title", req.Title}
    // ... 构建参数

    cmd := exec.CommandContext(ctx, c.LauncherPath, args...)
    output, err := cmd.Output()
    if err != nil {
        return nil, parseError(err)
    }

    var result struct {
        OK   bool         `json:"ok"`
        Data Observation  `json:"data"`
    }
    if err := json.Unmarshal(output, &result); err != nil {
        return nil, err
    }

    return &result.Data, nil
}
```

#### Rust SDK 示例

```rust
use serde::{Deserialize, Serialize};
use std::process::Command;

pub struct Client {
    profile: String,
    launcher: String,
}

#[derive(Debug, Deserialize)]
pub struct Observation {
    pub id: i64,
    pub title: String,
    pub summary: String,
    pub tags: Vec<String>,
}

impl Client {
    pub fn add(&self, req: AddRequest) -> Result<Observation, MemoryError> {
        let output = Command::new(&self.launcher)
            .args(&["memory", "add", "--json"])
            .arg("--title").arg(&req.title)
            .output()?;

        if !output.status.success() {
            return Err(parse_error(&output.stderr));
        }

        let result: ApiResponse<Observation> = serde_json::from_slice(&output.stdout)?;
        Ok(result.data)
    }
}
```

#### Node.js SDK 示例

```typescript
import { spawn } from 'child_process';

interface Observation {
  id: number;
  title: string;
  summary: string;
  tags: string[];
}

class MemoryClient {
  constructor(private profile: string = 'claude') {}

  async add(request: AddRequest): Promise<Observation> {
    const args = ['memory', 'add', '--json', '--title', request.title];
    const result = await this.runCommand(args);
    return result.data;
  }

  private runCommand(args: string[]): Promise<any> {
    return new Promise((resolve, reject) => {
      const proc = spawn('los-memory', args, {
        env: { ...process.env, MEMORY_PROFILE: this.profile }
      });

      let stdout = '';
      proc.stdout.on('data', (data) => { stdout += data; });

      proc.on('close', (code) => {
        if (code !== 0) {
          reject(new MemoryError(`Exit code: ${code}`));
        } else {
          resolve(JSON.parse(stdout));
        }
      });
    });
  }
}
```

### 4.6 数据库优化

#### 新增索引

```sql
-- 时间线查询优化
CREATE INDEX IF NOT EXISTS idx_observations_project_timestamp
ON observations(project, timestamp DESC);

-- 类型过滤优化
CREATE INDEX IF NOT EXISTS idx_observations_project_kind_timestamp
ON observations(project, kind, timestamp DESC);

-- 标签搜索优化
CREATE INDEX IF NOT EXISTS idx_observations_tags_text
ON observations(tags_text);

-- Session 查询优化
CREATE INDEX IF NOT EXISTS idx_observations_session_id
ON observations(session_id) WHERE session_id IS NOT NULL;

-- 工具调用查询优化
CREATE INDEX IF NOT EXISTS idx_tool_calls_project_timestamp
ON tool_calls(project, timestamp DESC);

-- 关联查询优化
CREATE INDEX IF NOT EXISTS idx_links_from_id
ON observation_links(from_id);
```

#### PRAGMA 配置

```python
# 连接时优化设置
def optimize_connection(conn):
    conn.execute("PRAGMA journal_mode=WAL")          # WAL 模式提高并发
    conn.execute("PRAGMA synchronous=NORMAL")        # 平衡性能和安全
    conn.execute("PRAGMA temp_store=MEMORY")         # 临时表放内存
    conn.execute("PRAGMA cache_size=-64000")         # 64MB 缓存
    conn.execute("PRAGMA mmap_size=268435456")       # 256MB 内存映射
```

---

## 5. 测试策略

### 5.1 测试金字塔

```
                    /\
                   /  \
                  / E2E \        <- CLI 端到端测试 (10%)
                 /________\          完整命令流程验证
                /          \
               / Integration \   <- 集成测试 (20%)
              /______________\     模块协作 + 数据库
             /                \
            /    Unit Tests     \  <- 单元测试 (70%)
           /____________________\    核心逻辑 + 边界条件

        额外维度:
        - Contract Tests: JSON Schema 契约测试
        - Performance Tests: 基准测试与回归
```

### 5.2 各层测试策略

| 层级 | 工具 | 目标覆盖率 | 关键测试 |
|------|------|-----------|----------|
| 单元测试 | pytest | 80%+ | utils, models, 纯函数 |
| 集成测试 | pytest-bdd | 60%+ | operations + database |
| E2E测试 | subprocess | 40%+ | CLI 完整流程 |
| 契约测试 | jsonschema | 100% API | JSON 输出验证 |
| 性能测试 | pytest-benchmark | 关键路径 | 检索延迟 |

### 5.3 关键测试场景

#### Doctor 命令测试

```python
# tests/e2e/test_doctor.py
def test_doctor_all_checks_pass():
    """All checks pass - exit 0"""
    result = run_command(["admin", "doctor"])
    assert result.exit_code == 0
    assert "All checks passed" in result.stdout

def test_doctor_missing_database():
    """Missing database - exit 2 with suggestion"""
    with temp_env(MEMORY_DB_PATH="/nonexistent/test.db"):
        result = run_command(["admin", "doctor"])
        assert result.exit_code == 2
        assert result.json["status"] == "unhealthy"
        assert "init" in result.json["suggestions"][0]
```

#### JSON Schema 契约测试

```python
# tests/contract/test_json_schema.py
@pytest.mark.parametrize("command", [
    ["memory", "add", "--title", "Test", "--json"],
    ["memory", "search", "test", "--json"],
    ["admin", "doctor", "--json"],
])
def test_json_output_schema(command):
    """All commands produce valid JSON output."""
    result = run_command(command)
    data = json.loads(result.stdout)

    # 验证顶层结构
    assert "ok" in data
    assert "meta" in data
    assert "schema_version" in data["meta"]

    # 验证成功/错误响应
    if data["ok"]:
        assert "data" in data
    else:
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
```

### 5.4 CI/CD 质量门禁

```yaml
# .github/workflows/ci.yml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Unit Tests
        run: pytest tests/unit --cov=memory_tool --cov-fail-under=80

      - name: Integration Tests
        run: pytest tests/integration

      - name: E2E Tests
        run: pytest tests/e2e

      - name: Contract Tests
        run: pytest tests/contract

      - name: Performance Regression
        run: pytest tests/perf --benchmark-compare-fail=min:5%
```

---

## 6. 实施计划

### 6.1 里程碑 1: 稳定基础（2-3 周）

**目标**: 解决接入脆弱性问题，建立可靠基础

| 任务 | 负责人 | 估算 | 依赖 |
|------|--------|------|------|
| 实现 doctor 命令 | 后端工程师 | 2d | - |
| 创建 launcher 脚本 | CLI专家 | 1d | - |
| JSON 输出标准化 | 后端工程师 | 3d | - |
| 配置文件系统 | 后端工程师 | 2d | - |
| 新增数据库索引 | 后端工程师 | 1d | - |
| 单元测试覆盖 | 测试工程师 | 2d | - |

**成功标准**:
- `los-memory doctor` 能诊断 90% 以上的环境问题
- 所有命令输出稳定 JSON
- 单元测试覆盖率 > 80%

### 6.2 里程碑 2: 易用性（2-3 周）

**目标**: 提升用户体验，降低接入门槛

| 任务 | 负责人 | 估算 | 依赖 |
|------|--------|------|------|
| 人类可读输出 | CLI专家 | 3d | JSON 标准化 |
| 交互式模式 | CLI专家 | 2d | - |
| Shell 补全脚本 | CLI专家 | 2d | - |
| Go SDK 模板 | 后端工程师 | 2d | JSON 标准化 |
| Rust SDK 模板 | 后端工程师 | 2d | JSON 标准化 |
| Node.js SDK 模板 | 后端工程师 | 2d | JSON 标准化 |
| 集成测试 | 测试工程师 | 2d | SDK 完成 |

**成功标准**:
- 提供 3 个语言的官方 SDK 模板
- 默认输出人类可读格式
- tab 补全可用

### 6.3 里程碑 3: 质量保障（1-2 周）

**目标**: 建立完整的测试和质量保障体系

| 任务 | 负责人 | 估算 | 依赖 |
|------|--------|------|------|
| E2E 测试覆盖 | 测试工程师 | 3d | CLI 稳定 |
| 契约测试 | 测试工程师 | 2d | JSON Schema |
| 性能基准 | 测试工程师 | 2d | - |
| CI/CD 流水线 | 测试工程师 | 2d | 测试完成 |
| 文档完善 | 产品经理 | 3d | 功能完成 |

**成功标准**:
- CI 全绿，门禁通过
- 性能基线建立
- 文档完整

### 6.4 时间线

```
Week 1-2:  里程碑 1 - 稳定基础
          [doctor][launcher][JSON][config][index]

Week 3-4:  里程碑 2 - 易用性
          [human-output][interactive][completions][SDKs]

Week 5-6:  里程碑 3 - 质量保障
          [E2E][contract][perf][CI/CD][docs]
```

---

## 7. 成功指标

### 7.1 技术指标

| 指标 | 目标值 | 测量方式 |
|------|--------|----------|
| doctor 检查通过率 | > 95% | 自动化测试 |
| JSON 解析成功率 | > 99.5% | 契约测试 |
| 单元测试覆盖率 | > 80% | pytest-cov |
| 集成测试覆盖率 | > 60% | pytest-cov |
| 检索延迟 P95 | < 200ms | benchmark |

### 7.2 用户指标

| 指标 | 目标值 | 测量方式 |
|------|--------|----------|
| 新用户接入时间 | < 30 分钟 | 文档到首次写入 |
| 问题诊断时间 | < 5 分钟 | doctor 命令效率 |
| SDK 集成时间 | < 1 小时 | 从模板到调用 |

### 7.3 健康度指标

| 指标 | 目标值 |
|------|--------|
| CI 成功率 | > 95% |
| 向后兼容性 | 100%（无破坏性变更） |
| 文档完整性 | 所有公开接口有文档 |

---

## 8. 风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| 向后兼容破坏 | 高 | 中 | 严格的版本管理，弃用流程，自动化兼容性测试 |
| 数据锁定本地 | 中 | 高 | 完善的 export/import，服务化演进规划 |
| CLI 延迟问题 | 中 | 中 | doctor 诊断，未来服务化选项 |
| 多语言 SDK 维护负担 | 中 | 高 | 最小化 SDK，依赖 CLI 包装，社区贡献 |
| Schema 变更导致中断 | 高 | 中 | 版本控制，自动迁移，兼容性测试 |

---

## 9. 附录

### 9.1 参考文档

| 文档 | 路径 | 说明 |
|------|------|------|
| 架构边界规格书 | `docs/ARCHITECTURE_BOUNDARY_SPEC.md` | 四项目架构边界 |
| 评审报告 | `docs/ARCHITECTURE_BOUNDARY_REVIEW.md` | 评审过程与结论 |
| BDD 测试指南 | `tests/BDD_TESTING_GUIDE.md` | 现有测试规范 |

### 9.2 相关项目

- los-ast: 代码理解内核
- lsclaw: LLM 路由治理
- VPS Agent Web: 任务编排控制面

---

**文档结束**
