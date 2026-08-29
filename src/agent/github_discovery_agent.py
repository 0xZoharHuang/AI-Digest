"""GitHub early-stage project discovery — deterministic collection + Agent analysis."""

import asyncio
import base64
import json
from datetime import datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel
from rich.console import Console

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage

console = Console()


# --- Pydantic Models ---


class CollectedProject(BaseModel):
    """A single candidate project from Phase 1 collection."""

    repo_id: str  # "owner/repo"
    full_name: str
    url: str
    description: str
    stars: int
    language: str = ""
    topics: list[str] = []
    created_at: str = ""
    pushed_at: str = ""
    forks: int = 0
    open_issues: int = 0
    license_name: str = ""
    readme_excerpt: str = ""  # first 3000 chars of README


class CollectedProjects(BaseModel):
    """Phase 1 output: all collected candidate projects."""

    candidates: list[CollectedProject] = []
    total_api_results: int = 0
    total_after_prefilter: int = 0
    total_with_readme: int = 0
    queries_executed: int = 0
    errors: list[str] = []
    collected_at: str = ""


class DiscoveryResult(BaseModel):
    """Result of a GitHub project discovery."""

    repo_id: str
    full_name: str
    url: str
    stars: int
    innovation_score: int
    innovation_summary: str
    code_quality: str = ""
    author_background: str = ""
    research_report: str
    recommendation: str = ""


class DiscoverySummary(BaseModel):
    """Summary of the discovery session."""

    discoveries: list[DiscoveryResult]
    total_scanned: int
    total_discovered: int


# --- Output Schemas for Agent ---

GITHUB_SCREENING_SCHEMA = {
    "type": "object",
    "properties": {
        "screened_projects": {
            "type": "array",
            "description": "Projects scoring >= 8 after evaluation",
            "items": {
                "type": "object",
                "properties": {
                    "repo_id": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo)",
                    },
                    "innovation_score": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Innovation score (1-10, only >= 8 included)",
                    },
                    "innovation_summary": {
                        "type": "string",
                        "description": "2-3 sentence summary of the technical innovation (Chinese)",
                    },
                    "code_quality_indicators": {
                        "type": "string",
                        "description": "Brief code quality notes from README analysis",
                    },
                    "author_credibility": {
                        "type": "string",
                        "description": "Brief author/team credibility notes",
                    },
                    "research_priority": {
                        "type": "string",
                        "enum": ["high", "medium"],
                        "description": "Priority for deep research phase",
                    },
                },
                "required": [
                    "repo_id",
                    "innovation_score",
                    "innovation_summary",
                    "research_priority",
                ],
            },
        },
        "total_evaluated": {
            "type": "integer",
            "description": "Total number of candidates evaluated",
        },
        "evaluation_summary": {
            "type": "string",
            "description": "Brief summary of evaluation findings (Chinese)",
        },
    },
    "required": ["screened_projects", "total_evaluated", "evaluation_summary"],
}

GITHUB_RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "research_report": {
            "type": "string",
            "description": "Complete deep research report in Markdown format (Chinese)",
        },
        "code_quality": {
            "type": "string",
            "description": "Detailed code quality assessment",
        },
        "author_background": {
            "type": "string",
            "description": "Detailed author/team background",
        },
        "recommendation": {
            "type": "string",
            "description": "Why this project is valuable and worth tracking",
        },
    },
    "required": ["research_report"],
}


# --- Prompts ---

