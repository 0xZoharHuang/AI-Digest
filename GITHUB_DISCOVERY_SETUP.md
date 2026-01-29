# GitHub Discovery System - Setup Guide

## 🎯 快速开始（5 分钟）

### 1. 获取 GitHub Token

1. 访问 https://github.com/settings/tokens/new
2. 选择 "Generate new token (classic)"
3. 设置权限：
   - ✅ `repo` (read)
   - ✅ `user` (read)
4. 点击 "Generate token"
5. 复制 token（类似 `ghp_xxxxxxxxxxxx`）

### 2. 配置文件

```bash
# 复制配置模板
cp config/github_config.json.example config/github_config.json

# 编辑配置，填入你的 GitHub token
nano config/github_config.json
```

配置内容：
```json
{
  "github_token": "ghp_your_actual_token_here",
  "min_stars": 100,
  "max_stars": 1000,
  "target_count": 15,
  "model": "sonnet",
  "sync_to_notion": false
}
```

### 3. 手动测试运行

```bash
# 运行一次发现流程
python scripts/run_github_discovery.py
```

**预期结果**：
- Agent 开始搜索 GitHub API
- 评估候选项目（快速过滤 + 深度评估）
- 输出 10-15 个高质量项目
- 成本：约 $15-20

### 4. （可选）配置 Notion 同步

如果要同步到 Notion：

1. **创建 Notion Database**：
   - 在 Notion 中新建 Database
   - 添加属性：
     - `Name` (title)
     - `Stars` (number)
     - `Score` (number)
     - `URL` (url)

2. **获取 Database ID**：
   - 打开 Database 页面
   - URL 格式：`https://notion.so/workspace/{database_id}?v=...`
   - 复制 `{database_id}` 部分

3. **更新配置**：

```bash
# 编辑 notion_config.json
nano config/notion_config.json
```

```json
{
  "token": "secret_xxx",
  "database_id": "your_main_database_id",
  "github_database_id": "your_github_discoveries_database_id"
}
```

4. **启用同步**：

```bash
# 编辑 github_config.json
nano config/github_config.json
```

```json
{
  "sync_to_notion": true
}
```

### 5. 启用定时任务

系统已配置为每 3 天（周一/周四/周日 10:00）自动运行。

启动调度器：

```bash
python scripts/run_scheduler.py
```

---

## 📁 新增文件清单

### 核心模块

| 文件 | 说明 | 状态 |
|------|------|------|
| `src/agent/github_discovery_agent.py` | Agent 驱动的发现和研究 | ✅ |
| `scripts/run_github_discovery.py` | 主入口脚本 | ✅ |
| `src/output/github_notion_sync.py` | Notion 同步模块 | ✅ |

### 数据库扩展

| 文件 | 说明 | 状态 |
|------|------|------|
| `src/storage/history_db.py` | 新增 early_repos 表 | ✅ 已扩展 |

### 调度器扩展

| 文件 | 说明 | 状态 |
|------|------|------|
| `src/scheduler/scheduler.py` | 新增 github_scan 任务 | ✅ 已扩展 |

### 配置文件

| 文件 | 说明 | 状态 |
|------|------|------|
| `config/github_config.json.example` | GitHub 配置模板 | ✅ |
| `config/notion_config.example.json` | 更新：添加 github_database_id | ✅ |

### 文档

| 文件 | 说明 | 状态 |
|------|------|------|
| `docs/GITHUB_DISCOVERY.md` | 完整功能文档 | ✅ |
| `GITHUB_DISCOVERY_SETUP.md` | 快速设置指南 | ✅ |

---

## 🔍 系统架构

```
定时任务 (每3天)
    ↓
GitHubDiscoveryAgent
    ├─ Bash: GitHub API 搜索
    ├─ WebFetch: 读取 README
    ├─ WebSearch: 背景调查
    ├─ 评分判断 (1-10)
    ├─ 评分 >= 8?
    │   ├─ 是: 克隆 → 代码阅读 → 深度报告
    │   └─ 否: skip
    ↓
输出结果 (10-15 个项目)
    ↓
去重 (HistoryDB)
    ↓
存储 (SQLite + JSON)
    ↓
（可选）同步到 Notion
```

