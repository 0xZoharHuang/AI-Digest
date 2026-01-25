#!/usr/bin/env python3
"""
AI Digest Scheduler - Long-running daemon for automated daily digests.

Usage:
    python scripts/run_scheduler.py           # Run in foreground
    python scripts/run_scheduler.py --test    # Run once immediately (for testing)

The scheduler runs the daily digest at:
    - 8:00 AM (morning)
    - 8:00 PM (evening)

It also performs:
    - Health checks every 6 hours
    - Weekly data cleanup on Sundays at 3 AM
"""

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

# Unset ANTHROPIC_API_KEY to use local Claude CLI (claude_agent_sdk)
# In production, set ANTHROPIC_API_KEY to use API directly
if "ANTHROPIC_API_KEY" in os.environ:
    del os.environ["ANTHROPIC_API_KEY"]

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

console = Console()


async def main(test_mode: bool = False):
    """Main entry point."""
    from src.scheduler import DigestScheduler

    scheduler = DigestScheduler()

    if test_mode:
        # Run once immediately for testing
        console.print("[blue]Test mode: running digest immediately[/blue]")
        await scheduler._run_daily_digest()
        return

    # Setup graceful shutdown
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def handle_signal():
        console.print("\n[yellow]Received shutdown signal...[/yellow]")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    # Start scheduler
    scheduler.start()

    console.print("\n[green]AI Digest Scheduler is running[/green]")
    console.print("Press Ctrl+C to stop\n")

    # Keep running until shutdown signal
    try:
        await shutdown_event.wait()
    finally:
        scheduler.shutdown()
        console.print("[green]Scheduler stopped gracefully[/green]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Digest Scheduler")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run the digest once immediately (for testing)"
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(test_mode=args.test))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
