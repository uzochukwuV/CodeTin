"""Command execution tools for agents."""

from __future__ import annotations

import asyncio
import os

from pydantic import BaseModel, Field

from orchestrator.agents.tools.base import Tool, ToolResult


class RunCommandInput(BaseModel):
    cmd: str = Field(description="Shell command to execute")
    timeout: int = Field(default=60, description="Timeout in seconds (default: 60)")


class RunCommandTool(Tool):
    """Execute a command in the sandbox for a project.

    Uses the SandboxManager for execution (Docker or subprocess fallback).
    """

    name = "run_command"
    description = "Execute a shell command in the project sandbox. Returns stdout, stderr, and exit code."
    input_schema = RunCommandInput

    def __init__(self, project_id: str):
        self.project_id = project_id

    async def execute(self, cmd: str, timeout: int = 60) -> ToolResult:
        from sandbox.manager import manager as sandbox_manager

        try:
            result = await asyncio.wait_for(
                sandbox_manager.exec(self.project_id, cmd),
                timeout=float(timeout),
            )
            output = f"exit_code: {result.exit_code}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
            text, truncated = self.truncate(output)
            return ToolResult(
                success=result.exit_code == 0,
                output=text,
                truncated=truncated,
            )
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class RunCommandLocalInput(BaseModel):
    cmd: str = Field(description="Shell command to execute")
    cwd: str = Field(default="", description="Working directory (default: current)")
    timeout: int = Field(default=60, description="Timeout in seconds (default: 60)")


class RunCommandLocalTool(Tool):
    """Execute a command directly on the host (non-sandboxed).

    Used when no sandbox is available or for tooling that must run outside containers.
    """

    name = "run_command_local"
    description = "Execute a shell command directly on the host (non-sandboxed). Use with caution."
    input_schema = RunCommandLocalInput

    def __init__(self, work_dir: str = ""):
        self.work_dir = work_dir or os.getcwd()

    async def execute(self, cmd: str, cwd: str = "", timeout: int = 60) -> ToolResult:
        working = cwd or self.work_dir
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=working,
                ),
                timeout=float(timeout),
            )
            stdout, stderr = await proc.communicate()
            output = f"exit_code: {proc.returncode}\n--- stdout ---\n{stdout.decode()}\n--- stderr ---\n{stderr.decode()}"
            text, truncated = self.truncate(output)
            return ToolResult(
                success=(proc.returncode or 0) == 0,
                output=text,
                truncated=truncated,
            )
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(success=False, error=str(e))
