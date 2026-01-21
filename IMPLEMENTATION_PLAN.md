# AI Daily Digest - 实现计划

## 项目概述

自动爬取 X 上的 AI 相关内容，用 Agent 进行深度研究，生成结构化报告同步到 Notion。

---

## 一、项目结构

```
ai-digest/
├── README.md                 # 项目说明
├── CLAUDE.md                 # Claude 开发指令
├── pyproject.toml            # Python 项目配置
│
├── src/
│   ├── __init__.py
│   │
│   ├── crawler/              # 数据采集模块
│   │   ├── __init__.py
│   │   ├── twitter_crawler.py    # twscrape 封装
│   │   └── config.py             # 账号配置
│   │
│   ├── filter/               # 筛选层
│   │   ├── __init__.py
│   │   └── tweet_filter.py       # LLM 筛选逻辑
│   │
│   ├── agent/                # 深度研究 Agent
│   │   ├── __init__.py
│   │   ├── research_agent.py     # Agent 主逻辑
│   │   ├── prompts.py            # Prompt 模板
│   │   └── tools/                # Agent 工具
│   │       ├── __init__.py
│   │       ├── fetch_url.py
│   │       ├── web_search.py
│   │       ├── read_repo.py
│   │       ├── analyze_paper.py
│   │       └── analyze_image.py
│   │
│   ├── integrator/           # 整合层
│   │   ├── __init__.py
│   │   └── report_generator.py   # 报告生成
│   │
│   ├── output/               # 输出层
│   │   ├── __init__.py
│   │   ├── notion_sync.py        # Notion 同步
│   │   └── markdown_export.py    # Markdown 导出
│   │
│   └── storage/              # 存储层
│       ├── __init__.py
│       ├── history_db.py         # SQLite 历史记录
│       └── progress_tracker.py   # 进度追踪
│
├── data/
│   ├── history.db            # SQLite 历史数据库
│   ├── media/                # 推文媒体文件
│   ├── temp/                 # 临时研究结果
│   └── reports/              # 生成的报告存档
│
├── logs/                     # 运行日志
├── config/                   # 配置文件（gitignore）
├── scripts/
│   ├── run_daily.py          # 每日运行脚本
│   └── resume.py             # 断点恢复脚本
└── tests/
```

---

## 二、依赖配置

### pyproject.toml 主要依赖

```toml
[project]
dependencies = [
    "twscrape>=0.12",          # Twitter 爬虫
    "crawl4ai>=0.4",           # 网页内容抓取
    "anthropic>=0.40",         # Claude API
    "notion-client>=2.2",      # Notion API
    "aiosqlite>=0.20",         # 异步 SQLite
    "httpx>=0.27",             # HTTP 客户端
    "pydantic>=2.0",           # 数据验证
    "rich>=13.0",              # 终端输出美化
    "apscheduler>=3.10",       # 定时任务
    "arxiv>=2.1",              # arxiv 论文解析
]
```

---

## 三、实现步骤

### 步骤 1: 项目初始化

**文件**: `pyproject.toml`, `README.md`, `CLAUDE.md`, `.gitignore`

**内容**:
- 配置 Python 项目元数据和依赖
- 编写项目说明文档
- 配置 Claude 开发指令
- 设置 gitignore（排除 config/、data/history.db 等敏感文件）

---

### 步骤 2: 存储层实现

**文件**: `src/storage/history_db.py`, `src/storage/progress_tracker.py`

**history_db.py 功能**:
```python
class HistoryDB:
    async def init_db()           # 初始化数据库表
    async def is_processed(tweet_id: str) -> bool
    async def is_url_processed(url: str) -> bool
    async def mark_processed(tweet_id: str, url: str, category: str, ...)
    async def get_recent_processed(days: int) -> List[dict]
```

**数据库表结构**:
```sql
CREATE TABLE processed_tweets (
    id INTEGER PRIMARY KEY,
    tweet_id TEXT UNIQUE NOT NULL,
    url TEXT,
    author TEXT,
    category TEXT,
    topic TEXT,
    title TEXT,
    notion_page_id TEXT,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tweet_id ON processed_tweets(tweet_id);
CREATE INDEX idx_url ON processed_tweets(url);
```

