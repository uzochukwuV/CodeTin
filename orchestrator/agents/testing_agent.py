"""Testing Agent — writes and runs tests to verify code correctness."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from orchestrator.agents.context import SharedContext
from orchestrator.agents.loop import AgentOutput, ReActLoop
from orchestrator.agents.tools.file_tools import ListFilesTool, ReadFileTool, WriteFileTool
from orchestrator.agents.tools.exec_tools import RunCommandTool

logger = logging.getLogger(__name__)

TESTING_SYSTEM = """You are a Testing agent. Your job is to write and execute tests to verify code correctness.

Guidelines:
- Use read_file to understand the code being tested
- Use list_files to discover existing test files
- Use write_file to create new test files
- Use run_command to execute test suites (npm test, pytest, go test, etc.)
- Write tests that cover: happy path, edge cases, error handling
- Report which tests pass and which fail

When done, summarize the test results.
"""


@dataclass
class TestResult:
    passed: bool
    total: int = 0
    failures: int = 0
    output: str = ""
    error: Optional[str] = None


class TestingAgent:
    """Generates and executes tests to verify code correctness.

    Uses a ReActLoop to autonomously explore, write, and run tests.
    """

    def __init__(self, client: Any = None):
        self.client = client

    async def test(self, task: str, context: SharedContext | dict) -> TestResult:
        """Generate and run tests for the given code.

        Args:
            task: Original task description.
            context: SharedContext or dict with work_dir, project_id.

        Returns:
            TestResult with pass/fail status and output.
        """
        if isinstance(context, dict):
            work_dir = context.get("work_dir", ".")
            project_id = context.get("project_id", "")
        else:
            work_dir = context.work_dir
            project_id = context.project_id

        logger.info(f"TestingAgent: testing code for task={task[:80]}...")

        tools = {
            "read_file": ReadFileTool(work_dir=work_dir),
            "write_file": WriteFileTool(work_dir=work_dir),
            "list_files": ListFilesTool(work_dir=work_dir),
        }

        if project_id:
            tools["run_command"] = RunCommandTool(project_id=project_id)

        testing_task = (
            f"Write and run tests for this task: {task}\n\n"
            "Explore the codebase, write comprehensive tests, "
            "and execute them. Report the results."
        )

        loop = ReActLoop(client=self.client, tools=tools, max_iterations=15)
        result: AgentOutput = await loop.run(TESTING_SYSTEM, testing_task)

        if result.success:
            passed = "fail" not in result.final_answer.lower().split("error")[:1]
            return TestResult(
                passed=passed,
                output=result.final_answer,
            )
        else:
            return TestResult(passed=False, error=result.error)
