# los-memory 扩展能力指南

**版本**: v2.0.0
**状态**: 架构收敛阶段

---

## 概述

los-memory v2.0.0 引入**核心/扩展分层架构**，将部分能力从核心中分离，作为可选扩展提供。

| 层级 | 兼容性承诺 | 默认状态 | 说明 |
|------|-----------|----------|------|
| **核心** | 完整向后兼容 | 始终启用 | Observation, Session, Checkpoint, Feedback, Link, ToolCall |
| **扩展** | 尽力而为 | 默认启用，可禁用 | Incident, Recovery, Knowledge, Attribution |
| **迁出** | 12个月移除 | 默认启用，建议迁移 | Approval (迁移至 VPS Agent Web) |

---

## 扩展管理

### 列出扩展

```bash
los-memory admin extensions list
```

输出示例：
```json
{
  "ok": true,
  "extensions": [
    {"name": "incident", "status": "experimental", "enabled": true},
    {"name": "recovery", "status": "experimental", "enabled": true},
    {"name": "knowledge", "status": "experimental", "enabled": true},
    {"name": "attribution", "status": "experimental", "enabled": true}
  ]
}
```

### 查看扩展状态

```bash
los-memory admin extensions status
```

### 禁用扩展

通过环境变量禁用指定扩展：

```bash
# 禁用单个扩展
export MEMORY_DISABLE_EXTENSIONS="incident"

# 禁用多个扩展
export MEMORY_DISABLE_EXTENSIONS="incident,recovery,knowledge,attribution"

# 验证禁用状态
los-memory incident list
# 错误: argument command: invalid choice: 'incident'
```

---

## 扩展详情

### Incident (事故管理)

**归属**: 扩展层
**状态**: experimental
**CLI**: `los-memory incident [EXT]`

提供事故生命周期管理：
- 创建/查看事故
- 状态流转 (detected → analyzing → recovering → resolved → closed)
- 严重度分级 (p0-p3)
- 关联 Observation

**使用示例**:
```bash
# 创建事故
los-memory incident create \
  --type error \
  --severity p1 \
  --title "数据库连接超时" \
  --description "生产环境数据库响应时间超过5秒"

# 列出未解决事故
los-memory incident list --status detected

# 更新状态
los-memory incident status 123 resolved --notes "已重启连接池"
```

**注意**: 首次使用时会显示 experimental 警告。

---

### Recovery (自动恢复)

**归属**: 扩展层
**状态**: experimental
**CLI**: `los-memory recovery [EXT]`

提供恢复动作执行：
- 注册恢复动作
- 手动/自动执行恢复
- 恢复策略管理
- 执行历史追踪

**使用示例**:
```bash
# 列出可用恢复动作
los-memory recovery list-actions

# 手动执行恢复
los-memory recovery execute \
  --incident-id 123 \
  --actions "restart_service,clear_cache"
```

---

### Knowledge (知识库)

**归属**: 扩展层
**状态**: experimental
**CLI**: `los-memory knowledge [EXT]`

提供经验知识管理：
- 从已解决事故提取知识
- 搜索相关知识
- 知识条目版本管理

**使用示例**:
```bash
# 从事故创建知识条目
los-memory knowledge add --from-incident 123

# 搜索知识
los-memory knowledge search "数据库超时" --min-success 0.8
```

---

### Attribution (根因分析)

**归属**: 扩展层
**状态**: experimental, internal
**CLI**: `los-memory incident attribution [EXT]`

为 Incident 提供根因分析支持：
- 事件时间线重建
- 可能原因分析
- 关联 Observation 归因

**使用方式**: 通过 `incident` 命令的子命令访问
```bash
# 分析事故根因
los-memory incident analyze 123

# 生成归因报告
los-memory incident report 123
```

**注意**: Attribution 绑定 Incident，不能独立禁用。

---

### Approval (审批系统) - 正在迁出

**归属**: 迁出层
**状态**: deprecated, 12个月移除
**目标**: VPS Agent Web
**CLI**: `los-memory approval [DEPRECATED]`

⚠️ **重要**: Approval 系统正在迁移至 VPS Agent Web，将在 12 个月后从 los-memory 移除。

**当前状态**:
- 功能仍可用，但显示废弃警告
- 建议开始迁移至 VPS Agent Web
- 详见 [MIGRATION_APPROVAL.md](./MIGRATION_APPROVAL.md)

**禁用**:
```bash
export MEMORY_DISABLE_EXTENSIONS="approval"
```

---

## 兼容性承诺

### 核心层

**承诺级别**: 完整向后兼容

- 所有核心功能承诺向后兼容
- CLI 命令和参数保持稳定
- 数据库 Schema 变更提供迁移脚本
- 重大变更通过版本号明确标识 (v2 → v3)

### 扩展层

**承诺级别**: 尽力而为

- 扩展功能可能随版本变更
- 重大变更会提前一个版本发布通知
- 实验性功能可能在任何版本变更或移除
- 建议生产环境谨慎使用

### 迁出层

**承诺级别**: 限时兼容

- Approval 系统将在 12 个月后移除
- 移除前提供数据导出工具
- 不承诺新功能开发

---

## 开发扩展

### 扩展结构

扩展位于 `memory_tool/extensions/{name}/`：

```
extensions/
├── __init__.py          # 扩展元数据
├── {name}/
│   ├── __init__.py      # 扩展入口，导出 register/handler
│   ├── models.py        # 数据模型
│   └── cli.py           # CLI 命令和处理器
```

### 扩展元数据

每个扩展必须定义元数据：

```python
# extensions/my_feature/__init__.py
EXTENSION_NAME = "my_feature"
EXTENSION_VERSION = "1.0.0"
EXTENSION_STATUS = "experimental"  # 或 "stable", "deprecated"

from .cli import add_my_feature_subcommands, handle_my_feature_command

__all__ = [
    "add_my_feature_subcommands",
    "handle_my_feature_command",
    "EXTENSION_NAME",
    "EXTENSION_VERSION",
    "EXTENSION_STATUS",
]
```

### 注册扩展

在 `extensions/__init__.py` 的注册表中添加：

```python
_EXTENSION_REGISTRY = {
    "my_feature": (
        "memory_tool.extensions.my_feature",
        "add_my_feature_subcommands",
        "handle_my_feature_command",
        "experimental"
    ),
}
```

---

## 故障排查

### 扩展命令不可用

**现象**: `los-memory incident` 提示 "invalid choice"

**原因**: 扩展被禁用

**解决**:
```bash
# 检查禁用列表
echo $MEMORY_DISABLE_EXTENSIONS

# 清空禁用列表
unset MEMORY_DISABLE_EXTENSIONS
```

### 扩展警告过多

**现象**: 每次使用都显示 experimental 警告

**解决**:
```bash
# 警告只显示一次 per session，可忽略
# 或禁用不需要的扩展
export MEMORY_DISABLE_EXTENSIONS="incident,recovery"
```

### 扩展依赖错误

**现象**: `ImportError` when loading extension

**原因**: 扩展依赖的核心模块变更

**解决**: 报告 issue，临时禁用该扩展

---

## 相关文档

- [ARCHITECTURE_BOUNDARY_SPEC.md](./ARCHITECTURE_BOUNDARY_SPEC.md) - 架构边界规范
- [MIGRATION_APPROVAL.md](./MIGRATION_APPROVAL.md) - Approval 迁移指南
- [EXECUTION_PLAN.md](../design/EXECUTION_PLAN.md) - 执行计划

---

**最后更新**: 2026-03-07
