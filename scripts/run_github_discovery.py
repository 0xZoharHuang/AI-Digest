#!/usr/bin/env python3
"""GitHub Discovery (Phase 3) — Deep research from screened candidate pool.

Reads screened candidates from DB → Deep research (3/batch × 10 batches) → Notion push.
Designed to run daily; candidates are populated and screened by run_github_collect.py (Phase 1+2).
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
from rich.table import Table

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.github_discovery_agent import (
    GitHubDiscoveryAgent,
    CollectedProject,
    CollectedProjects,
    DiscoveryResult,
)
from src.storage.history_db import HistoryDB

console = Console()

RESEARCH_LIMIT = 30  # candidates to deep-research per run


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


async def main(skip_notion: bool = False):
    """Run Phase 3: Deep research on screened candidates from pool."""
    console.print(Panel.fit(
        "[bold cyan]GitHub Discovery (Phase 3)[/bold cyan]\n"
        "Deep research from screened candidate pool",
        border_style="cyan"
    ))

    config = load_config()
    github_token = config["github_token"]
    model = config.get("model", "sonnet")

    # Initialize database
    db = HistoryDB()
    await db.init_db()

    # Show pool stats
    stats = await db.get_candidate_stats()
    console.print(f"\n[dim]Pool status: {stats}[/dim]")

    if stats["screened"] == 0:
        console.print("[yellow]No screened candidates in pool. Run run_github_collect.py first.[/yellow]")
        return

    # Fetch screened candidates (highest score first)
    candidates_raw = await db.get_screened_candidates(limit=RESEARCH_LIMIT)
    console.print(f"[cyan]Fetched {len(candidates_raw)} screened candidates for research[/cyan]\n")

    if not candidates_raw:
        console.print("[yellow]No screened candidates available.[/yellow]")
        return

    # Convert DB rows to CollectedProjects (for _research_single_project context)
    candidates = []
    for row in candidates_raw:
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

    collected = CollectedProjects(
        candidates=candidates,
        total_api_results=len(candidates),
        total_after_prefilter=len(candidates),
        total_with_readme=len(candidates),
        queries_executed=0,
        errors=[],
        collected_at=datetime.now().isoformat(),
    )

    # Build screened dicts (for _run_deep_research)
    screened = [{
        "repo_id": row["repo_id"],
        "innovation_score": row.get("innovation_score", 8),
        "innovation_summary": row.get("innovation_summary", ""),
    } for row in candidates_raw]

    # Initialize agent
    agent = GitHubDiscoveryAgent(
        github_token=github_token,
        model=model,
        db=db,
        config=config,
    )

    # Phase 3: Deep research (3/batch × 10 batches)
    total_batches = (len(screened) + 2) // 3
    console.print(
        f"[cyan]Phase 3: Deep research for {len(screened)} projects "
        f"({total_batches} batches of 3)...[/cyan]\n"
    )

    reports = await agent._run_deep_research(screened, collected)

    # Assemble final discoveries
    new_discoveries = []
    for row in candidates_raw:
        rid = row["repo_id"]
        report_data = reports.get(rid, {})

        # Skip if already in early_repos
        if await db.is_repo_tracked(rid):
            console.print(f"  [dim]Skipping (already tracked): {rid}[/dim]")
            continue

        # Update candidate status
        if report_data.get("research_report"):
            await db.update_candidate_researched(rid)

        repo_name = rid.split("/")[-1] if "/" in rid else rid
        summary_text = row.get("innovation_summary", "")
        full_name = f"{repo_name}: {summary_text[:60]}"

        discovery = DiscoveryResult(
            repo_id=rid,
            full_name=full_name,
            url=row.get("url", f"https://github.com/{rid}"),
            stars=row.get("stars", 0),
            innovation_score=row.get("innovation_score", 8),
            innovation_summary=summary_text,
            code_quality=report_data.get("code_quality", ""),
            author_background=report_data.get("author_background", ""),
            research_report=report_data.get("research_report", ""),
            recommendation=report_data.get("recommendation", ""),
        )
        new_discoveries.append(discovery)

    console.print(
        f"\n[green]Found {len(new_discoveries)} new projects[/green]\n"
    )

    # TLDR generation
    discoveries_with_reports = [d for d in new_discoveries if d.research_report]
    if discoveries_with_reports:
        try:
            console.print("[cyan]Generating TLDRs...[/cyan]")
            from src.agent.summary_agent import SummaryAgent

            summary_agent = SummaryAgent(model="haiku")
            for d in discoveries_with_reports:
                tldr = await summary_agent.generate_tldr(
                    title=d.full_name,
                    research_report=d.research_report,
                )
                if tldr and not d.recommendation:
                    d.recommendation = tldr
            console.print(
                f"[green]Generated {len(discoveries_with_reports)} TLDRs[/green]\n"
            )
        except Exception as e:
            console.print(f"[yellow]TLDR generation failed: {e}[/yellow]\n")

    # Save to early_repos table
    if new_discoveries:
        console.print("[cyan]Saving to early_repos...[/cyan]")
        for discovery in new_discoveries:
            await db.mark_repo_tracked(
                repo_id=discovery.repo_id,
                full_name=discovery.full_name,
                url=discovery.url,
                stars=discovery.stars,
                innovation_score=discovery.innovation_score,
                innovation_summary=discovery.innovation_summary,
            )
        console.print(
            f"[green]Saved {len(new_discoveries)} projects to database[/green]\n"
        )

    # Display results
    if new_discoveries:
        table = Table(
            title="Discovered Projects",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Repo", style="cyan", width=30)
        table.add_column("Stars", justify="right", style="yellow")
        table.add_column("Score", justify="center", style="green")
        table.add_column("Innovation", style="dim", width=40)

        for d in new_discoveries:
            table.add_row(
                d.repo_id,
                str(d.stars),
                f"{d.innovation_score}/10",
                (d.innovation_summary[:40] + "...")
                if len(d.innovation_summary) > 40
                else d.innovation_summary,
            )

        console.print(table)
        console.print()

    # Summary
    console.print(Panel.fit(
        f"[bold green]Discovery Complete[/bold green]\n\n"
        f"Candidates researched: {len(candidates_raw)}\n"
        f"Reports generated: {len(reports)}\n"
        f"New discoveries: {len(new_discoveries)}",
        border_style="green"
    ))

    # Sync to Notion
    if new_discoveries and not skip_notion and config.get("sync_to_notion", False):
        try:
            console.print("\n[cyan]Syncing to Notion...[/cyan]")
            from src.output.github_notion_sync import GitHubNotionSync

            notion_sync = GitHubNotionSync()
            page_ids = await notion_sync.sync_discoveries(new_discoveries)

            for discovery, page_id in zip(new_discoveries, page_ids):
                if page_id:
                    await db.update_repo_notion_page(discovery.repo_id, page_id)

            console.print(f"[green]Synced {len(page_ids)} projects to Notion[/green]")
        except Exception as e:
            console.print(f"[red]Notion sync failed: {e}[/red]")

    # Save results to file
    if new_discoveries:
        results_dir = Path("data/github_discoveries")
        results_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_file = results_dir / f"discovery_{timestamp}.json"

        with open(results_file, "w") as f:
            json.dump(
                {
                    "timestamp": datetime.now().isoformat(),
                    "summary": {
                        "candidates_researched": len(candidates_raw),
                        "reports_generated": len(reports),
                        "new_discoveries": len(new_discoveries),
                    },
                    "discoveries": [d.model_dump() for d in new_discoveries],
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        console.print(f"\n[dim]Results saved to: {results_file}[/dim]")

    # Show updated pool stats
    stats_after = await db.get_candidate_stats()
    console.print(f"\n[dim]Pool status after: {stats_after}[/dim]")


if __name__ == "__main__":
    skip_notion = "--skip-notion" in sys.argv

    try:
        asyncio.run(main(skip_notion=skip_notion))
    except KeyboardInterrupt:
        console.print("\n[yellow]Discovery interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Fatal error: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)
