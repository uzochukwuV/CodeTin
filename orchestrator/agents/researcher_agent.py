"""Researcher Agent — gathers information, patterns, and best practices."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from orchestrator.agents.context import SharedContext
from orchestrator.agents.loop import AgentOutput, ReActLoop
from orchestrator.agents.tools.file_tools import ListFilesTool, ReadFileTool
from orchestrator.agents.tools.search_tools import FetchUrlTool, RepoSearchTool, WebSearchTool

logger = logging.getLogger(__name__)

RESEARCHER_SYSTEM = """You are a Researcher agent. Your job is to gather information, best practices, and patterns before coding begins.

Guidelines:
- Use list_files and read_file to understand the existing codebase
- Use web_search to find best practices, libraries, and solutions
- Use fetch_url to read documentation and API docs
- Use repo_search to find relevant patterns in the current repo
- Identify: recommended technologies, common pitfalls, implementation patterns
- Provide actionable findings that will help the Coder agent

When done, summarize your research findings with references.
"""


@dataclass
class ResearchResult:
    success: bool
    findings: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None


class ResearcherAgent:
    """Researches best practices, patterns, and solutions before coding begins.

    Uses a ReActLoop to autonomously search the web and codebase.
    """

    def __init__(self, client: Any = None):
        self.client = client

    async def research(self, task: str, context: SharedContext | dict) -> ResearchResult:
        """Research the task and produce findings.

        Args:
            task: Natural language description of what to build.
            context: SharedContext or dict with work_dir, project_id.

        Returns:
            ResearchResult with findings and recommendations.
        """
        if isinstance(context, dict):
            work_dir = context.get("work_dir", ".")
        else:
            work_dir = context.work_dir

        logger.info(f"ResearcherAgent: researching task={task[:80]}...")

        tools = {
            "web_search": WebSearchTool(),
            "fetch_url": FetchUrlTool(),
            "read_file": ReadFileTool(work_dir=work_dir),
            "list_files": ListFilesTool(work_dir=work_dir),
            "repo_search": RepoSearchTool(work_dir=work_dir),
        }

        research_task = (
            f"Research and analyze this task: {task}\n\n"
            "Explore the existing codebase, search the web for best practices, "
            "and provide actionable findings."
        )

        loop = ReActLoop(client=self.client, tools=tools, max_iterations=15)
        result: AgentOutput = await loop.run(RESEARCHER_SYSTEM, research_task)

        if result.success:
            return ResearchResult(
                success=True,
                findings=[],
                references=[],
                summary=result.final_answer,
            )
        else:
            return ResearchResult(success=False, error=result.error)
