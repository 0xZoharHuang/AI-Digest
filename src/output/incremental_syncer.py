"""Incremental Notion sync manager for real-time report updates."""

import asyncio
from datetime import datetime
from typing import Optional

from rich.console import Console

from src.agent import ResearchResult
from src.output.notion_sync import NotionSync, TOPIC_EMOJIS

console = Console()


class IncrementalNotionSyncer:
    """
    Manages incremental Notion sync - pushes each research item as it completes.

    Flow:
    1. init_summary_page() - Create summary page at start
    2. push_item() - Push each item as research completes
    3. finalize() - Update final statistics
    """

    def __init__(self, notion_sync: Optional[NotionSync] = None):
        """
        Initialize incremental syncer.

        Args:
            notion_sync: NotionSync instance (creates new one if not provided)
        """
        self.notion_sync = notion_sync or NotionSync()
        self.summary_page_id: Optional[str] = None
        self.summary_url: Optional[str] = None
        self.synced_items: set[str] = set()  # Track synced tweet_ids
        self.current_section: Optional[str] = None  # Track current topic section
        self._lock = asyncio.Lock()  # Thread-safe updates

    async def init_summary_page(self, report_date: str, title: Optional[str] = None) -> str:
        """
        Create the summary page (placeholder) at the start.

        Args:
            report_date: Date string (YYYY-MM-DD)
            title: Optional custom title

        Returns:
            URL of the created summary page
        """
        timestamp = datetime.now().strftime("%H:%M")
        page_title = title or f"AI Daily Digest - {report_date} {timestamp}"

        console.print(f"[blue]Creating summary page: {page_title}[/blue]")

        # Create initial summary page with placeholder stats
        initial_blocks = [
            {
                "type": "callout",
                "callout": {
                    "rich_text": [{
                        "type": "text",
                        "text": {
                            "content": "🔄 正在研究中... 实时更新"
                        }
                    }],
                    "icon": {"emoji": "📋"},
                    "color": "blue_background"
                }
            },
            {"type": "divider", "divider": {}},
        ]

        response = await self.notion_sync.client.pages.create(
            parent={"database_id": self.notion_sync.database_id},
            properties={
                "Name": {
                    "title": [{"text": {"content": page_title}}]
                },
                "Date": {
                    "date": {"start": report_date}
                }
            },
            children=initial_blocks
        )

        self.summary_page_id = response["id"]
        self.summary_url = response["url"]

        console.print(f"[green]Summary page created: {self.summary_url}[/green]")
        return self.summary_url

    async def push_item(
        self,
        item: ResearchResult,
        topic: str,
        topic_emoji: Optional[str] = None,
    ) -> Optional[str]:
        """
        Push a single research item to Notion.

        Args:
            item: ResearchResult to push
            topic: Topic category (LLM, Agent, etc.)
            topic_emoji: Optional emoji for topic

        Returns:
            URL of the created child page, or None on failure
        """
        if not self.summary_page_id:
            console.print("[red]Summary page not initialized. Call init_summary_page() first.[/red]")
            return None

        if item.tweet_id in self.synced_items:
            console.print(f"[dim]Item already synced: {item.tweet_id}[/dim]")
            return None

        async with self._lock:
            try:
                # Add section heading if this is a new topic
                if topic != self.current_section:
                    emoji = topic_emoji or TOPIC_EMOJIS.get(topic, "📝")
                    await self._append_section_heading(topic, emoji)
                    self.current_section = topic

                # Create detail page
                child_page_id, child_url = await self.notion_sync._create_detail_page(
                    item=item,
                    parent_page_id=self.summary_page_id,
                    topic=topic,
                )

                # Add summary item with link
                await self.notion_sync._append_summary_item(
                    summary_page_id=self.summary_page_id,
                    child_page_id=child_page_id,
                    item=item,
                )

                self.synced_items.add(item.tweet_id)
                console.print(f"[green]Synced to Notion: {item.title[:50]}[/green]")

                # Rate limiting
                await asyncio.sleep(0.3)

                return child_url

            except Exception as e:
                console.print(f"[red]Failed to sync item: {e}[/red]")
                return None

    async def _append_section_heading(self, topic: str, emoji: str) -> None:
        """Add a section heading to the summary page."""
        await self.notion_sync.client.blocks.children.append(
            block_id=self.summary_page_id,
            children=[{
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": f"{emoji} {topic}"}
                    }]
                }
            }]
        )

    async def finalize(
        self,
        total_items: int,
        successful_items: int,
        failed_items: int,
    ) -> None:
        """
        Finalize the sync by updating statistics.

        Args:
            total_items: Total number of items
            successful_items: Number of successful items
            failed_items: Number of failed items
        """
        if not self.summary_page_id:
            return

        console.print("[blue]Finalizing Notion report...[/blue]")

        try:
            # Get existing blocks to find the stats callout
            blocks = await self.notion_sync.client.blocks.children.list(
                block_id=self.summary_page_id
            )

            # Update the first callout block with final stats
            for block in blocks.get("results", []):
                if block.get("type") == "callout":
                    await self.notion_sync.client.blocks.update(
                        block_id=block["id"],
                        callout={
                            "rich_text": [{
                                "type": "text",
                                "text": {
                                    "content": f"📊 共 {total_items} 条内容 | ✅ {successful_items} 成功 | ❌ {failed_items} 失败"
                                }
                            }],
                            "icon": {"emoji": "📋"},
                            "color": "blue_background"
                        }
                    )
                    break

            console.print(f"[green]Report finalized: {len(self.synced_items)} items synced[/green]")

        except Exception as e:
            console.print(f"[yellow]Failed to finalize stats: {e}[/yellow]")

    @property
    def is_initialized(self) -> bool:
        """Check if the syncer is initialized."""
        return self.summary_page_id is not None

    @property
    def synced_count(self) -> int:
        """Get the number of synced items."""
        return len(self.synced_items)
