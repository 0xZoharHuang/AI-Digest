"""Agent tools for deep research.

The current implementation uses Claude Agent SDK's built-in tools:
- WebFetch: Fetch and parse web content (articles, README, docs)
- WebSearch: Search the web for background information

These built-in tools are sufficient for MVP. Custom tools (read_repo,
analyze_paper, analyze_image) can be added later if more specialized
functionality is needed.
"""

# No custom tools needed - using Claude Agent SDK built-in tools
__all__ = []