GITHUB_SCREENING_PROMPT = """你是 AI 研究项目发现评估专家。任务：从以下候选项目中筛选出真正有价值的早期 AI 项目。

## 评估数据

以下是通过 GitHub Search API 收集的 {total_candidates} 个候选项目。每个项目包含基本元数据和 README 摘录。

### 候选项目列表
```json
{candidates_json}
```

## 采集统计
- API 搜索结果总数: {total_api_results}
- 预过滤后剩余: {total_after_prefilter}
- README 获取成功: {total_with_readme}
{collection_errors}

## 评估标准

对每个项目打分（1-10），只保留评分 >= 8 的项目：

### 技术创新度（60%权重）
- 解决了什么**新**问题？（不是已有方案的简单复现）
- 提出了什么新方法/新架构？
- 与现有方案的差异化在哪里？
- README 中是否展示了清晰的技术方案？

### 代码质量指标（20%权重）
从 README 和项目元数据判断：
- 是否提到测试、CI/CD、文档？
- 项目结构是否看起来专业？
- 是否有明确的 API/接口设计？
- issue 数量和 fork 数量是否合理？

### 作者可信度（20%权重）
从 README 和项目信息推断：
- 是否提到作者/团队背景？（研究者、公司、论文？）
- 是否有关联的论文、博客、演讲？
- GitHub 组织是否有其他知名项目？

## 扩展搜索（可选）

如果某个候选项目看起来有潜力但信息不足，可以使用：
- **WebSearch**: 搜索 "[项目名] github" 或 "[作者名] AI" 获取更多背景
- **WebFetch**: 获取项目相关的博客文章或论文链接

注意：扩展搜索仅用于补充信息，不要为每个项目都搜索。只对"可能>=8分但信息不够确定"的项目搜索。

## 输出要求

1. 只输出评分 >= 8 的项目
2. innovation_summary 用中文，2-3 句话概括技术创新点
3. research_priority: "high" 表示创新度 >= 9 或特别值得深入研究, "medium" 表示其他
4. 质量优先，宁缺毋滥 — 如果没有项目达到 8 分，返回空数组
5. evaluation_summary 用中文概括评估发现

开始评估。
"""

GITHUB_RESEARCH_PROMPT = """你是 AI 项目深度研究分析师。对以下高分项目进行深入研究并生成完整报告。

## 研究目标

**项目**: {repo_id}
**URL**: {url}
**Stars**: {stars}
**语言**: {language}
**初步评估**: {innovation_summary}

## README 摘录
{readme_excerpt}

## 项目元数据
- 创建时间: {created_at}
- 最近推送: {pushed_at}
- Forks: {forks}
- Open Issues: {open_issues}
- License: {license_name}
- Topics: {topics}

## 研究步骤

1. **WebFetch** 获取完整 README:
   - 尝试: https://raw.githubusercontent.com/{repo_id}/main/README.md
   - 备选: https://raw.githubusercontent.com/{repo_id}/master/README.md

2. **WebSearch** 针对性搜索（1-3次，不要过度搜索）:
   - "{repo_id}" — 项目相关讨论和新闻
   - 作者名/组织名 + "AI" — 作者背景（如果 README 中能找到作者信息）

3. **WebFetch** 获取关键源文件（可选，通过 raw.githubusercontent.com）:
   - 如果 README 引用了论文或技术文档，获取之
   - 如果需要验证架构声明，查看核心模块入口文件

## 输出

输出完整 Markdown 格式研究报告（中文），包含：

```markdown
# [项目名称]: [核心创新点一句话]

## 基本信息
- Repository: owner/repo
- Stars: ...
- Language: ...
- Last Updated: ...
- License: ...

## 技术创新（X/10）
- 解决的问题
- 创新的方法
- 与现有方案的对比

## 代码质量
- 测试情况
- 文档完善度
- 代码架构评价

## 作者背景
- 身份和经验
- 过往项目
- 社区影响力

## 推荐理由
[为什么值得关注？]
```

开始研究。
"""


# --- Collector ---


