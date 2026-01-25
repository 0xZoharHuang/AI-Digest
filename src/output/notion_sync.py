"""Notion sync module for publishing daily reports."""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from notion_client import AsyncClient
from rich.console import Console

from src.integrator.report_generator import DailyReport, ReportGenerator, TopicSection
from src.agent import ResearchResult

console = Console()

# Topic emoji mapping
TOPIC_EMOJIS = {
    "LLM": "🤖",
    "Agent": "🕹️",
    "多模态": "🖼️",
    "CV": "👁️",
    "推理优化": "⚡",
    "训练": "🏋️",
    "其他": "📝",
}


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

            console.print(f"[green]Notion page created (flat): {page_url}[/green]")
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
        Sync a report to Notion by creating a new page.

        Each run creates a new page with timestamp in the title.
        Old pages are preserved for history.

        Args:
            report: DailyReport to sync

        Returns:
            URL of the created page
        """
        # Always create a new page with timestamp
        # This preserves all historical reports
        return await self.create_page_with_timestamp(report)

    async def create_page_with_timestamp(self, report: DailyReport) -> str:
        """
        Create a new Notion page with timestamp in title.

        Args:
            report: DailyReport to publish

        Returns:
            URL of the created page
        """
        # Generate title with timestamp: "AI Daily Digest - 2026-01-24 08:00"
        timestamp = datetime.now().strftime("%H:%M")
        title_with_time = f"{report.title} {timestamp}"

        console.print(f"[blue]Creating Notion page: {title_with_time}...[/blue]")

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
                                    "content": title_with_time
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

            console.print(f"[green]Notion page created (with timestamp): {page_url}[/green]")
            return page_url

        except Exception as e:
            console.print(f"[red]Failed to create Notion page: {e}[/red]")
            raise

    # ========== Hierarchical Report Methods (New) ==========

    async def create_hierarchical_report(
        self,
        report: DailyReport,
        on_item_synced: Optional[callable] = None,
    ) -> str:
        """
        Create a hierarchical report with summary page + child pages.

        Args:
            report: DailyReport to publish
            on_item_synced: Optional callback when each item is synced

        Returns:
            URL of the summary page
        """
        timestamp = datetime.now().strftime("%H:%M")
        title = f"{report.title} {timestamp}"

        console.print(f"[blue]Creating hierarchical report: {title}[/blue]")

        # Step 1: Create summary page (initially with just stats)
        summary_page_id, summary_url = await self._create_summary_page(report, title)
        console.print(f"[green]Summary page created: {summary_url}[/green]")

        # Step 2: Create child pages for each research item
        child_pages = {}  # tweet_id -> (page_id, page_url, title)
        total_items = sum(len(section.items) for section in report.sections)
        current_item = 0

        for section in report.sections:
            # Add section heading to summary page
            await self._append_section_heading(summary_page_id, section.topic, section.topic_emoji)

            for item in section.items:
                current_item += 1
                console.print(f"[cyan][{current_item}/{total_items}] Creating page: {item.title[:50]}...[/cyan]")

                try:
                    child_page_id, child_url = await self._create_detail_page(
                        item=item,
                        parent_page_id=summary_page_id,
                        topic=section.topic,
                    )
                    child_pages[item.tweet_id] = (child_page_id, child_url, item.title)

                    # Add item to summary page with link
                    await self._append_summary_item(
                        summary_page_id=summary_page_id,
                        child_page_id=child_page_id,
                        item=item,
                    )

                    if on_item_synced:
                        on_item_synced(item, child_url)

                    # Rate limiting
                    await asyncio.sleep(0.3)

                except Exception as e:
                    console.print(f"[red]Failed to create page for {item.title[:30]}: {e}[/red]")
                    # Continue with other items

        console.print(f"[green]Hierarchical report created: {len(child_pages)} child pages[/green]")
        return summary_url

    async def _create_summary_page(
        self,
        report: DailyReport,
        title: str,
    ) -> tuple[str, str]:
        """
        Create the summary page with stats callout.

        Returns:
            Tuple of (page_id, page_url)
        """
        # Initial blocks: stats callout and divider
        initial_blocks = [
            {
                "type": "callout",
                "callout": {
                    "rich_text": [{
                        "type": "text",
                        "text": {
                            "content": f"📊 共 {report.total_items} 条内容 | ✅ {report.successful_items} 成功 | ❌ {report.failed_items} 失败"
                        }
                    }],
                    "icon": {"emoji": "📋"},
                    "color": "blue_background"
                }
            },
            {"type": "divider", "divider": {}},
        ]

        response = await self.client.pages.create(
            parent={"database_id": self.database_id},
            properties={
                "Name": {
                    "title": [{"text": {"content": title}}]
                },
                "Date": {
                    "date": {"start": report.date}
                }
            },
            children=initial_blocks
        )

        return response["id"], response["url"]

    async def _create_detail_page(
        self,
        item: ResearchResult,
        parent_page_id: str,
        topic: str,
    ) -> tuple[str, str]:
        """
        Create a detail page for a single research item.

        Args:
            item: ResearchResult to publish
            parent_page_id: ID of the summary page (parent)
            topic: Topic category

        Returns:
            Tuple of (page_id, page_url)
        """
        # Generate blocks for detail page
        blocks = self._generate_detail_blocks(item)

        # Split into chunks
        block_chunks = [blocks[i:i + 100] for i in range(0, len(blocks), 100)]

        # Create page as child of summary page
        response = await self.client.pages.create(
            parent={"page_id": parent_page_id},
            properties={
                "title": {
                    "title": [{"text": {"content": item.title[:100]}}]
                }
            },
            icon={"emoji": TOPIC_EMOJIS.get(topic, "📝")},
            children=block_chunks[0] if block_chunks else []
        )

        page_id = response["id"]
        page_url = response["url"]

        # Append remaining blocks
        for chunk in block_chunks[1:]:
            await self.client.blocks.children.append(
                block_id=page_id,
                children=chunk
            )
            await asyncio.sleep(0.2)

        return page_id, page_url

    async def _append_section_heading(
        self,
        summary_page_id: str,
        topic: str,
        topic_emoji: str,
    ) -> None:
        """Append a section heading to the summary page."""
        await self.client.blocks.children.append(
            block_id=summary_page_id,
            children=[{
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": f"{topic_emoji} {topic}"}
                    }]
                }
            }]
        )

    async def _append_summary_item(
        self,
        summary_page_id: str,
        child_page_id: str,
        item: ResearchResult,
    ) -> None:
        """Append a summary item with link to child page."""
        # Use TLDR if available, otherwise use one_liner
        summary_text = item.tldr if item.tldr else item.one_liner

        await self.client.blocks.children.append(
            block_id=summary_page_id,
            children=[{
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        # Page mention (clickable)
                        {
                            "type": "mention",
                            "mention": {
                                "type": "page",
                                "page": {"id": child_page_id}
                            }
                        },
                        {
                            "type": "text",
                            "text": {"content": f"\n{summary_text[:500]}"}
                        }
                    ]
                }
            }]
        )

    def _generate_detail_blocks(self, item: ResearchResult) -> list[dict]:
        """Generate Notion blocks for a detail page."""
        blocks = []

        # Metadata callout
        metadata = f"来源: @{item.author} | 类型: {item.category} | 主题: {item.topic}"
        if item.source_url:
            metadata += f"\n🔗 {item.source_url}"

        blocks.append({
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": metadata}}],
                "icon": {"emoji": "ℹ️"},
                "color": "gray_background"
            }
        })

        # TLDR if available
        if item.tldr:
            blocks.append({
                "type": "callout",
                "callout": {
                    "rich_text": [{"type": "text", "text": {"content": f"📌 TLDR: {item.tldr}"}}],
                    "icon": {"emoji": "⚡"},
                    "color": "yellow_background"
                }
            })

        blocks.append({"type": "divider", "divider": {}})

        # Research report content
        if item.success:
            content_blocks = self.report_generator._markdown_to_notion_blocks(item.research_report)
            blocks.extend(content_blocks)
        else:
            blocks.append({
                "type": "callout",
                "callout": {
                    "rich_text": [{"type": "text", "text": {"content": f"研究失败: {item.error or '未知错误'}"}}],
                    "icon": {"emoji": "⚠️"},
                    "color": "red_background"
                }
            })

        return blocks

    async def sync_report_hierarchical(self, report: DailyReport) -> str:
        """
        Sync report using hierarchical structure (summary + child pages).

        Args:
            report: DailyReport to sync

        Returns:
            URL of the summary page
        """
        return await self.create_hierarchical_report(report)
