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
import os
import sys
from datetime import datetime
from pathlib import Path

# Unset ANTHROPIC_API_KEY to use local Claude CLI (claude_agent_sdk)
# In production, set ANTHROPIC_API_KEY to use API directly
if "ANTHROPIC_API_KEY" in os.environ:
    del os.environ["ANTHROPIC_API_KEY"]

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rich.console import Console
from rich.table import Table

from src.crawler import PlaywrightCrawler, PlaywrightTweet
from src.filter import TweetFilter, FilteredTweet, TweetGroup, group_tweets_by_topic, get_group_stats
from src.agent import ResearchAgent, ResearchResult, SummaryAgent
from src.integrator import ReportGenerator, OverviewStats, FilteredItem
from src.output import NotionSync, MarkdownExporter, IncrementalNotionSyncer
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
    summary_agent = SummaryAgent(model="haiku")  # Use haiku for cost efficiency
    report_generator = ReportGenerator()
    markdown_exporter = MarkdownExporter()

    # Concurrency control for research (2 concurrent)
    RESEARCH_CONCURRENCY = 2
    research_semaphore = asyncio.Semaphore(RESEARCH_CONCURRENCY)

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
        console.print(f"[yellow]Limiting to {limit} tweets (from {len(valuable_tweets)})[/yellow]")
        valuable_tweets = valuable_tweets[:limit]

    # Group tweets by topic (2 tweets per group for batch research)
    tweet_groups = group_tweets_by_topic(valuable_tweets, max_per_group=2)
    group_stats = get_group_stats(tweet_groups)
    console.print(f"[blue]Grouped into {group_stats['total_groups']} groups ({group_stats['total_tweets']} tweets)[/blue]")
    console.print(f"[dim]By topic: {group_stats['by_topic']}[/dim]")

    # Initialize incremental Notion sync (before research starts)
    incremental_syncer = None
    report_date = datetime.now().strftime("%Y-%m-%d")
    if not skip_notion:
        try:
            incremental_syncer = IncrementalNotionSyncer()
            await incremental_syncer.init_summary_page(report_date)
        except Exception as e:
            console.print(f"[yellow]Failed to init incremental sync: {e}[/yellow]")
            console.print("[yellow]Will fall back to batch sync at the end[/yellow]")
            incremental_syncer = None

    # Step 5: Deep research (concurrent group processing)
    # 2 concurrent agents × 2 tweets/group = 4 tweets processed in parallel
    total_tweets_in_groups = sum(len(g.tweets) for g in tweet_groups)
    console.print(f"\n[blue]Step 5/7: Deep research ({len(tweet_groups)} groups, {total_tweets_in_groups} tweets, {RESEARCH_CONCURRENCY} concurrent agents)...[/blue]")
    progress.save_progress(run_id, {
        "current_phase": "research",
        "total_groups": len(tweet_groups),
        "total_tweets": total_tweets_in_groups,
        "processed_groups": len(results),
    })

    # Filter out already completed groups (by checking if all tweet IDs are completed)
    pending_groups = []
    for i, group in enumerate(tweet_groups):
        all_completed = all(tid in completed_ids for tid in group.tweet_ids)
        if not all_completed:
            pending_groups.append((i, group))

    if completed_ids:
        console.print(f"[dim]Skipping {len(tweet_groups) - len(pending_groups)} already processed groups[/dim]")

    # Track completion progress
    completed_count = len(results)
    total_count = len(tweet_groups)
    results_lock = asyncio.Lock()

    async def research_group_item(index: int, group: TweetGroup) -> ResearchResult | None:
        """Research a group of tweets with semaphore control."""
        nonlocal completed_count

        async with research_semaphore:
            tweet_count = len(group.tweets)
            console.print(f"\n[cyan][{index+1}/{total_count}] Researching group ({tweet_count} tweets): {group.combined_summary[:60]}...[/cyan]")

            # Enrich tweets with thread content
            enriched_texts = []
            all_urls = []
            for ft in group.tweets:
                enriched = await crawler.enrich_with_thread(ft.tweet)
                enriched_texts.append(f"[推文] @{enriched.author}:\n{enriched.full_content}")
                all_urls.extend(enriched.urls)

            combined_text = "\n\n---\n\n".join(enriched_texts)
            unique_urls = list(dict.fromkeys(all_urls))  # Preserve order, remove dups

            # Perform group research with retry
            result = await research_agent.research_group_with_retry(
                group_id=group.group_id,
                topic=group.topic.value,
                combined_tweets=combined_text,
                urls=unique_urls,
                combined_summary=group.combined_summary,
                tweet_ids=group.tweet_ids,
                primary_author=group.primary_tweet.tweet.author,
                max_retries=2,
            )

            # Generate TLDR summary if research succeeded
            if result.success and result.research_report:
                console.print(f"[dim]Generating TLDR for: {result.title[:40]}...[/dim]")
                tldr = await summary_agent.generate_tldr(
                    title=result.title,
                    research_report=result.research_report,
                )
                result.tldr = tldr

                # Incremental Notion sync: push immediately after research + TLDR
                if incremental_syncer and incremental_syncer.is_initialized:
                    await incremental_syncer.push_item(
                        item=result,
                        topic=group.topic.value,
                    )

            # Thread-safe update
            async with results_lock:
                completed_count += 1
                console.print(f"[green]Completed {completed_count}/{total_count}: {result.title[:50]} ({tweet_count} tweets)[/green]")

            # Save progress for each tweet in the group
            for tid in group.tweet_ids:
                progress.save_result(run_id, tid, result.model_dump())

            progress.save_progress(run_id, {
                "current_phase": "research",
                "total_groups": total_count,
                "processed_groups": completed_count,
                "current_group_id": group.group_id,
            })

            # Mark all tweets in group as processed
            for ft in group.tweets:
                await db.mark_processed(
                    tweet_id=ft.tweet.id,
                    url=ft.tweet.urls[0] if ft.tweet.urls else None,
                    author=ft.tweet.author,
                    category=ft.category.value,
                    topic=ft.topic.value,
                    title=result.title,
                )

            return result

    # Run concurrent group research
    tasks = [research_group_item(i, group) for i, group in pending_groups]
    concurrent_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect results (filter out exceptions)
    for res in concurrent_results:
        if isinstance(res, Exception):
            console.print(f"[red]Research task failed: {res}[/red]")
        elif res is not None:
            results.append(res)

    console.print(f"\n[green]Research completed: {len(results)} items[/green]")

    if not results:
        console.print("[yellow]No research results. Exiting.[/yellow]")
        return

    # Step 6: Generate report
    console.print("\n[blue]Step 6/7: Generating report...[/blue]")
    progress.save_progress(run_id, {"current_phase": "integrate"})

    # Calculate overview stats
    # For groups, tweet_id may be comma-separated (multiple IDs)
    researched_ids = set()
    for r in results:
        for tid in r.tweet_id.split(","):
            researched_ids.add(tid.strip())

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
        # If incremental sync was used, finalize it
        if incremental_syncer and incremental_syncer.is_initialized:
            try:
                await incremental_syncer.finalize(
                    total_items=report.total_items,
                    successful_items=report.successful_items,
                    failed_items=report.failed_items,
                )
                notion_url = incremental_syncer.summary_url
                console.print(f"[green]Incremental sync finalized: {incremental_syncer.synced_count} items[/green]")
            except Exception as e:
                console.print(f"[yellow]Failed to finalize incremental sync: {e}[/yellow]")
                # Fall back to batch sync
                incremental_syncer = None

        # Fall back to batch sync if incremental sync wasn't used or failed
        if not incremental_syncer or not incremental_syncer.is_initialized:
            try:
                notion_sync = NotionSync()
                console.print("[blue]Creating hierarchical Notion report (batch sync)...[/blue]")
                notion_url = await notion_sync.sync_report_hierarchical(report)
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
