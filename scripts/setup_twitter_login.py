#!/usr/bin/env python3
"""Setup Twitter login for Playwright crawler.

This script opens a browser window for you to manually log in to Twitter.
After login, cookies are saved for future automated crawling.

Usage:
    python scripts/setup_twitter_login.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.crawler.playwright_crawler import PlaywrightCrawler
from rich.console import Console

console = Console()


async def main():
    console.print("\n[bold cyan]Twitter Login Setup[/bold cyan]\n")
    console.print("This will open a browser window for you to log in to Twitter.")
    console.print("After logging in, your session cookies will be saved for automated crawling.\n")
    console.print("[yellow]Important:[/yellow]")
    console.print("  1. Make sure you're connected to a VPN if needed")
    console.print("  2. Log in normally (you may need to verify via email/phone)")
    console.print("  3. Wait until you see the home feed before pressing Enter\n")

    input("Press Enter to open the browser...")

    crawler = PlaywrightCrawler()
    try:
        success = await crawler.login_manual()
        if success:
            console.print("\n[bold green]Setup complete![/bold green]")
            console.print("Cookies saved to: config/twitter_cookies.json")
            console.print("\nYou can now run: python scripts/run_daily.py --limit 10 --skip-notion")
        else:
            console.print("\n[bold red]Setup failed.[/bold red]")
            console.print("Please try again, making sure you're fully logged in before pressing Enter.")
    finally:
        await crawler.close()


if __name__ == "__main__":
    asyncio.run(main())
