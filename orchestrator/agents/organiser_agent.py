"""Organiser Agent — orchestrates the other 4 agents in a coordinated workflow."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from orchestrator.agents.context import SharedContext
from orchestrator.agents.loop import AgentOutput, ReActLoop
from orchestrator.agents.tools.file_tools import GrepTool, ListFilesTool, ReadFileTool
from orchestrator.agents.coder_agent import CoderAgent, CoderResult
from orchestrator.agents.reviewer_agent import ReviewerAgent, ReviewResult
from orchestrator.agents.testing_agent import TestingAgent, TestResult
from orchestrator.agents.researcher_agent import ResearcherAgent, ResearchResult

logger = logging.getLogger(__name__)

ORGANISER_SYSTEM = """You are an Organiser agent. Your job is to plan and decompose tasks into execution steps.

Guidelines:
- Use list_files to understand the project scope
- Use read_file to check key files (package.json, requirements.txt, etc.)
- Use grep to find existing patterns and conventions
- Break the task into ordered, actionable steps
- Estimate complexity (low, medium, high)

When done, provide the execution plan with steps.
"""


@dataclass
class OrchestrationResult:
    success: bool
    phases_completed: list[str] = field(default_factory=list)
    output: str = ""
    error: Optional[str] = None
    duration_ms: float = 0
    context: Optional[SharedContext] = None


class OrganiserAgent:
    """Main orchestrator that coordinates the 5-agent pipeline.

    Pipeline:
    1. Organiser — plans and decomposes the task
    2. Researcher — gathers patterns and best practices
    3. Coder — implements the code
    4. Reviewer — reviews the changes
    5. Testing — writes and runs tests

    The Organiser runs the main loop, handles failures with retries,
    and manages the coder <-> reviewer fix loop.
    """

    MAX_REVIEW_CYCLES = 3  # Max coder <-> reviewer iterations

    def __init__(self, client: Any = None):
        self.client = client
        self.researcher = ResearcherAgent(client)
        self.coder = CoderAgent(client)
        self.reviewer = ReviewerAgent(client)
        self.tester = TestingAgent(client)

    async def orchestrate(self, task: str, context: SharedContext | dict) -> OrchestrationResult:
        """Execute the full 5-agent pipeline.

        Args:
            task: Natural language task description.
            context: SharedContext or dict with project info.

        Returns:
            OrchestrationResult with overall status and output.
        """
        start = time.monotonic()
        logger.info(f"OrganiserAgent: starting orchestration for task={task[:80]}...")

        # Normalize context
        if isinstance(context, dict):
            ctx = SharedContext(
                project_id=context.get("project_id", ""),
                work_dir=context.get("work_dir", "."),
                language=context.get("language", ""),
            )
        else:
            ctx = context

        phases_completed = []

        try:
            # Phase 1: Organiser plans
            plan = await self._plan(task, ctx)
            if not plan:
                return OrchestrationResult(
                    success=False, error="Failed to create execution plan",
                    phases_completed=phases_completed,
                    duration_ms=(time.monotonic() - start) * 1000,
                    context=ctx,
                )
            ctx.plan_steps = plan.get("steps", [])
            ctx.complexity = plan.get("complexity", "medium")
            phases_completed.append("plan")

            # Phase 2: Researcher gathers info
            research = await self.researcher.research(task, ctx)
            if not research.success:
                return OrchestrationResult(
                    success=False, error=f"Research failed: {research.error}",
                    phases_completed=phases_completed,
                    duration_ms=(time.monotonic() - start) * 1000,
                    context=ctx,
                )
            ctx.research_summary = research.summary
            phases_completed.append("research")

            # Phase 3: Coder implements (with reviewer loop)
            coder_result = None
            review: ReviewResult | None = None
            for cycle in range(self.MAX_REVIEW_CYCLES):
                if cycle == 0:
                    coder_task = (
                        f"Task: {task}\n\n"
                        f"Research summary: {ctx.research_summary}\n\n"
                        f"Plan steps: {ctx.plan_steps}"
                    )
                else:
                    coder_task = (
                        f"Fix the review comments from the previous attempt:\n\n"
                        f"{review.summary if review else 'No feedback available'}"
                    )

                coder_result = await self.coder.execute(coder_task, ctx)
                if not coder_result.success:
                    return OrchestrationResult(
                        success=False, error=f"Coding failed: {coder_result.error}",
                        phases_completed=phases_completed,
                        duration_ms=(time.monotonic() - start) * 1000,
                        context=ctx,
                    )

                ctx.files_changed = coder_result.files_changed
                ctx.code_summary = coder_result.output

                # Review the changes
                review = await self.reviewer.review(task, ctx)
                if review.approved:
                    ctx.review_approved = True
                    ctx.review_summary = review.summary
                    ctx.review_comments = review.comments
                    phases_completed.append("code")
                    phases_completed.append("review")
                    break

                ctx.review_comments = review.comments
                logger.info(
                    f"OrganiserAgent: review cycle {cycle + 1}/{self.MAX_REVIEW_CYCLES} "
                    f"not approved, re-coding"
                )
            else:
                return OrchestrationResult(
                    success=False,
                    error=f"Failed to pass review after {self.MAX_REVIEW_CYCLES} cycles",
                    phases_completed=phases_completed,
                    duration_ms=(time.monotonic() - start) * 1000,
                    context=ctx,
                )

            # Phase 5: Testing
            test_result = await self.tester.test(task, ctx)
            if not test_result.passed:
                return OrchestrationResult(
                    success=False, error=f"Tests failed: {test_result.output}",
                    phases_completed=phases_completed,
                    duration_ms=(time.monotonic() - start) * 1000,
                    context=ctx,
                )
            ctx.test_passed = True
            ctx.test_output = test_result.output
            phases_completed.append("testing")

            return OrchestrationResult(
                success=True,
                phases_completed=phases_completed,
                output=f"All phases completed: {', '.join(phases_completed)}",
                duration_ms=(time.monotonic() - start) * 1000,
                context=ctx,
            )

        except Exception as e:
            logger.error(f"OrganiserAgent: orchestration failed: {e}")
            return OrchestrationResult(
                success=False, error=str(e),
                phases_completed=phases_completed,
                duration_ms=(time.monotonic() - start) * 1000,
                context=ctx,
            )

    async def _plan(self, task: str, ctx: SharedContext) -> Optional[dict]:
        """Decompose the task into an execution plan using the ReActLoop."""
        tools = {
            "list_files": ListFilesTool(work_dir=ctx.work_dir),
            "read_file": ReadFileTool(work_dir=ctx.work_dir),
            "grep": GrepTool(work_dir=ctx.work_dir),
        }

        plan_task = f"Plan the execution of this task: {task}\n\nExamine the project structure and provide ordered steps."

        loop = ReActLoop(client=self.client, tools=tools, max_iterations=10)
        result: AgentOutput = await loop.run(ORGANISER_SYSTEM, plan_task)

        if result.success:
            return {
                "steps": [{"description": result.final_answer}],
                "complexity": "medium",
            }
        return None
