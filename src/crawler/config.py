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


class DelayConfig(BaseModel):
    """Request delay configuration for risk control."""

    min_seconds: float = 2.0  # Minimum delay between requests
    max_seconds: float = 7.0  # Maximum delay between requests
    page_delay_seconds: float = 15.0  # Extra delay when paginating


class RetryConfig(BaseModel):
    """Retry configuration with exponential backoff."""

    max_retries: int = 5
    backoff_base: float = 2.0  # Exponential backoff base
    max_backoff_seconds: float = 300.0  # Max wait time (5 minutes)


class CrawlerConfig(BaseModel):
    """Crawler configuration."""

    accounts: list[TwitterAccount]
    for_you_limit: int = 200  # Number of tweets to fetch from For You
    following_limit: int = 200  # Number of tweets to fetch from Following
    time_range_hours: int = 48  # Only process tweets from last N hours
    delay: DelayConfig = DelayConfig()  # Request delay settings
    retry: RetryConfig = RetryConfig()  # Retry settings


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
