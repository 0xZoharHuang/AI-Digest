"""TLDR Summary Agent for generating concise summaries from research reports."""

from rich.console import Console
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

console = Console()


SUMMARY_PROMPT = """请将以下研究报告压缩为 2-3 句话的精炼摘要。

要求：
1. 保留最核心的发现、观点或价值
2. 使用简洁有力的中文表达
3. 不要使用"本文"、"该研究"、"这篇"等开头
4. 直接陈述事实和关键信息
5. 如有重要的数据/数字，保留它们

标题: {title}

研究报告（部分）:
{report_content}

请直接输出摘要，不要有任何前缀或解释："""


class SummaryAgent:
    """Agent for generating TLDR summaries from research reports."""

    def __init__(self, model: str = "haiku"):
        """
        Initialize summary agent.

        Args:
            model: Model to use - "haiku" (recommended for cost), "sonnet", or "opus"
        """
        self.model = model

    async def generate_tldr(
        self,
        title: str,
        research_report: str,
        max_chars: int = 3000,
    ) -> str:
        """
        Generate a TLDR summary from a research report.

        Args:
            title: Title of the research
            research_report: Full research report in markdown
            max_chars: Max chars of report to include in prompt

        Returns:
            TLDR summary (2-3 sentences)
        """
        # Truncate report to avoid token limits
        report_content = research_report[:max_chars]
        if len(research_report) > max_chars:
            report_content += "\n\n[... 内容过长，已截断 ...]"

        prompt = SUMMARY_PROMPT.format(
            title=title,
            report_content=report_content,
        )

        try:
            response_text = ""
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    allowed_tools=[],  # No tools needed
                    permission_mode="bypassPermissions",
                    model=self.model,
                    max_turns=1,  # Single turn
                )
            ):
                if isinstance(message, AssistantMessage):
                    if hasattr(message, "content"):
                        for block in message.content:
                            if hasattr(block, "text"):
                                response_text = block.text
                elif isinstance(message, ResultMessage):
                    if message.subtype == "success":
                        console.print(f"[dim]TLDR cost: ${message.total_cost_usd:.4f}[/dim]")

            # Clean up the response
            tldr = response_text.strip()
            # Remove any markdown formatting that might have been added
            if tldr.startswith(("```", "**TLDR**", "摘要：")):
                lines = tldr.split("\n")
                tldr = "\n".join(line for line in lines if not line.startswith(("```", "**", "#")))
                tldr = tldr.strip()

            return tldr if tldr else "摘要生成失败"

        except Exception as e:
            console.print(f"[yellow]TLDR generation failed: {e}[/yellow]")
            return f"摘要生成失败: {str(e)[:50]}"

    async def generate_tldr_batch(
        self,
        items: list[dict],
    ) -> list[str]:
        """
        Generate TLDR summaries for multiple items.

        Args:
            items: List of dicts with 'title' and 'research_report' keys

        Returns:
            List of TLDR summaries
        """
        results = []
        for item in items:
            tldr = await self.generate_tldr(
                title=item.get("title", ""),
                research_report=item.get("research_report", ""),
            )
            results.append(tldr)
        return results
