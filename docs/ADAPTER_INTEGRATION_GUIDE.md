# Approval Adapter 对接集成指南

**文档版本**: 1.0
**适用版本**: los-memory >= 2.1.0
**最后更新**: 2026-03-07

---

## 目录

1. [快速开始](#快速开始)
2. [对接方式选择](#对接方式选择)
3. [环境配置](#环境配置)
4. [Python API 对接](#python-api-对接)
5. [HMAC 签名验证](#hmac-签名验证)
6. [双写模式配置](#双写模式配置)
7. [常见问题与故障排查](#常见问题与故障排查)
8. [性能优化建议](#性能优化建议)
9. [安全注意事项](#安全注意事项)

---

## 快速开始

### 最小配置示例

```python
from memory_tool.migrate_out.approval import (
    MigrationConfig, MigrationPhase, ApprovalMigrationAdapter
)
import sqlite3

# 1. 创建配置
config = MigrationConfig(
    phase=MigrationPhase.REMOTE_ONLY,  # 直接对接 VPS Agent Web
    vps_agent_web=VPSAgentWebConfig(
        url="https://your-vps-agent-web.example.com",
    ),
)

# 2. 创建适配器
conn = sqlite3.connect(":memory:")
adapter = ApprovalMigrationAdapter(config, conn)

# 3. 使用
result = adapter.create_request(
    job_id="deploy-123",
    command="deploy production",
    risk_level="high",
)
```

---

## 对接方式选择

| 对接方式 | 适用场景 | 复杂度 | 灵活性 |
|----------|----------|--------|--------|
| **Python API** | Python 项目 | 低 | 高 |
| **CLI 命令** | Shell 脚本 | 低 | 中 |
| **HTTP 直接调用** | 非 Python 项目 | 中 | 高 |
| **HMAC 签名验证** | 需要安全回调 | 中 | 中 |

### 决策流程

```
项目类型?
├── Python 项目
│   └── 使用: Python API 对接
├── Shell/脚本
│   └── 使用: CLI 命令
└── 其他语言
    └── 使用: HTTP 直接调用 VPS Agent Web
```

---

## 环境配置

### 必需环境变量

```bash
# VPS Agent Web 连接
export VPS_AGENT_WEB_URL="https://vps-agent-web.example.com"

# HMAC 密钥 (用于签名验证)
export APPROVAL_HMAC_SECRET="your-legacy-secret"
export VPS_AGENT_HMAC_SECRET="your-vps-secret"
```

### 可选环境变量

```bash
# 迁移阶段 (默认: local-only)
# 选项: local-only, dual-write, remote-only, removed
export APPROVAL_MIGRATION_PHASE="dual-write"

# 双写模式 (默认: strict)
# 选项: strict, local_preferred, remote_preferred, read_only
export APPROVAL_MIGRATION_MODE="strict"

# 超时设置 (秒)
export APPROVAL_MIGRATION_TIMEOUT="30"

# 重试次数
export APPROVAL_MIGRATION_RETRY="3"

# 禁用弃用警告
export MEMORY_APPROVAL_SILENCE_WARNING="1"
```

### 配置优先级

1. 代码中显式传入的参数 (最高优先级)
2. 环境变量
3. 配置文件 (如果存在)
4. 默认值 (最低优先级)

---

## Python API 对接

### 基本用法

```python
from memory_tool.migrate_out.approval import (
    MigrationConfig,
    MigrationPhase,
    ApprovalMigrationAdapter,
    VPSAgentWebConfig,
    HMACConfig,
)

# 配置
config = MigrationConfig(
    phase=MigrationPhase.DUAL_WRITE,
    vps_agent_web=VPSAgentWebConfig(
        url="https://vps-agent-web.example.com",
        timeout_seconds=30,
        retry_count=3,
    ),
    hmac=HMACConfig(
        legacy_active_secret="legacy-secret",
        vps_active_secret="vps-secret",
        vps_key_id="v1",
    ),
)

# 创建适配器
adapter = ApprovalMigrationAdapter(config, sqlite_conn)

# 创建审批请求
result = adapter.create_request(
    job_id="job-123",
    command="restart_service",
    risk_level="high",
    requested_by="user-456",
    context={"service": "api-gateway"},
)

# 批准请求
result = adapter.approve_request(
    job_id="job-123",
    actor_id="admin-789",
    version=1,
    reason="Verified safe",
)

# 查询状态
status = adapter.get_request_status("job-123")
```

### 健康检查

```python
# 检查后端健康
health = adapter.health_check()

if health["overall_healthy"]:
    print("✅ 所有后端正常")
else:
    if not health["local"]["healthy"]:
        print(f"⚠️ 本地后端异常: {health['local'].get('error')}")
    if not health["remote"]["healthy"]:
        print(f"⚠️ 远程后端异常: {health['remote'].get('error')}")

# 获取迁移状态
status = adapter.get_migration_status()
print(f"当前阶段: {status['phase']}")
print(f"操作模式: {status['mode']}")
```

### 错误处理

```python
from memory_tool.migrate_out.approval import (
    VPSAgentWebError,
    HMACVerificationError,
)

try:
    result = adapter.approve_request(...)
except HMACVerificationError as e:
    # HMAC 验证失败
    print(f"签名验证失败: {e}")
except VPSAgentWebError as e:
    # VPS Agent Web 调用失败
    print(f"远程调用失败: {e.message}")
    print(f"HTTP 状态: {e.status_code}")
    print(f"错误代码: {e.error_code}")
except RuntimeError as e:
    # 配置错误或其他运行时错误
    print(f"运行时错误: {e}")
```

---

## HMAC 签名验证

### 验证外部请求

```python
from memory_tool.migrate_out.approval import HMACBridge, HMACConfig

# 创建 HMAC 桥接器
config = HMACConfig(
    legacy_active_secret="shared-secret",
    vps_active_secret="vps-secret",
)
bridge = HMACBridge(config)

# 接收到的请求头
headers = {
    "X-Signature": "base64-signature",
    "X-Timestamp": "1709811600",
    "X-Nonce": "uuid-v4-string",
    "X-Key-Id": "v1",
}

# 请求体
payload = {
    "job_id": "job-123",
    "action": "approve",
    "actor_id": "user-456",
    "version": 1,
    "reason": "Verified safe",
}

# 验证
try:
    bridge.verify_local(headers, payload)
    print("✅ 签名验证通过")

    # 重新签名给 VPS Agent Web
    remote_headers = bridge.resign_for_remote(headers, payload)

except HMACVerificationError as e:
    print(f"❌ 验证失败: {e}")
    # 拒绝请求
```

### 生成签名

```python
# 为请求生成签名
payload = {
    "job_id": "job-123",
    "action": "approve",
    "actor_id": "user-456",
    "version": 1,
    "reason": "",
}

headers = bridge.generate_local_signature(payload)
print(headers["X-Signature"])   # base64 签名
print(headers["X-Timestamp"])   # Unix 时间戳
print(headers["X-Nonce"])       # UUID
```

---

## 双写模式配置

### 模式说明

| 模式 | 行为 | 适用场景 |
|------|------|----------|
| `strict` | 两个系统都必须成功 | 数据一致性要求高 |
| `local_preferred` | 本地成功即算成功 | 优先保证本地可用性 |
| `remote_preferred` | 远程成功即算成功 | 优先保证远程写入 |
| `read_only` | 禁止写入 | 维护窗口期 |

### 配置示例

```python
from memory_tool.migrate_out.approval import (
    MigrationConfig,
    MigrationPhase,
    DualWriteConfig,
    DualWriteMode,
)

# STRICT 模式 - 保证数据一致性
config = MigrationConfig(
    phase=MigrationPhase.DUAL_WRITE,
    dual_write=DualWriteConfig(
        mode=DualWriteMode.STRICT,
    ),
)

# LOCAL_PREFERRED 模式 - 本地优先
config = MigrationConfig(
    phase=MigrationPhase.DUAL_WRITE,
    dual_write=DualWriteConfig(
        mode=DualWriteMode.LOCAL_PREFERRED,
    ),
)
```

### 处理部分失败

```python
result = adapter.create_request(...)

if result.get("source") == "dual-write":
    local_success = result["local"]["success"]
    remote_success = result["remote"]["success"]

    if local_success and not remote_success:
        # 本地成功，远程失败
        # 记录日志，稍后重试同步
        print(f"⚠️ 远程写入失败: {result['remote']['result']}")

    elif not local_success and remote_success:
        # 本地失败，远程成功
        print(f"⚠️ 本地写入失败: {result['local']['result']}")
```

---

## 常见问题与故障排查

### Q1: HMAC 验证失败 "Timestamp in future"

**症状**:
```
HMACVerificationError: Timestamp 1709811600s is in the future
```

**原因**: 服务器时间与客户端时间不同步，超过 60 秒

**解决方案**:
1. 同步服务器时间: `ntpdate -s time.google.com`
2. 检查客户端时间设置
3. 增大容忍度 (不推荐): 修改 `MAX_CLOCK_SKEW`

### Q2: "Nonce has already been used"

**症状**:
```
HMACVerificationError: Nonce has already been used (replay attack detected)
```

**原因**: 同一 nonce 在 5 分钟内被重复使用

**解决方案**:
1. 确保每次请求使用新的 UUID 作为 nonce
2. 检查客户端是否正确生成唯一 nonce
3. 等待 5 分钟后重试

### Q3: 连接超时

**症状**:
```
VPSAgentWebError: Request failed after 3 attempts
```

**解决方案**:
```bash
# 增加超时时间
export APPROVAL_MIGRATION_TIMEOUT=60

# 或切换到本地优先模式
export APPROVAL_MIGRATION_MODE=local_preferred
```

### Q4: SQLite "objects created in a thread can only be used in that same thread"

**症状**: 多线程环境下出现此错误

**解决方案**:
```python
# 使用连接工厂而不是直接传入连接
def get_conn():
    return sqlite3.connect("/path/to/db.sqlite")

adapter = ApprovalMigrationAdapter(config, get_conn)
```

### Q5: 如何禁用弃用警告

**解决方案**:
```bash
export MEMORY_APPROVAL_SILENCE_WARNING=1
```

或在 Python 中:
```python
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
```

---

## 性能优化建议

### 1. 连接池

对于高并发场景，建议使用连接池:

```python
from urllib3 import PoolManager

# 自定义 HTTP 客户端使用连接池
# (需要扩展 VPSAgentWebClient)
```

### 2. 批量操作

避免频繁的小操作，尽量批量处理:

```python
# ❌ 不推荐: 逐个查询
for job_id in job_ids:
    status = adapter.get_request_status(job_id)

# ✅ 推荐: 批量查询
statuses = adapter.list_all_requests(limit=100)
```

### 3. 缓存策略

对于不频繁变化的数据，添加缓存:

```python
from functools import lru_cache

@lru_cache(maxsize=128)
def get_cached_status(job_id):
    return adapter.get_request_status(job_id)
```

---

## 安全注意事项

### 1. 密钥管理

- **永远不要** 将密钥硬编码在代码中
- 使用环境变量或密钥管理服务
- 定期轮换密钥 (建议 90 天)

```bash
# ✅ 推荐
export APPROVAL_HMAC_SECRET=$(cat /run/secrets/hmac_secret)

# ❌ 不推荐
HMAC_SECRET = "hardcoded-secret"  # 危险！
```

### 2. TLS/SSL

- 始终使用 HTTPS 连接 VPS Agent Web
- 验证服务器证书
- 不要使用自签名证书 (生产环境)

### 3. 输入验证

- 验证所有用户输入
- 限制 job_id 长度和字符集
- 防止 SQL 注入 (使用参数化查询)

### 4. 审计日志

记录所有关键操作:

```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("approval")

# 记录审批操作
logger.info(f"Approval created: job_id={job_id}, actor={actor_id}")
```

### 5. 最小权限原则

- 使用专用的服务账户
- 限制数据库权限
- 定期审查访问日志

---

## 升级与迁移

### 从旧版本升级

```bash
# 1. 更新代码
pip install --upgrade los-memory

# 2. 更新配置
export APPROVAL_MIGRATION_PHASE=dual-write

# 3. 验证
python -c "from memory_tool.migrate_out.approval import ApprovalMigrationAdapter; print('✅ 导入成功')"
```

### 迁移到 VPS Agent Web

```python
# 1. 导出数据
adapter = ApprovalMigrationAdapter(config, conn)
status = adapter.get_migration_status()

# 2. 验证数据一致性
if status.get("statistics", {}).get("sync_needed"):
    print("⚠️ 需要同步数据")

# 3. 切换到远程模式
config.phase = MigrationPhase.REMOTE_ONLY
```

---

## 获取帮助

- **GitHub Issues**: https://github.com/CanCpuMemory/los-memory/issues
- **文档**: https://docs.vps-agent-web.example.com/migration
- **邮件支持**: migration@example.com

---

**文档维护**: 如有更新需求，请提交 PR 或 Issue。
