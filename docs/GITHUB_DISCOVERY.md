# GitHub Early-Stage Project Discovery System

## 概述

GitHub Discovery 是一个 Agent 驱动的自动化系统，用于发现 GitHub 上 100-1000 stars 的有价值的早期 AI 研究项目。

## 核心特性

- **Agent 驱动**: 使用 Claude Agent SDK，让 Agent 自主决策搜索、评估和深度研究
- **深度评估**: 不依赖预设规则，Agent 使用工具（Bash, WebFetch, WebSearch, Read, Glob）进行深度调研
- **质量优先**: 只输出创新得分 >= 8 的项目，宁缺毋滥
- **自动去重**: 使用 SQLite 追踪已发现的项目，避免重复
- **定时任务**: 每 3 天自动运行一次（周一/周四/周日 10:00）
- **Notion 同步**: 可选的 Notion 页面自动创建

## 架构设计

### Agent 工作流程

```
定时任务触发
  ↓
GitHubDiscoveryAgent
  ├─ Bash: 调用 GitHub API 搜索候选项目
  ├─ WebFetch: 获取 README 快速筛选
  ├─ WebSearch: 查询作者背景和社区讨论
  ├─ 评分判断: 技术创新度(60%) + 代码质量(20%) + 作者背景(20%)
  ├─ 评分 >= 8?
  │   ├─ 是: Bash克隆repo → Read代码 → 生成深度报告
  │   └─ 否: skip，继续下一个
  ↓
输出: 10-15 个高质量项目的详细报告
  ↓
去重 + 存储到 HistoryDB
  ↓
（可选）同步到 Notion
```

### 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| Discovery Agent | `src/agent/github_discovery_agent.py` | Agent 驱动的发现和研究 |
| 主入口 | `scripts/run_github_discovery.py` | 执行发现流程 |
| 数据库扩展 | `src/storage/history_db.py` | early_repos 表 |
| 定时任务 | `src/scheduler/scheduler.py` | 每 3 天运行 |
| Notion 输出 | `src/output/github_notion_sync.py` | 创建 Notion 页面 |

## 快速开始

### 1. 配置 GitHub Token

访问 https://github.com/settings/tokens 创建 Personal Access Token。

**需要的权限**:
- `repo` (read): 读取仓库信息
- `user` (read): 查询用户信息

**创建配置文件**:

```bash
cp config/github_config.json.example config/github_config.json
```

编辑 `config/github_config.json`:

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

### 2. （可选）配置 Notion

如果要自动同步到 Notion，需要：

1. 在 Notion 中创建新的 Database（用于 GitHub discoveries）
2. 添加以下属性：
   - `Name`: title
   - `Stars`: number
   - `Score`: number
   - `URL`: url

3. 更新 `config/notion_config.json`:

```json
{
  "token": "secret_xxx",
  "database_id": "your_main_database_id",
  "github_database_id": "your_github_discoveries_database_id"
}
```

4. 在 `config/github_config.json` 中启用同步:

```json
{
  "sync_to_notion": true
}
```

### 3. 手动运行测试

```bash
python scripts/run_github_discovery.py
```

**预期输出**:

```
╭─────────────────────────────────────────────────╮
│  GitHub Early-Stage Project Discovery          │
│  Finding valuable AI projects with 100-1000 stars │
╰─────────────────────────────────────────────────╯

Configuration:
  Stars range: 100-1000
  Target count: 15
  Model: sonnet

Starting discovery...

[Agent开始工作，自主搜索和评估...]

Discovery completed. Cost: $18.50

Found 12 high-value projects

╭─────────────────────────────────────────────────╮
│  Discovery Complete                             │
│                                                 │
│  Total scanned: 87                              │
│  High-value found: 12                           │
│  New discoveries: 12                            │
│  Duplicates skipped: 0                          │
╰─────────────────────────────────────────────────╯

Results saved to: data/github_discoveries/discovery_20250128_103045.json
```

### 4. 启用定时任务

系统已自动配置定时任务，每 3 天（周一/周四/周日 10:00）运行一次。

要启动调度器：

```bash
python scripts/run_scheduler.py
```

## 评估标准

Agent 会对每个项目进行 1-10 分评估，只有 >= 8 分的才会深度研究：

### 评分维度

| 维度 | 权重 | 评估内容 |
|------|------|----------|
| **技术创新度** | 60% | 解决什么新问题？提出什么新方法？与现有方案的差异？ |
| **代码质量** | 20% | 测试覆盖？文档完善度？代码结构？ |
| **作者背景** | 20% | 研究者/工程师/学生？过往项目质量？社区影响力？ |

### 快速过滤规则

Agent 会自动跳过：
- 名称含 "example/demo/tutorial/template"
- README < 200 字符或无实质内容
- 大公司的官方项目（我们要找早期项目）
- 课程作业或学习项目

