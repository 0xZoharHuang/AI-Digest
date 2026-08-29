"""OpenClaw ecosystem tracker - deterministic collection + Agent analysis."""

import asyncio
import json
import hashlib
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from pydantic import BaseModel
from rich.console import Console

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage

console = Console()


# --- Pydantic Models ---


class CollectedData(BaseModel):
    """Phase 1: deterministic collection results."""

    releases: list[dict] = []
    commits: list[dict] = []
    org_repos: list[dict] = []
    changelog_content: str = ""
    blog_content: str = ""
    npm_versions: list[dict] = []
    collected_at: str = ""
    errors: list[str] = []


class OpenClawUpdate(BaseModel):
    """A single tracked update in the OpenClaw ecosystem."""

    update_id: str
    source_type: str  # release, commit, issue, article, blog_post, social, companion_app, ecosystem_repo
    repo: str = ""
    title: str
    url: str
    importance: int  # 1-10
    summary: str
    research_report: str = ""
    tldr: str = ""
    date: str = ""
    tags: list[str] = []


class OpenClawTrackingResult(BaseModel):
    """Result of a tracking session."""

    updates: list[OpenClawUpdate] = []
    period_summary: str = ""
    stats: dict = {}


# --- Output Schemas for Agent ---


# Phase 1: Screening schema (no research_report field)
OPENCLAW_SCREENING_SCHEMA = {
    "type": "object",
    "properties": {
        "updates": {
            "type": "array",
            "description": "List of significant OpenClaw ecosystem updates (importance >= min_importance)",
            "items": {
                "type": "object",
                "properties": {
                    "update_id": {
                        "type": "string",
                        "description": "Unique ID: release:<tag>, commit:<sha8>, article:<url_hash>, social:<url_hash>, repo:<owner/name>",
                    },
                    "source_type": {
                        "type": "string",
                        "enum": [
                            "release",
                            "commit",
                            "issue",
                            "article",
                            "blog_post",
                            "social",
                            "companion_app",
                            "ecosystem_repo",
                        ],
                        "description": "Type of update source",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository (e.g. openclaw/openclaw). Empty for non-GitHub sources.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Descriptive title of the update (Chinese)",
                    },
                    "url": {
                        "type": "string",
                        "description": "Primary URL for this update",
                    },
                    "importance": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Importance score",
                    },
                    "summary": {
                        "type": "string",
                        "description": "2-3 sentence summary (Chinese)",
                    },
                    "date": {
                        "type": "string",
                        "description": "Date of the update (ISO format or approximate)",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags: breaking-change, new-feature, bug-fix, performance, security, documentation, community, companion-app, ecosystem",
                    },
                },
                "required": [
                    "update_id",
                    "source_type",
                    "title",
                    "url",
                    "importance",
                    "summary",
                ],
            },
        },
        "period_summary": {
            "type": "string",
            "description": "Executive summary of all OpenClaw activity in this tracking period (Chinese, Markdown)",
        },
        "stats": {
            "type": "object",
            "properties": {
                "total_sources_checked": {"type": "integer"},
                "total_updates_found": {"type": "integer"},
                "high_importance_count": {"type": "integer"},
                "repos_scanned": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "total_sources_checked",
                "total_updates_found",
                "high_importance_count",
            ],
        },
    },
    "required": ["updates", "period_summary", "stats"],
}

# Phase 2: Research schema (single report per agent call)
OPENCLAW_RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "research_report": {
            "type": "string",
            "description": "Complete deep research report in Markdown format (Chinese)",
        },
    },
    "required": ["research_report"],
}


# --- Agent Prompt ---


