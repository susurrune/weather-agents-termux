"""Skill: Web Researcher — deep searching and multi-source fact gathering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from weather_agents.core.skill import Skill
from weather_agents.core.tool import Tool, ToolParameter

if TYPE_CHECKING:
    from weather_agents.core.tool import ToolRegistry


def _make_fetch_page_handler():
    """Create the fetch_page handler — fetches and extracts text from a web page."""

    async def fetch_page(url: str, extract_text: bool = True) -> str:
        import re

        try:
            import httpx
        except ImportError:
            return "Error: httpx not available"

        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "WeatherAgents/1.0 WebResearcher"},
                )
                if resp.status_code != 200:
                    return f"HTTP {resp.status_code}: Could not fetch {url}"

                if not extract_text:
                    return resp.text[:5000]

                # Extract visible text from HTML
                html = resp.text
                # Remove scripts and styles
                html = re.sub(
                    r"<(script|style|noscript|iframe|svg)[^>]*>.*?</\1>",
                    "",
                    html,
                    flags=re.DOTALL | re.IGNORECASE,
                )
                # Remove HTML tags
                html = re.sub(r"<[^>]+>", " ", html)
                # Collapse whitespace
                html = re.sub(r"\s+", " ", html).strip()
                # Decode common entities
                html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                html = html.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")

                return html[:3000] if len(html) > 3000 else html
        except Exception as e:
            return f"Error fetching {url}: {e}"

    return fetch_page


def create_skill() -> Skill:
    return Skill(
        name="web_research",
        description="Deep web searching, multi-source fact gathering, cross-reference verification",
        required_tools=["web_search", "http_get", "read_file"],
        handler=lambda agent, registry: _inject_fetch_tool(registry),
        system_prompt="""## Skill: Web Researcher
You have activated the Web Researcher skill. In this mode:
1. Understand the research objective first, then determine keywords and search strategy
2. Collect information from multiple sources and cross-verify facts
3. Label confidence levels and sources for all information
4. Produce structured analysis reports with citations
5. Clearly identify contradictions and uncertainty in information
6. Use the `fetch_page` tool to extract and read full page content beyond search snippets""",
    )


def _inject_fetch_tool(registry: ToolRegistry) -> list[Tool]:
    tool = Tool(
        name="fetch_page",
        description="Fetch a web page and extract its visible text content. Strips HTML tags, scripts, and styles.",
        parameters=[
            ToolParameter(
                name="url", type="string", description="URL of the page to fetch", required=True
            ),
            ToolParameter(
                name="extract_text",
                type="boolean",
                description="Extract visible text from HTML? (default: true)",
                required=False,
            ),
        ],
        handler=_make_fetch_page_handler(),
    )
    registry.register(tool)
    return [tool]
