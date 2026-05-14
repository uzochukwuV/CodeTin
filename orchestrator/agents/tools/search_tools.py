"""Search tools for agents — web search, URL fetch, repo search."""

from __future__ import annotations

import os

import httpx
from pydantic import BaseModel, Field

from orchestrator.agents.tools.base import Tool, ToolResult


class WebSearchInput(BaseModel):
    query: str = Field(description="Search query")
    num_results: int = Field(default=5, description="Number of results (default: 5)")


class WebSearchTool(Tool):
    """Search the web using Exa API or fallback to a simple HTTP search.

    Requires EXA_API_KEY env var. Falls back to returning a message
    indicating the key is not set.
    """

    name = "web_search"
    description = "Search the web for information. Returns relevant results with URLs and snippets."
    input_schema = WebSearchInput

    async def execute(self, query: str, num_results: int = 5) -> ToolResult:
        api_key = os.environ.get("EXA_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="EXA_API_KEY not set. Web search unavailable.",
            )

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.exa.ai/search",
                    headers={
                        "x-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": query,
                        "numResults": num_results,
                        "useAutoprompt": True,
                    },
                )
                data = resp.json()
                results = []
                for r in data.get("results", []):
                    results.append(f"[{r.get('title', 'N/A')}]({r.get('url', 'N/A')})\n{r.get('text', '')[:300]}")
                output = "\n\n---\n\n".join(results) if results else "No results found"
                text, truncated = self.truncate(output)
                return ToolResult(success=True, output=text, truncated=truncated)
        except Exception as e:
            return ToolResult(success=False, error=f"Web search failed: {e}")


class FetchUrlInput(BaseModel):
    url: str = Field(description="URL to fetch and read")


class FetchUrlTool(Tool):
    """Fetch a webpage and return its text content."""

    name = "fetch_url"
    description = "Fetch a URL and return its text content. Useful for reading documentation, APIs, etc."
    input_schema = FetchUrlInput

    async def execute(self, url: str) -> ToolResult:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                # Simple HTML-to-text: strip tags
                import re
                text = re.sub(r"<[^>]+>", " ", resp.text)
                text = re.sub(r"\s+", " ", text).strip()
                output, truncated = self.truncate(text)
                return ToolResult(success=True, output=output, truncated=truncated)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to fetch URL: {e}")


class RepoSearchInput(BaseModel):
    pattern: str = Field(description="Text or regex to search for")
    path: str = Field(default="", description="Subdirectory to search in (default: root)")
    file_pattern: str = Field(default="*", description="File glob filter (default: all files)")


class RepoSearchTool(Tool):
    """Search across the repo for files and content matching a pattern.

    Combines glob + grep for comprehensive repo-wide search.
    """

    name = "repo_search"
    description = "Search the entire repo for files and content matching a pattern. Returns file paths and matching lines."
    input_schema = RepoSearchInput

    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    async def execute(self, pattern: str, path: str = "", file_pattern: str = "*") -> ToolResult:
        import fnmatch
        import re

        search_dir = os.path.join(self.work_dir, path) if path else self.work_dir
        if not os.path.isdir(search_dir):
            return ToolResult(success=False, error=f"Directory not found: {path or '.'}")

        try:
            regex = re.compile(pattern, re.IGNORECASE)
            file_matches = []
            content_matches = []

            for root, dirs, files in os.walk(search_dir):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv", "dist", "build")]
                for fname in files:
                    if file_pattern != "*" and not fnmatch.fnmatch(fname, file_pattern):
                        continue
                    fpath = os.path.join(root, fname)
                    rel = os.path.relpath(fpath, self.work_dir)
                    if regex.search(rel):
                        file_matches.append(rel)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            for i, line in enumerate(f, 1):
                                if regex.search(line):
                                    content_matches.append(f"{rel}:{i}: {line.rstrip()}")
                    except (UnicodeDecodeError, PermissionError):
                        continue

            lines = []
            if file_matches:
                lines.append(f"Files matching pattern ({len(file_matches)}):")
                lines.extend(f"  {m}" for m in file_matches[:50])
            if content_matches:
                lines.append(f"\nContent matches ({len(content_matches)}):")
                lines.extend(f"  {m}" for m in content_matches[:100])
            if not lines:
                lines.append("No matches found")

            output = "\n".join(lines)
            text, truncated = self.truncate(output)
            return ToolResult(success=True, output=text, truncated=truncated)
        except re.error as e:
            return ToolResult(success=False, error=f"Invalid regex: {e}")
