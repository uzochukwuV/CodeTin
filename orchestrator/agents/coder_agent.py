"""Coder Agent — implements code changes based on specifications."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from orchestrator.agents.context import SharedContext
from orchestrator.agents.loop import AgentOutput, ReActLoop
from orchestrator.agents.tools.file_tools import (
    EditFileTool,
    GlobTool,
    GrepTool,
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
)
from orchestrator.agents.tools.exec_tools import RunCommandTool

logger = logging.getLogger(__name__)

CODER_SYSTEM = """You are a Coder agent. Your job is to write and modify code to fulfill the given task.

Guidelines:
- Use read_file to understand existing code before modifying
- Use write_file to create new files
- Use edit_file for surgical changes
- Use list_files to understand project structure
- Use run_command to verify your changes compile/run
- Use glob and grep to find relevant files
- When done, summarize what you changed

Always read files before editing them to understand the context.
"""


@dataclass
class CoderResult:
    success: bool
    files_changed: list[str] = field(default_factory=list)
    output: str = ""
    error: Optional[str] = None


class CoderAgent:
    """Responsible for writing and modifying code based on task specifications.

    Uses a ReActLoop with file and execution tools to autonomously
    read, write, edit, and verify code changes.
    """

    def __init__(self, client: Any = None):
        self.client = client

    async def execute(self, task: str, context: SharedContext | dict) -> CoderResult:
        """Execute a coding task.

        Args:
            task: Natural language description of what to build/change.
            context: SharedContext or dict with work_dir, project_id.

        Returns:
            CoderResult with files changed and output.
        """
        if isinstance(context, dict):
            work_dir = context.get("work_dir", ".")
            project_id = context.get("project_id", "")
        else:
            work_dir = context.work_dir
            project_id = context.project_id

        logger.info(f"CoderAgent: starting task={task[:80]}...")

        tools = {
            "read_file": ReadFileTool(work_dir=work_dir),
            "write_file": WriteFileTool(work_dir=work_dir),
            "edit_file": EditFileTool(work_dir=work_dir),
            "list_files": ListFilesTool(work_dir=work_dir),
            "glob": GlobTool(work_dir=work_dir),
            "grep": GrepTool(work_dir=work_dir),
        }

        if project_id:
            tools["run_command"] = RunCommandTool(project_id=project_id)

        loop = ReActLoop(client=self.client, tools=tools, max_iterations=20)
        result: AgentOutput = await loop.run(CODER_SYSTEM, task)

        if result.success:
            files_changed = [tc.get("args", {}).get("path", "") for tc in result.tool_calls if tc.get("tool") == "write_file"]
            return CoderResult(
                success=True,
                files_changed=files_changed,
                output=result.final_answer,
            )
        else:
            return CoderResult(success=False, error=result.error)
