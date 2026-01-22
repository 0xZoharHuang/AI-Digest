"""Deep research agent using Claude Agent SDK."""

import asyncio
from typing import Optional

from pydantic import BaseModel
from rich.console import Console

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage

from .prompts import get_research_prompt, RESEARCH_SYSTEM_PROMPT

console = Console()


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
    success: bool = True
    error: Optional[str] = None


class ResearchAgent:
    """Agent for deep research using Claude Agent SDK."""

    def __init__(self, model: str = "sonnet", work_dir: str = "data/temp/repos"):
        """
        Initialize research agent.

        Args:
            model: Model to use - "sonnet", "opus", or "haiku"
            work_dir: Working directory for cloned repos
        """
        self.model = model
        self.work_dir = work_dir

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
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    system_prompt=RESEARCH_SYSTEM_PROMPT,
                    allowed_tools=self.allowed_tools,
                    permission_mode="bypassPermissions",
                    model=self.model,
                    max_turns=50,  # Allow enough turns for thorough research
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
                    else:
                        console.print(f"[yellow]Research ended: {message.subtype}[/yellow]")

            # Extract title from the report (first heading or first line)
            title = self._extract_title(result_text, initial_summary)

            return ResearchResult(
                tweet_id=tweet_id,
                category=category,
                topic=topic,
                title=title,
                author=author,
                source_url=urls[0] if urls else None,
                one_liner=initial_summary,
                research_report=result_text,
                success=True,
            )

        except Exception as e:
            console.print(f"[red]Research failed: {e}[/red]")
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

    def _extract_title(self, report: str, fallback: str) -> str:
        """Extract title from research report (Markdown heading only)."""
        lines = report.strip().split("\n")

        # First pass: look for H1 heading (# Title)
        for line in lines:
            line = line.strip()
            if line.startswith("# ") and not line.startswith("##"):
                # Remove emoji if present at the start
                title = line[2:].strip()
                return title[:100]

        # Second pass: look for H2 heading (## Title)
        for line in lines:
            line = line.strip()
            if line.startswith("## "):
                title = line[3:].strip()
                return title[:100]

        # Fallback to initial_summary
        return fallback[:50]

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