**progress_tracker.py 功能**:
```python
class ProgressTracker:
    def save_progress(run_id: str, state: dict)     # 保存当前进度
    def load_progress(run_id: str) -> dict | None   # 加载上次进度
    def save_result(run_id: str, tweet_id: str, result: dict)  # 保存单条结果
    def load_results(run_id: str) -> List[dict]     # 加载已完成结果
    def clear_progress(run_id: str)                 # 清除进度文件
```

---

### 步骤 3: 爬虫模块实现

**文件**: `src/crawler/twitter_crawler.py`, `src/crawler/config.py`

**twitter_crawler.py 功能**:
```python
class TwitterCrawler:
    async def init_accounts()                     # 初始化登录账号
    async def get_for_you_feed(limit: int) -> List[Tweet]
    async def get_following_feed(limit: int) -> List[Tweet]
    async def get_all_feeds() -> List[Tweet]      # 合并两个 feed
```

**Tweet 数据模型**:
```python
class Tweet(BaseModel):
    id: str
    text: str
    author: str
    author_id: str
    created_at: datetime
    urls: List[str]              # 提取的链接
    media_urls: List[str]        # 图片/视频 URL
    retweet_count: int
    like_count: int
    reply_count: int
    is_retweet: bool
    is_quote: bool
    quoted_tweet: Optional['Tweet']
```

**config.py**:
- 从 `config/twitter_accounts.json` 加载账号配置
- 支持多账号轮换

---

### 步骤 4: 筛选层实现

**文件**: `src/filter/tweet_filter.py`

**功能**:
```python
class TweetFilter:
    async def filter_tweets(tweets: List[Tweet]) -> List[FilteredTweet]
```

**筛选 Prompt**:
```
你是一个 AI 领域内容筛选专家。请分析以下推文，判断其是否值得深入研究。

判断标准：
1. 是否与 AI/ML 技术相关（LLM、Agent、多模态、CV、推理、训练等）
2. 是否有实质内容可深入研究
3. 排除：纯新闻报道、营销推广、无信息量的个人感想

对于每条推文，请输出：
- is_valuable: bool - 是否值得深研
- category: paper/repo/tool/blog/product/sharing/other - 内容类型
- topic: LLM/Agent/多模态/CV/推理优化/训练/其他 - 主题分类
- initial_summary: string - 一句话初步判断
- research_priority: high/medium/low - 研究优先级

推文列表：
{tweets_json}

请以 JSON 数组格式输出结果。
```

**批量处理**:
- 每批 10-20 条推文
- 使用 Claude Sonnet
- 返回 `List[FilteredTweet]`

---

### 步骤 5: Agent 工具实现

**文件**: `src/agent/tools/*.py`

#### fetch_url.py
```python
async def fetch_url(url: str) -> str:
    """使用 Crawl4AI 抓取网页内容，返回 Markdown 格式"""
    # 支持普通网页、Medium、Substack 等
    # 自动处理 JavaScript 渲染
    # 返回清理后的正文内容
```

#### web_search.py
```python
async def web_search(query: str, num_results: int = 5) -> List[SearchResult]:
    """联网搜索，返回搜索结果列表"""
    # 使用 DuckDuckGo 或 Tavily API
    # 返回标题、URL、摘要
```

#### read_repo.py
```python
async def read_repo(repo_url: str) -> RepoInfo:
    """读取 GitHub 仓库信息"""
    # 获取 README.md
    # 获取目录结构
    # 获取核心文件内容（根据语言判断）
    # 获取 star 数、最近更新时间等
```

#### analyze_paper.py
```python
async def analyze_paper(arxiv_url: str) -> PaperInfo:
    """解析 arxiv 论文"""
    # 使用 arxiv 库获取元数据
    # 获取 PDF 链接
    # 提取摘要、作者、发布时间
    # 可选：使用 LLM 分析 PDF 内容
```

#### analyze_image.py
```python
async def analyze_image(image_url: str, context: str) -> str:
    """使用视觉能力分析图片"""
    # 下载图片
    # 使用 Claude Vision 分析
    # 返回图片内容描述
```

---

### 步骤 6: Agent 核心实现

**文件**: `src/agent/research_agent.py`, `src/agent/prompts.py`

