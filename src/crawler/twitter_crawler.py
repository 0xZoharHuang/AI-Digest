"""Twitter crawler using twscrape."""

import asyncio
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel
from rich.console import Console
from twscrape import API, gather
from twscrape.logger import set_log_level

from .config import CrawlerConfig, load_config

console = Console()

# Rate limiting configuration
MIN_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 3.0


async def _rate_limit_delay():
    """Add random delay to avoid rate limiting and reduce ban risk."""
    delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
    await asyncio.sleep(delay)


class Tweet(BaseModel):
    """Parsed tweet data."""

    id: str
    text: str
    author: str
    author_id: str
    created_at: datetime
    urls: list[str] = []
    media_urls: list[str] = []
    retweet_count: int = 0
    like_count: int = 0
    reply_count: int = 0
    is_retweet: bool = False
    is_quote: bool = False
    quoted_text: Optional[str] = None
    quoted_author: Optional[str] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class TwitterCrawler:
    """Twitter crawler using twscrape."""

    def __init__(self, config: Optional[CrawlerConfig] = None):
        self.config = config or load_config()
        self.api: Optional[API] = None
        set_log_level("WARNING")  # Reduce twscrape verbosity

    async def init_accounts(self) -> None:
        """Initialize and login Twitter accounts."""
        self.api = API()

        for account in self.config.accounts:
            # Add account to pool
            await self.api.pool.add_account(
                username=account.username,
                password=account.password,
                email=account.email,
                email_password=account.password,  # Assuming same password
            )

        # Login all accounts
        await self.api.pool.login_all()
        console.print(f"[green]Logged in {len(self.config.accounts)} account(s)[/green]")

    def _extract_urls(self, text: str) -> list[str]:
        """Extract URLs from tweet text."""
        # Match t.co links and regular URLs
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        urls = re.findall(url_pattern, text)
        return urls

    def _parse_tweet(self, raw_tweet) -> Tweet:
        """Parse raw twscrape tweet to our Tweet model."""
        # Extract URLs from text
        urls = self._extract_urls(raw_tweet.rawContent)

        # Extract media URLs
        media_urls = []
        if hasattr(raw_tweet, "media") and raw_tweet.media:
            for media in raw_tweet.media:
                if hasattr(media, "url"):
                    media_urls.append(media.url)
                elif hasattr(media, "fullUrl"):
                    media_urls.append(media.fullUrl)

        # Check if it's a retweet or quote
        is_retweet = raw_tweet.rawContent.startswith("RT @")
        is_quote = hasattr(raw_tweet, "quotedTweet") and raw_tweet.quotedTweet is not None

        quoted_text = None
        quoted_author = None
        if is_quote and raw_tweet.quotedTweet:
            quoted_text = raw_tweet.quotedTweet.rawContent
            quoted_author = raw_tweet.quotedTweet.user.username

        return Tweet(
            id=str(raw_tweet.id),
            text=raw_tweet.rawContent,
            author=raw_tweet.user.username,
            author_id=str(raw_tweet.user.id),
            created_at=raw_tweet.date,
            urls=urls,
            media_urls=media_urls,
            retweet_count=raw_tweet.retweetCount,
            like_count=raw_tweet.likeCount,
            reply_count=raw_tweet.replyCount,
            is_retweet=is_retweet,
            is_quote=is_quote,
            quoted_text=quoted_text,
            quoted_author=quoted_author,
        )

    def _filter_by_time(self, tweets: list[Tweet]) -> list[Tweet]:
        """Filter tweets to only include those within time range."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.config.time_range_hours)
        filtered = []
        for tweet in tweets:
            # Ensure tweet.created_at is timezone-aware
            tweet_time = tweet.created_at
            if tweet_time.tzinfo is None:
                tweet_time = tweet_time.replace(tzinfo=timezone.utc)
            if tweet_time > cutoff:
                filtered.append(tweet)
        return filtered

    async def get_for_you_feed(self) -> list[Tweet]:
        """Get tweets from For You feed (requires login)."""
        if not self.api:
            await self.init_accounts()

        console.print("[blue]Fetching For You feed...[/blue]")
        tweets = []

        try:
            # Add delay before API call to reduce ban risk
            await _rate_limit_delay()

            # Use home timeline which approximates For You
            raw_tweets = await gather(self.api.home_timeline(limit=self.config.for_you_limit))
            for raw_tweet in raw_tweets:
                tweet = self._parse_tweet(raw_tweet)
                tweets.append(tweet)
            console.print(f"[green]Fetched {len(tweets)} tweets from For You[/green]")
        except Exception as e:
            error_msg = str(e).lower()
            if "suspended" in error_msg or "locked" in error_msg:
                console.print(f"[red]Account may be suspended! Error: {e}[/red]")
            elif "rate limit" in error_msg:
                console.print(f"[yellow]Rate limited. Waiting before retry...[/yellow]")
            else:
                console.print(f"[red]Error fetching For You feed: {e}[/red]")

        return self._filter_by_time(tweets)

    async def get_following_feed(self) -> list[Tweet]:
        """Get tweets from Following feed."""
        if not self.api:
            await self.init_accounts()

        console.print("[blue]Fetching Following feed...[/blue]")
        tweets = []

        try:
            # Add delay before API call to reduce ban risk
            await _rate_limit_delay()

            # Use following timeline
            raw_tweets = await gather(
                self.api.following_timeline(limit=self.config.following_limit)
            )
            for raw_tweet in raw_tweets:
                tweet = self._parse_tweet(raw_tweet)
                tweets.append(tweet)
            console.print(f"[green]Fetched {len(tweets)} tweets from Following[/green]")
        except Exception as e:
            error_msg = str(e).lower()
            if "suspended" in error_msg or "locked" in error_msg:
                console.print(f"[red]Account may be suspended! Error: {e}[/red]")
            elif "rate limit" in error_msg:
                console.print(f"[yellow]Rate limited. Waiting before retry...[/yellow]")
            else:
                console.print(f"[red]Error fetching Following feed: {e}[/red]")

        return self._filter_by_time(tweets)

    async def get_user_tweets(self, username: str, limit: int = 20) -> list[Tweet]:
        """Get tweets from a specific user."""
        if not self.api:
            await self.init_accounts()

        tweets = []
        try:
            # Add delay before API call
            await _rate_limit_delay()

            # Get user ID first
            user = await self.api.user_by_login(username)
            if not user:
                console.print(f"[yellow]User not found: {username}[/yellow]")
                return tweets

            await _rate_limit_delay()
            raw_tweets = await gather(self.api.user_tweets(user.id, limit=limit))
            for raw_tweet in raw_tweets:
                tweet = self._parse_tweet(raw_tweet)
                tweets.append(tweet)
        except Exception as e:
            console.print(f"[red]Error fetching tweets for @{username}: {e}[/red]")

        return self._filter_by_time(tweets)

    async def get_all_feeds(self) -> list[Tweet]:
        """Get tweets from both For You and Following feeds, deduplicated."""
        for_you = await self.get_for_you_feed()
        following = await self.get_following_feed()

        # Deduplicate by tweet ID
        seen_ids = set()
        all_tweets = []

        for tweet in for_you + following:
            if tweet.id not in seen_ids:
                seen_ids.add(tweet.id)
                all_tweets.append(tweet)

        # Sort by created_at (newest first)
        all_tweets.sort(key=lambda t: t.created_at, reverse=True)

        console.print(f"[green]Total unique tweets: {len(all_tweets)}[/green]")
        return all_tweets

    async def search_tweets(self, query: str, limit: int = 50) -> list[Tweet]:
        """Search for tweets matching a query."""
        if not self.api:
            await self.init_accounts()

        tweets = []
        try:
            # Add delay before API call
            await _rate_limit_delay()

            raw_tweets = await gather(self.api.search(query, limit=limit))
            for raw_tweet in raw_tweets:
                tweet = self._parse_tweet(raw_tweet)
                tweets.append(tweet)
            console.print(f"[green]Found {len(tweets)} tweets for query: {query}[/green]")
        except Exception as e:
            console.print(f"[red]Error searching tweets: {e}[/red]")

        return self._filter_by_time(tweets)