OPENCLAW_TRACKER_PROMPT = """你是 OpenClaw 生态追踪分析师。任务：全面追踪 OpenClaw 生态系统在过去 {days_back} 天内的所有重要动态。

## 背景
OpenClaw 是一个超大规模开源 AI Agent 项目（145k+ GitHub stars），由 Peter Steinberger (@steipete) 创建。
- 主仓库: openclaw/openclaw
- 官网: openclaw.ai
- 生态项目: NanoClaw, Crabwalk, CoClaw 等

## 已采集数据（确定性来源）

以下数据已通过 API 和页面抓取获得，请作为分析基础：

### GitHub Releases（最近10个）
```json
{releases_json}
```

### 近期 Commits（过去{days_back}天）
```json
{commits_json}
```

### 组织仓库列表
```json
{org_repos_json}
```

### 官方 Changelog 页面内容
{changelog_content}

### 官方博客内容
{blog_content}

### npm 版本历史
```json
{npm_versions_json}
```

{collection_errors}

## 你的任务

### 步骤 1: 扩展搜索（发现上面没覆盖的来源）

使用 WebSearch 搜索以下内容（每个搜索独立执行）：

1. `"openclaw" OR "open claw" site:x.com` — @openclaw 和 @steipete 最新动态
2. `"openclaw" site:reddit.com` — Reddit 社区讨论
3. `"openclaw" site:news.ycombinator.com` — Hacker News 讨论
4. `"openclaw" 2026` — 最新新闻/博客文章
5. `"openclaw" companion OR crabwalk OR coclaw OR nanoclaw` — 生态项目动态

如果发现值得深入的链接，使用 WebFetch 获取详情。

### 步骤 2: 汇总评估

合并已采集数据和搜索发现，对每条更新打分（1-10）：

**重要性评分标准：**
- **10**: 大版本发布 / 架构性 breaking change
- **8-9**: 新功能发布 / 新 companion app / 重大性能改进 / 重大安全修复
- **6-7**: 重要 bug 修复 / 文档重大更新 / 有影响力的社区讨论 / 重要的新版本
- **4-5**: 常规 bug 修复 / 小功能 / 普通讨论
- **1-3**: 日常维护 / typo 修复 / 依赖更新

**注意**：
- 只保留评分 >= {min_importance} 的更新
- 多个相关 commits 可以合并为一个更新（如 "v2026.2.6 发布" 包含该版本的所有 commits）
- 相同内容出现在多个源时去重，保留最权威的源

### 步骤 3: 生成 period_summary

用中文概括本追踪周期所有重要动态，包括：
- 最重要的 1-3 个事件
- 版本迭代概况
- 生态发展趋势
- 社区关注焦点

## 输出要求

1. updates 数组中只包含 importance >= {min_importance} 的更新
2. **不要**生成 research_report（深度研究将在后续独立步骤中完成）
3. 每个更新只需 summary（2-3句概括）
4. update_id 格式：`release:<tag>`, `commit:<sha前8位>`, `article:<url的md5前8位>`, `social:<url的md5前8位>`, `repo:<owner/name>`
5. 使用中文输出
6. 标题要高信息量，突出关键变化

开始执行。
"""


OPENCLAW_RESEARCH_PROMPT = """你是 OpenClaw 生态深度研究分析师。对以下更新进行针对性研究。

## 研究目标

**标题**: {title}
**类型**: {source_type}
**URL**: {url}
**初步摘要**: {summary}

## 相关背景数据

{context_data}

## 研究要求

1. 使用 WebFetch 获取目标 URL 内容
2. 根据更新类型，做 1-2 次针对性 WebSearch（不要泛搜）
3. 输出聚焦于：核心变化是什么、影响范围、用户需要知道的关键信息

## 输出

输出 Markdown 格式研究报告（中文），聚焦核心发现，避免冗余。
"""


# --- Collector ---


