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
- **线程支持**: 自动检测并获取完整 Twitter 线程内容
- **报告输出**: 按主题聚类，同步到 Notion + 本地 Markdown
- **断点续传**: 支持从中断处恢复
- **风险控制**: 随机延迟、账号健康监控、指数退避

## 快速开始

```bash
# 1. 克隆并安装
git clone https://github.com/0xZoharHuang/AI-Digest.git
cd AI-Digest
pip install -e .

# 2. 配置 Twitter 账号
cp config/twitter_accounts.example.json config/twitter_accounts.json
# 编辑填入你的 Twitter 小号凭证

# 3. 测试运行（限制 3 条，跳过 Notion）
python scripts/run_daily.py --limit 3 --skip-notion
```

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
    "time_range_hours": 48,
    "delay": {
        "min_seconds": 2.0,
        "max_seconds": 7.0,
        "page_delay_seconds": 15.0
    }
}
```

**配置说明**：
- `for_you_limit` / `following_limit`: 每个 Feed 爬取的推文数量
- `time_range_hours`: 只处理最近 N 小时内的推文
- `delay`: 请求延迟配置（降低封号风险）
  - `min_seconds` / `max_seconds`: 请求间隔范围
  - `page_delay_seconds`: 翻页额外延迟

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

## 线程处理

系统自动检测并获取完整的 Twitter 线程（作者连续回复自己的推文）：

**检测模式**：
- 包含 🧵 符号
- 包含 "thread"、"1/N" 等标记
- 提到 "分享 N 个技巧/策略" 等

**工作原理**：
1. Crawler 层检测线程模式
2. 使用 `conversation_id` 获取同一对话中作者的所有推文
3. 合并为完整内容传给 Agent 进行研究

## 风险控制

系统内置多层风险控制机制：

| 机制 | 说明 |
|------|------|
| **随机延迟** | 2-7 秒 + 高斯抖动，模拟人类行为 |
| **账号健康监控** | 追踪错误率，计算风险分数 (0-100) |
| **指数退避** | 遇到限流时逐步增加等待时间 |
| **错误分类** | 自动识别验证码、限流、封号等信号 |

**风险等级**：
- 0-30: 安全
- 31-60: 低风险
- 61-80: 中风险（建议冷却）
- 81-100: 高风险（停止使用）

## 注意事项

1. **封号风险**: twscrape 使用非官方 API，有封号风险。建议使用专用小号。
2. **成本控制**: 可通过 `--limit` 参数控制研究数量。
3. **断点续传**: 每条研究完成后自动保存进度，中断后可用 `--resume` 恢复。
4. **VPN 建议**: 建议使用稳定的 VPN 或代理，避免 IP 被封。

## License

MIT
