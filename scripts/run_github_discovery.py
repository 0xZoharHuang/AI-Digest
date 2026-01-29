#!/usr/bin/env python3
"""GitHub Discovery - Find early-stage valuable AI projects.

This script uses an Agent to discover, evaluate, and deeply research
100-1000 star GitHub projects with high innovation potential.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Unset ANTHROPIC_API_KEY to use local Claude CLI (claude_agent_sdk)
# In production, set ANTHROPIC_API_KEY to use API directly
if "ANTHROPIC_API_KEY" in os.environ:
    del os.environ["ANTHROPIC_API_KEY"]

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.github_discovery_agent import GitHubDiscoveryAgent
from src.storage.history_db import HistoryDB

console = Console()


def load_config() -> dict:
    """Load GitHub configuration."""
    config_path = Path("config/github_config.json")

    if not config_path.exists():
        console.print("[red]Error: config/github_config.json not found[/red]")
        console.print("[yellow]Please create it from the example:[/yellow]")
        console.print("  cp config/github_config.json.example config/github_config.json")
        console.print("  # Edit config/github_config.json and add your GitHub token")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    if not config.get("github_token"):
        console.print("[red]Error: github_token not configured[/red]")
        console.print("[yellow]Please add your GitHub token to config/github_config.json[/yellow]")
        console.print("\nGet your token at: https://github.com/settings/tokens")
        console.print("Required permissions: repo (read), user (read)")
        sys.exit(1)

    return config


async def main():
    """Run GitHub project discovery."""
    console.print(Panel.fit(
        "[bold cyan]GitHub Early-Stage Project Discovery[/bold cyan]\n"
        "Finding valuable AI projects with 100-1000 stars",
        border_style="cyan"
    ))

    # Load configuration
    config = load_config()
    github_token = config["github_token"]

    # Get parameters from config or use defaults
    min_stars = config.get("min_stars", 100)
    max_stars = config.get("max_stars", 1000)
    target_count = config.get("target_count", 15)
    model = config.get("model", "sonnet")

    console.print(f"\n[dim]Configuration:[/dim]")
    console.print(f"  Stars range: {min_stars}-{max_stars}")
    console.print(f"  Target count: {target_count}")
    console.print(f"  Model: {model}\n")

    # Initialize database
    db = HistoryDB()
    await db.init_db()

    # Initialize agent
    agent = GitHubDiscoveryAgent(github_token=github_token, model=model)

    # Run discovery
    console.print("[cyan]Starting discovery...[/cyan]\n")
    result = await agent.discover(
        min_stars=min_stars,
        max_stars=max_stars,
        target_count=target_count,
    )

    # Filter out already tracked repos
    console.print(f"\n[cyan]Filtering duplicates...[/cyan]")
    new_discoveries = []
    skipped_count = 0

    for discovery in result.discoveries:
        if await db.is_repo_tracked(discovery.repo_id):
            console.print(f"  [dim]Skipping (already tracked): {discovery.repo_id}[/dim]")
            skipped_count += 1
        else:
            new_discoveries.append(discovery)

    console.print(f"[green]Found {len(new_discoveries)} new projects ({skipped_count} duplicates skipped)[/green]\n")

    # Save to database
    if new_discoveries:
        console.print("[cyan]Saving to database...[/cyan]")
        for discovery in new_discoveries:
            await db.mark_repo_tracked(
                repo_id=discovery.repo_id,
                full_name=discovery.full_name,
                url=discovery.url,
                stars=discovery.stars,
                innovation_score=discovery.innovation_score,
                innovation_summary=discovery.innovation_summary,
            )
        console.print(f"[green]Saved {len(new_discoveries)} projects to database[/green]\n")

    # Display results
    if new_discoveries:
        table = Table(title="Discovered Projects", show_header=True, header_style="bold magenta")
        table.add_column("Repo", style="cyan", width=30)
        table.add_column("Stars", justify="right", style="yellow")
        table.add_column("Score", justify="center", style="green")
        table.add_column("Innovation", style="dim", width=40)

        for d in new_discoveries:
            table.add_row(
                d.repo_id,
                str(d.stars),
                f"{d.innovation_score}/10",
                d.innovation_summary[:40] + "..." if len(d.innovation_summary) > 40 else d.innovation_summary
            )

        console.print(table)
        console.print()

    # Summary
    console.print(Panel.fit(
        f"[bold green]Discovery Complete[/bold green]\n\n"
        f"Total scanned: {result.total_scanned}\n"
        f"High-value found: {result.total_discovered}\n"
        f"New discoveries: {len(new_discoveries)}\n"
        f"Duplicates skipped: {skipped_count}",
        border_style="green"
    ))

    # Sync to Notion if configured
    if new_discoveries and config.get("sync_to_notion", False):
        try:
            console.print("\n[cyan]Syncing to Notion...[/cyan]")
            from src.output.github_notion_sync import GitHubNotionSync

            notion_sync = GitHubNotionSync()
            page_ids = await notion_sync.sync_discoveries(new_discoveries)

            # Update database with Notion page IDs
            for discovery, page_id in zip(new_discoveries, page_ids):
                if page_id:
                    await db.update_repo_notion_page(discovery.repo_id, page_id)

            console.print(f"[green]Synced {len(page_ids)} projects to Notion[/green]")
        except Exception as e:
            console.print(f"[red]Notion sync failed: {e}[/red]")

    # Save results to file
    results_dir = Path("data/github_discoveries")
    results_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = results_dir / f"discovery_{timestamp}.json"

    with open(results_file, "w") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "config": {
                    "min_stars": min_stars,
                    "max_stars": max_stars,
                    "target_count": target_count,
                },
                "summary": {
                    "total_scanned": result.total_scanned,
                    "total_discovered": result.total_discovered,
                    "new_discoveries": len(new_discoveries),
                },
                "discoveries": [d.model_dump() for d in new_discoveries],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    console.print(f"\n[dim]Results saved to: {results_file}[/dim]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Discovery interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Fatal error: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)
