"""Notion sync module for OpenClaw ecosystem tracking."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from notion_client import AsyncClient
from rich.console import Console

from src.agent.openclaw_tracker_agent import OpenClawUpdate, OpenClawTrackingResult

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


class OpenClawNotionSync:
    """Sync OpenClaw tracking results to Notion."""

    def __init__(self, token: Optional[str] = None, database_id: Optional[str] = None):
        self.token = token or os.environ.get("NOTION_TOKEN")
        self.database_id = database_id

        if not self.token or not self.database_id:
            try:
                config = load_notion_config()
                self.token = self.token or config.get("token")
                self.database_id = (
                    self.database_id
                    or config.get("openclaw_database_id")
                    or config.get("database_id")
                )
            except FileNotFoundError:
                pass

        if not self.token:
            raise ValueError(
                "Notion token not found. Set NOTION_TOKEN env var or config/notion_config.json"
            )
        if not self.database_id:
            raise ValueError(
                "Notion database ID not found. Add 'openclaw_database_id' to config/notion_config.json"
            )

        self.client = AsyncClient(auth=self.token)

    def _update_to_blocks(self, update: OpenClawUpdate) -> list[dict]:
        """Convert an OpenClawUpdate to Notion blocks."""
        blocks = []

        # Metadata callout
        meta_parts = []
        if update.repo:
            meta_parts.append(f"Repo: {update.repo}")
        meta_parts.append(f"Source: {update.source_type}")
        meta_parts.append(f"Importance: {update.importance}/10")
        if update.date:
            meta_parts.append(f"Date: {update.date}")
        if update.tags:
            meta_parts.append(f"Tags: {', '.join(update.tags)}")

        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "icon": {"emoji": "📋"},
                "rich_text": [{
                    "type": "text",
                    "text": {"content": " | ".join(meta_parts)},
                }],
            },
        })

        # TLDR callout (if available)
        if update.tldr:
            blocks.append({
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"emoji": "💡"},
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": f"TLDR: {update.tldr}"},
                    }],
                },
            })

        # Summary
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "摘要"}}]
            },
        })
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": update.summary}}]
            },
        })

        # Source link
        if update.url:
            blocks.append({
                "object": "block",
                "type": "bookmark",
                "bookmark": {"url": update.url},
            })

        # Research report (if present)
        if update.research_report:
            blocks.append({
                "object": "block",
                "type": "divider",
                "divider": {},
            })
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": "深度研究报告"},
                    }]
                },
            })

            # Convert report paragraphs to blocks
            for para in update.research_report.split("\n\n"):
                text = para.strip()
                if not text:
                    continue
                # Truncate individual blocks to Notion's 2000-char limit
                while text:
                    chunk = text[:2000]
                    text = text[2000:]
                    blocks.append({
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{
                                "type": "text",
                                "text": {"content": chunk},
                            }]
                        },
                    })

        return blocks

    def _summary_blocks(
        self, result: OpenClawTrackingResult, date_range: str
    ) -> list[dict]:
        """Generate Notion blocks for the summary page."""
        blocks = []

        # Stats callout
        stats = result.stats
        stats_text = (
            f"Sources checked: {stats.get('total_sources_checked', 0)} | "
            f"Updates found: {stats.get('total_updates_found', 0)} | "
            f"High importance: {stats.get('high_importance_count', 0)}"
        )
        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "icon": {"emoji": "📊"},
                "rich_text": [{
                    "type": "text",
                    "text": {"content": stats_text},
                }],
            },
        })

        # Period summary
        if result.period_summary:
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": "本期概览"},
                    }]
                },
            })
            for para in result.period_summary.split("\n\n"):
                text = para.strip()
                if text:
                    blocks.append({
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{
                                "type": "text",
                                "text": {"content": text[:2000]},
                            }]
                        },
                    })

        # Divider
        blocks.append({"object": "block", "type": "divider", "divider": {}})

        # Updates list heading
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": "更新列表"},
                }]
            },
        })

        return blocks

    async def create_update_page(
        self,
        update: OpenClawUpdate,
        parent_page_id: Optional[str] = None,
    ) -> str:
        """Create a Notion page for a single update."""
        console.print(f"[blue]Creating page: {update.title[:50]}...[/blue]")

        blocks = self._update_to_blocks(update)
        block_chunks = [blocks[i : i + 100] for i in range(0, len(blocks), 100)]

        # Map source_type to emoji for select
        source_emoji = {
            "release": "🚀",
            "commit": "📝",
            "issue": "🐛",
            "article": "📰",
            "blog_post": "✍️",
            "social": "💬",
            "companion_app": "📱",
            "ecosystem_repo": "🔧",
        }
        source_label = f"{source_emoji.get(update.source_type, '📌')} {update.source_type}"

        # Build properties based on parent type
        if parent_page_id:
            # Child page under a page — use "title" not "Name"
            parent = {"page_id": parent_page_id}
            properties = {
                "title": {
                    "title": [{"text": {"content": update.title[:100]}}],
                },
            }
        else:
            # Database entry — use "Name" + "Date"
            parent = {"database_id": self.database_id}
            properties = {
                "Name": {
                    "title": [{"text": {"content": update.title[:100]}}],
                },
                "Date": {
                    "date": {"start": datetime.now().strftime("%Y-%m-%d")},
                },
            }

        try:
            response = await self.client.pages.create(
                parent=parent,
                properties=properties,
                children=block_chunks[0] if block_chunks else [],
            )
            page_id = response["id"]

            for chunk in block_chunks[1:]:
                await self.client.blocks.children.append(
                    block_id=page_id, children=chunk
                )

            console.print(f"[green]Page created: {response['url']}[/green]")
            return page_id

        except Exception as e:
            console.print(f"[red]Failed to create page: {e}[/red]")
            raise

    async def sync_tracking_result(
        self, result: OpenClawTrackingResult
    ) -> dict[str, Optional[str]]:
        """
        Sync full tracking result to Notion.

        Creates:
        1. Summary page in the database
        2. Detail child pages for importance >= 7

        Returns:
            Dict mapping update_id -> notion_page_id (or None on failure)
        """
        if not result.updates:
            console.print("[yellow]No updates to sync[/yellow]")
            return {}

        date_range = datetime.now().strftime("%Y-%m-%d")
        title = f"OpenClaw Tracker - {date_range}"

        # Create summary page
        console.print(f"\n[cyan]Creating summary page: {title}[/cyan]")
        summary_blocks = self._summary_blocks(result, date_range)

        # Add bullet items for all updates on the summary page
        for update in sorted(result.updates, key=lambda u: -u.importance):
            importance_emoji = "🔴" if update.importance >= 8 else "🟡" if update.importance >= 6 else "🟢"
            bullet_text = f"{importance_emoji} [{update.importance}] {update.title}"
            if update.summary:
                bullet_text += f" — {update.summary[:100]}"

            summary_blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": bullet_text[:2000]},
                    }]
                },
            })

        # Split summary blocks into chunks
        block_chunks = [
            summary_blocks[i : i + 100] for i in range(0, len(summary_blocks), 100)
        ]

        try:
            summary_response = await self.client.pages.create(
                parent={"database_id": self.database_id},
                properties={
                    "Name": {"title": [{"text": {"content": title}}]},
                    "Date": {"date": {"start": date_range}},
                },
                children=block_chunks[0] if block_chunks else [],
            )
            summary_page_id = summary_response["id"]

            for chunk in block_chunks[1:]:
                await self.client.blocks.children.append(
                    block_id=summary_page_id, children=chunk
                )

            console.print(
                f"[green]Summary page created: {summary_response['url']}[/green]"
            )

        except Exception as e:
            console.print(f"[red]Failed to create summary page: {e}[/red]")
            return {}

        # Create detail pages for high-importance updates as children
        page_ids = {}
        high_importance = [u for u in result.updates if u.importance >= 7]

        for i, update in enumerate(high_importance, 1):
            console.print(
                f"\n[cyan]Creating detail page {i}/{len(high_importance)}: "
                f"{update.title[:40]}...[/cyan]"
            )
            try:
                page_id = await self.create_update_page(
                    update, parent_page_id=summary_page_id
                )
                page_ids[update.update_id] = page_id
            except Exception as e:
                console.print(f"[red]Failed: {e}[/red]")
                page_ids[update.update_id] = None

        return page_ids