class GitHubProjectCollector:
    """Deterministic collector for GitHub AI project candidates."""

    SKIP_NAME_PATTERNS = [
        "example",
        "demo",
        "tutorial",
        "template",
        "awesome-",
        "course",
        "learn",
        "workshop",
        "starter",
        "boilerplate",
        "sample",
    ]

    def __init__(
        self,
        github_token: str,
        db: Any = None,
        config: dict = None,
    ):
        self.github_token = github_token
        self.db = db
        config = config or {}
        self.topics = config.get(
            "topics",
            ["llm", "agent", "ai", "ml", "transformers", "rag", "langchain"],
        )
        self.languages = config.get(
            "languages",
            ["python", "typescript", "rust", "go"],
        )
        self.min_stars = config.get("min_stars", 100)
        self.max_stars = config.get("max_stars", 3000)
        self.pushed_within_days = config.get("pushed_within_days", 60)

    def _build_search_queries(self) -> list[str]:
        """Build GitHub Search API query strings."""
        since = (
            datetime.now() - timedelta(days=self.pushed_within_days)
        ).strftime("%Y-%m-%d")
        stars = f"{self.min_stars}..{self.max_stars}"

        queries = []

        # Tier 1: one query per topic (broad, language-agnostic)
        for topic in self.topics:
            queries.append(
                f"topic:{topic} stars:{stars} pushed:>{since} fork:false"
            )

        # Tier 2: language-specific with AI keywords (catches repos without topic tags)
        for lang in self.languages:
            queries.append(
                f"(ai OR llm OR agent OR transformer OR neural) "
                f"language:{lang} stars:{stars} pushed:>{since} fork:false"
            )

        return queries

    def _prefilter(self, repos: list[dict]) -> list[dict]:
        """Deterministic pre-filtering in Python."""
        filtered = []
        for repo in repos:
            if repo.get("fork", False):
                continue

            name = (repo.get("name") or "").lower()
            if any(pat in name for pat in self.SKIP_NAME_PATTERNS):
                continue

            description = repo.get("description") or ""
            if len(description.strip()) < 50:
                continue

            filtered.append(repo)

        return filtered

    async def _is_already_tracked(self, repo_id: str) -> bool:
        """Check if repo is already in DB."""
        if not self.db:
            return False
        try:
            return await self.db.is_repo_tracked(repo_id)
        except Exception:
            return False

    async def _search_repos(
        self, client: httpx.AsyncClient, query_string: str
    ) -> list[dict]:
        """Execute a single GitHub Search API call."""
        resp = await client.get(
            "https://api.github.com/search/repositories",
            params={"q": query_string, "sort": "updated", "per_page": 100},
            headers={
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])

    async def _fetch_readme(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> str:
        """Fetch README via GitHub API, decode base64, truncate."""
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/readme",
                headers={
                    "Authorization": f"token {self.github_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if resp.status_code != 200:
                return ""

            data = resp.json()
            content = data.get("content", "")
            encoding = data.get("encoding", "")

            if encoding == "base64" and content:
                decoded = base64.b64decode(content).decode(
                    "utf-8", errors="replace"
                )
                return decoded[:3000]
            return ""
        except Exception:
            return ""

    async def collect(self) -> CollectedProjects:
        """Execute all search queries, pre-filter, fetch READMEs."""
        console.print("[cyan]Phase 1: Collecting GitHub project candidates...[/cyan]")

        queries = self._build_search_queries()
        all_repos: dict[str, dict] = {}  # keyed by repo_id for dedup
        errors: list[str] = []
        total_api_results = 0

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Execute all search queries
            for i, q in enumerate(queries):
                try:
                    repos = await self._search_repos(client, q)
                    total_api_results += len(repos)
                    for repo in repos:
                        rid = repo.get("full_name", "")
                        if rid and rid not in all_repos:
                            all_repos[rid] = repo
                    console.print(
                        f"  [green]Query {i + 1}/{len(queries)}: "
                        f"{len(repos)} results (total unique: {len(all_repos)})[/green]"
                    )
                except Exception as e:
                    errors.append(f"Search query {i + 1}: {str(e)[:100]}")
                    console.print(f"  [yellow]Query {i + 1} failed: {e}[/yellow]")

                # Rate limit: 2 second delay between search queries
                await asyncio.sleep(2.0)

            console.print(
                f"[dim]Total unique repos from API: {len(all_repos)}[/dim]"
            )

            # Step 2: Deterministic pre-filter
            filtered = self._prefilter(list(all_repos.values()))
            total_after_prefilter = len(filtered)
            console.print(f"[dim]After pre-filter: {total_after_prefilter}[/dim]")

            # Step 3: DB dedup
            deduped = []
            for repo in filtered:
                rid = repo.get("full_name", "")
                if not await self._is_already_tracked(rid):
                    deduped.append(repo)

            if len(deduped) < len(filtered):
                console.print(
                    f"[dim]After DB dedup: {len(deduped)} "
                    f"({len(filtered) - len(deduped)} already tracked)[/dim]"
                )

            # Step 4: Fetch READMEs
            console.print(
                f"[dim]Fetching READMEs for {len(deduped)} repos...[/dim]"
            )
            candidates: list[CollectedProject] = []
            readme_fail_count = 0

            for repo in deduped:
                rid = repo.get("full_name", "")
                owner, name = rid.split("/", 1)
                readme = await self._fetch_readme(client, owner, name)
                await asyncio.sleep(0.1)  # rate limit

                if len(readme) < 200:
                    readme_fail_count += 1
                    continue

                candidates.append(
                    CollectedProject(
                        repo_id=rid,
                        full_name=repo.get("full_name", ""),
                        url=repo.get("html_url", ""),
                        description=(repo.get("description") or "")[:500],
                        stars=repo.get("stargazers_count", 0),
                        language=repo.get("language") or "",
                        topics=repo.get("topics", []),
                        created_at=repo.get("created_at", ""),
                        pushed_at=repo.get("pushed_at", ""),
                        forks=repo.get("forks_count", 0),
                        open_issues=repo.get("open_issues_count", 0),
                        license_name=(repo.get("license") or {}).get(
                            "name", ""
                        ),
                        readme_excerpt=readme[:3000],
                    )
                )

            if readme_fail_count:
                console.print(
                    f"[dim]Skipped {readme_fail_count} repos with short/missing README[/dim]"
                )

        # If too many candidates, truncate README to control screening context
        if len(candidates) > 100:
            console.print(
                f"[dim]{len(candidates)} candidates > 100, "
                f"truncating README excerpts to 1500 chars[/dim]"
            )
            for c in candidates:
                c.readme_excerpt = c.readme_excerpt[:1500]

        console.print(
            f"[green]Phase 1 complete: {len(candidates)} candidates "
            f"(from {total_api_results} API results)[/green]"
        )

        return CollectedProjects(
            candidates=candidates,
            total_api_results=total_api_results,
            total_after_prefilter=total_after_prefilter,
            total_with_readme=len(candidates),
            queries_executed=len(queries),
            errors=errors,
            collected_at=datetime.now().isoformat(),
        )


# --- Agent ---


class GitHubDiscoveryAgent:
    """Agent for discovering early-stage AI projects on GitHub.

    3-phase architecture:
    Phase 1: Deterministic collection (httpx)
    Phase 2: Agent screening (single agent call)
    Phase 3: Deep research (batch of 3, no retry)
    """

    def __init__(
        self,
        github_token: str,
        model: str = "sonnet",
        db: Any = None,
        config: dict = None,
    ):
        self.github_token = github_token
        self.model = model
        self.db = db
        self.config = config or {}
        self.collector = GitHubProjectCollector(
            github_token=github_token,
            db=db,
            config=self.config,
        )
        self.allowed_tools = ["WebFetch", "WebSearch"]

    async def discover(
        self,
        collected: CollectedProjects = None,
        min_stars: int = 100,
        max_stars: int = 3000,
        target_count: int = 15,
    ) -> DiscoverySummary:
        """Main entry point. Runs Phase 2+3 (or all 3 phases if collected=None)."""
        console.print("[cyan]Starting GitHub project discovery...[/cyan]")

        if collected is None:
            # Compatibility mode: run Phase 1 internally
            console.print(
                f"[dim]Target: {target_count} projects with "
                f"{min_stars}-{max_stars} stars[/dim]"
            )
            self.collector.min_stars = min_stars
            self.collector.max_stars = max_stars
            collected = await self.collector.collect()
        else:
            console.print(
                f"[dim]Using pre-collected {len(collected.candidates)} candidates[/dim]"
            )

        if not collected.candidates:
            console.print("[yellow]No candidates found in Phase 1[/yellow]")
            return DiscoverySummary(
                discoveries=[],
                total_scanned=collected.total_api_results,
                total_discovered=0,
            )

        # Phase 2: Agent screening
        console.print(
            f"\n[cyan]Phase 2: Screening {len(collected.candidates)} "
            f"candidates...[/cyan]"
        )
        screened = await self._run_screening(collected)

        if not screened:
            console.print(
                "[yellow]No projects passed screening (score >= 8)[/yellow]"
            )
            return DiscoverySummary(
                discoveries=[],
                total_scanned=collected.total_api_results,
                total_discovered=0,
            )

        console.print(f"[green]{len(screened)} projects passed screening[/green]")

        # Phase 3: Deep research (batch of 2)
        console.print(
            f"\n[cyan]Phase 3: Deep research for "
            f"{len(screened)} projects...[/cyan]"
        )
        reports = await self._run_deep_research(screened, collected)

        # Assemble final DiscoveryResult objects
        discoveries = []
        for s in screened:
            rid = s["repo_id"]
            orig = next(
                (c for c in collected.candidates if c.repo_id == rid),
                None,
            )
            report_data = reports.get(rid, {})

            # Build descriptive full_name from research or screening data
            repo_name = rid.split("/")[-1] if "/" in rid else rid
            summary_text = s.get("innovation_summary", "")
            full_name = f"{repo_name}: {summary_text[:60]}"

            discoveries.append(
                DiscoveryResult(
                    repo_id=rid,
                    full_name=full_name,
                    url=orig.url if orig else f"https://github.com/{rid}",
                    stars=orig.stars if orig else 0,
                    innovation_score=s.get("innovation_score", 8),
                    innovation_summary=summary_text,
                    code_quality=report_data.get(
                        "code_quality",
                        s.get("code_quality_indicators", ""),
                    ),
                    author_background=report_data.get(
                        "author_background",
                        s.get("author_credibility", ""),
                    ),
                    research_report=report_data.get("research_report", ""),
                    recommendation=report_data.get("recommendation", ""),
                )
            )

        console.print(
            f"[green]Discovery complete: {len(discoveries)} projects[/green]"
        )

        return DiscoverySummary(
            discoveries=discoveries,
            total_scanned=collected.total_api_results,
            total_discovered=len(discoveries),
        )

    async def _run_screening_batched(
        self, collected: CollectedProjects, batch_size: int = 100
    ) -> list[dict]:
        """Screen all candidates in batches. Returns merged list."""
        all_screened = []
        total_batches = (len(collected.candidates) + batch_size - 1) // batch_size

        for i in range(0, len(collected.candidates), batch_size):
            batch_candidates = collected.candidates[i : i + batch_size]
            batch_collected = CollectedProjects(
                candidates=batch_candidates,
                total_api_results=collected.total_api_results,
                total_after_prefilter=collected.total_after_prefilter,
                total_with_readme=collected.total_with_readme,
                queries_executed=collected.queries_executed,
                errors=collected.errors,
                collected_at=collected.collected_at,
            )
            batch_num = i // batch_size + 1
            console.print(
                f"\n[cyan]Screening batch {batch_num}/{total_batches} "
                f"({len(batch_candidates)} candidates)...[/cyan]"
            )

            screened = await self._run_screening(batch_collected)
            all_screened.extend(screened)
            console.print(
                f"[green]Batch {batch_num}: {len(screened)} passed[/green]"
            )

        return all_screened

    async def _run_screening(
        self, collected: CollectedProjects
    ) -> list[dict]:
        """Phase 2: Single agent call to evaluate all candidates."""

        # Format candidates for the prompt (exclude readme_excerpt from JSON
        # to save tokens — pass it separately in a condensed format)
        candidates_for_json = []
        for c in collected.candidates:
            candidates_for_json.append(
                {
                    "repo_id": c.repo_id,
                    "description": c.description,
                    "stars": c.stars,
                    "language": c.language,
                    "topics": c.topics,
                    "pushed_at": c.pushed_at,
                    "forks": c.forks,
                    "open_issues": c.open_issues,
                    "license": c.license_name,
                    "readme_excerpt": c.readme_excerpt,
                }
            )

        collection_errors = ""
        if collected.errors:
            collection_errors = "### 采集失败的源\n" + "\n".join(
                f"- {e}" for e in collected.errors
            )

        prompt = GITHUB_SCREENING_PROMPT.format(
            total_candidates=len(collected.candidates),
            candidates_json=json.dumps(
                candidates_for_json, indent=2, ensure_ascii=False
            ),
            total_api_results=collected.total_api_results,
            total_after_prefilter=collected.total_after_prefilter,
            total_with_readme=collected.total_with_readme,
            collection_errors=collection_errors,
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
                    max_turns=50,
                    output_format={
                        "type": "json_schema",
                        "schema": GITHUB_SCREENING_SCHEMA,
                    },
                ),
            ):
                if isinstance(message, AssistantMessage):
                    if hasattr(message, "content"):
                        for block in message.content:
                            if hasattr(block, "text") and block.text.strip():
                                lines = block.text.strip().split("\n")
                                for line in lines:
                                    if line.strip():
                                        console.print(f"[dim]{line}[/dim]")
                elif isinstance(message, ResultMessage):
                    if message.subtype == "success":
                        total_cost = message.total_cost_usd
                        console.print(
                            f"[green]Screening completed. "
                            f"Cost: ${total_cost:.2f}[/green]"
                        )
                        if (
                            hasattr(message, "structured_output")
                            and message.structured_output
                        ):
                            result_data = message.structured_output
                    else:
                        console.print(
                            f"[yellow]Screening ended: {message.subtype}[/yellow]"
                        )

            if not result_data:
                console.print(
                    "[red]No structured output received from screening agent[/red]"
                )
                return []

            screened = result_data.get("screened_projects", [])
            total_evaluated = result_data.get("total_evaluated", 0)
            summary = result_data.get("evaluation_summary", "")

            console.print(
                f"[dim]Evaluated {total_evaluated} projects. "
                f"Summary: {summary[:100]}[/dim]"
            )

            return screened

        except Exception as e:
            console.print(f"[red]Screening agent failed: {e}[/red]")
            import traceback

            traceback.print_exc()
            return []

    async def _run_deep_research(
        self,
        screened: list[dict],
        collected: CollectedProjects,
    ) -> dict[str, dict]:
        """Phase 3: batch of 3, no retry."""
        reports: dict[str, dict] = {}

        for i in range(0, len(screened), 3):
            batch = screened[i : i + 3]
            batch_num = i // 3 + 1
            total_batches = (len(screened) + 2) // 3
            console.print(
                f"\n  [cyan]Batch {batch_num}/{total_batches}: "
                f"{', '.join(p['repo_id'] for p in batch)}[/cyan]"
            )

            tasks = [
                self._research_single_project(p, collected) for p in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, result in enumerate(results):
                rid = batch[j]["repo_id"]
                if isinstance(result, dict) and result.get("research_report"):
                    reports[rid] = result
                    console.print(
                        f"  [green]Done: {rid} "
                        f"({len(result['research_report'])} chars)[/green]"
                    )
                elif isinstance(result, Exception):
                    console.print(
                        f"  [red]Research error for {rid}: {result}[/red]"
                    )
                else:
                    console.print(f"  [yellow]No report for {rid}[/yellow]")

        return reports

    async def _research_single_project(
        self,
        project: dict,
        collected: CollectedProjects,
    ) -> dict:
        """Research a single project. Returns dict with research fields."""
        rid = project["repo_id"]

        # Find original collected data for context
        orig = next(
            (c for c in collected.candidates if c.repo_id == rid),
            None,
        )

        prompt = GITHUB_RESEARCH_PROMPT.format(
            repo_id=rid,
            url=f"https://github.com/{rid}",
            stars=orig.stars if orig else 0,
            language=orig.language if orig else "",
            innovation_summary=project.get("innovation_summary", ""),
            readme_excerpt=(orig.readme_excerpt if orig else "")[:2000],
            created_at=orig.created_at if orig else "",
            pushed_at=orig.pushed_at if orig else "",
            forks=orig.forks if orig else 0,
            open_issues=orig.open_issues if orig else 0,
            license_name=orig.license_name if orig else "",
            topics=", ".join(orig.topics) if orig else "",
        )

        try:
            result_data = None

            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    allowed_tools=self.allowed_tools,
                    permission_mode="bypassPermissions",
                    model=self.model,
                    max_turns=30,
                    output_format={
                        "type": "json_schema",
                        "schema": GITHUB_RESEARCH_SCHEMA,
                    },
                ),
            ):
                if isinstance(message, AssistantMessage):
                    if hasattr(message, "content"):
                        for block in message.content:
                            if hasattr(block, "text") and block.text.strip():
                                first_line = block.text.strip().split("\n")[0]
                                if first_line:
                                    console.print(
                                        f"    [dim]{first_line[:80]}[/dim]"
                                    )
                elif isinstance(message, ResultMessage):
                    if message.subtype == "success":
                        cost = getattr(message, "total_cost_usd", 0)
                        console.print(
                            f"    [green]Research done. Cost: ${cost:.2f}[/green]"
                        )
                        if (
                            hasattr(message, "structured_output")
                            and message.structured_output
                        ):
                            result_data = message.structured_output
                    else:
                        console.print(
                            f"    [yellow]Research ended: "
                            f"{message.subtype}[/yellow]"
                        )

            if result_data and result_data.get("research_report"):
                return result_data
            return {}

        except Exception as e:
            console.print(f"  [red]Research failed for {rid}: {e}[/red]")
            return {}
