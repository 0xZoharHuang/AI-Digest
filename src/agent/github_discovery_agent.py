"""GitHub early-stage project discovery agent using Claude Agent SDK."""

import asyncio
from typing import Any

from pydantic import BaseModel
from rich.console import Console

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage

console = Console()


# Structured Output Schema for GitHub discovery results
DISCOVERY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "discoveries": {
            "type": "array",
            "description": "List of high-value projects discovered (score >= 8)",
            "items": {
                "type": "object",
                "properties": {
                    "repo_id": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo)",
                    },
                    "full_name": {
                        "type": "string",
                        "description": "Full repository name",
                    },
                    "url": {
                        "type": "string",
                        "description": "GitHub repository URL",
                    },
                    "stars": {
                        "type": "integer",
                        "description": "Current star count",
                    },
                    "innovation_score": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Innovation score (1-10, only >= 8 included)",
                    },
                    "innovation_summary": {
                        "type": "string",
                        "description": "Summary of the technical innovation (2-3 sentences)",
                    },
                    "code_quality": {
                        "type": "string",
                        "description": "Code quality assessment (tests, docs, architecture)",
                    },
                    "author_background": {
                        "type": "string",
                        "description": "Author/team background and credibility",
                    },
                    "research_report": {
                        "type": "string",
                        "description": "Complete research report (Markdown format)",
                    },
                    "recommendation": {
                        "type": "string",
                        "description": "Why this project is valuable and worth tracking",
                    },
                },
                "required": [
                    "repo_id",
                    "full_name",
                    "url",
                    "stars",
                    "innovation_score",
                    "innovation_summary",
                    "research_report",
                ],
            },
        },
        "total_scanned": {
            "type": "integer",
            "description": "Total number of repositories scanned",
        },
        "total_discovered": {
            "type": "integer",
            "description": "Number of high-value projects discovered",
        },
    },
    "required": ["discoveries", "total_scanned", "total_discovered"],
}


# Agent prompt for GitHub discovery
GITHUB_DISCOVERY_PROMPT = """你是 AI 研究项目发现专家。任务：在 GitHub 发现"未爆火但有价值的早期 AI 项目"。

## 目标
发现 {target_count} 个真正有价值的项目（stars {min_stars}-{max_stars}），生成详细报告。

## 工作流程

### 第1步：搜索候选（Bash调用GitHub API）

使用 GitHub API 搜索 AI 相关的活跃项目：

```bash
curl -H "Authorization: token {github_token}" \\
  "https://api.github.com/search/repositories?q=topic:llm+OR+topic:agent+OR+topic:ai+OR+topic:ml+stars:{min_stars}..{max_stars}+pushed:>2025-01-01+language:python&sort=updated&per_page=100"
```

如果需要更多结果，可以调整搜索条件或使用分页：
- 添加 `&page=2` 等参数获取更多页
- 尝试不同的topic组合：`topic:transformers`, `topic:rag`, `topic:langchain`等
- 尝试其他语言：`language:typescript`, `language:rust`等

### 第2步：逐个评估（自主决策）

对每个项目：

1. **快速过滤**：
   - 名称含"example/demo/tutorial/template"？→ skip
   - README<200字符或无实质内容？→ skip
   - 是大公司的官方项目？→ skip（我们要找早期项目）
   - 是课程作业或学习项目？→ skip

2. **深度评估**：
   - **WebFetch** 获取完整README（从 https://raw.githubusercontent.com/owner/repo/main/README.md）
   - **WebSearch** 查询：
     * 项目相关讨论（site:reddit.com OR site:twitter.com OR site:news.ycombinator.com）
     * 作者背景（搜索作者名字 + GitHub/论文/博客）
   - **Bash** 调用GitHub API获取更多信息：
     ```bash
     curl -H "Authorization: token {github_token}" \\
       "https://api.github.com/repos/owner/repo"
     ```

3. **价值判断**（打分1-10）：
   - **技术创新度（60%）**：
     * 解决了什么新问题？
     * 提出了什么新方法/新架构？
     * 与现有方案的差异化在哪里？
   - **代码质量（20%）**：
     * 是否有测试？
     * 文档是否完善？
     * 代码结构是否清晰？
   - **作者背景（20%）**：
     * 作者是谁？（研究者/工程师/学生）
     * 过往项目质量如何？
     * 在社区的影响力？

4. **决策**：
   - 评分 >= 8？→ 深度研究
   - 评分 < 8？→ 记录并skip

### 第3步：深度研究（评分>=8）

1. **克隆repo**（如果repo较小）：
   ```bash
   # 先检查repo大小
   repo_size=$(curl -s -H "Authorization: token {github_token}" \\
     "https://api.github.com/repos/owner/repo" | grep -o '"size":[0-9]*' | head -1 | cut -d':' -f2)

   # 只克隆小于200MB的repo（size单位是KB）
   if [ "$repo_size" -lt 200000 ]; then
     git clone --depth 1 https://github.com/owner/repo /tmp/ai-digest-repos/repo
   else
     echo "Repo too large, skipping clone"
   fi
   ```

2. **代码阅读**（5-10个关键文件）：
   - **Glob** 查找关键文件：
     * README.md, docs/*, setup.py/pyproject.toml
     * src/*/main.py, core/, models/, agents/
     * tests/, examples/
   - **Read** 核心文件，理解架构
   - 验证：创新是真实的吗？代码质量如何？

3. **生成报告**（结构化，Markdown格式）：
   ```markdown
   # [项目名称]: [核心创新点]

   ## 基本信息
   - Repository: owner/repo
   - Stars: 250
   - Language: Python
   - Last Updated: 2025-01-20

   ## 技术创新（9/10）
   [详细描述技术创新点，包括：]
   - 解决的问题
   - 创新的方法
   - 与现有方案的对比

   ## 代码质量
   [评估代码质量：]
   - 测试覆盖率
   - 文档完善度
   - 代码架构

   ## 作者背景
   [作者信息：]
   - 身份和经验
   - 过往项目
   - 社区影响力

   ## 推荐理由
   [为什么值得关注这个项目？]
   ```

## 输出要求
- 只输出评分 >= 8 的项目（目标 {target_count} 个）
- 每个项目包含完整研究报告
- 标题高信息量，突出创新点
- 使用结构化JSON Schema输出

## 注意事项
1. **质量优先**：宁缺毋滥，不够8分就不要勉强
2. **深度研究**：对高分项目一定要深入，不能浅尝辄止
3. **创新识别**：真正的创新 vs 简单的组合/复现
4. **成本控制**：合理使用工具，避免过度探索
5. **repo清理**：使用绝对路径 `/tmp/ai-digest-repos/{{repo}}/...`

开始执行。
"""


