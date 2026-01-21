# AI Daily Digest

自动爬取 X (Twitter) 上的 AI 相关内容，用 Agent 进行深度研究，生成结构化报告同步到 Notion。

## 核心流程

```
Twitter爬取 → LLM筛选(Haiku) → Agent研究(Sonnet) → Notion/Markdown输出
```

## 功能特性

- **数据采集**: twscrape 爬取 For You + Following Feed（默认 400 条/次）
- **智能筛选**: Claude Haiku 判断内容是否与 AI/ML 相关（~$0.04/批次）
- **深度研究**: Claude Sonnet Agent 抓取链接、联网搜索、生成深度分析（~$0.48/条）
- **报告输出**: 按主题聚类，同步到 Notion + 本地 Markdown
- **断点续传**: 支持从中断处恢复

## 安装

```bash
# 克隆项目
git clone https://github.com/0xZoharHuang/AI-Digest.git
cd AI-Digest

# 安装依赖
pip install -e .

# 或使用 uv（推荐）
uv pip install -e .
```

## 配置

### 1. Twitter 账号配置

```bash
cp config/twitter_accounts.example.json config/twitter_accounts.json
```

编辑 `config/twitter_accounts.json`：

```json
{
    "accounts": [
        {
            "username": "your_username",
            "password": "your_password",
            "email": "your_email@example.com"
        }
    ],
    "for_you_limit": 200,
    "following_limit": 200,
    "time_range_hours": 48
}
```

**重要**: 建议使用专用小号，避免主力账号被封风险。

### 2. Notion 配置（可选）

```bash
cp config/notion_config.example.json config/notion_config.json
```

编辑 `config/notion_config.json`：

```json
{
    "token": "your_notion_integration_token",
    "database_id": "your_database_id"
}
```

获取 Notion token：https://www.notion.so/my-integrations

### 3. Claude 认证

项目使用 Claude Agent SDK，支持两种认证方式：

**方式 A: OAuth（推荐）**
```bash
# 首次运行会自动弹出浏览器登录
python scripts/run_daily.py
```

**方式 B: API Key**
```bash
export ANTHROPIC_API_KEY="your_key"
```

## 使用

### 基本使用

```bash
# 完整运行
python scripts/run_daily.py

# 限制研究数量（测试用）
python scripts/run_daily.py --limit 3

# 跳过 Notion 同步
python scripts/run_daily.py --skip-notion

# 从断点恢复
python scripts/run_daily.py --resume
```

### 输出位置

- **Markdown**: `data/reports/YYYY-MM-DD.md`
- **Notion**: 自动创建到配置的 Database
- **历史记录**: `data/history.db` (SQLite)

## 成本估算

| 阶段 | 模型 | 单价估算 |
|------|------|---------|
| 筛选 | Claude Haiku | ~$0.04/批次（15条） |
| 研究 | Claude Sonnet | ~$0.48/条 |

**示例**：爬取 400 条 → 筛选出 50 条有价值 → 深研 50 条
- 筛选成本：~$0.12 (3 批次)
- 研究成本：~$24 (50 条)
- **总计**：~$25/天

## 项目结构

```
ai-digest/
├── src/
│   ├── crawler/      # Twitter 数据采集 (twscrape)
│   ├── filter/       # LLM 筛选 (Claude Haiku)
│   ├── agent/        # 深度研究 (Claude Sonnet)
│   ├── integrator/   # 报告聚合
│   ├── output/       # Notion + Markdown 输出
│   └── storage/      # SQLite 存储 + 进度追踪
├── scripts/
│   └── run_daily.py  # 主运行脚本
├── config/           # 配置文件
└── data/             # 数据目录
```

## 技术栈

- **爬虫**: twscrape（Twitter 非官方 API）
- **LLM**: Claude Agent SDK（Haiku + Sonnet）
- **存储**: SQLite
- **输出**: Notion API + Markdown

## 注意事项

1. **封号风险**: twscrape 使用非官方 API，有封号风险。建议使用专用小号。
2. **成本控制**: 可通过 `--limit` 参数控制研究数量。
3. **断点续传**: 每条研究完成后自动保存进度，中断后可用 `--resume` 恢复。

## License

MIT
