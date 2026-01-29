"""Notion sync module for GitHub early-stage project discoveries."""

import json
import os
from pathlib import Path
from typing import Optional

from notion_client import AsyncClient
from rich.console import Console

from src.agent.github_discovery_agent import DiscoveryResult

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


class GitHubNotionSync:
    """Sync GitHub discoveries to Notion."""

    def __init__(self, token: Optional[str] = None, database_id: Optional[str] = None):
        """
        Initialize GitHub Notion sync.

        Args:
            token: Notion integration token (or from env/config)
            database_id: Target Notion database ID for GitHub discoveries
                        (uses 'github_database_id' from config or falls back to main database_id)
        """
        # Try to get from args, then env, then config file
        self.token = token or os.environ.get("NOTION_TOKEN")
        self.database_id = database_id

        # Load from config if still not set
        if not self.token or not self.database_id:
            try:
                config = load_notion_config()
                self.token = self.token or config.get("token")
                # Use github-specific database if available, otherwise fall back to main
                self.database_id = self.database_id or config.get("github_database_id") or config.get("database_id")
            except FileNotFoundError:
                pass

        if not self.token:
            raise ValueError(
                "Notion token not found. Set NOTION_TOKEN env var or config/notion_config.json"
            )
        if not self.database_id:
            raise ValueError(
                "Notion database ID not found. Set NOTION_DATABASE_ID env var or add 'github_database_id' to config/notion_config.json"
            )

        self.client = AsyncClient(auth=self.token)

    def _discovery_to_blocks(self, discovery: DiscoveryResult) -> list[dict]:
        """
        Convert a DiscoveryResult to Notion blocks.

        Args:
            discovery: DiscoveryResult object

        Returns:
            List of Notion block objects
        """
        blocks = []

        # Header with repo info
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": "📊 基本信息"}
                }]
            }
        })

        # Basic info
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"Repository: "},
                }, {
                    "type": "text",
                    "text": {
                        "content": discovery.repo_id,
                        "link": {"url": discovery.url}
                    },
                    "annotations": {"code": True}
                }]
            }
        })

        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"Stars: {discovery.stars} ⭐"}
                }]
            }
        })

        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"Innovation Score: {discovery.innovation_score}/10"}
                }]
            }
        })

        # Innovation summary
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": "💡 创新点"}
                }]
            }
        })

        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": discovery.innovation_summary}
                }]
            }
        })

        # Code quality
        if discovery.code_quality:
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": "🔧 代码质量"}
                    }]
                }
            })

            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": discovery.code_quality}
                    }]
                }
            })

        # Author background
        if discovery.author_background:
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": "👤 作者背景"}
                    }]
                }
            })

            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": discovery.author_background}
                    }]
                }
            })

        # Recommendation
        if discovery.recommendation:
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": "🎯 推荐理由"}
                    }]
                }
            })

            blocks.append({
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"emoji": "💎"},
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": discovery.recommendation}
                    }]
                }
            })

        # Full research report
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": "📝 完整研究报告"}
                }]
            }
        })

        # Convert markdown report to Notion blocks
        # For simplicity, just add as paragraphs (could be improved to parse markdown)
        report_lines = discovery.research_report.split("\n\n")
        for line in report_lines:
            if line.strip():
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{
                            "type": "text",
                            "text": {"content": line.strip()}
                        }]
                    }
                })

        return blocks

    async def create_discovery_page(self, discovery: DiscoveryResult) -> str:
        """
        Create a Notion page for a GitHub discovery.

        Args:
            discovery: DiscoveryResult object

        Returns:
            Page ID of the created page
        """
        console.print(f"[blue]Creating Notion page for {discovery.repo_id}...[/blue]")

        # Generate title: "RepoName: Innovation Summary"
        title = f"{discovery.full_name}: {discovery.innovation_summary[:60]}"

        # Generate blocks
        blocks = self._discovery_to_blocks(discovery)

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
                                    "content": title
                                }
                            }
                        ]
                    },
                    "Stars": {
                        "number": discovery.stars
                    },
                    "Score": {
                        "number": discovery.innovation_score
                    },
                    "URL": {
                        "url": discovery.url
                    },
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
            return page_id

        except Exception as e:
            console.print(f"[red]Failed to create Notion page: {e}[/red]")
            raise

    async def sync_discoveries(self, discoveries: list[DiscoveryResult]) -> list[str]:
        """
        Sync multiple discoveries to Notion.

        Args:
            discoveries: List of DiscoveryResult objects

        Returns:
            List of created page IDs
        """
        page_ids = []

        for i, discovery in enumerate(discoveries, 1):
            console.print(f"\n[cyan]Syncing {i}/{len(discoveries)}: {discovery.repo_id}[/cyan]")
            try:
                page_id = await self.create_discovery_page(discovery)
                page_ids.append(page_id)
            except Exception as e:
                console.print(f"[red]Failed to sync {discovery.repo_id}: {e}[/red]")
                page_ids.append(None)

        return page_ids
