"""Data collection module using Playwright."""

from .account_health import AccountHealthMonitor, ErrorType, RiskLevel
from .playwright_crawler import PlaywrightCrawler, PlaywrightTweet

# Alias for compatibility
Tweet = PlaywrightTweet

__all__ = [
    "PlaywrightCrawler",
    "PlaywrightTweet",
    "Tweet",
    "AccountHealthMonitor",
    "ErrorType",
    "RiskLevel",
]