class OpenClawCollector:
    """Deterministic collector for known OpenClaw information sources."""

    def __init__(self, github_token: str, config: dict = None):
        self.github_token = github_token
        config = config or {}
        self.tracked_repos = config.get("tracked_repos", ["openclaw/openclaw"])
        self.tracked_org = config.get("tracked_org", "openclaw")
        self.changelog_url = config.get(
            "official_changelog", "https://www.getopenclaw.ai/changelog"
        )
        self.blog_url = config.get("official_blog", "https://openclaw.ai/blog")
        self.npm_package = config.get("npm_package", "openclaw")

    async def collect(self, days_back: int = 3) -> CollectedData:
        """Collect data from all known sources concurrently."""
        console.print("[cyan]Phase 1: Collecting from known sources...[/cyan]")

        since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        errors = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Run all fetches concurrently
            tasks = {
                "releases": self._fetch_github_releases(client),
                "commits": self._fetch_github_commits(client, since_date),
                "org_repos": self._fetch_org_repos(client),
                "changelog": self._fetch_url_content(client, self.changelog_url),
                "blog": self._fetch_url_content(client, self.blog_url),
                "npm": self._fetch_npm_versions(client),
            }

            results = {}
            for name, task in tasks.items():
                try:
                    results[name] = await task
                    console.print(f"  [green]OK[/green] {name}")
                except Exception as e:
                    results[name] = [] if name not in ("changelog", "blog") else ""
                    errors.append(f"{name}: {str(e)[:100]}")
                    console.print(f"  [yellow]FAIL[/yellow] {name}: {e}")

        collected = CollectedData(
            releases=results.get("releases", []),
            commits=results.get("commits", []),
            org_repos=results.get("org_repos", []),
            changelog_content=results.get("changelog", ""),
            blog_content=results.get("blog", ""),
            npm_versions=results.get("npm", []),
            collected_at=datetime.now().isoformat(),
            errors=errors,
        )

        total = (
            len(collected.releases)
            + len(collected.commits)
            + len(collected.org_repos)
            + len(collected.npm_versions)
            + (1 if collected.changelog_content else 0)
            + (1 if collected.blog_content else 0)
        )
        console.print(f"[green]Collected {total} data points from {6 - len(errors)}/6 sources[/green]")
        return collected

    async def _fetch_github_releases(self, client: httpx.AsyncClient) -> list[dict]:
        """Fetch recent releases for all tracked repos."""
        all_releases = []
        for repo in self.tracked_repos:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}/releases",
                params={"per_page": 10},
                headers={"Authorization": f"token {self.github_token}"},
            )
            resp.raise_for_status()
            releases = resp.json()
            for r in releases:
                all_releases.append({
                    "repo": repo,
                    "tag_name": r.get("tag_name", ""),
                    "name": r.get("name", ""),
                    "body": (r.get("body") or "")[:2000],
                    "published_at": r.get("published_at", ""),
                    "html_url": r.get("html_url", ""),
                })
        return all_releases

    async def _fetch_github_commits(
        self, client: httpx.AsyncClient, since: str
    ) -> list[dict]:
        """Fetch recent commits for all tracked repos."""
        all_commits = []
        for repo in self.tracked_repos:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}/commits",
                params={"since": since, "per_page": 50},
                headers={"Authorization": f"token {self.github_token}"},
            )
            resp.raise_for_status()
            commits = resp.json()
            for c in commits:
                all_commits.append({
                    "repo": repo,
                    "sha": c.get("sha", "")[:8],
                    "message": (c.get("commit", {}).get("message") or "")[:200],
                    "author": c.get("commit", {}).get("author", {}).get("name", ""),
                    "date": c.get("commit", {}).get("author", {}).get("date", ""),
                    "url": c.get("html_url", ""),
                })
        return all_commits

    async def _fetch_org_repos(self, client: httpx.AsyncClient) -> list[dict]:
        """Fetch all repos in the tracked org."""
        resp = await client.get(
            f"https://api.github.com/orgs/{self.tracked_org}/repos",
            params={"sort": "updated", "per_page": 30},
            headers={"Authorization": f"token {self.github_token}"},
        )
        resp.raise_for_status()
        repos = resp.json()
        return [
            {
                "name": r.get("name", ""),
                "full_name": r.get("full_name", ""),
                "description": (r.get("description") or "")[:200],
                "stars": r.get("stargazers_count", 0),
                "updated_at": r.get("updated_at", ""),
                "html_url": r.get("html_url", ""),
                "language": r.get("language", ""),
            }
            for r in repos
        ]

    async def _fetch_url_content(self, client: httpx.AsyncClient, url: str) -> str:
        """Fetch URL content as text, truncated."""
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        # Truncate to avoid huge prompts
        text = resp.text[:8000]
        return text

    async def _fetch_npm_versions(self, client: httpx.AsyncClient) -> list[dict]:
        """Fetch recent npm version info."""
        resp = await client.get(
            f"https://registry.npmjs.org/{self.npm_package}",
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract recent versions with timestamps
        time_info = data.get("time", {})
        versions = []
        for version, timestamp in sorted(
            time_info.items(), key=lambda x: x[1], reverse=True
        ):
            if version in ("created", "modified"):
                continue
            versions.append({"version": version, "published_at": timestamp})
            if len(versions) >= 10:
                break
        return versions


# --- Agent ---


class OpenClawTrackerAgent:
    """OpenClaw ecosystem tracker: deterministic collection + Agent analysis."""

    def __init__(self, github_token: str, model: str = "sonnet", config: dict = None):
        self.github_token = github_token
        self.model = model
        self.config = config or {}
        self.collector = OpenClawCollector(github_token, config=self.config)
        self.allowed_tools = [
            "WebFetch",
            "WebSearch",
        ]

    async def track(
        self, days_back: int = 3, min_importance: int = 5
    ) -> OpenClawTrackingResult:
        """
        Track OpenClaw ecosystem updates.

        Phase 1: Deterministic collection (httpx)
        Phase 2: Agent screening + scoring (1 agent call)
        Phase 3: Deep research per high-importance update (N concurrent agent calls)
        """
        # Phase 1: Deterministic collection
        collected = await self.collector.collect(days_back)

        # Phase 2: Agent screening + extended search
        console.print("\n[cyan]Phase 2: Agent screening + extended search...[/cyan]")
        result = await self._run_screening(collected, days_back, min_importance)

        if not result.updates:
            return result

        # Phase 3: Deep research for high-importance updates
        high_imp = [u for u in result.updates if u.importance >= 7]
        if high_imp:
            console.print(
                f"\n[cyan]Phase 3: Deep research for {len(high_imp)} "
                f"high-importance updates (concurrent)...[/cyan]"
            )
            reports = await self._run_deep_research(high_imp, collected)
            for u in result.updates:
                if u.update_id in reports:
                    u.research_report = reports[u.update_id]

            success_count = sum(1 for u in high_imp if u.research_report)
            console.print(
                f"[green]Deep research completed: "
                f"{success_count}/{len(high_imp)} reports generated[/green]"
            )

        return result

    async def _run_screening(
        self,
        collected: CollectedData,
        days_back: int,
        min_importance: int,
    ) -> OpenClawTrackingResult:
        """Phase 2: Screen and score all updates (no research reports)."""

        collection_errors = ""
        if collected.errors:
            collection_errors = "### 采集失败的源\n" + "\n".join(
                f"- {e}" for e in collected.errors
            )

        prompt = OPENCLAW_TRACKER_PROMPT.format(
            days_back=days_back,
            min_importance=min_importance,
            releases_json=json.dumps(collected.releases, indent=2, ensure_ascii=False),
            commits_json=json.dumps(
                collected.commits[:30], indent=2, ensure_ascii=False
            ),
            org_repos_json=json.dumps(
                collected.org_repos, indent=2, ensure_ascii=False
            ),
            changelog_content=collected.changelog_content[:4000] or "(获取失败)",
            blog_content=collected.blog_content[:4000] or "(获取失败)",
            npm_versions_json=json.dumps(
                collected.npm_versions, indent=2, ensure_ascii=False
            ),
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
                        "schema": OPENCLAW_SCREENING_SCHEMA,
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
                            f"[green]Screening completed. Cost: ${total_cost:.2f}[/green]"
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
                console.print("[red]No structured output received from screening agent[/red]")
                return OpenClawTrackingResult()

            updates = [
                OpenClawUpdate(**u) for u in result_data.get("updates", [])
            ]

            return OpenClawTrackingResult(
                updates=updates,
                period_summary=result_data.get("period_summary", ""),
                stats=result_data.get("stats", {}),
            )

        except Exception as e:
            console.print(f"[red]Screening agent failed: {e}[/red]")
            import traceback
            traceback.print_exc()
            return OpenClawTrackingResult()

    async def _run_deep_research(
        self,
        updates: list[OpenClawUpdate],
        collected: CollectedData,
    ) -> dict[str, str]:
        """Phase 3: Run deep research in batches of 2, no retry."""

        reports = {}

        for i in range(0, len(updates), 2):
            batch = updates[i : i + 2]
            batch_num = i // 2 + 1
            total_batches = (len(updates) + 1) // 2
            console.print(
                f"\n  [cyan]Batch {batch_num}/{total_batches}: "
                f"{', '.join(u.title[:30] + '...' for u in batch)}[/cyan]"
            )

            tasks = [
                self._research_single_update(u, collected) for u in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, result in enumerate(results):
                uid = batch[j].update_id
                if isinstance(result, str) and result:
                    reports[uid] = result
                    console.print(
                        f"  [green]Done: {uid} ({len(result)} chars)[/green]"
                    )
                elif isinstance(result, Exception):
                    console.print(f"  [red]Research error for {uid}: {result}[/red]")
                else:
                    console.print(f"  [yellow]No report for {uid}[/yellow]")

        return reports

    async def _research_single_update(
        self,
        update: OpenClawUpdate,
        collected: CollectedData,
    ) -> str:
        """Research a single update and return the report string."""

        # Build context data relevant to this update
        context_parts = []
        if update.source_type == "release" and collected.releases:
            matching = [
                r for r in collected.releases
                if r.get("tag_name", "") in update.update_id
            ]
            if matching:
                context_parts.append(
                    "### Release 详情\n```json\n"
                    + json.dumps(matching[0], indent=2, ensure_ascii=False)
                    + "\n```"
                )
        if update.source_type == "commit" and collected.commits:
            sha = update.update_id.replace("commit:", "")
            matching = [c for c in collected.commits if c.get("sha", "").startswith(sha)]
            if matching:
                context_parts.append(
                    "### Commit 详情\n```json\n"
                    + json.dumps(matching[0], indent=2, ensure_ascii=False)
                    + "\n```"
                )
        # Always include a brief ecosystem overview
        if collected.org_repos:
            repo_names = [r.get("full_name", "") for r in collected.org_repos[:10]]
            context_parts.append(f"### OpenClaw 组织仓库\n{', '.join(repo_names)}")

        context_data = "\n\n".join(context_parts) if context_parts else "(无额外背景数据)"

        prompt = OPENCLAW_RESEARCH_PROMPT.format(
            title=update.title,
            source_type=update.source_type,
            url=update.url,
            summary=update.summary,
            context_data=context_data,
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
                        "schema": OPENCLAW_RESEARCH_SCHEMA,
                    },
                ),
            ):
                if isinstance(message, AssistantMessage):
                    if hasattr(message, "content"):
                        for block in message.content:
                            if hasattr(block, "text") and block.text.strip():
                                # Log progress (first line only)
                                first_line = block.text.strip().split("\n")[0]
                                if first_line:
                                    console.print(f"    [dim]{first_line[:80]}[/dim]")
                elif isinstance(message, ResultMessage):
                    if message.subtype == "success":
                        cost = getattr(message, "total_cost_usd", 0)
                        console.print(f"    [green]Research done. Cost: ${cost:.2f}[/green]")
                        if (
                            hasattr(message, "structured_output")
                            and message.structured_output
                        ):
                            result_data = message.structured_output
                    else:
                        console.print(f"    [yellow]Research ended: {message.subtype}[/yellow]")

            if result_data and result_data.get("research_report"):
                return result_data["research_report"]
            return ""

        except Exception as e:
            console.print(
                f"  [red]Research failed for {update.update_id}: {e}[/red]"
            )
            return ""
