"""ReAct-style agent loop for autonomous tool use."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from orchestrator.agents.tools.base import Tool, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


@dataclass
class AgentOutput:
    """Final output from an agent loop run."""
    success: bool
    final_answer: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None


class ReActLoop:
    """Autonomous agent loop using the ReAct (Reasoning + Acting) pattern.

    The loop:
    1. Sends system prompt + task to the LLM
    2. LLM responds with either a tool call or a final answer
    3. If tool call: execute tool, append observation, loop back
    4. If final answer: return result
    5. Stop conditions: LLM signals done, max iterations, error

    Each specialist agent creates its own ReActLoop with its specific tools.
    """

    def __init__(
        self,
        client: Any,
        tools: dict[str, Tool],
        max_iterations: int = 15,
    ):
        self.client = client
        self.tools = tools
        self.registry = ToolRegistry()
        for tool in tools.values():
            self.registry.register(tool)
        self.max_iterations = max_iterations
        self.messages: list[dict] = []
        self.tool_call_history: list[dict] = []

    async def run(self, system_prompt: str, task: str) -> AgentOutput:
        """Run the agent loop until completion or max iterations."""
        logger.info(f"ReActLoop: starting task={task[:80]}...")

        self.messages = [
            {"role": "system", "content": self._build_system_prompt(system_prompt)},
            {"role": "user", "content": task},
        ]

        for iteration in range(self.max_iterations):
            try:
                # Step 1: Call LLM
                response = await self._call_llm()
                logger.debug(f"ReActLoop: iteration {iteration + 1}, response={response[:200]}")

                # Step 2: Parse response for tool call or final answer
                tool_call = self._parse_tool_call(response)
                if tool_call is None:
                    # LLM provided a final answer
                    return AgentOutput(
                        success=True,
                        final_answer=response,
                        tool_calls=self.tool_call_history,
                    )

                # Step 3: Execute the tool
                tool_name = tool_call["name"]
                tool_args = tool_call.get("args", {})
                tool = self.registry.get(tool_name)

                if tool is None:
                    observation = f"Error: Unknown tool '{tool_name}'"
                    logger.warning(f"ReActLoop: unknown tool {tool_name}")
                else:
                    try:
                        result: ToolResult = await tool.execute(**tool_args)
                        observation = result.output if result.success else f"Error: {result.error}"
                        logger.debug(f"ReActLoop: tool {tool_name} result: {observation[:200]}")
                    except Exception as e:
                        observation = f"Error: Tool execution failed: {e}"
                        logger.error(f"ReActLoop: tool {tool_name} failed: {e}")

                # Record the tool call
                self.tool_call_history.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "observation": observation,
                })

                # Step 4: Append to conversation and loop
                self.messages.append({"role": "assistant", "content": response})
                self.messages.append({"role": "user", "content": f"Observation: {observation}"})

            except Exception as e:
                logger.error(f"ReActLoop: iteration {iteration + 1} failed: {e}")
                return AgentOutput(
                    success=False,
                    error=f"Agent loop failed at iteration {iteration + 1}: {e}",
                    tool_calls=self.tool_call_history,
                )

        # Max iterations reached
        return AgentOutput(
            success=False,
            error=f"Max iterations ({self.max_iterations}) reached without completion",
            tool_calls=self.tool_call_history,
        )

    def _build_system_prompt(self, base_prompt: str) -> str:
        """Build the full system prompt with tool descriptions."""
        tool_desc = self.registry.tool_descriptions()
        tool_format = """
When you need to use a tool, respond with JSON:
{"tool": "tool_name", "args": {"arg1": "value1", ...}}

When you are done, respond with your final answer (not JSON).

You can only use the tools listed above. Do not make up tool names.
Execute one tool call per response. Wait for the observation before calling the next tool.
"""
        return f"{base_prompt}\n\n{tool_desc}\n{tool_format}"

    async def _call_llm(self) -> str:
        """Call the LLM and return the response text."""
        if self.client:
            # Use AgentFieldClient
            try:
                response = await self.client.execute_async(
                    node="swe-fast.build",
                    input_data={
                        "messages": self.messages,
                        "max_tokens": 4000,
                    },
                )
                # Extract text from response
                if isinstance(response, dict):
                    return response.get("output", response.get("text", str(response)))
                return str(response)
            except Exception as e:
                logger.error(f"ReActLoop: LLM call failed: {e}")
                raise
        else:
            # Fallback: return a simple observation
            return "No LLM client configured. Returning placeholder response."

    def _parse_tool_call(self, response: str) -> dict | None:
        """Parse the LLM response for a tool call.

        Expects JSON like: {"tool": "name", "args": {...}}
        Returns None if no tool call found (meaning it's a final answer).
        """
        # Try to find JSON in the response
        json_match = re.search(r"\{[^{}]*\"tool\"[^{}]*\}", response, re.DOTALL)
        if not json_match:
            # Also try with ```json fences
            json_match = re.search(r"```json\s*(\{[^`]*\})\s*```", response, re.DOTALL)

        if not json_match:
            return None

        try:
            data = json.loads(json_match.group(1) if json_match.lastindex else json_match.group(0))
            if "tool" in data:
                return {"name": data["tool"], "args": data.get("args", {})}
        except json.JSONDecodeError:
            pass

        return None
