"""Tweet grouping by topic for batch research."""

from collections import defaultdict
from typing import Optional

from pydantic import BaseModel

from .tweet_filter import FilteredTweet, ContentTopic


class TweetGroup(BaseModel):
    """A group of tweets with the same topic for batch research."""

    group_id: str  # Unique identifier for this group
    topic: ContentTopic
    tweets: list[FilteredTweet]  # 1-2 tweets per group

    class Config:
        arbitrary_types_allowed = True

    @property
    def primary_tweet(self) -> FilteredTweet:
        """Get the primary (first) tweet in the group."""
        return self.tweets[0]

    @property
    def tweet_ids(self) -> list[str]:
        """Get all tweet IDs in this group."""
        return [ft.tweet.id for ft in self.tweets]

    @property
    def all_urls(self) -> list[str]:
        """Get all unique URLs from all tweets in the group."""
        urls = []
        seen = set()
        for ft in self.tweets:
            for url in ft.tweet.urls:
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
        return urls

    @property
    def combined_text(self) -> str:
        """Get combined text from all tweets."""
        texts = []
        for i, ft in enumerate(self.tweets, 1):
            texts.append(f"[推文 {i}] @{ft.tweet.author}:\n{ft.tweet.full_content}")
        return "\n\n---\n\n".join(texts)

    @property
    def combined_summary(self) -> str:
        """Get combined initial summaries."""
        summaries = [ft.initial_summary for ft in self.tweets]
        return " | ".join(summaries)


def group_tweets_by_topic(
    filtered_tweets: list[FilteredTweet],
    max_per_group: int = 2,
) -> list[TweetGroup]:
    """
    Group filtered tweets by topic.

    Args:
        filtered_tweets: List of filtered tweets (already marked as valuable)
        max_per_group: Maximum tweets per group (default 2)

    Returns:
        List of TweetGroup objects
    """
    # Group by topic
    topic_groups: dict[ContentTopic, list[FilteredTweet]] = defaultdict(list)
    for ft in filtered_tweets:
        topic_groups[ft.topic].append(ft)

    # Create groups of max_per_group tweets
    all_groups = []
    group_counter = 0

    for topic, tweets in topic_groups.items():
        # Split into chunks of max_per_group
        for i in range(0, len(tweets), max_per_group):
            chunk = tweets[i:i + max_per_group]
            group_counter += 1
            group = TweetGroup(
                group_id=f"group_{group_counter:03d}",
                topic=topic,
                tweets=chunk,
            )
            all_groups.append(group)

    return all_groups


def get_group_stats(groups: list[TweetGroup]) -> dict:
    """Get statistics about tweet groups."""
    total_tweets = sum(len(g.tweets) for g in groups)
    topic_counts = defaultdict(int)
    for g in groups:
        topic_counts[g.topic.value] += len(g.tweets)

    return {
        "total_groups": len(groups),
        "total_tweets": total_tweets,
        "tweets_per_group_avg": total_tweets / len(groups) if groups else 0,
        "by_topic": dict(topic_counts),
    }
