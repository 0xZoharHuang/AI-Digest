"""APScheduler-based scheduler for AI Digest daily runs."""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from rich.console import Console

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

console = Console()


class DigestScheduler:
    """Scheduler for AI Digest automated runs."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self._setup_jobs()

    def _setup_jobs(self) -> None:
        """Configure scheduled jobs."""
        # Daily digest at 8:00 AM
        self.scheduler.add_job(
            self._run_daily_digest,
            CronTrigger(hour=8, minute=0),
            id="daily_digest_morning",
            name="Daily AI Digest (Morning)",
            replace_existing=True,
        )

        # Daily digest at 8:00 PM
        self.scheduler.add_job(
            self._run_daily_digest,
            CronTrigger(hour=20, minute=0),
            id="daily_digest_evening",
            name="Daily AI Digest (Evening)",
            replace_existing=True,
        )

        # Health check every 6 hours
        self.scheduler.add_job(
            self._check_account_health,
            CronTrigger(hour="*/6"),
            id="health_check",
            name="Account Health Check",
            replace_existing=True,
        )

        # Weekly cleanup on Sunday at 3 AM
        self.scheduler.add_job(
            self._cleanup_old_data,
            CronTrigger(day_of_week="sun", hour=3),
            id="weekly_cleanup",
            name="Weekly Data Cleanup",
            replace_existing=True,
        )

    async def _run_daily_digest(self) -> None:
        """Execute the daily digest workflow."""
        console.print(f"\n[cyan]{'='*60}[/cyan]")
        console.print(f"[cyan]Scheduled task triggered: {datetime.now()}[/cyan]")
        console.print(f"[cyan]{'='*60}[/cyan]\n")

        try:
            from scripts.run_daily import main
            await main()
            console.print("[green]Daily digest completed successfully[/green]")
        except Exception as e:
            console.print(f"[red]Daily digest failed: {e}[/red]")
            # Log error but don't crash the scheduler
            import traceback
            traceback.print_exc()

    async def _check_account_health(self) -> None:
        """Check account health status."""
        console.print(f"[blue]Health check: {datetime.now()}[/blue]")

        try:
            from src.crawler.account_health import AccountHealthMonitor

            monitor = AccountHealthMonitor()
            monitor.load_state("data/account_health.json")

            for username in monitor.accounts:
                status = monitor.get_status_summary(username)
                console.print(f"  Account: {username}")
                console.print(f"    Risk: {status['risk_level']} (score: {status['risk_score']})")
                console.print(f"    Success rate: {status['success_rate']}")

                if status['should_cooldown']:
                    console.print(f"    [yellow]In cooldown period[/yellow]")

        except Exception as e:
            console.print(f"[yellow]Health check failed: {e}[/yellow]")

    async def _cleanup_old_data(self) -> None:
        """Clean up old data from database, repo cache, and temp files."""
        console.print(f"[blue]Weekly cleanup: {datetime.now()}[/blue]")

        try:
            # 1. Database cleanup (existing)
            from src.storage.history_db import HistoryDB

            db = HistoryDB()
            await db.init_db()
            deleted = await db.clear_old_records(days=30)
            console.print(f"[green]Cleaned up {deleted} old database records[/green]")

            # 2. Repo cache cleanup (NEW)
            from pathlib import Path
            import shutil

            repo_dir = Path("/tmp/ai-digest-repos")
            if repo_dir.exists():
                repo_count = 0
                for repo in repo_dir.iterdir():
                    if repo.is_dir():
                        try:
                            shutil.rmtree(repo)
                            repo_count += 1
                        except Exception as e:
                            console.print(f"[yellow]Failed to remove {repo.name}: {e}[/yellow]")
                console.print(f"[green]Cleaned up {repo_count} repositories from cache[/green]")

            # 3. Old progress files cleanup (NEW)
            from src.storage import ProgressTracker
            import time

            progress = ProgressTracker()
            now = time.time()
            cleaned_progress = 0
            cleaned_results = 0

            # Remove progress files older than 7 days
            for f in progress.temp_dir.glob("progress_*.json"):
                if (now - f.stat().st_mtime) > 7 * 24 * 3600:
                    try:
                        f.unlink()
                        cleaned_progress += 1
                    except Exception as e:
                        console.print(f"[yellow]Failed to remove {f.name}: {e}[/yellow]")

            # Remove results files older than 7 days
            for f in progress.temp_dir.glob("results_*.json"):
                if (now - f.stat().st_mtime) > 7 * 24 * 3600:
                    try:
                        f.unlink()
                        cleaned_results += 1
                    except Exception as e:
                        console.print(f"[yellow]Failed to remove {f.name}: {e}[/yellow]")

            if cleaned_progress > 0 or cleaned_results > 0:
                console.print(f"[green]Cleaned up {cleaned_progress} progress files and {cleaned_results} result files[/green]")

        except Exception as e:
            console.print(f"[yellow]Cleanup failed: {e}[/yellow]")

    def start(self) -> None:
        """Start the scheduler."""
        self.scheduler.start()
        console.print("[green]Scheduler started[/green]")
        console.print("Scheduled jobs:")
        for job in self.scheduler.get_jobs():
            console.print(f"  - {job.name}: {job.trigger}")

    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        self.scheduler.shutdown()
        console.print("[yellow]Scheduler stopped[/yellow]")

    def run_now(self) -> None:
        """Manually trigger the daily digest (for testing)."""
        console.print("[blue]Manually triggering daily digest...[/blue]")
        asyncio.create_task(self._run_daily_digest())
