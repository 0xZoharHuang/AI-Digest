#!/usr/bin/env python3
"""OpenClaw Tracker - Track all updates in the OpenClaw ecosystem.

This script collects data from GitHub, official changelog, blog, npm,
and uses an Agent to search web/social sources, evaluate, and deeply
research high-importance updates.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Unset ANTHROPIC_API_KEY to use local Claude CLI (claude_agent_sdk)
if "ANTHROPIC_API_KEY" in os.environ:
    del os.environ["ANTHROPIC_API_KEY"]

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.openclaw_tracker_agent import OpenClawTrackerAgent, OpenClawTrackingResult
from src.agent.summary_agent import SummaryAgent
from src.storage.history_db import HistoryDB

console = Console()


def load_config() -> dict:
    """Load OpenClaw tracker configuration."""
    config_path = Path("config/openclaw_config.json")

    if not config_path.exists():
        console.print("[red]Error: config/openclaw_config.json not found[/red]")
        console.print("[yellow]Please create it from the example:[/yellow]")
        console.print("  cp config/openclaw_config.json.example config/openclaw_config.json")
        console.print("  # Edit and add your GitHub token")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    if not config.get("github_token"):
        console.print("[red]Error: github_token not configured[/red]")
        console.print("[yellow]Please add your GitHub token to config/openclaw_config.json[/yellow]")
        console.print("\nGet your token at: https://github.com/settings/tokens")
        sys.exit(1)

    return config


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="OpenClaw Ecosystem Tracker")
    parser.add_argument(
        "--skip-notion",
        action="store_true",
        help="Skip Notion sync",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=None,
        help="Days to look back (overrides config)",
    )
    return parser.parse_args()


async def main(skip_notion: bool = False, days_back_override: int = None):
    """Run OpenClaw ecosystem tracking."""
    console.print(
        Panel.fit(
            "[bold cyan]OpenClaw Ecosystem Tracker[/bold cyan]\n"
            "Tracking updates across GitHub, web, and social media",
            border_style="cyan",
        )
    )

    # Load config
    config = load_config()
    github_token = config["github_token"]
    days_back = days_back_override or config.get("days_back", 3)
    min_importance = config.get("min_importance", 5)
    model = config.get("model", "sonnet")

    console.print(f"\n[dim]Configuration:[/dim]")
    console.print(f"  Days back: {days_back}")
    console.print(f"  Min importance: {min_importance}")
    console.print(f"  Model: {model}\n")

    # Initialize database
    db = HistoryDB()
    await db.init_db()

    # Initialize agent
    agent = OpenClawTrackerAgent(
        github_token=github_token, model=model, config=config
    )

    # Run tracking (Phase 1 + Phase 2)
    console.print("[cyan]Starting tracking...[/cyan]\n")
    result = await agent.track(days_back=days_back, min_importance=min_importance)

    if not result.updates:
        console.print("[yellow]No updates found in this tracking period.[/yellow]")
        return

    # Filter duplicates
    console.print(f"\n[cyan]Filtering duplicates...[/cyan]")
    new_updates = []
    skipped_count = 0

    for update in result.updates:
        if await db.is_openclaw_update_tracked(update.update_id):
            console.print(f"  [dim]Skipping (already tracked): {update.update_id}[/dim]")
            skipped_count += 1
        else:
            new_updates.append(update)

    console.print(
        f"[green]Found {len(new_updates)} new updates "
        f"({skipped_count} duplicates skipped)[/green]\n"
    )

    # Generate TLDR for updates with research reports
    updates_with_reports = [u for u in new_updates if u.research_report]
    if updates_with_reports:
        console.print("[cyan]Generating TLDR summaries...[/cyan]")
        summary_agent = SummaryAgent(model="haiku")
        for update in updates_with_reports:
            tldr = await summary_agent.generate_tldr(
                title=update.title,
                research_report=update.research_report,
            )
            update.tldr = tldr
            console.print(f"  [dim]TLDR: {update.title[:40]}...[/dim]")

    # Save to database
    if new_updates:
        console.print("[cyan]Saving to database...[/cyan]")
        for update in new_updates:
            await db.mark_openclaw_update(
                update_id=update.update_id,
                source_type=update.source_type,
                repo=update.repo,
                title=update.title,
                url=update.url,
                importance=update.importance,
                summary=update.summary,
                tags=json.dumps(update.tags, ensure_ascii=False),
            )
        console.print(f"[green]Saved {len(new_updates)} updates to database[/green]\n")

    # Display results table
    if new_updates:
        table = Table(
            title="OpenClaw Updates",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("ID", style="dim", width=25)
        table.add_column("Type", style="cyan", width=10)
        table.add_column("Imp", justify="center", style="yellow", width=4)
        table.add_column("Title", style="white", width=50)

        for u in sorted(new_updates, key=lambda x: -x.importance):
            imp_color = "red" if u.importance >= 8 else "yellow" if u.importance >= 6 else "green"
            table.add_row(
                u.update_id[:25],
                u.source_type,
                f"[{imp_color}]{u.importance}[/{imp_color}]",
                u.title[:50] + ("..." if len(u.title) > 50 else ""),
            )

        console.print(table)
        console.print()

    # Period summary
    if result.period_summary:
        console.print(
            Panel(
                result.period_summary,
                title="本期概览",
                border_style="blue",
            )
        )

    # Summary stats
    console.print(
        Panel.fit(
            f"[bold green]Tracking Complete[/bold green]\n\n"
            f"Total updates found: {len(result.updates)}\n"
            f"New updates: {len(new_updates)}\n"
            f"Duplicates skipped: {skipped_count}\n"
            f"High importance (>=7): {sum(1 for u in new_updates if u.importance >= 7)}",
            border_style="green",
        )
    )

    # Sync to Notion
    if new_updates and not skip_notion and config.get("sync_to_notion", True):
        try:
            console.print("\n[cyan]Syncing to Notion...[/cyan]")
            from src.output.openclaw_notion_sync import OpenClawNotionSync

            # Build a result with only new updates for syncing
            sync_result = OpenClawTrackingResult(
                updates=new_updates,
                period_summary=result.period_summary,
                stats=result.stats,
            )

            notion_sync = OpenClawNotionSync()
            page_ids = await notion_sync.sync_tracking_result(sync_result)

            # Update database with Notion page IDs
            for update_id, page_id in page_ids.items():
                if page_id:
                    await db.update_openclaw_notion_page(update_id, page_id)

            console.print(
                f"[green]Synced to Notion "
                f"({sum(1 for v in page_ids.values() if v)} detail pages created)[/green]"
            )
        except Exception as e:
            console.print(f"[red]Notion sync failed: {e}[/red]")
            import traceback
            traceback.print_exc()

    # Save results to file
    results_dir = Path("data/openclaw_tracking")
    results_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = results_dir / f"tracking_{timestamp}.json"

    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "config": {
                    "days_back": days_back,
                    "min_importance": min_importance,
                    "model": model,
                },
                "summary": {
                    "total_found": len(result.updates),
                    "new_updates": len(new_updates),
                    "duplicates_skipped": skipped_count,
                },
                "period_summary": result.period_summary,
                "updates": [u.model_dump() for u in new_updates],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    console.print(f"\n[dim]Results saved to: {results_file}[/dim]")


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(
            main(
                skip_notion=args.skip_notion,
                days_back_override=args.days_back,
            )
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Tracking interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Fatal error: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)
