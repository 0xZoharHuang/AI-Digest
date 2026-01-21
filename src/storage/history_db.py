"""SQLite database for tracking processed tweets and URLs."""

import aiosqlite
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class ProcessedTweet(BaseModel):
    """Record of a processed tweet."""

    tweet_id: str
    url: Optional[str] = None
    author: Optional[str] = None
    category: Optional[str] = None
    topic: Optional[str] = None
    title: Optional[str] = None
    notion_page_id: Optional[str] = None
    processed_at: datetime = datetime.now()


class HistoryDB:
    """SQLite database for deduplication and history tracking."""

    def __init__(self, db_path: str | Path = "data/history.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self) -> None:
        """Initialize database tables."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS processed_tweets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tweet_id TEXT UNIQUE NOT NULL,
                    url TEXT,
                    author TEXT,
                    category TEXT,
                    topic TEXT,
                    title TEXT,
                    notion_page_id TEXT,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_tweet_id ON processed_tweets(tweet_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_url ON processed_tweets(url)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_processed_at ON processed_tweets(processed_at)
            """)
            await db.commit()

    async def is_processed(self, tweet_id: str) -> bool:
        """Check if a tweet has been processed."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM processed_tweets WHERE tweet_id = ?", (tweet_id,)
            )
            row = await cursor.fetchone()
            return row is not None

    async def is_url_processed(self, url: str) -> bool:
        """Check if a URL has been processed."""
        if not url:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM processed_tweets WHERE url = ?", (url,)
            )
            row = await cursor.fetchone()
            return row is not None

    async def mark_processed(
        self,
        tweet_id: str,
        url: Optional[str] = None,
        author: Optional[str] = None,
        category: Optional[str] = None,
        topic: Optional[str] = None,
        title: Optional[str] = None,
        notion_page_id: Optional[str] = None,
    ) -> None:
        """Mark a tweet as processed."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO processed_tweets
                (tweet_id, url, author, category, topic, title, notion_page_id, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tweet_id,
                    url,
                    author,
                    category,
                    topic,
                    title,
                    notion_page_id,
                    datetime.now().isoformat(),
                ),
            )
            await db.commit()

    async def get_recent_processed(self, days: int = 7) -> list[ProcessedTweet]:
        """Get recently processed tweets."""
        cutoff = datetime.now() - timedelta(days=days)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT tweet_id, url, author, category, topic, title, notion_page_id, processed_at
                FROM processed_tweets
                WHERE processed_at > ?
                ORDER BY processed_at DESC
                """,
                (cutoff.isoformat(),),
            )
            rows = await cursor.fetchall()
            return [
                ProcessedTweet(
                    tweet_id=row["tweet_id"],
                    url=row["url"],
                    author=row["author"],
                    category=row["category"],
                    topic=row["topic"],
                    title=row["title"],
                    notion_page_id=row["notion_page_id"],
                    processed_at=datetime.fromisoformat(row["processed_at"])
                    if row["processed_at"]
                    else datetime.now(),
                )
                for row in rows
            ]

    async def get_processed_count(self, days: int = 1) -> int:
        """Get count of tweets processed in the last N days."""
        cutoff = datetime.now() - timedelta(days=days)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM processed_tweets WHERE processed_at > ?",
                (cutoff.isoformat(),),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def clear_old_records(self, days: int = 30) -> int:
        """Clear records older than N days. Returns number of deleted records."""
        cutoff = datetime.now() - timedelta(days=days)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM processed_tweets WHERE processed_at < ?",
                (cutoff.isoformat(),),
            )
            await db.commit()
            return cursor.rowcount
