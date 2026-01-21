"""Notion sync module for publishing daily reports."""

import json
import os
from pathlib import Path
from typing import Optional

from notion_client import AsyncClient
from rich.console import Console

from src.integrator.report_generator import DailyReport, ReportGenerator

console = Console()


def load_notion_config(config_path: str | Path = "config/notion_config.json") -> dict:
    """Load Notion configuration from file."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Notion config not found: {config_path}. "
            f"Please copy {config_path.with_suffix('.example.json')} and fill in your credentials."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


class NotionSync:
    """Sync daily reports to Notion."""

    def __init__(self, token: Optional[str] = None, database_id: Optional[str] = None):
        """
        Initialize Notion sync.

        Args:
            token: Notion integration token (or from env/config)
            database_id: Target Notion database ID (or from env/config)
        """
        # Try to get from args, then env, then config file
        self.token = token or os.environ.get("NOTION_TOKEN")
        self.database_id = database_id or os.environ.get("NOTION_DATABASE_ID")

        # Load from config if still not set
        if not self.token or not self.database_id:
            try:
                config = load_notion_config()
                self.token = self.token or config.get("token")
                self.database_id = self.database_id or config.get("database_id")
            except FileNotFoundError:
                pass

        if not self.token:
            raise ValueError(
                "Notion token not found. Set NOTION_TOKEN env var or config/notion_config.json"
            )
        if not self.database_id:
            raise ValueError(
                "Notion database ID not found. Set NOTION_DATABASE_ID env var or config/notion_config.json"
            )

        self.client = AsyncClient(auth=self.token)
        self.report_generator = ReportGenerator()

    async def create_page(self, report: DailyReport) -> str:
        """
        Create a new Notion page for the daily report.

        Args:
            report: DailyReport to publish

        Returns:
            URL of the created page
        """
        console.print(f"[blue]Creating Notion page for {report.date}...[/blue]")

        # Generate Notion blocks from report
        blocks = self.report_generator.to_notion_blocks(report)

        # Split blocks into chunks of 100 (Notion API limit)
        block_chunks = [blocks[i:i + 100] for i in range(0, len(blocks), 100)]

        try:
            # Create the page with first chunk of blocks
            response = await self.client.pages.create(
                parent={"database_id": self.database_id},
                properties={
                    "Name": {
                        "title": [
                            {
                                "text": {
                                    "content": report.title
                                }
                            }
                        ]
                    },
                    "Date": {
                        "date": {
                            "start": report.date
                        }
                    }
                },
                children=block_chunks[0] if block_chunks else []
            )

            page_id = response["id"]
            page_url = response["url"]

            # Append remaining blocks if any
            for chunk in block_chunks[1:]:
                await self.client.blocks.children.append(
                    block_id=page_id,
                    children=chunk
                )

            console.print(f"[green]Notion page created: {page_url}[/green]")
            return page_url

        except Exception as e:
            console.print(f"[red]Failed to create Notion page: {e}[/red]")
            raise

    async def update_page(self, page_id: str, report: DailyReport) -> None:
        """
        Update an existing Notion page.

        Args:
            page_id: ID of the page to update
            report: DailyReport with updated content
        """
        console.print(f"[blue]Updating Notion page {page_id}...[/blue]")

        # Generate new blocks
        blocks = self.report_generator.to_notion_blocks(report)
        block_chunks = [blocks[i:i + 100] for i in range(0, len(blocks), 100)]

        try:
            # First, delete existing content blocks
            existing_blocks = await self.client.blocks.children.list(block_id=page_id)
            for block in existing_blocks.get("results", []):
                await self.client.blocks.delete(block_id=block["id"])

            # Add new blocks
            for chunk in block_chunks:
                await self.client.blocks.children.append(
                    block_id=page_id,
                    children=chunk
                )

            # Update title
            await self.client.pages.update(
                page_id=page_id,
                properties={
                    "Name": {
                        "title": [
                            {
                                "text": {
                                    "content": report.title
                                }
                            }
                        ]
                    }
                }
            )

            console.print(f"[green]Notion page updated[/green]")

        except Exception as e:
            console.print(f"[red]Failed to update Notion page: {e}[/red]")
            raise

    async def find_page_by_date(self, date: str) -> Optional[str]:
        """
        Find an existing page by date.

        Args:
            date: Date string (YYYY-MM-DD)

        Returns:
            Page ID if found, None otherwise
        """
        try:
            response = await self.client.databases.query(
                database_id=self.database_id,
                filter={
                    "property": "Date",
                    "date": {
                        "equals": date
                    }
                }
            )

            results = response.get("results", [])
            if results:
                return results[0]["id"]
            return None

        except Exception as e:
            console.print(f"[yellow]Failed to query Notion database: {e}[/yellow]")
            return None

    async def sync_report(self, report: DailyReport) -> str:
        """
        Sync a report to Notion (create or update).

        Args:
            report: DailyReport to sync

        Returns:
            URL of the synced page
        """
        # Check if page already exists for this date
        existing_page_id = await self.find_page_by_date(report.date)

        if existing_page_id:
            console.print(f"[yellow]Found existing page for {report.date}, updating...[/yellow]")
            await self.update_page(existing_page_id, report)
            # Get the page URL
            page = await self.client.pages.retrieve(page_id=existing_page_id)
            return page["url"]
        else:
            return await self.create_page(report)