**研究 Agent 架构**:
```python
class ResearchAgent:
    def __init__(self, model: str = "claude-sonnet-4-20250514"):
        self.client = Anthropic()
        self.tools = [fetch_url, web_search, read_repo, analyze_paper, analyze_image]

    async def research(self, tweet: FilteredTweet) -> ResearchResult:
        """对单条推文进行深度研究"""
        # 1. 根据 category 选择研究策略
        # 2. 执行工具调用循环
        # 3. 生成结构化研究报告
        # 4. 返回 ResearchResult
```

**prompts.py - 分类型研究 Prompt**:

```python
PAPER_RESEARCH_PROMPT = """
你是一个 AI 论文研究专家。请深度分析这篇论文，提取以下信息：

1. 核心问题：这篇论文要解决什么问题？
2. 方法创新：用了什么方法？关键创新是什么？
3. 实验结果：在什么数据集上测试？结果如何？
4. 局限性：作者承认的局限 + 你发现的潜在问题
5. 意义评估：这个工作为什么重要？对领域有什么影响？

用人话讲清楚，让读者不需要看原文就能理解核心内容。

推文内容：{tweet_text}
论文信息：{paper_info}
"""

REPO_RESEARCH_PROMPT = """
你是一个开源项目研究专家。请深度分析这个仓库：

1. 功能定位：这个项目是做什么的？解决什么问题？
2. 技术架构：代码怎么组织的？核心模块是什么？数据流是什么？
3. 创新点：和现有方案相比有什么不同？
4. 使用方法：怎么用？核心 API 是什么？
5. 质量评估：代码质量、文档完善度、社区活跃度

用人话讲解技术架构和创新点。

推文内容：{tweet_text}
仓库信息：{repo_info}
"""

BLOG_RESEARCH_PROMPT = """
你是一个技术博客分析专家。请分析这篇文章：

1. 核心论点：作者想说明什么？
2. 论据支撑：用什么支撑论点？
3. 可信度评估：这个人说的靠谱吗？有没有 bias？
4. 启发价值：对读者有什么启发？

推文内容：{tweet_text}
文章内容：{article_content}
"""

TOOL_RESEARCH_PROMPT = """
你是一个 AI 工具/产品分析专家。请分析这个工具/产品：

1. 功能介绍：这个工具/产品是做什么的？
2. 使用场景：适合什么场景使用？
3. 技术实现：背后用了什么技术？
4. 竞品对比：和现有方案相比如何？
5. 价值评估：值得关注/使用吗？

推文内容：{tweet_text}
产品信息：{product_info}
"""
```

**容错处理**:
```python
async def research_with_fallback(self, tweet: FilteredTweet) -> ResearchResult:
    try:
        # 尝试主要研究路径
        result = await self.research(tweet)
    except URLFetchError:
        # 链接失败，尝试搜索
        search_results = await web_search(tweet.initial_summary)
        result = await self.research_from_search(tweet, search_results)
    except Exception as e:
        # 其他错误，生成基本报告
        result = self.generate_basic_report(tweet, error=str(e))
    return result
```

---

### 步骤 7: 整合层实现

**文件**: `src/integrator/report_generator.py`

**功能**:
```python
class ReportGenerator:
    async def generate_daily_report(
        results: List[ResearchResult],
        date: str
    ) -> DailyReport:
        """生成日报"""
        # 1. 按 topic 聚类
        # 2. 每个 topic 内按价值排序
        # 3. 生成结构化报告
```

**聚类 Prompt**:
```
请将以下研究结果按主题分类，并在每个主题内按价值排序：

研究结果：
{results_json}

输出格式：
{
    "LLM": [...],
    "Agent": [...],
    "多模态": [...],
    "CV": [...],
    "工具/产品": [...],
    "其他": [...]
}
```

---

### 步骤 8: Notion 输出实现

**文件**: `src/output/notion_sync.py`

**功能**:
```python
class NotionSync:
    def __init__(self, token: str, database_id: str):
        self.client = NotionClient(auth=token)
        self.database_id = database_id

    async def create_daily_page(self, report: DailyReport) -> str:
        """创建日报页面，返回页面 ID"""
        # 1. 创建页面
        # 2. 添加标题：AI Daily Digest - YYYY-MM-DD
        # 3. 按主题添加内容块
        # 4. 返回页面 ID

    async def update_page(self, page_id: str, report: DailyReport):
        """更新已有页面"""
```

