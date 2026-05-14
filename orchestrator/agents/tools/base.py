"""Tool abstraction layer for autonomous agents."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


MAX_OUTPUT_CHARS = 8000


class ToolResult:
    """Result of a tool execution."""

    def __init__(self, success: bool, output: str, error: str | None = None, truncated: bool = False):
        self.success = success
        self.output = output
        self.error = error
        self.truncated = truncated

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "truncated": self.truncated,
        }

    def __repr__(self) -> str:
        if self.success:
            return f"ToolResult(ok, output={self.output[:100]!r})"
        return f"ToolResult(err, error={self.error!r})"


class Tool(abc.ABC):
    """Base class for all agent tools."""

    name: str = ""
    description: str = ""
    input_schema: type[BaseModel] | None = None

    @abc.abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with the given arguments."""
        ...

    @staticmethod
    def truncate(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
        """Truncate text to fit within context limits. Returns (text, was_truncated)."""
        if len(text) <= max_chars:
            return text, False
        return text[:max_chars] + f"\n... [truncated, {len(text)} chars total]", True


class ToolRegistry:
    """Simple registry that maps tool names to instances."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> dict[str, dict]:
        """Return tool descriptions for LLM prompt building."""
        result = {}
        for name, tool in self._tools.items():
            schema = {}
            if tool.input_schema:
                schema = tool.input_schema.model_json_schema()
            result[name] = {
                "description": tool.description,
                "schema": schema,
            }
        return result

    def tool_descriptions(self) -> str:
        """Return a human-readable tool description string for system prompts."""
        lines = ["Available tools:"]
        for name, info in self.list_tools().items():
            lines.append(f"- {name}: {info['description']}")
        return "\n".join(lines)
