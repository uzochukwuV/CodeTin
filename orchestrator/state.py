"""Project state management for CodeTin.

Tracks active projects, sandbox containers, workspace paths,
and maps execution IDs to WebSocket connections for streaming.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProjectState:
    """Runtime state for a project."""

    project_id: str
    name: str
    language: str
    workspace_path: str = ""
    sandbox_port: int = 0
    active_executions: dict[str, dict] = field(default_factory=dict)


class StateManager:
    """Manages project state and execution-to-WebSocket mappings."""

    def __init__(self):
        self._projects: dict[str, ProjectState] = {}
        self._execution_streams: dict[str, asyncio.Queue] = {}

    def create_project(self, project_id: str, name: str, language: str, workspace_path: str) -> ProjectState:
        """Register a new project."""
        state = ProjectState(
            project_id=project_id,
            name=name,
            language=language,
            workspace_path=workspace_path,
        )
        self._projects[project_id] = state
        return state

    def get_project(self, project_id: str) -> ProjectState | None:
        """Get project state."""
        return self._projects.get(project_id)

    def delete_project(self, project_id: str) -> None:
        """Remove project state."""
        self._projects.pop(project_id, None)

    def list_projects(self) -> list[ProjectState]:
        """List all projects."""
        return list(self._projects.values())

    def update_project(self, project_id: str, **kwargs) -> ProjectState | None:
        """Update project state fields."""
        state = self._projects.get(project_id)
        if state:
            for key, value in kwargs.items():
                if hasattr(state, key):
                    setattr(state, key, value)
        return state

    def add_execution(self, project_id: str, execution_id: str, data: dict) -> None:
        """Track an active execution."""
        state = self._projects.get(project_id)
        if state:
            state.active_executions[execution_id] = data

    def remove_execution(self, project_id: str, execution_id: str) -> None:
        """Remove a completed execution."""
        state = self._projects.get(project_id)
        if state:
            state.active_executions.pop(execution_id, None)

    async def create_stream(self, execution_id: str) -> asyncio.Queue:
        """Create a stream queue for agent progress events."""
        queue: asyncio.Queue = asyncio.Queue()
        self._execution_streams[execution_id] = queue
        return queue

    async def push_event(self, execution_id: str, event: dict) -> None:
        """Push an event to a stream."""
        queue = self._execution_streams.get(execution_id)
        if queue:
            await queue.put(event)

    async def get_stream(self, execution_id: str) -> asyncio.Queue | None:
        """Get the stream queue for an execution."""
        return self._execution_streams.get(execution_id)

    def close_stream(self, execution_id: str) -> None:
        """Close and remove a stream."""
        queue = self._execution_streams.pop(execution_id, None)
        if queue:
            # Sentinel to wake up any waiting consumers
            queue.put_nowait(None)


# Singleton instance
state = StateManager()
