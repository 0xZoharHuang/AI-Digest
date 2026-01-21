# Claude Development Instructions

## 项目概述

AI Daily Digest 是一个自动化的 AI 资讯聚合系统，从 X (Twitter) 爬取内容，使用 Agent 进行深度研究，输出到 Notion。

## 技术栈

- Python 3.11+
- twscrape: Twitter 爬虫
- crawl4ai: 网页内容抓取
- anthropic: Claude API (使用 Sonnet)
- notion-client: Notion API
- aiosqlite: 异步 SQLite
- pydantic: 数据验证

## 代码规范

- 使用 async/await 处理 I/O 操作
- 使用 pydantic 模型定义数据结构
- 使用 rich 进行日志和控制台输出
- 错误处理要完善，Agent 不应因单个链接失败而中断

## 核心模块

### 1. 爬虫 (src/crawler/)
- 使用 twscrape 登录态爬取
- 同时获取 For You 和 Following Feed
- 提取推文中的链接和媒体

### 2. 筛选 (src/filter/)
- 批量调用 Claude Sonnet
- 判断是否 AI 相关并分类
- 输出: is_valuable, category, topic, priority

### 3. Agent (src/agent/)
- 根据内容类型选择研究策略
- 工具: fetch_url, web_search, read_repo, analyze_paper, analyze_image
- 串行处理，支持断点续传
- 生成结构化研究报告

### 4. 整合 (src/integrator/)
- 按 topic 聚类
- 按价值排序
- 生成日报结构

### 5. 输出 (src/output/)
- Notion API 创建页面
- Markdown 备份导出

### 6. 存储 (src/storage/)
- SQLite 记录处理历史
- JSON 文件保存断点进度

## 运行命令

```bash
# 安装
pip install -e .

# 运行
python scripts/run_daily.py

# 恢复
python scripts/resume.py --run-id <id>
```

## 注意事项

1. API 限流：控制请求频率
2. 敏感信息：config/ 目录不要提交
3. 日志：重要操作都要记录
4. 容错：Agent 要能处理各种失败情况
