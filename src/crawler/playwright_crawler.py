"""Playwright-based Twitter crawler for For You feed."""

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, Page
from pydantic import BaseModel
from rich.console import Console

from .config import CrawlerConfig, load_config

console = Console()


class PlaywrightTweet(BaseModel):
    """Tweet data from Playwright scraping."""

    id: str
    text: str
    author: str
    author_id: str = ""
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
    thread_content: Optional[str] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}

    @property
    def tweet_url(self) -> str:
        return f"https://x.com/{self.author}/status/{self.id}"

    @property
    def full_content(self) -> str:
        if self.thread_content:
            return self.thread_content
        return self.text


class PlaywrightCrawler:
    """Twitter crawler using Playwright for For You feed."""

    COOKIES_FILE = "config/twitter_cookies.json"

    def __init__(self, config: Optional[CrawlerConfig] = None):
        self.config = config or load_config()
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None

    async def _init_browser(self, headless: bool = True) -> None:
        """Initialize Playwright browser."""
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]
        )
        context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        self.page = await context.new_page()

    async def _load_cookies(self) -> bool:
        """Load cookies from file."""
        cookies_path = Path(self.COOKIES_FILE)
        if not cookies_path.exists():
            return False

        try:
            with open(cookies_path, "r") as f:
                cookies = json.load(f)
            await self.page.context.add_cookies(cookies)
            console.print(f"[green]Loaded {len(cookies)} cookies[/green]")
            return True
        except Exception as e:
            console.print(f"[red]Failed to load cookies: {e}[/red]")
            return False

    async def _save_cookies(self) -> None:
        """Save cookies to file."""
        cookies = await self.page.context.cookies()
        cookies_path = Path(self.COOKIES_FILE)
        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cookies_path, "w") as f:
            json.dump(cookies, f)
        console.print(f"[green]Saved {len(cookies)} cookies[/green]")

    async def login_manual(self) -> bool:
        """
        Open browser for manual login. User logs in, then cookies are saved.

        Returns:
            True if login successful
        """
        console.print("[yellow]Opening browser for manual login...[/yellow]")
        console.print("[yellow]Please log in to Twitter, then press Enter here when done.[/yellow]")

        await self._init_browser(headless=False)
        await self.page.goto("https://x.com/login")

        # Wait for user to login
        input("\n>>> Press Enter after you've logged in to Twitter... ")

        # Check if logged in by looking for home timeline
        try:
            await self.page.goto("https://x.com/home")
            await self.page.wait_for_selector('[data-testid="tweet"]', timeout=10000)
            console.print("[green]Login successful![/green]")
            await self._save_cookies()
            return True
        except Exception as e:
            console.print(f"[red]Login verification failed: {e}[/red]")
            return False

    async def _check_login(self) -> bool:
        """Check if we're logged in."""
        try:
            await self.page.goto("https://x.com/home", timeout=30000)
            await self.page.wait_for_selector('[data-testid="tweet"]', timeout=15000)
            return True
        except Exception:
            return False

    def _extract_urls(self, text: str) -> list[str]:
        """Extract URLs from text."""
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        return re.findall(url_pattern, text)

    def _parse_count(self, text: str) -> int:
        """Parse count text like '1.2K' to integer."""
        if not text:
            return 0
        text = text.strip().upper()
        try:
            if 'K' in text:
                return int(float(text.replace('K', '')) * 1000)
            elif 'M' in text:
                return int(float(text.replace('M', '')) * 1000000)
            else:
                return int(text.replace(',', ''))
        except ValueError:
            return 0

    async def _extract_tweets_from_page(self) -> list[dict]:
        """Extract tweet data from current page using JavaScript."""
        tweets_data = await self.page.evaluate("""
            () => {
                const tweets = [];
                const tweetElements = document.querySelectorAll('[data-testid="tweet"]');

                tweetElements.forEach(tweet => {
                    try {
                        // Get tweet link to extract ID
                        const timeLink = tweet.querySelector('time')?.parentElement;
                        const tweetUrl = timeLink?.href || '';
                        const tweetId = tweetUrl.split('/status/')[1]?.split('?')[0] || '';

                        // Get author
                        const authorEl = tweet.querySelector('[data-testid="User-Name"]');
                        const authorLink = authorEl?.querySelector('a[href^="/"]');
                        const author = authorLink?.href?.split('/')[3] || '';

                        // Get text
                        const textEl = tweet.querySelector('[data-testid="tweetText"]');
                        const text = textEl?.innerText || '';

                        // Get time
                        const timeEl = tweet.querySelector('time');
                        const datetime = timeEl?.getAttribute('datetime') || '';

                        // Get metrics
                        const replyEl = tweet.querySelector('[data-testid="reply"]');
                        const retweetEl = tweet.querySelector('[data-testid="retweet"]');
                        const likeEl = tweet.querySelector('[data-testid="like"]');

                        const replyCount = replyEl?.querySelector('span')?.innerText || '0';
                        const retweetCount = retweetEl?.querySelector('span')?.innerText || '0';
                        const likeCount = likeEl?.querySelector('span')?.innerText || '0';

                        // Check if retweet
                        const isRetweet = !!(text.startsWith('RT @') ||
                            tweet.querySelector('[data-testid="socialContext"]')?.innerText?.includes('reposted'));

                        // Get media
                        const mediaElements = tweet.querySelectorAll('[data-testid="tweetPhoto"] img, video');
                        const mediaUrls = Array.from(mediaElements).map(el => el.src || el.poster).filter(Boolean);

                        if (tweetId && text) {
                            tweets.push({
                                id: tweetId,
                                author: author,
                                text: text,
                                datetime: datetime,
                                replyCount: replyCount,
                                retweetCount: retweetCount,
                                likeCount: likeCount,
                                isRetweet: isRetweet,
                                mediaUrls: mediaUrls
                            });
                        }
                    } catch (e) {
                        console.error('Error extracting tweet:', e);
                    }
                });

                return tweets;
            }
        """)
        return tweets_data

    async def get_for_you_feed(self, limit: int = 50) -> list[PlaywrightTweet]:
        """
        Get tweets from For You feed.

        Args:
            limit: Maximum number of tweets to fetch

        Returns:
            List of tweets
        """
        if not self.browser:
            await self._init_browser(headless=True)

        # Load cookies
        if not await self._load_cookies():
            console.print("[red]No cookies found. Please run login_manual() first.[/red]")
            return []

        # Check if logged in
        console.print("[blue]Checking login status...[/blue]")
        if not await self._check_login():
            console.print("[red]Not logged in. Please run login_manual() first.[/red]")
            return []

        console.print("[blue]Fetching For You feed...[/blue]")

        all_tweets = []
        seen_ids = set()
        scroll_count = 0
        max_scrolls = limit // 5 + 5  # Estimate ~5 tweets per scroll

        while len(all_tweets) < limit and scroll_count < max_scrolls:
            # Extract tweets from current view
            tweets_data = await self._extract_tweets_from_page()

            for data in tweets_data:
                if data['id'] not in seen_ids:
                    seen_ids.add(data['id'])

                    # Parse datetime
                    try:
                        created_at = datetime.fromisoformat(data['datetime'].replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        created_at = datetime.now(timezone.utc)

                    tweet = PlaywrightTweet(
                        id=data['id'],
                        text=data['text'],
                        author=data['author'],
                        created_at=created_at,
                        urls=self._extract_urls(data['text']),
                        media_urls=data.get('mediaUrls', []),
                        reply_count=self._parse_count(data.get('replyCount', '0')),
                        retweet_count=self._parse_count(data.get('retweetCount', '0')),
                        like_count=self._parse_count(data.get('likeCount', '0')),
                        is_retweet=data.get('isRetweet', False),
                    )
                    all_tweets.append(tweet)

            console.print(f"[dim]Collected {len(all_tweets)} tweets...[/dim]")

            if len(all_tweets) >= limit:
                break

            # Scroll down
            await self.page.evaluate("window.scrollBy(0, 1000)")
            await asyncio.sleep(self.config.delay.min_seconds)
            scroll_count += 1

        console.print(f"[green]Fetched {len(all_tweets)} tweets from For You[/green]")
        return all_tweets[:limit]

    async def get_following_feed(self, limit: int = 50) -> list[PlaywrightTweet]:
        """Get tweets from Following feed."""
        if not self.browser:
            await self._init_browser(headless=True)

        if not await self._load_cookies():
            console.print("[red]No cookies found.[/red]")
            return []

        # Check if logged in (same as get_for_you_feed)
        console.print("[blue]Checking login status...[/blue]")
        if not await self._check_login():
            console.print("[red]Not logged in. Please run login_manual() first.[/red]")
            return []

        console.print("[blue]Fetching Following feed...[/blue]")

        # Navigate to home first
        await self.page.goto("https://x.com/home", timeout=30000)
        await asyncio.sleep(3)

        # Use JavaScript to find and click Following tab (CSS :has-text() not supported by wait_for_selector)
        clicked = await self.page.evaluate("""
            () => {
                const tabs = document.querySelectorAll('a[role="tab"]');
                for (const tab of tabs) {
                    if (tab.textContent.includes('Following')) {
                        tab.click();
                        return true;
                    }
                }
                return false;
            }
        """)

        if clicked:
            await asyncio.sleep(3)
            console.print("[green]Switched to Following tab[/green]")
        else:
            console.print("[yellow]Could not find Following tab, staying on For You[/yellow]")

        # Wait for tweets to load
        try:
            await self.page.wait_for_selector('[data-testid="tweet"]', timeout=10000)
        except Exception:
            console.print("[yellow]No tweets found in Following feed[/yellow]")
            return []

        all_tweets = []
        seen_ids = set()
        scroll_count = 0
        max_scrolls = limit // 5 + 5

        while len(all_tweets) < limit and scroll_count < max_scrolls:
            tweets_data = await self._extract_tweets_from_page()

            for data in tweets_data:
                if data['id'] not in seen_ids:
                    seen_ids.add(data['id'])

                    try:
                        created_at = datetime.fromisoformat(data['datetime'].replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        created_at = datetime.now(timezone.utc)

                    tweet = PlaywrightTweet(
                        id=data['id'],
                        text=data['text'],
                        author=data['author'],
                        created_at=created_at,
                        urls=self._extract_urls(data['text']),
                        media_urls=data.get('mediaUrls', []),
                        reply_count=self._parse_count(data.get('replyCount', '0')),
                        retweet_count=self._parse_count(data.get('retweetCount', '0')),
                        like_count=self._parse_count(data.get('likeCount', '0')),
                        is_retweet=data.get('isRetweet', False),
                    )
                    all_tweets.append(tweet)

            console.print(f"[dim]Collected {len(all_tweets)} tweets...[/dim]")

            if len(all_tweets) >= limit:
                break

            await self.page.evaluate("window.scrollBy(0, 1000)")
            await asyncio.sleep(self.config.delay.min_seconds)
            scroll_count += 1

        console.print(f"[green]Fetched {len(all_tweets)} tweets from Following[/green]")
        return all_tweets[:limit]

    async def get_all_feeds(self) -> list[PlaywrightTweet]:
        """Get tweets from both For You and Following feeds."""
        for_you = await self.get_for_you_feed(self.config.for_you_limit)
        following = await self.get_following_feed(self.config.following_limit)

        # Deduplicate
        seen_ids = set()
        all_tweets = []

        for tweet in for_you + following:
            if tweet.id not in seen_ids:
                seen_ids.add(tweet.id)
                all_tweets.append(tweet)

        all_tweets.sort(key=lambda t: t.created_at, reverse=True)
        console.print(f"[green]Total unique tweets: {len(all_tweets)}[/green]")
        return all_tweets

    async def enrich_with_thread(self, tweet: PlaywrightTweet) -> PlaywrightTweet:
        """
        Enrich tweet with thread content if applicable.
        Fetches the full thread if the tweet appears to be part of one.
        """
        # Check if tweet looks like a thread (numbered or has "thread" mention)
        text_lower = tweet.text.lower()
        is_thread = (
            "🧵" in tweet.text or
            "thread" in text_lower or
            re.match(r'^1[./)]', tweet.text.strip()) or
            "/1" in tweet.text[:50]
        )

        if not is_thread:
            return tweet

        try:
            console.print(f"[dim]Fetching thread for tweet {tweet.id}...[/dim]")

            if not self.browser:
                await self._init_browser(headless=True)
                await self._load_cookies()

            # Navigate to tweet page
            await self.page.goto(f"https://x.com/{tweet.author}/status/{tweet.id}", timeout=30000)
            await asyncio.sleep(3)

            # Scroll to load more replies
            for _ in range(3):
                await self.page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(1)

            # Extract thread content (tweets from same author in conversation)
            thread_texts = await self.page.evaluate("""
                (authorHandle) => {
                    const texts = [];
                    const tweets = document.querySelectorAll('[data-testid="tweet"]');

                    tweets.forEach(tweet => {
                        const authorEl = tweet.querySelector('[data-testid="User-Name"] a[href^="/"]');
                        const author = authorEl?.href?.split('/')[3] || '';

                        if (author.toLowerCase() === authorHandle.toLowerCase()) {
                            const textEl = tweet.querySelector('[data-testid="tweetText"]');
                            if (textEl) {
                                texts.push(textEl.innerText);
                            }
                        }
                    });

                    return texts;
                }
            """, tweet.author)

            if thread_texts and len(thread_texts) > 1:
                # Combine thread texts
                thread_content = "\n\n---\n\n".join(thread_texts)
                tweet.thread_content = thread_content
                console.print(f"[green]Found thread with {len(thread_texts)} parts[/green]")

        except Exception as e:
            console.print(f"[yellow]Could not fetch thread: {e}[/yellow]")

        return tweet

    async def close(self) -> None:
        """Close browser."""
        if self.browser:
            await self.browser.close()
            self.browser = None
            self.page = None


async def setup_login():
    """Helper function to set up login interactively."""
    crawler = PlaywrightCrawler()
    try:
        success = await crawler.login_manual()
        if success:
            console.print("[green]Setup complete! Cookies saved.[/green]")
        else:
            console.print("[red]Setup failed. Please try again.[/red]")
    finally:
        await crawler.close()


if __name__ == "__main__":
    asyncio.run(setup_login())
