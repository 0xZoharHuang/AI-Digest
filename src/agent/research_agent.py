"""Deep research agent using Claude Agent SDK."""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Optional, Any

from pydantic import BaseModel
from rich.console import Console

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage

from .prompts import get_research_prompt, get_group_research_prompt, RESEARCH_SYSTEM_PROMPT

console = Console()


# Structured Output Schema for research results
# This schema enforces key fields while keeping flexibility for different content types
RESEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "高信息量标题，格式：主体 + 创新点/矛盾点/对比点。例：'Claude Code 悖论：代码交付加速，技术理解减速'",
        },
        "sources": {
            "type": "array",
            "description": "引用的信息来源（URL 或描述）",
            "items": {"type": "string"},
        },
        "content_type": {
            "type": "string",
            "description": "内容类型：paper/repo/blog/tool/sharing",
        },
        "background": {
            "type": "string",
            "description": "背景信息：作者/团队、领域定位、历史背景（可选）",
        },
        "comparison": {
            "type": "string",
            "description": "竞品/替代方案对比：现有方案、优劣势分析（可选）",
        },
        "insights": {
            "type": "string",
            "description": "核心洞察：本质问题、创新点、实际价值（可选）",
        },
        "limitations": {
            "type": "string",
            "description": "局限性和适用边界（可选）",
        },
        "report": {
            "type": "string",
            "description": "完整研究报告（Markdown 格式）",
        },
    },
    "required": ["title", "sources", "report"],
}


class ResearchResult(BaseModel):
    """Result of deep research on a tweet."""

    tweet_id: str
    category: str
    topic: str
    title: str
    author: str
    source_url: Optional[str] = None
    one_liner: str  # 一句话总结
    research_report: str  # 完整研究报告 (Markdown)
    tldr: Optional[str] = None  # 精炼摘要 (2-3句话，用于摘要页展示)
    sources: Optional[list[str]] = None  # 引用的信息来源
    success: bool = True
    error: Optional[str] = None


