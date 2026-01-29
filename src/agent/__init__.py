"""Deep research agent module."""

from .research_agent import ResearchAgent, ResearchResult
from .summary_agent import SummaryAgent
from .github_discovery_agent import GitHubDiscoveryAgent, DiscoveryResult, DiscoverySummary

__all__ = [
    "ResearchAgent",
    "ResearchResult",
    "SummaryAgent",
    "GitHubDiscoveryAgent",
    "DiscoveryResult",
    "DiscoverySummary",
]
