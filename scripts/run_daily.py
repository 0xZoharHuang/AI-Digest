#!/usr/bin/env python3
"""Daily run script for AI Digest.

Usage:
    python scripts/run_daily.py              # Full run
    python scripts/run_daily.py --limit 3    # Limit to 3 items (for testing)
    python scripts/run_daily.py --skip-notion # Skip Notion sync
    python scripts/run_daily.py --resume      # Resume from checkpoint
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rich.console import Console
from rich.table import Table

from src.crawler import PlaywrightCrawler, PlaywrightTweet
from src.filter import TweetFilter, FilteredTweet
from src.agent import ResearchAgent, ResearchResult
from src.integrator import ReportGenerator, OverviewStats, FilteredItem
from src.output import NotionSync, MarkdownExporter
from src.storage import HistoryDB, ProgressTracker

console = Console()

# Alias for compatibility
Tweet = PlaywrightTweet


async def main(
    limit: int | None = None,
    skip_notion: bool = False,
    resume: bool = False,
):
    """Run the full daily digest pipeline."""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    console.print(f"\n[bold cyan]AI Daily Digest - Run {run_id}[/bold cyan]\n")

    # Initialize components
    db = HistoryDB()
    progress = ProgressTracker()
    crawler = PlaywrightCrawler()
    tweet_filter = TweetFilter()
    research_agent = ResearchAgent(model="sonnet")
    report_generator = ReportGenerator()
    markdown_exporter = MarkdownExporter()

    # Initialize database
    console.print("[blue]Step 1/7: Initializing database...[/blue]")
    await db.init_db()

    # Check for resume
    valuable_tweets: list[FilteredTweet] = []
    all_filtered_tweets: list[FilteredTweet] = []  # Track all filtered (for summary)
    completed_ids: set[str] = set()
    results: list[ResearchResult] = []
    crawled_total: int = 0

    if resume:
        incomplete_runs = progress.list_incomplete_runs()
        if incomplete_runs:
            last_run = incomplete_runs[-1]
            console.print(f"[yellow]Resuming from run: {last_run}[/yellow]")
            run_id = last_run

            # Load existing progress
            state = progress.load_progress(run_id)
            if state:
                completed_ids = progress.get_completed_tweet_ids(run_id)
                console.print(f"[green]Found {len(completed_ids)} completed items[/green]")

                # Load existing results
                saved_results = progress.load_results(run_id)
                for r in saved_results:
                    results.append(ResearchResult(**r["result"]))
        else:
            console.print("[yellow]No incomplete runs found, starting fresh[/yellow]")
            resume = False

    if not resume:
        # Step 2: Crawl tweets
        console.print("\n[blue]Step 2/7: Crawling tweets...[/blue]")
        progress.save_progress(run_id, {"current_phase": "crawl"})

        try:
            all_tweets = await crawler.get_all_feeds()
        except Exception as e:
            console.print(f"[red]Crawling failed: {e}[/red]")
            console.print("[yellow]Please check your Twitter account configuration[/yellow]")
            return

        console.print(f"Crawled {len(all_tweets)} tweets")

        # Step 3: Filter processed tweets (deduplication)
        console.print("\n[blue]Step 3/7: Filtering already processed tweets...[/blue]")
        progress.save_progress(run_id, {"current_phase": "dedup"})

        new_tweets: list[Tweet] = []
        for tweet in all_tweets:
            is_processed = await db.is_processed(tweet.id)
            if not is_processed:
                # Also check URL-level dedup
                url_processed = False
                for url in tweet.urls:
                    if await db.is_url_processed(url):
                        url_processed = True
                        break
                if not url_processed:
                    new_tweets.append(tweet)

        console.print(f"Found {len(new_tweets)} new tweets (filtered {len(all_tweets) - len(new_tweets)} duplicates)")

        if not new_tweets:
            console.print("[yellow]No new tweets to process. Exiting.[/yellow]")
            return

        # Step 4: LLM filtering
        console.print("\n[blue]Step 4/7: LLM filtering for valuable content...[/blue]")
        progress.save_progress(run_id, {"current_phase": "filter"})

        filtered_tweets = await tweet_filter.filter_tweets(new_tweets)
        all_filtered_tweets = filtered_tweets  # Keep all for summary
        crawled_total = len(all_tweets)
        valuable_tweets = tweet_filter.get_valuable_tweets(filtered_tweets)

        console.print(f"Found {len(valuable_tweets)} valuable tweets for research")

        if not valuable_tweets:
            console.print("[yellow]No valuable tweets found. Exiting.[/yellow]")
            return

    # Apply limit if specified
    if limit and len(valuable_tweets) > limit:
        console.print(f"[yellow]Limiting to {limit} items (from {len(valuable_tweets)})[/yellow]")
        valuable_tweets = valuable_tweets[:limit]

    # Step 5: Deep research (serial processing with progress tracking)
    console.print(f"\n[blue]Step 5/7: Deep research ({len(valuable_tweets)} items)...[/blue]")
    progress.save_progress(run_id, {
        "current_phase": "research",
        "total_tweets": len(valuable_tweets),
        "processed_tweets": len(results),
    })

    for i, filtered in enumerate(valuable_tweets):
        tweet = filtered.tweet

        # Skip if already completed
        if tweet.id in completed_ids:
            console.print(f"[dim]Skipping already processed: {tweet.id}[/dim]")
            continue

        console.print(f"\n[cyan][{i+1}/{len(valuable_tweets)}] Researching: {filtered.initial_summary[:60]}...[/cyan]")

        # Enrich with thread content if this looks like a thread
        tweet = await crawler.enrich_with_thread(tweet)

        # Perform research with retry
        # Use full_content which includes thread content if available
        result = await research_agent.research_with_retry(
            tweet_id=tweet.id,
            tweet_text=tweet.full_content,  # Includes thread if fetched
            author=tweet.author,
            category=filtered.category.value,
            topic=filtered.topic.value,
            urls=tweet.urls,
            initial_summary=filtered.initial_summary,
            tweet_url=tweet.tweet_url,
            max_retries=2,
        )

        results.append(result)

        # Save progress immediately after each item
        progress.save_result(run_id, tweet.id, result.model_dump())
        progress.save_progress(run_id, {
            "current_phase": "research",
            "total_tweets": len(valuable_tweets),
            "processed_tweets": len(results),
            "current_tweet_id": tweet.id,
        })

        # Mark as processed in database
        await db.mark_processed(
            tweet_id=tweet.id,
            url=tweet.urls[0] if tweet.urls else None,
            author=tweet.author,
            category=filtered.category.value,
            topic=filtered.topic.value,
            title=result.title,
        )

    console.print(f"\n[green]Research completed: {len(results)} items[/green]")

    if not results:
        console.print("[yellow]No research results. Exiting.[/yellow]")
        return

    # Step 6: Generate report
    console.print("\n[blue]Step 6/7: Generating report...[/blue]")
    progress.save_progress(run_id, {"current_phase": "integrate"})

    # Calculate overview stats
    researched_ids = {r.tweet_id for r in results}
    valuable_not_researched = [
        ft for ft in valuable_tweets
        if ft.tweet.id not in researched_ids
    ]

    # Create FilteredItem objects for valuable tweets not researched
    filtered_items = [
        FilteredItem(
            author=ft.tweet.author,
            summary=ft.initial_summary,
            topic=ft.topic.value,
            category=ft.category.value,
            tweet_url=ft.tweet.tweet_url,
        )
        for ft in valuable_not_researched
    ]

    # Build overview stats
    overview = OverviewStats(
        crawled_total=crawled_total,
        valuable_count=len(valuable_tweets),
        researched_count=len(results),
        related_not_researched=len(valuable_not_researched),
        filtered_out=crawled_total - len(valuable_tweets) if crawled_total > 0 else 0,
    )

    report = report_generator.generate_report(
        results,
        overview=overview,
        filtered_items=filtered_items if filtered_items else None,
    )

    # Step 7: Output
    console.print("\n[blue]Step 7/7: Exporting report...[/blue]")
    progress.save_progress(run_id, {"current_phase": "output"})

    # Export to Markdown
    md_path = markdown_exporter.export(report)

    # Sync to Notion
    notion_url = None
    if not skip_notion:
        try:
            notion_sync = NotionSync()
            notion_url = await notion_sync.sync_report(report)
        except Exception as e:
            console.print(f"[yellow]Notion sync failed: {e}[/yellow]")
            console.print("[yellow]Report saved to local markdown only[/yellow]")
    else:
        console.print("[yellow]Skipping Notion sync as requested[/yellow]")

    # Mark run as completed
    progress.save_progress(run_id, {"current_phase": "completed"})

    # Print summary
    console.print("\n")
    table = Table(title="Run Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Run ID", run_id)
    table.add_row("Date", report.date)
    table.add_row("Total Items", str(report.total_items))
    table.add_row("Successful", str(report.successful_items))
    table.add_row("Failed", str(report.failed_items))
    table.add_row("Markdown", str(md_path))
    if notion_url:
        table.add_row("Notion", notion_url)

    console.print(table)

    # Clean up progress files on successful completion
    progress.clear_progress(run_id)
    console.print("\n[bold green]Done![/bold green]\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run AI Daily Digest")
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of items to research (for testing)",
    )
    parser.add_argument(
        "--skip-notion",
        action="store_true",
        help="Skip Notion synchronization",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last incomplete run",
    )

    args = parser.parse_args()

    asyncio.run(main(
        limit=args.limit,
        skip_notion=args.skip_notion,
        resume=args.resume,
    ))
