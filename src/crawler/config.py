"""Twitter account configuration."""

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class TwitterAccount(BaseModel):
    """Twitter account credentials."""

    username: str
    password: str
    email: str
    cookies: Optional[str] = None


class CrawlerConfig(BaseModel):
    """Crawler configuration."""

    accounts: list[TwitterAccount]
    for_you_limit: int = 200  # Number of tweets to fetch from For You
    following_limit: int = 200  # Number of tweets to fetch from Following
    time_range_hours: int = 48  # Only process tweets from last N hours


def load_config(config_path: str | Path = "config/twitter_accounts.json") -> CrawlerConfig:
    """Load crawler configuration from file."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            f"Please copy {config_path.with_suffix('.example.json')} and fill in your credentials."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return CrawlerConfig(**data)