---

## 🧪 测试验证

### 测试 1: 模块导入

```bash
python -c "
from src.agent import GitHubDiscoveryAgent
from src.output import GitHubNotionSync
from src.storage.history_db import HistoryDB
print('✓ All imports OK')
"
```

### 测试 2: 数据库功能

```bash
python -c "
import asyncio
from src.storage.history_db import HistoryDB

async def test():
    db = HistoryDB('data/test_history.db')
    await db.init_db()
    await db.mark_repo_tracked(
        'test/repo', 'test/repo', 'https://github.com/test/repo',
        100, 8, 'Test innovation'
    )
    assert await db.is_repo_tracked('test/repo')
    print('✓ Database tests passed')

asyncio.run(test())
"
```

### 测试 3: 配置文件

```bash
# 检查配置文件是否存在
ls -la config/github_config.json

# 验证 JSON 格式
python -c "
import json
with open('config/github_config.json') as f:
    config = json.load(f)
    assert 'github_token' in config
    assert config['github_token'] != 'ghp_xxxx'
    print('✓ Config file valid')
"
```

---

## 💰 成本估算

| 项目 | 说明 | 估算 |
|------|------|------|
| 搜索 100 个 repos | Agent 调用 GitHub API | 少量 tokens |
| 快速过滤 (~30 个) | WebFetch + 初步判断 | ~$5 |
| 深度研究 (10-15 个) | 克隆 + 代码阅读 | ~$15 |
| **单次总计** | | **~$20** |
| **月度成本** | 每 3 天 × 10 次/月 | **~$200** |

---

## 🛠️ 故障排查

### 问题：找不到配置文件

```
Error: config/github_config.json not found
```

**解决**：
```bash
cp config/github_config.json.example config/github_config.json
nano config/github_config.json  # 填入 GitHub token
```

### 问题：GitHub API 限流

```
403 Forbidden / Rate limit exceeded
```

**解决**：
- 等待 1 小时（API 限制重置）
- 检查 token 是否有效
- 确认 token 有正确权限（repo, user）

### 问题：成本过高

**症状**：单次运行超过 $25

**调整**：
```json
{
  "target_count": 10,    // 减少目标数量
  "max_stars": 500,      // 缩小范围
  "model": "haiku"       // 使用更便宜的模型
}
```

### 问题：Agent 运行时间过长

**症状**：超过 30 分钟还未完成

**可能原因**：
- Agent 发现了太多高分项目（需要深度研究）
- 克隆大型 repos（超过 200MB）

**解决**：
- 在 prompt 中强调 "快速过滤"
- 调整 stars 范围，避开热门项目

---

## 📊 监控命令

### 查看最近发现的项目

```bash
sqlite3 data/history.db "
SELECT repo_id, stars, innovation_score, discovered_at
FROM early_repos
ORDER BY discovered_at DESC
LIMIT 10;
"
```

### 统计数据

```bash
sqlite3 data/history.db "
SELECT
  COUNT(*) as total,
  AVG(innovation_score) as avg_score,
  MAX(stars) as max_stars
FROM early_repos;
"
```

### 查看调度任务

```bash
# 运行调度器时会显示所有任务
python scripts/run_scheduler.py
```

---

## 📝 下一步

1. **测试运行**：
   ```bash
   python scripts/run_github_discovery.py
   ```

2. **检查结果**：
   - 查看 `data/github_discoveries/discovery_*.json`
   - 检查 SQLite 数据库

3. **启用定时任务**：
   ```bash
   python scripts/run_scheduler.py
   ```

4. **（可选）配置 Notion 同步**

---

## 📖 更多文档

- [完整功能文档](docs/GITHUB_DISCOVERY.md) - 详细的系统说明
- [项目 README](README.md) - 整体项目介绍
- [CLAUDE.md](CLAUDE.md) - 项目指导原则

---

## 🆘 获取帮助

如有问题：
1. 查看 `docs/GITHUB_DISCOVERY.md` 故障排查部分
2. 检查日志输出
3. 提交 issue 到项目仓库