class ResearchAgent:
    """Agent for deep research using Claude Agent SDK."""

    # Repo clone directory (specified in prompts, cleaned on system restart)
    # Cross-platform: uses system temp directory
    REPO_DIR = str(Path(tempfile.gettempdir()) / "ai-digest-repos")

    def __init__(self, model: str = "sonnet"):
        """
        Initialize research agent.

        Args:
            model: Model to use - "sonnet", "opus", or "haiku"
        """
        self.model = model

        # Built-in tools from Claude Agent SDK
        # Full set of tools for comprehensive research:
        # - WebFetch: Fetch web pages, articles, README files
        # - WebSearch: Search for background info and related work
        # - Bash: Execute commands (git clone, curl, etc.)
        # - Read: Read local files (after cloning repos)
        # - Glob: Find files by pattern (navigate repo structure)
        self.allowed_tools = [
            "WebFetch",
            "WebSearch",
            "Bash",
            "Read",
            "Glob",
        ]

    async def research(
        self,
        tweet_id: str,
        tweet_text: str,
        author: str,
        category: str,
        topic: str,
        urls: list[str],
        initial_summary: str,
        tweet_url: str = "",
    ) -> ResearchResult:
        """
        Perform deep research on a tweet.

        Args:
            tweet_id: Tweet ID
            tweet_text: Tweet content
            author: Tweet author
            category: Content category (paper, repo, blog, tool, etc.)
            topic: Content topic (LLM, Agent, etc.)
            urls: URLs in the tweet
            initial_summary: Initial summary from filtering
            tweet_url: URL to the tweet itself (for fetching threads)

        Returns:
            ResearchResult with the research report
        """
        console.print(f"[blue]Researching: {initial_summary[:50]}...[/blue]")

        # Get the appropriate prompt for this category
        prompt = get_research_prompt(category, tweet_text, author, urls, tweet_url)

        try:
            # Use Claude Agent SDK's query function
            result_text = ""
            structured_output: dict[str, Any] | None = None

            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    system_prompt=RESEARCH_SYSTEM_PROMPT,
                    allowed_tools=self.allowed_tools,
                    permission_mode="bypassPermissions",
                    model=self.model,
                    max_turns=50,  # Allow enough turns for thorough research
                    output_format={
                        "type": "json_schema",
                        "schema": RESEARCH_OUTPUT_SCHEMA,
                    },
                )
            ):
                # Collect assistant messages for the final report
                if isinstance(message, AssistantMessage):
                    if hasattr(message, "content"):
                        for block in message.content:
                            if hasattr(block, "text"):
                                result_text = block.text  # Keep updating with latest
                elif isinstance(message, ResultMessage):
                    # Final result
                    if message.subtype == "success":
                        console.print(f"[green]Research completed. Cost: ${message.total_cost_usd:.4f}[/green]")
                        # Get structured output from ResultMessage
                        if hasattr(message, "structured_output") and message.structured_output:
                            structured_output = message.structured_output
                    else:
                        console.print(f"[yellow]Research ended: {message.subtype}[/yellow]")

            # Extract data from structured output or fall back to text parsing
            title, report, sources = self._parse_research_output(
                structured_output, result_text, initial_summary
            )

            result = ResearchResult(
                tweet_id=tweet_id,
                category=category,
                topic=topic,
                title=title,
                author=author,
                source_url=urls[0] if urls else None,
                one_liner=initial_summary,
                research_report=report,
                sources=sources,
                success=True,
            )

            # Clean up old repos after successful research
            await self._cleanup_research_artifacts()

            return result

        except Exception as e:
            console.print(f"[red]Research failed: {e}[/red]")

            # Clean up even on failure
            await self._cleanup_research_artifacts()

            return ResearchResult(
                tweet_id=tweet_id,
                category=category,
                topic=topic,
                title=initial_summary[:50],
                author=author,
                source_url=urls[0] if urls else None,
                one_liner=initial_summary,
                research_report=f"研究失败: {str(e)}",
                success=False,
                error=str(e),
            )

    # Low-quality title patterns to filter out
    BAD_TITLE_PATTERNS = [
        "核心内容", "核心观点", "背景调查", "总结", "概述", "分析", "研究",
        "一、", "二、", "三、", "1.", "2.", "3.", "##",
    ]

    def _parse_research_output(
        self,
        structured_output: dict[str, Any] | None,
        fallback_text: str,
        fallback_summary: str,
    ) -> tuple[str, str, list[str] | None]:
        """
        Parse research output from structured output or fallback to text.

        Returns:
            tuple of (title, report, sources)
        """
        if structured_output:
            # Use structured output fields
            title = structured_output.get("title", "")
            report = structured_output.get("report", fallback_text)
            sources = structured_output.get("sources", [])

            # Validate title quality
            if not title or not self._is_valid_title(title):
                title = self._extract_title(report, fallback_summary)

            console.print(f"[dim]Structured output: title='{title[:40]}...', sources={len(sources)}[/dim]")
            return title, report, sources if sources else None

        # Fallback to text parsing
        title = self._extract_title(fallback_text, fallback_summary)
        return title, fallback_text, None

    def _extract_title(self, report: str, fallback: str) -> str:
        """Extract and validate title from research report (Markdown heading)."""
        lines = report.strip().split("\n")

        # First pass: look for H1 heading (# Title) in first 15 lines
        for line in lines[:15]:
            line = line.strip()
            if line.startswith("# ") and not line.startswith("##"):
                title = line[2:].strip()
                if self._is_valid_title(title):
                    return title[:120]

        # Second pass: look for H2 heading (## Title)
        for line in lines[:15]:
            line = line.strip()
            if line.startswith("## "):
                title = line[3:].strip()
                if self._is_valid_title(title):
                    return title[:120]

        # Fallback to initial_summary
        return fallback[:80]

    def _is_valid_title(self, title: str) -> bool:
        """Check if title has enough information value."""
        if len(title) < 8:
            return False
        # Check against bad patterns
        for bad in self.BAD_TITLE_PATTERNS:
            if title.startswith(bad) or title == bad:
                return False
        return True

    async def research_group(
        self,
        group_id: str,
        topic: str,
        combined_tweets: str,
        urls: list[str],
        combined_summary: str,
        tweet_ids: list[str],
        primary_author: str,
    ) -> ResearchResult:
        """
        Research a group of tweets together.

        Args:
            group_id: Unique group identifier
            topic: Content topic (LLM, Agent, etc.)
            combined_tweets: Combined text from all tweets
            urls: All URLs from all tweets
            combined_summary: Combined initial summaries
            tweet_ids: List of tweet IDs in this group
            primary_author: Author of the first tweet

        Returns:
            ResearchResult with the combined research report
        """
        console.print(f"[blue]Researching group ({len(tweet_ids)} tweets): {combined_summary[:50]}...[/blue]")

        prompt = get_group_research_prompt(
            topic=topic,
            combined_tweets=combined_tweets,
            urls=urls,
            tweet_count=len(tweet_ids),
        )

        try:
            result_text = ""
            structured_output: dict[str, Any] | None = None

            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    system_prompt=RESEARCH_SYSTEM_PROMPT,
                    allowed_tools=self.allowed_tools,
                    permission_mode="bypassPermissions",
                    model=self.model,
                    max_turns=50,
                    output_format={
                        "type": "json_schema",
                        "schema": RESEARCH_OUTPUT_SCHEMA,
                    },
                )
            ):
                if isinstance(message, AssistantMessage):
                    if hasattr(message, "content"):
                        for block in message.content:
                            if hasattr(block, "text"):
                                result_text = block.text
                elif isinstance(message, ResultMessage):
                    if message.subtype == "success":
                        console.print(f"[green]Group research completed. Cost: ${message.total_cost_usd:.4f}[/green]")
                        # Get structured output from ResultMessage
                        if hasattr(message, "structured_output") and message.structured_output:
                            structured_output = message.structured_output
                    else:
                        console.print(f"[yellow]Group research ended: {message.subtype}[/yellow]")

            # Extract data from structured output or fall back to text parsing
            title, report, sources = self._parse_research_output(
                structured_output, result_text, combined_summary
            )

            result = ResearchResult(
                tweet_id=",".join(tweet_ids),  # Comma-separated for groups
                category="group",
                topic=topic,
                title=title,
                author=primary_author,
                source_url=urls[0] if urls else None,
                one_liner=combined_summary,
                research_report=report,
                sources=sources,
                success=True,
            )

            # Clean up old repos after successful research
            await self._cleanup_research_artifacts()

            return result

        except Exception as e:
            console.print(f"[red]Group research failed: {e}[/red]")

            # Clean up even on failure
            await self._cleanup_research_artifacts()

            return ResearchResult(
                tweet_id=",".join(tweet_ids),
                category="group",
                topic=topic,
                title=combined_summary[:50],
                author=primary_author,
                source_url=urls[0] if urls else None,
                one_liner=combined_summary,
                research_report=f"组研究失败: {str(e)}",
                success=False,
                error=str(e),
            )

    async def research_group_with_retry(
        self,
        group_id: str,
        topic: str,
        combined_tweets: str,
        urls: list[str],
        combined_summary: str,
        tweet_ids: list[str],
        primary_author: str,
        max_retries: int = 2,
    ) -> ResearchResult:
        """Research group with retry on failure."""
        last_error = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                console.print(f"[yellow]Retry {attempt}/{max_retries}...[/yellow]")
                await asyncio.sleep(2 ** attempt)

            result = await self.research_group(
                group_id=group_id,
                topic=topic,
                combined_tweets=combined_tweets,
                urls=urls,
                combined_summary=combined_summary,
                tweet_ids=tweet_ids,
                primary_author=primary_author,
            )

            if result.success:
                return result
            last_error = result.error

        return ResearchResult(
            tweet_id=",".join(tweet_ids),
            category="group",
            topic=topic,
            title=combined_summary[:50],
            author=primary_author,
            source_url=urls[0] if urls else None,
            one_liner=combined_summary,
            research_report=f"组研究失败（重试 {max_retries} 次后）: {last_error}",
            success=False,
            error=last_error,
        )

    async def research_with_retry(
        self,
        tweet_id: str,
        tweet_text: str,
        author: str,
        category: str,
        topic: str,
        urls: list[str],
        initial_summary: str,
        tweet_url: str = "",
        max_retries: int = 2,
    ) -> ResearchResult:
        """Research with retry on failure."""
        last_error = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                console.print(f"[yellow]Retry {attempt}/{max_retries}...[/yellow]")
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

            result = await self.research(
                tweet_id=tweet_id,
                tweet_text=tweet_text,
                author=author,
                category=category,
                topic=topic,
                urls=urls,
                initial_summary=initial_summary,
                tweet_url=tweet_url,
            )

            if result.success:
                return result
            last_error = result.error

        # All retries failed
        return ResearchResult(
            tweet_id=tweet_id,
            category=category,
            topic=topic,
            title=initial_summary[:50],
            author=author,
            source_url=urls[0] if urls else None,
            one_liner=initial_summary,
            research_report=f"研究失败（重试 {max_retries} 次后）: {last_error}",
            success=False,
            error=last_error,
        )

    async def _cleanup_research_artifacts(self) -> None:
        """Clean up old repositories using LRU strategy.

        Keeps only the most recent 5 repositories to prevent disk space issues.
        This method is called after each research or research_group completion.
        """
        import shutil
        from pathlib import Path

        repo_path = Path(self.REPO_DIR)
        if not repo_path.exists():
            return

        try:
            # Get all repos sorted by modification time (oldest first)
            repos = sorted(
                [p for p in repo_path.iterdir() if p.is_dir()],
                key=lambda p: p.stat().st_mtime
            )

            # LRU: Keep only the most recent MAX_REPOS
            MAX_REPOS = 5

            if len(repos) > MAX_REPOS:
                for repo in repos[:-MAX_REPOS]:
                    try:
                        shutil.rmtree(repo)
                        console.print(f"[dim]Cleaned up old repo: {repo.name}[/dim]")
                    except Exception as e:
                        console.print(f"[yellow]Failed to clean {repo.name}: {e}[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Cleanup failed: {e}[/yellow]")
