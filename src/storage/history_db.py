"""SQLite database for tracking processed tweets and URLs."""

import json

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
            # Processed tweets table
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

            # Early-stage GitHub repos table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS early_repos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_id TEXT UNIQUE NOT NULL,
                    full_name TEXT,
                    url TEXT,
                    stars INTEGER,
                    innovation_score INTEGER,
                    innovation_summary TEXT,
                    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notion_page_id TEXT
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_repo_id ON early_repos(repo_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_discovered_at ON early_repos(discovered_at)
            """)

            # OpenClaw ecosystem updates table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS openclaw_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    update_id TEXT UNIQUE NOT NULL,
                    source_type TEXT NOT NULL,
                    repo TEXT DEFAULT '',
                    title TEXT NOT NULL,
                    url TEXT,
                    importance INTEGER DEFAULT 5,
                    summary TEXT,
                    tags TEXT,
                    tracked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notion_page_id TEXT
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_openclaw_update_id
                ON openclaw_updates(update_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_openclaw_tracked_at
                ON openclaw_updates(tracked_at)
            """)

            # GitHub candidates pool table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS github_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_id TEXT UNIQUE NOT NULL,
                    full_name TEXT,
                    url TEXT,
                    description TEXT,
                    stars INTEGER,
                    language TEXT DEFAULT '',
                    topics TEXT DEFAULT '[]',
                    created_at TEXT,
                    pushed_at TEXT,
                    forks INTEGER DEFAULT 0,
                    open_issues INTEGER DEFAULT 0,
                    license_name TEXT DEFAULT '',
                    readme_excerpt TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    innovation_score INTEGER,
                    innovation_summary TEXT,
                    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    screened_at TIMESTAMP,
                    researched_at TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_candidate_repo_id
                ON github_candidates(repo_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_candidate_status
                ON github_candidates(status)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_candidate_collected_at
                ON github_candidates(collected_at)
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

    # GitHub early repos tracking methods

    async def is_repo_tracked(self, repo_id: str) -> bool:
        """Check if a GitHub repo has been tracked."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM early_repos WHERE repo_id = ?", (repo_id,)
            )
            row = await cursor.fetchone()
            return row is not None

    async def mark_repo_tracked(
        self,
        repo_id: str,
        full_name: str,
        url: str,
        stars: int,
        innovation_score: int,
        innovation_summary: str,
        notion_page_id: Optional[str] = None,
    ) -> None:
        """Mark a GitHub repo as tracked."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO early_repos
                (repo_id, full_name, url, stars, innovation_score, innovation_summary, notion_page_id, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repo_id,
                    full_name,
                    url,
                    stars,
                    innovation_score,
                    innovation_summary,
                    notion_page_id,
                    datetime.now().isoformat(),
                ),
            )
            await db.commit()

    async def update_repo_notion_page(self, repo_id: str, notion_page_id: str) -> None:
        """Update the Notion page ID for a tracked repo."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE early_repos SET notion_page_id = ? WHERE repo_id = ?",
                (notion_page_id, repo_id),
            )
            await db.commit()

    async def get_recent_repos(self, days: int = 30) -> list[dict]:
        """Get recently discovered repos."""
        cutoff = datetime.now() - timedelta(days=days)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT repo_id, full_name, url, stars, innovation_score, innovation_summary, discovered_at, notion_page_id
                FROM early_repos
                WHERE discovered_at > ?
                ORDER BY discovered_at DESC
                """,
                (cutoff.isoformat(),),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_repo_count(self, days: int = 7) -> int:
        """Get count of repos discovered in the last N days."""
        cutoff = datetime.now() - timedelta(days=days)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM early_repos WHERE discovered_at > ?",
                (cutoff.isoformat(),),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    # OpenClaw ecosystem tracking methods

    async def is_openclaw_update_tracked(self, update_id: str) -> bool:
        """Check if an OpenClaw update has been tracked."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM openclaw_updates WHERE update_id = ?", (update_id,)
            )
            row = await cursor.fetchone()
            return row is not None

    async def mark_openclaw_update(
        self,
        update_id: str,
        source_type: str,
        repo: str,
        title: str,
        url: str,
        importance: int,
        summary: str,
        tags: str = "[]",
        notion_page_id: Optional[str] = None,
    ) -> None:
        """Mark an OpenClaw update as tracked."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO openclaw_updates
                (update_id, source_type, repo, title, url, importance, summary, tags,
                 notion_page_id, tracked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    update_id,
                    source_type,
                    repo,
                    title,
                    url,
                    importance,
                    summary,
                    tags,
                    notion_page_id,
                    datetime.now().isoformat(),
                ),
            )
            await db.commit()

    async def update_openclaw_notion_page(self, update_id: str, notion_page_id: str) -> None:
        """Update the Notion page ID for a tracked OpenClaw update."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE openclaw_updates SET notion_page_id = ? WHERE update_id = ?",
                (notion_page_id, update_id),
            )
            await db.commit()

    async def get_recent_openclaw_updates(self, days: int = 14) -> list[dict]:
        """Get recently tracked OpenClaw updates."""
        cutoff = datetime.now() - timedelta(days=days)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT update_id, source_type, repo, title, url, importance,
                       summary, tags, tracked_at, notion_page_id
                FROM openclaw_updates
                WHERE tracked_at > ?
                ORDER BY tracked_at DESC
                """,
                (cutoff.isoformat(),),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # GitHub candidates pool methods

    async def save_github_candidate(self, candidate: dict) -> bool:
        """Save a candidate to the pool. Returns True if newly inserted."""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO github_candidates
                    (repo_id, full_name, url, description, stars, language, topics,
                     created_at, pushed_at, forks, open_issues, license_name,
                     readme_excerpt, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate["repo_id"],
                        candidate.get("full_name", ""),
                        candidate.get("url", ""),
                        candidate.get("description", ""),
                        candidate.get("stars", 0),
                        candidate.get("language", ""),
                        json.dumps(candidate.get("topics", [])),
                        candidate.get("created_at", ""),
                        candidate.get("pushed_at", ""),
                        candidate.get("forks", 0),
                        candidate.get("open_issues", 0),
                        candidate.get("license_name", ""),
                        candidate.get("readme_excerpt", ""),
                        datetime.now().isoformat(),
                    ),
                )
                await db.commit()
                return db.total_changes > 0
            except Exception:
                return False

    async def is_candidate_exists(self, repo_id: str) -> bool:
        """Check if a repo is already in the candidate pool."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM github_candidates WHERE repo_id = ?", (repo_id,)
            )
            row = await cursor.fetchone()
            return row is not None

    async def get_pending_candidates(self, limit: int = 30) -> list[dict]:
        """Get pending candidates for screening, oldest first."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT repo_id, full_name, url, description, stars, language,
                       topics, created_at, pushed_at, forks, open_issues,
                       license_name, readme_excerpt
                FROM github_candidates
                WHERE status = 'pending'
                ORDER BY collected_at ASC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                # Parse topics JSON back to list
                try:
                    d["topics"] = json.loads(d["topics"])
                except (json.JSONDecodeError, TypeError):
                    d["topics"] = []
                result.append(d)
            return result

    async def update_candidate_screened(
        self, repo_id: str, score: int, summary: str
    ) -> None:
        """Mark candidate as screened with score and summary."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE github_candidates
                SET status = 'screened', innovation_score = ?,
                    innovation_summary = ?, screened_at = ?
                WHERE repo_id = ?
                """,
                (score, summary, datetime.now().isoformat(), repo_id),
            )
            await db.commit()

    async def skip_candidate(self, repo_id: str) -> None:
        """Mark candidate as skipped (low score)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE github_candidates
                SET status = 'skipped', screened_at = ?
                WHERE repo_id = ?
                """,
                (datetime.now().isoformat(), repo_id),
            )
            await db.commit()

    async def update_candidate_researched(self, repo_id: str) -> None:
        """Mark candidate as researched."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE github_candidates
                SET status = 'researched', researched_at = ?
                WHERE repo_id = ?
                """,
                (datetime.now().isoformat(), repo_id),
            )
            await db.commit()

    async def get_screened_candidates(self, limit: int = 30) -> list[dict]:
        """Get screened candidates for deep research, highest score first."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT repo_id, full_name, url, description, stars, language,
                       topics, created_at, pushed_at, forks, open_issues,
                       license_name, readme_excerpt,
                       innovation_score, innovation_summary
                FROM github_candidates
                WHERE status = 'screened'
                ORDER BY innovation_score DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                try:
                    d["topics"] = json.loads(d["topics"])
                except (json.JSONDecodeError, TypeError):
                    d["topics"] = []
                result.append(d)
            return result

    async def get_candidate_stats(self) -> dict:
        """Get statistics for the candidate pool."""
        async with aiosqlite.connect(self.db_path) as db:
            stats = {}
            for status in ["pending", "screened", "researched", "skipped"]:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM github_candidates WHERE status = ?",
                    (status,),
                )
                row = await cursor.fetchone()
                stats[status] = row[0] if row else 0
            stats["total"] = sum(stats.values())
            return stats