**Notion 页面结构**:
```
📅 AI Daily Digest - 2025-01-21

## 🤖 LLM 相关
### [标题1]
- **来源**：@author | 链接
- **一句话**：xxx
- **核心内容**：
  - 要点1
  - 要点2
- **深度分析**：xxx
- **我的评估**：xxx

## 🕹️ Agent 相关
...
```

---

### 步骤 9: 运行脚本实现

**文件**: `scripts/run_daily.py`, `scripts/resume.py`

**run_daily.py**:
```python
async def main():
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1. 初始化组件
    crawler = TwitterCrawler()
    filter = TweetFilter()
    agent = ResearchAgent()
    generator = ReportGenerator()
    notion = NotionSync()
    history_db = HistoryDB()
    progress = ProgressTracker()

    # 2. 爬取推文
    tweets = await crawler.get_all_feeds()

    # 3. 去重
    new_tweets = [t for t in tweets if not await history_db.is_processed(t.id)]

    # 4. 筛选
    filtered = await filter.filter_tweets(new_tweets)
    valuable = [t for t in filtered if t.is_valuable]

    # 5. 深度研究（串行 + 断点续传）
    results = []
    for tweet in valuable:
        result = await agent.research(tweet)
        results.append(result)
        progress.save_result(run_id, tweet.id, result)
        await history_db.mark_processed(tweet.id, ...)

    # 6. 生成报告
    report = await generator.generate_daily_report(results)

    # 7. 同步到 Notion
    page_id = await notion.create_daily_page(report)

    # 8. 清理进度文件
    progress.clear_progress(run_id)
```

**resume.py**:
```python
async def resume(run_id: str):
    """从断点恢复"""
    progress = ProgressTracker()
    state = progress.load_progress(run_id)
    completed_results = progress.load_results(run_id)

    # 继续处理未完成的推文
    ...
```

---

## 四、配置文件模板

### config/twitter_accounts.json
```json
{
    "accounts": [
        {
            "username": "your_username",
            "password": "your_password",
            "email": "your_email",
            "cookies": null
        }
    ]
}
```

### config/notion_config.json
```json
{
    "token": "secret_xxx",
    "database_id": "xxx",
    "parent_page_id": "xxx"
}
```

---

## 五、实现顺序

1. **项目初始化** - pyproject.toml, README.md, CLAUDE.md, .gitignore
2. **存储层** - history_db.py, progress_tracker.py
3. **爬虫模块** - twitter_crawler.py, config.py
4. **筛选层** - tweet_filter.py
5. **Agent 工具** - fetch_url.py, web_search.py, read_repo.py, analyze_paper.py, analyze_image.py
6. **Agent 核心** - research_agent.py, prompts.py
7. **整合层** - report_generator.py
8. **输出层** - notion_sync.py, markdown_export.py
9. **运行脚本** - run_daily.py, resume.py
10. **测试与调优**

---

## 六、验证检查点

### 每个模块完成后验证

- [ ] 存储层：能正确读写 SQLite，进度文件正常工作
- [ ] 爬虫：能登录并爬取 For You + Following Feed
- [ ] 筛选：能正确分类 AI 相关内容
- [ ] Agent 工具：每个工具能独立工作
- [ ] Agent 核心：能完成端到端的深度研究
- [ ] 整合层：能按主题聚类并排序
- [ ] Notion：能创建格式正确的页面

### 端到端验证

```bash
# 完整流程测试
python scripts/run_daily.py

# 断点恢复测试
# 1. 运行到一半手动中断
# 2. 运行 resume.py 继续
python scripts/resume.py --run-id <run_id>
```

---

## 七、注意事项

1. **API 限流**：twscrape 和 Notion API 都有限流，需要合理控制请求频率
2. **错误处理**：Agent 要能优雅处理各种链接失败情况
3. **成本控制**：Sonnet API 调用需要监控 token 消耗
4. **敏感信息**：所有 API key 和账号信息都放在 config/ 目录，加入 gitignore
5. **日志记录**：重要操作都要记录日志，方便排查问题
