"""Test script for fetching Twitter threads using twscrape."""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from twscrape import API, gather


async def fetch_thread(tweet_id: int, username: str = None) -> list[dict]:
    """
    Fetch a complete Twitter thread.

    Strategy:
    1. Get the original tweet to find conversation_id
    2. Search for all tweets by the same author in the conversation
    3. Or use tweet_replies to get self-replies

    Args:
        tweet_id: The tweet ID (can be any tweet in the thread)
        username: Optional username for filtering

    Returns:
        List of tweet data dicts
    """
    api = API()

    # Check if we have logged-in accounts
    accounts = await api.pool.accounts_info()
    if not accounts:
        print("No accounts configured. Please run account setup first.")
        print("You can test with mock data instead.")
        return []

    results = []

    try:
        # Step 1: Get the tweet details
        print(f"Fetching tweet {tweet_id}...")
        tweet = await api.tweet_details(tweet_id)

        if not tweet:
            print(f"Tweet {tweet_id} not found")
            return []

        print(f"Found tweet by @{tweet.user.username}")
        print(f"Text: {tweet.rawContent[:100]}...")
        print(f"Conversation ID: {tweet.conversationId}")
        print(f"In reply to: {tweet.inReplyToTweetId}")

        # Collect the main tweet
        results.append({
            "id": tweet.id,
            "text": tweet.rawContent,
            "author": tweet.user.username,
            "conversation_id": tweet.conversationId,
            "in_reply_to": tweet.inReplyToTweetId,
            "created_at": str(tweet.date),
        })

        # Step 2: Try to get thread by searching for same author + conversation
        # Using search with conversation_id
        author_username = tweet.user.username
        conversation_id = tweet.conversationId

        print(f"\nSearching for thread tweets...")

        # Method A: Use tweet_replies (limited but works)
        print("Trying tweet_replies...")
        try:
            replies = await gather(api.tweet_replies(tweet_id, limit=50))
            print(f"Got {len(replies)} replies")

            # Filter for self-replies (same author = thread continuation)
            for reply in replies:
                if reply.user.username == author_username:
                    results.append({
                        "id": reply.id,
                        "text": reply.rawContent,
                        "author": reply.user.username,
                        "conversation_id": reply.conversationId,
                        "in_reply_to": reply.inReplyToTweetId,
                        "created_at": str(reply.date),
                    })
        except Exception as e:
            print(f"tweet_replies failed: {e}")

        # Method B: Search for conversation
        print("\nTrying search with conversation_id...")
        try:
            search_query = f"conversation_id:{conversation_id} from:{author_username}"
            print(f"Search query: {search_query}")
            search_results = await gather(api.search(search_query, limit=50))
            print(f"Got {len(search_results)} search results")

            for tweet in search_results:
                # Avoid duplicates
                if tweet.id not in [r["id"] for r in results]:
                    results.append({
                        "id": tweet.id,
                        "text": tweet.rawContent,
                        "author": tweet.user.username,
                        "conversation_id": tweet.conversationId,
                        "in_reply_to": tweet.inReplyToTweetId,
                        "created_at": str(tweet.date),
                    })
        except Exception as e:
            print(f"Search failed: {e}")

        # Sort by tweet ID (chronological order)
        results.sort(key=lambda x: x["id"])

        print(f"\n=== Thread Summary ===")
        print(f"Total tweets in thread: {len(results)}")
        for i, r in enumerate(results):
            print(f"\n[{i+1}] @{r['author']}:")
            print(f"    {r['text'][:150]}...")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

    return results


async def main():
    # Test with a known thread tweet
    # Using Tyler Denk's thread as example (if account is configured)
    tweet_id = 1881172227912057265

    print("=" * 60)
    print("Twitter Thread Fetcher Test")
    print("=" * 60)

    results = await fetch_thread(tweet_id)

    if results:
        print(f"\n\n=== SUCCESS: Fetched {len(results)} tweets ===")
    else:
        print("\n\n=== No results or account not configured ===")


if __name__ == "__main__":
    asyncio.run(main())
