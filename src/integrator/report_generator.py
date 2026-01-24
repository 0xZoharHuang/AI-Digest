"""Report generator for aggregating research results into daily digest."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from rich.console import Console

from src.agent import ResearchResult

console = Console()


class FilteredItem(BaseModel):
    """Summary of a filtered but not researched tweet."""

    author: str
    summary: str
    topic: str
    category: str
    tweet_url: str


class TopicSection(BaseModel):
    """A section of the report grouped by topic."""

    topic: str
    topic_emoji: str
    items: list[ResearchResult]


class FilteredSection(BaseModel):
    """Section for filtered but not researched items."""

    topic: str
    topic_emoji: str
    items: list[FilteredItem]


class OverviewStats(BaseModel):
    """Statistics overview for the report."""

    crawled_total: int = 0
    valuable_count: int = 0
    researched_count: int = 0
    related_not_researched: int = 0
    filtered_out: int = 0


class DailyReport(BaseModel):
    """Complete daily digest report."""

    date: str
    title: str
    sections: list[TopicSection]
    total_items: int
    successful_items: int
    failed_items: int
    # New fields for filtering summary
    overview: Optional[OverviewStats] = None
    filtered_sections: list[FilteredSection] = []


# Topic to emoji mapping
TOPIC_EMOJIS = {
    "LLM": "🤖",
    "Agent": "🕹️",
    "多模态": "🖼️",
    "CV": "👁️",
    "推理优化": "⚡",
    "训练": "🏋️",
    "其他": "📝",
}


class ReportGenerator:
    """Generate daily digest reports from research results."""

    def __init__(self):
        # Define topic order
        self.topic_order = ["LLM", "Agent", "多模态", "CV", "推理优化", "训练", "其他"]

    def generate_report(
        self,
        results: list[ResearchResult],
        date: Optional[str] = None,
        overview: Optional[OverviewStats] = None,
        filtered_items: Optional[list[FilteredItem]] = None,
    ) -> DailyReport:
        """
        Generate a daily report from research results.

        Args:
            results: List of research results
            date: Report date (defaults to today)
            overview: Statistics overview
            filtered_items: Items that were filtered but not researched

        Returns:
            DailyReport with organized sections
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        console.print(f"[blue]Generating report for {date}...[/blue]")

        # Group by topic
        topic_groups: dict[str, list[ResearchResult]] = {}
        for result in results:
            topic = result.topic if result.topic in self.topic_order else "其他"
            if topic not in topic_groups:
                topic_groups[topic] = []
            topic_groups[topic].append(result)

        # Create sections in order
        sections = []
        for topic in self.topic_order:
            if topic in topic_groups:
                items = topic_groups[topic]
                # Sort by success (successful first), then by category
                items.sort(key=lambda x: (not x.success, x.category))
                sections.append(
                    TopicSection(
                        topic=topic,
                        topic_emoji=TOPIC_EMOJIS.get(topic, "📝"),
                        items=items,
                    )
                )

        # Group filtered items by topic
        filtered_sections = []
        if filtered_items:
            filtered_groups: dict[str, list[FilteredItem]] = {}
            for item in filtered_items:
                topic = item.topic if item.topic in self.topic_order else "其他"
                if topic not in filtered_groups:
                    filtered_groups[topic] = []
                filtered_groups[topic].append(item)

            for topic in self.topic_order:
                if topic in filtered_groups:
                    filtered_sections.append(
                        FilteredSection(
                            topic=topic,
                            topic_emoji=TOPIC_EMOJIS.get(topic, "📝"),
                            items=filtered_groups[topic],
                        )
                    )

        # Count stats
        successful = sum(1 for r in results if r.success)
        failed = len(results) - successful

        report = DailyReport(
            date=date,
            title=f"AI Daily Digest - {date}",
            sections=sections,
            total_items=len(results),
            successful_items=successful,
            failed_items=failed,
            overview=overview,
            filtered_sections=filtered_sections,
        )

        console.print(
            f"[green]Report generated: {report.total_items} items "
            f"({report.successful_items} successful, {report.failed_items} failed)[/green]"
        )

        return report

    def to_markdown(self, report: DailyReport) -> str:
        """
        Convert report to Markdown format.

        Args:
            report: DailyReport to convert

        Returns:
            Markdown string
        """
        lines = []

        # Header
        lines.append(f"# 📅 {report.title}")
        lines.append("")

        # Overview stats table (if available)
        if report.overview:
            lines.append("## 📊 今日概览")
            lines.append("")
            lines.append("| 类别 | 数量 |")
            lines.append("|------|------|")
            lines.append(f"| 爬取总数 | {report.overview.crawled_total} |")
            lines.append(f"| 有价值内容 | {report.overview.valuable_count} |")
            lines.append(f"| 深度研究 | {report.overview.researched_count} |")
            if report.overview.related_not_researched > 0:
                lines.append(f"| 相关未深研 | {report.overview.related_not_researched} |")
            lines.append(f"| 无关/过滤 | {report.overview.filtered_out} |")
            lines.append("")
        else:
            lines.append(
                f"> 共 {report.total_items} 条内容 | "
                f"✅ {report.successful_items} 成功 | "
                f"❌ {report.failed_items} 失败"
            )
            lines.append("")

        # Table of contents
        lines.append("## 目录")
        lines.append("")
        for section in report.sections:
            lines.append(f"- [{section.topic_emoji} {section.topic}](#{section.topic.lower().replace(' ', '-')}) ({len(section.items)})")
        if report.filtered_sections:
            lines.append(f"- [📝 相关但未深研](#相关但未深研) ({sum(len(s.items) for s in report.filtered_sections)})")
        lines.append("")

        # Sections
        for section in report.sections:
            lines.append(f"## {section.topic_emoji} {section.topic}")
            lines.append("")

            for item in section.items:
                # Item header
                lines.append(f"### {item.title}")
                lines.append("")

                # Metadata
                lines.append(f"- **来源**: @{item.author}")
                if item.source_url:
                    lines.append(f"- **链接**: {item.source_url}")
                lines.append(f"- **类型**: {item.category}")
                lines.append(f"- **一句话**: {item.one_liner}")
                lines.append("")

                # Research report
                if item.success:
                    lines.append(item.research_report)
                else:
                    lines.append(f"⚠️ 研究失败: {item.error}")
                lines.append("")
                lines.append("---")
                lines.append("")

        # Filtered but not researched sections
        if report.filtered_sections:
            lines.append("## 📝 相关但未深研")
            lines.append("")
            lines.append("> 以下内容与 AI 相关但未进行深度研究，供快速浏览")
            lines.append("")

            for section in report.filtered_sections:
                lines.append(f"### {section.topic_emoji} {section.topic} ({len(section.items)} 条)")
                lines.append("")
                for item in section.items:
                    lines.append(f"- **@{item.author}**: {item.summary} [{item.category}]({item.tweet_url})")
                lines.append("")

        return "\n".join(lines)

    def to_notion_blocks(self, report: DailyReport) -> list[dict]:
        """
        Convert report to Notion blocks format.

        Args:
            report: DailyReport to convert

        Returns:
            List of Notion block objects
        """
        blocks = []

        # Stats callout
        blocks.append({
            "type": "callout",
            "callout": {
                "rich_text": [{
                    "type": "text",
                    "text": {
                        "content": f"共 {report.total_items} 条内容 | ✅ {report.successful_items} 成功 | ❌ {report.failed_items} 失败"
                    }
                }],
                "icon": {"emoji": "📊"},
            }
        })

        # Table of contents
        blocks.append({"type": "table_of_contents", "table_of_contents": {}})
        blocks.append({"type": "divider", "divider": {}})

        # Sections
        for section in report.sections:
            # Section heading
            blocks.append({
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": f"{section.topic_emoji} {section.topic}"}
                    }]
                }
            })

            for item in section.items:
                # Item heading
                blocks.append({
                    "type": "heading_3",
                    "heading_3": {
                        "rich_text": [{
                            "type": "text",
                            "text": {"content": item.title[:100]}  # Notion has 100 char limit
                        }]
                    }
                })

                # Metadata
                metadata_text = f"来源: @{item.author}"
                if item.source_url:
                    metadata_text += f" | 链接: {item.source_url}"
                metadata_text += f" | 类型: {item.category}"

                blocks.append({
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{
                            "type": "text",
                            "text": {"content": metadata_text[:2000]},  # Notion limit
                            "annotations": {"color": "gray"}
                        }]
                    }
                })

                # One-liner
                blocks.append({
                    "type": "quote",
                    "quote": {
                        "rich_text": [{
                            "type": "text",
                            "text": {"content": item.one_liner[:2000]}
                        }]
                    }
                })

                # Research content - split into paragraphs
                if item.success:
                    content_blocks = self._markdown_to_notion_blocks(item.research_report)
                    blocks.extend(content_blocks)
                else:
                    blocks.append({
                        "type": "callout",
                        "callout": {
                            "rich_text": [{
                                "type": "text",
                                "text": {"content": f"研究失败: {item.error or '未知错误'}"[:2000]}
                            }],
                            "icon": {"emoji": "⚠️"},
                            "color": "red_background"
                        }
                    })

                # Divider between items
                blocks.append({"type": "divider", "divider": {}})

        return blocks

    def _markdown_to_notion_blocks(self, markdown: str) -> list[dict]:
        """Convert markdown text to Notion blocks (simplified)."""
        blocks = []
        lines = markdown.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            if not line:
                i += 1
                continue

            # Heading 2
            if line.startswith("## "):
                blocks.append({
                    "type": "heading_3",  # Use h3 since we're nested
                    "heading_3": {
                        "rich_text": [{"type": "text", "text": {"content": line[3:][:100]}}]
                    }
                })
            # Heading 3
            elif line.startswith("### "):
                blocks.append({
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{
                            "type": "text",
                            "text": {"content": line[4:][:2000]},
                            "annotations": {"bold": True}
                        }]
                    }
                })
            # Bullet point
            elif line.startswith("- "):
                blocks.append({
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": line[2:][:2000]}}]
                    }
                })
            # Numbered list
            elif line and line[0].isdigit() and ". " in line:
                content = line.split(". ", 1)[1] if ". " in line else line
                blocks.append({
                    "type": "numbered_list_item",
                    "numbered_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": content[:2000]}}]
                    }
                })
            # Quote
            elif line.startswith("> "):
                blocks.append({
                    "type": "quote",
                    "quote": {
                        "rich_text": [{"type": "text", "text": {"content": line[2:][:2000]}}]
                    }
                })
            # Regular paragraph
            else:
                blocks.append({
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": line[:2000]}}]
                    }
                })

            i += 1

        return blocks
