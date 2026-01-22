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

from .account_health import AccountHealthMonitor, ErrorType, classify_error
from .config import CrawlerConfig, load_config

console = Console()


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
    thread_content: Optional[str] = None  # Full thread text if this is a thread

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}

    @property
    def tweet_url(self) -> str:
        """Get the URL to this tweet (for fetching full thread)."""
        return f"https://x.com/{self.author}/status/{self.id}"

    @property
    def full_content(self) -> str:
        """Get full content including thread if available."""
        if self.thread_content:
            return self.thread_content
        return self.text


class TwitterCrawler:
    """Twitter crawler using twscrape."""

    def __init__(self, config: Optional[CrawlerConfig] = None):
        self.config = config or load_config()
        self.api: Optional[API] = None
        self.health_monitor = AccountHealthMonitor()
        self._current_username: Optional[str] = None
        set_log_level("WARNING")  # Reduce twscrape verbosity

    async def _rate_limit_delay(self, context: str = "request") -> None:
        """
        Enhanced random delay with jitter to mimic human behavior.

        Args:
            context: "request" for normal requests, "page" for pagination
        """
        if context == "page":
            base_delay = self.config.delay.page_delay_seconds
        else:
            base_delay = random.uniform(
                self.config.delay.min_seconds,
                self.config.delay.max_seconds
            )

        # Add Gaussian jitter to avoid uniform patterns
        jitter = random.gauss(0, base_delay * 0.2)
        delay = max(1.0, base_delay + jitter)

        await asyncio.sleep(delay)

    async def _handle_error_with_retry(
        self,
        error: Exception,
        retry_count: int,
        operation: str
    ) -> bool:
        """
        Handle error with exponential backoff.

        Returns:
            True if should retry, False otherwise
        """
        error_str = str(error).lower()
        username = self._current_username or "unknown"

        # Classify error and record in health monitor
        error_type = classify_error(str(error))
        if error_type:
            self.health_monitor.record_error(username, error_type)

        # Check if max retries exceeded
        if retry_count >= self.config.retry.max_retries:
            console.print(f"[red]Max retries ({self.config.retry.max_retries}) exceeded for {operation}[/red]")
            return False

        # Handle different error types
        if "rate limit" in error_str or "429" in error_str:
            wait_time = min(
                self.config.retry.backoff_base ** retry_count + random.uniform(0, 1),
                self.config.retry.max_backoff_seconds
            )
            console.print(f"[yellow]Rate limited, waiting {wait_time:.1f}s (retry {retry_count + 1})...[/yellow]")
            await asyncio.sleep(wait_time)
            return True

        elif "suspended" in error_str or "locked" in error_str or "banned" in error_str:
            console.print(f"[red]Account suspended or locked![/red]")
            self.health_monitor.mark_suspended(username)
            return False

        elif "unusual activity" in error_str or "suspicious" in error_str:
            console.print(f"[red]Suspicious activity detected, cooling down 1 hour...[/red]")
            self.health_monitor.set_cooldown(username, hours=1.0)
            await asyncio.sleep(3600)
            return True

        else:
            # Generic error: exponential backoff
            wait_time = min(
                self.config.retry.backoff_base ** retry_count + random.uniform(0, 1),
                self.config.retry.max_backoff_seconds
            )
            console.print(f"[yellow]Error: {error}, retrying in {wait_time:.1f}s...[/yellow]")
            await asyncio.sleep(wait_time)
            return retry_count < self.config.retry.max_retries

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
            # Track current username for health monitoring (single account)
            self._current_username = account.username

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
        retry_count = 0

        while retry_count <= self.config.retry.max_retries:
            try:
                # Add delay before API call to reduce ban risk
                await self._rate_limit_delay()

                # Use home timeline which approximates For You
                raw_tweets = await gather(self.api.home_timeline(limit=self.config.for_you_limit))
                for raw_tweet in raw_tweets:
                    tweet = self._parse_tweet(raw_tweet)
                    tweets.append(tweet)

                # Record success in health monitor
                if self._current_username:
                    self.health_monitor.record_success(self._current_username)

                console.print(f"[green]Fetched {len(tweets)} tweets from For You[/green]")
                break  # Success, exit retry loop

            except Exception as e:
                should_retry = await self._handle_error_with_retry(e, retry_count, "For You feed")
                if not should_retry:
                    break
                retry_count += 1

        return self._filter_by_time(tweets)

    async def get_following_feed(self) -> list[Tweet]:
        """Get tweets from Following feed."""
        if not self.api:
            await self.init_accounts()

        console.print("[blue]Fetching Following feed...[/blue]")
        tweets = []
        retry_count = 0

        while retry_count <= self.config.retry.max_retries:
            try:
                # Add delay before API call to reduce ban risk
                await self._rate_limit_delay()

                # Use following timeline
                raw_tweets = await gather(
                    self.api.following_timeline(limit=self.config.following_limit)
                )
                for raw_tweet in raw_tweets:
                    tweet = self._parse_tweet(raw_tweet)
                    tweets.append(tweet)

                # Record success in health monitor
                if self._current_username:
                    self.health_monitor.record_success(self._current_username)

                console.print(f"[green]Fetched {len(tweets)} tweets from Following[/green]")
                break  # Success, exit retry loop

            except Exception as e:
                should_retry = await self._handle_error_with_retry(e, retry_count, "Following feed")
                if not should_retry:
                    break
                retry_count += 1

        return self._filter_by_time(tweets)

    async def get_user_tweets(self, username: str, limit: int = 20) -> list[Tweet]:
        """Get tweets from a specific user."""
        if not self.api:
            await self.init_accounts()

        tweets = []
        retry_count = 0

        while retry_count <= self.config.retry.max_retries:
            try:
                # Add delay before API call
                await self._rate_limit_delay()

                # Get user ID first
                user = await self.api.user_by_login(username)
                if not user:
                    console.print(f"[yellow]User not found: {username}[/yellow]")
                    return tweets

                await self._rate_limit_delay()
                raw_tweets = await gather(self.api.user_tweets(user.id, limit=limit))
                for raw_tweet in raw_tweets:
                    tweet = self._parse_tweet(raw_tweet)
                    tweets.append(tweet)

                # Record success
                if self._current_username:
                    self.health_monitor.record_success(self._current_username)
                break

            except Exception as e:
                should_retry = await self._handle_error_with_retry(e, retry_count, f"user @{username}")
                if not should_retry:
                    break
                retry_count += 1

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
        retry_count = 0

        while retry_count <= self.config.retry.max_retries:
            try:
                # Add delay before API call
                await self._rate_limit_delay()

                raw_tweets = await gather(self.api.search(query, limit=limit))
                for raw_tweet in raw_tweets:
                    tweet = self._parse_tweet(raw_tweet)
                    tweets.append(tweet)

                # Record success
                if self._current_username:
                    self.health_monitor.record_success(self._current_username)

                console.print(f"[green]Found {len(tweets)} tweets for query: {query}[/green]")
                break

            except Exception as e:
                should_retry = await self._handle_error_with_retry(e, retry_count, f"search '{query}'")
                if not should_retry:
                    break
                retry_count += 1

        return self._filter_by_time(tweets)

    def get_health_status(self) -> dict | None:
        """Get current account health status."""
        if self._current_username:
            return self.health_monitor.get_status_summary(self._current_username)
        return None

    async def enrich_with_thread(self, tweet: Tweet) -> Tweet:
        """
        Enrich a tweet with full thread content if it looks like a thread.

        This method should be called after filtering to avoid unnecessary API calls.
        Only call for tweets that will be researched.

        Args:
            tweet: The tweet to potentially enrich

        Returns:
            The same tweet, possibly with thread_content populated
        """
        from .thread_fetcher import is_likely_thread, fetch_thread

        # Check if it looks like a thread
        if not is_likely_thread(tweet.text):
            return tweet

        if not self.api:
            await self.init_accounts()

        console.print(f"[blue]Detected thread pattern, fetching full thread for {tweet.id}...[/blue]")

        try:
            await self._rate_limit_delay()

            thread = await fetch_thread(
                api=self.api,
                tweet_id=int(tweet.id),
                author_username=tweet.author,
            )

            if thread and thread.is_thread:
                console.print(f"[green]Fetched thread with {thread.tweet_count} tweets[/green]")
                # Create a new Tweet with thread_content
                return Tweet(
                    id=tweet.id,
                    text=tweet.text,
                    author=tweet.author,
                    author_id=tweet.author_id,
                    created_at=tweet.created_at,
                    urls=tweet.urls,
                    media_urls=tweet.media_urls,
                    retweet_count=tweet.retweet_count,
                    like_count=tweet.like_count,
                    reply_count=tweet.reply_count,
                    is_retweet=tweet.is_retweet,
                    is_quote=tweet.is_quote,
                    quoted_text=tweet.quoted_text,
                    quoted_author=tweet.quoted_author,
                    thread_content=thread.full_text,
                )
            else:
                console.print(f"[dim]Not a thread or couldn't fetch[/dim]")

        except Exception as e:
            console.print(f"[yellow]Failed to fetch thread: {e}[/yellow]")

        return tweet
