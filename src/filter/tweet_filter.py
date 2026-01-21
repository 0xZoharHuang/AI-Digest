"""LLM-based tweet filtering for AI-related content."""

import asyncio
import json
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from rich.console import Console

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

from src.crawler import Tweet

console = Console()


class ContentCategory(str, Enum):
    """Content category types."""

    PAPER = "paper"
    REPO = "repo"
    TOOL = "tool"
    BLOG = "blog"
    PRODUCT = "product"
    SHARING = "sharing"
    OTHER = "other"


class ContentTopic(str, Enum):
    """Content topic types."""

    LLM = "LLM"
    AGENT = "Agent"
    MULTIMODAL = "多模态"
    CV = "CV"
    REASONING = "推理优化"
    TRAINING = "训练"
    OTHER = "其他"


class ResearchPriority(str, Enum):
    """Research priority levels."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FilteredTweet(BaseModel):
    """Tweet with filtering metadata."""

    tweet: Tweet
    is_valuable: bool
    category: ContentCategory
    topic: ContentTopic
    initial_summary: str
    research_priority: ResearchPriority


FILTER_PROMPT = """你是一个 AI 领域内容筛选专家。请分析以下推文，判断其是否值得深入研究。

判断标准：
1. 是否与 AI/ML 技术相关（LLM、Agent、多模态、CV、推理、训练等）
2. 是否有实质内容可深入研究（带链接的论文、代码仓库、博客文章、工具产品等）
3. 排除：纯新闻报道、营销推广、无信息量的个人感想、纯表情/梗图

对于每条推文，请输出 JSON 格式：
- is_valuable: boolean - 是否值得深研
- category: "paper" | "repo" | "tool" | "blog" | "product" | "sharing" | "other"
- topic: "LLM" | "Agent" | "多模态" | "CV" | "推理优化" | "训练" | "其他"
- initial_summary: string - 一句话初步判断（中文）
- research_priority: "high" | "medium" | "low"

推文列表（JSON 格式）：
{tweets_json}

