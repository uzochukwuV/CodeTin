"""Shared context/memory passed through the agent pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SharedContext:
    """Shared memory passed between agents in the pipeline.

    Each phase reads from and writes to this context so findings,
    file changes, review feedback, and test results propagate
    through the orchestration.
    """

    # Core project info
    project_id: str = ""
    work_dir: str = ""
    language: str = ""

    # Researcher findings
    findings: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    research_summary: str = ""

    # Coder output
    files_changed: list[str] = field(default_factory=list)
    code_summary: str = ""

    # Reviewer feedback
    review_comments: list[dict] = field(default_factory=list)
    review_approved: bool = False
    review_summary: str = ""

    # Testing results
    test_passed: bool = False
    test_output: str = ""

    # Organiser planning
    plan_steps: list[dict] = field(default_factory=list)
    complexity: str = "medium"

    # Arbitrary extras for custom data
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a dict for logging or API response."""
        return {
            "project_id": self.project_id,
            "work_dir": self.work_dir,
            "language": self.language,
            "findings_count": len(self.findings),
            "files_changed": self.files_changed,
            "review_approved": self.review_approved,
            "test_passed": self.test_passed,
            "plan_steps_count": len(self.plan_steps),
        }
