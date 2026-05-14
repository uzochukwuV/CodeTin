"""Reviewer Agent — reviews code for quality, correctness, and best practices."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from orchestrator.agents.context import SharedContext
from orchestrator.agents.loop import AgentOutput, ReActLoop
from orchestrator.agents.tools.file_tools import GrepTool, ListFilesTool, ReadFileTool
from orchestrator.agents.tools.exec_tools import RunCommandTool

logger = logging.getLogger(__name__)

REVIEWER_SYSTEM = """You are a Reviewer agent. Your job is to review code changes for correctness, style, security, and best practices.

Guidelines:
- Use list_files to see the project structure
- Use read_file to examine changed files
- Use grep to find related code that might be affected
- Use run_command to run linters, type checkers, or formatters if available
- Check for: bugs, security issues, style inconsistencies, missing error handling
- Provide specific, actionable feedback with file and line references

When done, provide your review verdict (approved or needs changes) with detailed comments.
"""


@dataclass
class ReviewComment:
    file: str
    line: int
    severity: str  # "error", "warning", "info"
    message: str


@dataclass
class ReviewResult:
    approved: bool
    comments: list[ReviewComment] = field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None


class ReviewerAgent:
    """Reviews code changes for correctness, style, security, and best practices.

    Uses a ReActLoop to autonomously explore and review the codebase.
    """

    def __init__(self, client: Any = None):
        self.client = client

    async def review(self, task: str, context: SharedContext | dict) -> ReviewResult:
        """Review code changes.

        Args:
            task: Original task description.
            context: SharedContext or dict with work_dir, project_id.

        Returns:
            ReviewResult with approval status and comments.
        """
        if isinstance(context, dict):
            work_dir = context.get("work_dir", ".")
            project_id = context.get("project_id", "")
        else:
            work_dir = context.work_dir
            project_id = context.project_id

        logger.info(f"ReviewerAgent: reviewing changes in {work_dir}")

        tools = {
            "read_file": ReadFileTool(work_dir=work_dir),
            "list_files": ListFilesTool(work_dir=work_dir),
            "grep": GrepTool(work_dir=work_dir),
        }

        if project_id:
            tools["run_command"] = RunCommandTool(project_id=project_id)

        review_task = (
            f"Review the code changes for this task: {task}\n\n"
            "Examine the project structure, read the relevant files, "
            "and provide a thorough review. Check for bugs, security issues, "
            "style inconsistencies, and best practices violations."
        )

        loop = ReActLoop(client=self.client, tools=tools, max_iterations=15)
        result: AgentOutput = await loop.run(REVIEWER_SYSTEM, review_task)

        if result.success:
            approved = "approved" in result.final_answer.lower() and "needs" not in result.final_answer.lower()
            return ReviewResult(
                approved=approved,
                summary=result.final_answer,
            )
        else:
            return ReviewResult(approved=False, error=result.error)