请以 JSON 数组格式输出，每条推文对应一个结果对象。只输出 JSON，不要其他文字。"""


class TweetFilter:
    """Filter tweets using LLM to identify AI-related content."""

    def __init__(self, model: str = "haiku"):
        """
        Initialize tweet filter.

        Args:
            model: Model to use - "haiku" (recommended for filtering), "sonnet", or "opus"
        """
        self.model = model
        self.batch_size = 15  # Process 15 tweets at a time

    def _prepare_tweets_for_prompt(self, tweets: list[Tweet]) -> str:
        """Prepare tweets as JSON for the prompt."""
        tweet_data = []
        for tweet in tweets:
            data = {
                "id": tweet.id,
                "author": tweet.author,
                "text": tweet.text,
                "urls": tweet.urls,
                "has_media": len(tweet.media_urls) > 0,
                "likes": tweet.like_count,
                "retweets": tweet.retweet_count,
            }
            if tweet.is_quote and tweet.quoted_text:
                data["quoted_text"] = tweet.quoted_text
                data["quoted_author"] = tweet.quoted_author
            tweet_data.append(data)
        return json.dumps(tweet_data, ensure_ascii=False, indent=2)

    def _parse_filter_response(
        self, response: str, tweets: list[Tweet]
    ) -> list[FilteredTweet]:
        """Parse LLM response into FilteredTweet objects."""
        # Clean response - remove markdown code blocks if present
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            response = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        try:
            results = json.loads(response)
        except json.JSONDecodeError as e:
            console.print(f"[red]Failed to parse filter response: {e}[/red]")
            console.print(f"[yellow]Response: {response[:500]}...[/yellow]")
            # Return all tweets as low priority on parse failure
            return [
                FilteredTweet(
                    tweet=tweet,
                    is_valuable=False,
                    category=ContentCategory.OTHER,
                    topic=ContentTopic.OTHER,
                    initial_summary="筛选失败",
                    research_priority=ResearchPriority.LOW,
                )
                for tweet in tweets
            ]

        filtered_tweets = []
        for i, result in enumerate(results):
            if i >= len(tweets):
                break
            tweet = tweets[i]

            # Parse category
            category_str = result.get("category", "other").lower()
            try:
                category = ContentCategory(category_str)
            except ValueError:
                category = ContentCategory.OTHER

            # Parse topic
            topic_str = result.get("topic", "其他")
            try:
                topic = ContentTopic(topic_str)
            except ValueError:
                topic = ContentTopic.OTHER

            # Parse priority
            priority_str = result.get("research_priority", "low").lower()
            try:
                priority = ResearchPriority(priority_str)
            except ValueError:
                priority = ResearchPriority.LOW

            filtered_tweets.append(
                FilteredTweet(
                    tweet=tweet,
                    is_valuable=result.get("is_valuable", False),
                    category=category,
                    topic=topic,
                    initial_summary=result.get("initial_summary", ""),
                    research_priority=priority,
                )
            )

        return filtered_tweets

    async def filter_batch(self, tweets: list[Tweet]) -> list[FilteredTweet]:
        """Filter a batch of tweets using Claude Agent SDK."""
        if not tweets:
            return []

        tweets_json = self._prepare_tweets_for_prompt(tweets)
        prompt = FILTER_PROMPT.format(tweets_json=tweets_json)

        try:
            # Use Claude Agent SDK with no tools (pure LLM call)
            response_text = ""
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    allowed_tools=[],  # No tools needed for filtering
                    permission_mode="bypassPermissions",
                    model=self.model,
                    max_turns=1,  # Single turn, no iteration needed
                )
            ):
                if isinstance(message, AssistantMessage):
                    if hasattr(message, "content"):
                        for block in message.content:
                            if hasattr(block, "text"):
                                response_text = block.text
                elif isinstance(message, ResultMessage):
                    if message.subtype == "success":
                        console.print(f"[dim]Filter cost: ${message.total_cost_usd:.4f}[/dim]")

            return self._parse_filter_response(response_text, tweets)
        except Exception as e:
            console.print(f"[red]Error filtering batch: {e}[/red]")
            # Return all tweets as unfiltered on error
            return [
                FilteredTweet(
                    tweet=tweet,
                    is_valuable=False,
                    category=ContentCategory.OTHER,
                    topic=ContentTopic.OTHER,
                    initial_summary=f"筛选出错: {str(e)[:50]}",
                    research_priority=ResearchPriority.LOW,
                )
                for tweet in tweets
            ]

    async def filter_tweets(self, tweets: list[Tweet]) -> list[FilteredTweet]:
        """Filter all tweets in batches."""
        console.print(f"[blue]Filtering {len(tweets)} tweets...[/blue]")

        all_filtered = []
        for i in range(0, len(tweets), self.batch_size):
            batch = tweets[i : i + self.batch_size]
            console.print(
                f"[dim]Processing batch {i // self.batch_size + 1}/"
                f"{(len(tweets) - 1) // self.batch_size + 1}[/dim]"
            )
            filtered = await self.filter_batch(batch)
            all_filtered.extend(filtered)

        # Count valuable tweets
        valuable_count = sum(1 for f in all_filtered if f.is_valuable)
        console.print(f"[green]Found {valuable_count} valuable tweets out of {len(tweets)}[/green]")

        return all_filtered

    def get_valuable_tweets(
        self, filtered_tweets: list[FilteredTweet]
    ) -> list[FilteredTweet]:
        """Get only valuable tweets, sorted by priority."""
        valuable = [f for f in filtered_tweets if f.is_valuable]
        # Sort by priority (high first)
        priority_order = {
            ResearchPriority.HIGH: 0,
            ResearchPriority.MEDIUM: 1,
            ResearchPriority.LOW: 2,
        }
        valuable.sort(key=lambda f: priority_order[f.research_priority])
        return valuable