## 成本估算

基于 Sonnet 模型：

- 每次运行: ~$15-20
- 月度成本（每 3 天运行）: ~$150-200

成本浮动取决于 Agent 发现多少高分项目（需要深度研究）。

## 数据存储

### SQLite 数据库 (`data/history.db`)

**early_repos 表**:

```sql
CREATE TABLE early_repos (
    id INTEGER PRIMARY KEY,
    repo_id TEXT UNIQUE NOT NULL,         -- owner/repo
    full_name TEXT,                        -- 完整名称
    url TEXT,                              -- GitHub URL
    stars INTEGER,                         -- star 数量
    innovation_score INTEGER,              -- 创新得分 (1-10)
    innovation_summary TEXT,               -- 创新摘要
    discovered_at TIMESTAMP,               -- 发现时间
    notion_page_id TEXT                    -- Notion 页面 ID
);
```

### JSON 结果文件 (`data/github_discoveries/`)

每次运行会保存完整的结果到 JSON 文件：

```json
{
  "timestamp": "2025-01-28T10:30:45",
  "config": {
    "min_stars": 100,
    "max_stars": 1000,
    "target_count": 15
  },
  "summary": {
    "total_scanned": 87,
    "total_discovered": 12,
    "new_discoveries": 12
  },
  "discoveries": [
    {
      "repo_id": "owner/repo",
      "full_name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "stars": 250,
      "innovation_score": 9,
      "innovation_summary": "...",
      "code_quality": "...",
      "author_background": "...",
      "research_report": "...",
      "recommendation": "..."
    }
  ]
}
```

## 故障排查

### 问题：GitHub API 限流

**症状**: Agent 报告 403 或 rate limit 错误

**解决**:
- 检查 token 是否有效
- GitHub API 限制：5000 次/小时（authenticated）
- 系统会自动实现指数退避

### 问题：Notion 同步失败

**症状**: "Failed to create Notion page"

**检查**:
1. `notion_config.json` 中的 token 和 database_id 是否正确
2. Database 是否有正确的属性（Name, Stars, Score, URL）
3. Notion integration 是否有权限访问 database

### 问题：Agent 成本过高

**症状**: 单次运行超过 $25

**调整**:
- 减少 `target_count`（默认 15）
- 调整 `max_stars` 范围（减少候选项目）
- 使用 `model: "haiku"` (更便宜但质量稍低)

### 问题：磁盘空间不足

**症状**: `/tmp/ai-digest-repos` 占用过多空间

**说明**:
- Agent 会克隆 repo 进行深度研究
- 系统使用 LRU 策略，自动保留最新 5 个 repos
- Weekly cleanup 会清空所有 repos

**手动清理**:
```bash
rm -rf /tmp/ai-digest-repos/*
```

## 监控和维护

### 查看最近发现的项目

```bash
sqlite3 data/history.db "SELECT repo_id, stars, innovation_score, discovered_at FROM early_repos ORDER BY discovered_at DESC LIMIT 10;"
```

### 检查数据库统计

```bash
sqlite3 data/history.db "SELECT COUNT(*) as total, AVG(innovation_score) as avg_score FROM early_repos;"
```

### 查看调度器状态

```bash
# 在 run_scheduler.py 运行时，会显示所有已配置的任务
python scripts/run_scheduler.py
```

## 高级配置

### 自定义搜索条件

在 `src/agent/github_discovery_agent.py` 的 prompt 中，可以调整 GitHub API 查询：

```python
# 原始查询
q=topic:llm+OR+topic:agent+OR+topic:ai

# 可以添加更多 topics
q=topic:llm+OR+topic:rag+OR+topic:transformers

# 按语言筛选
language:python
language:typescript
language:rust

# 按时间筛选
pushed:>2025-01-01
created:>2024-06-01
```

### 调整评分权重

在 prompt 中修改评分标准：

```
技术创新度（60%） → 可调整为 70%
代码质量（20%）   → 可调整为 15%
作者背景（20%）   → 可调整为 15%
```

## 未来扩展

计划中的功能：

1. **持续监控** (Phase 2):
   - 追踪已发现项目的 stars 增长
   - "即将爆火" 项目 alert

2. **社交发现** (Phase 2):
   - 追踪知名研究者的新 repos
   - 从优质项目的 star 列表发现

3. **趋势分析** (Phase 3):
   - 技术趋势报告
   - 领域热点识别

## 相关文档

- [完整实施计划](../CLAUDE.md) - 原始设计文档
- [Agent SDK 文档](https://github.com/anthropics/claude-agent-sdk)
- [GitHub API 文档](https://docs.github.com/en/rest)
- [Notion API 文档](https://developers.notion.com/)
