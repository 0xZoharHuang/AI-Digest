"""Twitter thread fetcher using twscrape's conversation_id."""

import re
from dataclasses import dataclass
from typing import Optional

from twscrape import API, gather


# Patterns that indicate a tweet might be part of a thread
THREAD_PATTERNS = [
    r"🧵",  # Thread emoji
    r"\bthread\b",  # Word "thread"
    r"\b1/\d+\b",  # Pattern like "1/10"
    r"\b1️⃣",  # Number emoji
    r"分享\s*\d+\s*个",  # Chinese: "分享 N 个"
    r"sharing\s+\d+\s+",  # "sharing N things"
    r"here'?s?\s+\d+\s+",  # "here's N things"
    r"^\d+\.\s+",  # Starts with "1. "
]

THREAD_PATTERN_REGEX = re.compile("|".join(THREAD_PATTERNS), re.IGNORECASE)


@dataclass
class ThreadContent:
    """Complete thread content."""

    tweet_id: str
    author: str
    conversation_id: int
    tweets: list[dict]  # List of {id, text, created_at}
    full_text: str  # Combined text of all tweets
    tweet_count: int
    is_thread: bool


def is_likely_thread(text: str) -> bool:
    """Check if tweet text suggests it's part of a thread."""
    return bool(THREAD_PATTERN_REGEX.search(text))


async def fetch_thread(
    api: API,
    tweet_id: int,
    author_username: str,
    max_tweets: int = 30,
) -> Optional[ThreadContent]:
    """
    Fetch complete thread content using conversation_id.

    Args:
        api: Authenticated twscrape API instance
        tweet_id: The tweet ID (can be any tweet in the thread)
        author_username: The author's username
        max_tweets: Maximum number of tweets to fetch

    Returns:
        ThreadContent with combined thread text, or None if failed
    """
    try:
        # Step 1: Get the original tweet to find conversation_id
        tweet = await api.tweet_details(tweet_id)

        if not tweet:
            return None

        conversation_id = tweet.conversationId
        author = tweet.user.username

        # Collect tweets in the thread
        thread_tweets = []

        # Add the original tweet
        thread_tweets.append({
            "id": tweet.id,
            "text": tweet.rawContent,
            "created_at": str(tweet.date),
        })

        # Step 2: Try to get more tweets from the same conversation by the same author
        # Method: Use search with conversation_id
        try:
            search_query = f"conversation_id:{conversation_id} from:{author}"
            search_results = await gather(api.search(search_query, limit=max_tweets))

            for t in search_results:
                if t.id != tweet.id and t.user.username == author:
                    thread_tweets.append({
                        "id": t.id,
                        "text": t.rawContent,
                        "created_at": str(t.date),
                    })
        except Exception:
            # Search might fail, try tweet_replies as fallback
            pass

        # Step 3: Also try tweet_replies for self-replies
        try:
            replies = await gather(api.tweet_replies(tweet_id, limit=max_tweets))
            for r in replies:
                if r.user.username == author and r.id not in [t["id"] for t in thread_tweets]:
                    thread_tweets.append({
                        "id": r.id,
                        "text": r.rawContent,
                        "created_at": str(r.date),
                    })
        except Exception:
            pass

        # Sort by tweet ID (chronological order)
        thread_tweets.sort(key=lambda x: x["id"])

        # Combine text
        full_text = "\n\n---\n\n".join([t["text"] for t in thread_tweets])

        is_thread = len(thread_tweets) > 1

        return ThreadContent(
            tweet_id=str(tweet_id),
            author=author,
            conversation_id=conversation_id,
            tweets=thread_tweets,
            full_text=full_text,
            tweet_count=len(thread_tweets),
            is_thread=is_thread,
        )

    except Exception as e:
        print(f"Error fetching thread: {e}")
        return None


async def maybe_fetch_thread(
    api: API,
    tweet_id: int,
    tweet_text: str,
    author_username: str,
) -> Optional[str]:
    """
    Conditionally fetch thread content if the tweet looks like a thread.

    Args:
        api: Authenticated twscrape API instance
        tweet_id: The tweet ID
        tweet_text: The tweet text (to check for thread patterns)
        author_username: The author's username

    Returns:
        Full thread text if it's a thread, None otherwise
    """
    # Check if it looks like a thread
    if not is_likely_thread(tweet_text):
        return None

    # Fetch the thread
    thread = await fetch_thread(api, tweet_id, author_username)

    if thread and thread.is_thread:
        return thread.full_text

    return None