class DiscoveryResult(BaseModel):
    """Result of a GitHub project discovery."""

    repo_id: str
    full_name: str
    url: str
    stars: int
    innovation_score: int
    innovation_summary: str
    code_quality: str
    author_background: str
    research_report: str
    recommendation: str


class DiscoverySummary(BaseModel):
    """Summary of the discovery session."""

    discoveries: list[DiscoveryResult]
    total_scanned: int
    total_discovered: int


class GitHubDiscoveryAgent:
    """Agent for discovering early-stage AI projects on GitHub."""

    def __init__(self, github_token: str, model: str = "sonnet"):
        """
        Initialize GitHub discovery agent.

        Args:
            github_token: GitHub API token for authentication
            model: Model to use - "sonnet", "opus", or "haiku"
        """
        self.github_token = github_token
        self.model = model

        # Tools needed for GitHub discovery
        self.allowed_tools = [
            "Bash",        # Call GitHub API, clone repos
            "WebFetch",    # Fetch README files
            "WebSearch",   # Search for background info
            "Read",        # Read cloned repo files
            "Glob",        # Navigate repo structure
        ]

    async def discover(
        self,
        min_stars: int = 100,
        max_stars: int = 1000,
        target_count: int = 15,
    ) -> DiscoverySummary:
        """
        Discover high-value early-stage AI projects.

        Args:
            min_stars: Minimum star count (default: 100)
            max_stars: Maximum star count (default: 1000)
            target_count: Target number of projects to discover (default: 15)

        Returns:
            DiscoverySummary with discovered projects
        """
        console.print("[cyan]Starting GitHub project discovery...[/cyan]")
        console.print(f"[dim]Target: {target_count} projects with {min_stars}-{max_stars} stars[/dim]")

        # Format prompt with parameters
        prompt = GITHUB_DISCOVERY_PROMPT.format(
            github_token=self.github_token,
            min_stars=min_stars,
            max_stars=max_stars,
            target_count=target_count,
        )

        try:
            result_data = None
            total_cost = 0.0

            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    allowed_tools=self.allowed_tools,
                    permission_mode="bypassPermissions",
                    model=self.model,
                    max_turns=50,  # Allow deep exploration
                    output_format={
                        "type": "json_schema",
                        "schema": DISCOVERY_OUTPUT_SCHEMA,
                    },
                ),
            ):
                if isinstance(message, AssistantMessage):
                    # Stream assistant messages to show progress
                    if hasattr(message, "content"):
                        for block in message.content:
                            if hasattr(block, "text") and block.text.strip():
                                # Print progress updates
                                lines = block.text.strip().split("\n")
                                for line in lines:
                                    if line.strip():
                                        console.print(f"[dim]{line}[/dim]")

                elif isinstance(message, ResultMessage):
                    # Final result
                    if message.subtype == "success":
                        total_cost = message.total_cost_usd
                        console.print(f"[green]Discovery completed. Cost: ${total_cost:.2f}[/green]")
                        if hasattr(message, "structured_output") and message.structured_output:
                            result_data = message.structured_output
                    else:
                        console.print(f"[yellow]Discovery ended: {message.subtype}[/yellow]")

            # Parse result
            if not result_data:
                console.print("[red]No structured output received[/red]")
                return DiscoverySummary(
                    discoveries=[],
                    total_scanned=0,
                    total_discovered=0,
                )

            # Convert to Pydantic models
            discoveries = [
                DiscoveryResult(**d) for d in result_data.get("discoveries", [])
            ]

            summary = DiscoverySummary(
                discoveries=discoveries,
                total_scanned=result_data.get("total_scanned", 0),
                total_discovered=result_data.get("total_discovered", 0),
            )

            console.print(f"[green]Found {len(discoveries)} high-value projects[/green]")
            return summary

        except Exception as e:
            console.print(f"[red]Discovery failed: {e}[/red]")
            import traceback
            traceback.print_exc()

            return DiscoverySummary(
                discoveries=[],
                total_scanned=0,
                total_discovered=0,
            )
