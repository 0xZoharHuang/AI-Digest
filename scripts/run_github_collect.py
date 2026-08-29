#!/usr/bin/env python3
"""GitHub Collection + Screening (Phase 1+2).

Phase 1: GitHub API → pre-filter → DB dedup → fetch READMEs → save to github_candidates table.
Phase 2: Batch-screen ALL pending candidates (100/batch) → mark screened/skipped in DB.

Runs every 3 days. Phase 1 ~2 min, Phase 2 ~15-20 min.
"""

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

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.github_discovery_agent import (
    GitHubDiscoveryAgent,
    GitHubProjectCollector,
    CollectedProject,
    CollectedProjects,
)
from src.storage.history_db import HistoryDB

console = Console()


def load_config() -> dict:
    """Load GitHub configuration."""
    config_path = Path("config/github_config.json")

    if not config_path.exists():
        console.print("[red]Error: config/github_config.json not found[/red]")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    if not config.get("github_token"):
        console.print("[red]Error: github_token not configured[/red]")
        sys.exit(1)

    return config


async def main(skip_screening: bool = False):
    """Run Phase 1 (collect) + Phase 2 (batch screen)."""
    console.print(Panel.fit(
        "[bold cyan]GitHub Collection + Screening (Phase 1+2)[/bold cyan]\n"
        "Collecting candidates → batch screening all pending",
        border_style="cyan"
    ))

    config = load_config()
    github_token = config["github_token"]
    model = config.get("model", "sonnet")

    console.print(f"\n[dim]Configuration:[/dim]")
    console.print(f"  Stars range: {config.get('min_stars', 100)}-{config.get('max_stars', 3000)}")

    # Initialize database
    db = HistoryDB()
    await db.init_db()

    # Show pool stats before collection
    stats = await db.get_candidate_stats()
    console.print(f"\n[dim]Pool before collection: {stats}[/dim]")

    # ============================================================
    # Phase 1: Deterministic collection
    # ============================================================
    console.print(f"\n[cyan]{'='*50}[/cyan]")
    console.print("[bold cyan]Phase 1: Collecting candidates[/bold cyan]")
    console.print(f"[cyan]{'='*50}[/cyan]\n")

    collector = GitHubProjectCollector(
        github_token=github_token,
        db=db,
        config=config,
    )

    collected = await collector.collect()

    # Save candidates to DB pool
    new_count = 0
    skip_count = 0

    console.print(f"\n[cyan]Saving {len(collected.candidates)} candidates to pool...[/cyan]")

    for candidate in collected.candidates:
        if await db.is_candidate_exists(candidate.repo_id):
            skip_count += 1
            continue

        inserted = await db.save_github_candidate({
            "repo_id": candidate.repo_id,
            "full_name": candidate.full_name,
            "url": candidate.url,
            "description": candidate.description,
            "stars": candidate.stars,
            "language": candidate.language,
            "topics": candidate.topics,
            "created_at": candidate.created_at,
            "pushed_at": candidate.pushed_at,
            "forks": candidate.forks,
            "open_issues": candidate.open_issues,
            "license_name": candidate.license_name,
            "readme_excerpt": candidate.readme_excerpt,
        })
        if inserted:
            new_count += 1
        else:
            skip_count += 1

    stats_after_collect = await db.get_candidate_stats()

    console.print(Panel.fit(
        f"[bold green]Phase 1 Complete[/bold green]\n\n"
        f"API results: {collected.total_api_results}\n"
        f"After pre-filter: {collected.total_after_prefilter}\n"
        f"With valid README: {collected.total_with_readme}\n"
        f"New candidates saved: {new_count}\n"
        f"Already in pool: {skip_count}\n"
        f"Errors: {len(collected.errors)}\n\n"
        f"Pool status: {stats_after_collect}",
        border_style="green"
    ))

    if skip_screening:
        console.print("[yellow]Skipping Phase 2 screening (--skip-screening)[/yellow]")
        return

    # ============================================================
    # Phase 2: Batch screen ALL pending candidates
    # ============================================================
    pending_count = stats_after_collect["pending"]
    if pending_count == 0:
        console.print("\n[yellow]No pending candidates to screen[/yellow]")
        return

    console.print(f"\n[cyan]{'='*50}[/cyan]")
    console.print(f"[bold cyan]Phase 2: Screening {pending_count} pending candidates[/bold cyan]")
    console.print(f"[cyan]{'='*50}[/cyan]\n")

    # Fetch all pending candidates
    pending = await db.get_pending_candidates(limit=2000)

    # Convert DB rows to CollectedProjects
    candidates = []
    for row in pending:
        candidates.append(CollectedProject(
            repo_id=row["repo_id"],
            full_name=row["full_name"],
            url=row["url"],
            description=row.get("description", ""),
            stars=row.get("stars", 0),
            language=row.get("language", ""),
            topics=row.get("topics", []),
            created_at=row.get("created_at", ""),
            pushed_at=row.get("pushed_at", ""),
            forks=row.get("forks", 0),
            open_issues=row.get("open_issues", 0),
            license_name=row.get("license_name", ""),
            readme_excerpt=row.get("readme_excerpt", ""),
        ))

    collected_for_screen = CollectedProjects(
        candidates=candidates,
        total_api_results=len(candidates),
        total_after_prefilter=len(candidates),
        total_with_readme=len(candidates),
        queries_executed=0,
        errors=[],
        collected_at=datetime.now().isoformat(),
    )

    # Initialize agent for screening
    agent = GitHubDiscoveryAgent(
        github_token=github_token,
        model=model,
        db=db,
        config=config,
    )

    all_screened = await agent._run_screening_batched(
        collected_for_screen, batch_size=100
    )

    # Update DB: mark screened and skipped
    screened_repo_ids = set()
    for s in all_screened:
        rid = s["repo_id"]
        screened_repo_ids.add(rid)
        await db.update_candidate_screened(
            repo_id=rid,
            score=s.get("innovation_score", 0),
            summary=s.get("innovation_summary", ""),
        )

    skipped_count = 0
    for row in pending:
        if row["repo_id"] not in screened_repo_ids:
            await db.skip_candidate(row["repo_id"])
            skipped_count += 1

    stats_final = await db.get_candidate_stats()

    console.print(Panel.fit(
        f"[bold green]Phase 2 Complete[/bold green]\n\n"
        f"Candidates screened: {len(pending)}\n"
        f"Passed (score >= 8): {len(all_screened)}\n"
        f"Skipped (score < 8): {skipped_count}\n\n"
        f"Pool status: {stats_final}",
        border_style="green"
    ))


if __name__ == "__main__":
    skip_screening = "--skip-screening" in sys.argv

    try:
        asyncio.run(main(skip_screening=skip_screening))
    except KeyboardInterrupt:
        console.print("\n[yellow]Collection interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Fatal error: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)
