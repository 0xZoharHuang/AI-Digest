# AI Daily Digest

自动爬取 X (Twitter) 上的 AI 相关内容，用 Agent 进行深度研究，生成结构化报告同步到 Notion。

## 功能

- **数据采集**: 爬取 For You + Following Feed，提取带链接的推文
- **智能筛选**: 使用 LLM 判断内容是否与 AI/ML 相关
- **深度研究**: Agent 自动抓取链接内容、联网搜索、生成深度分析
- **报告生成**: 按主题聚类，生成结构化日报
- **Notion 同步**: 自动创建/更新 Notion 页面

## 安装

```bash
# 安装依赖
pip install -e .

# 或使用 uv
uv pip install -e .
```

## 配置

1. 复制配置模板：

```bash
cp config/twitter_accounts.example.json config/twitter_accounts.json
cp config/notion_config.example.json config/notion_config.json
```

2. 填写 X 账号和 Notion API 配置

3. 设置环境变量（可选，也可以放在配置文件中）：

```bash
export ANTHROPIC_API_KEY="your_key"
```

## 使用

```bash
# 运行每日任务
python scripts/run_daily.py

# 从断点恢复
python scripts/resume.py --run-id <run_id>
```

## 项目结构

```
ai-digest/
├── src/
│   ├── crawler/      # 数据采集模块
│   ├── filter/       # 筛选层
│   ├── agent/        # 深度研究 Agent
│   ├── integrator/   # 整合层
│   ├── output/       # 输出层
│   └── storage/      # 存储层
├── data/             # 数据文件
├── config/           # 配置文件
├── scripts/          # 运行脚本
└── tests/            # 测试
```

## License

MIT
