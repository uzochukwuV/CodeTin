"""Docker sandbox manager for CodeTin.

Each project gets an isolated Docker container with:
- Language-specific base image
- Workspace volume mount at /workspace
- Dynamic port mapping for app preview
- Resource limits (memory, CPU)
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Language → base image mapping
LANGUAGE_IMAGES: dict[str, str] = {
    "python": "python:3.12-slim",
    "node": "node:20-slim",
    "go": "golang:1.22-slim",
    "rust": "rust:1.75-slim",
    "ruby": "ruby:3.3-slim",
    "java": "eclipse-temurin:21-jre",
}

# Default preview ports per language (what the dev server typically listens on)
LANGUAGE_PREVIEW_PORTS: dict[str, int] = {
    "python": 8000,
    "node": 3000,
    "go": 8080,
    "rust": 8000,
    "ruby": 4567,
    "java": 8080,
}

WORKSPACES_ROOT = os.environ.get("CODETIN_WORKSPACES", "/workspaces")

# Port range for preview mapping
PREVIEW_PORT_START = int(os.environ.get("CODETIN_PREVIEW_PORT_START", "40000"))
PREVIEW_PORT_END = int(os.environ.get("CODETIN_PREVIEW_PORT_END", "40100"))


@dataclass
class SandboxInfo:
    """Metadata about an active sandbox container."""

    project_id: str
    language: str
    container_id: str | None = None
    workspace_path: str = ""
    preview_host_port: int = 0
    preview_container_port: int = 0
    status: str = "stopped"


@dataclass
class ExecResult:
    """Result of a command executed in a sandbox."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


class SandboxManager:
    """Manages Docker containers for code execution sandboxes."""

    def __init__(self):
        self._projects: dict[str, SandboxInfo] = {}
        self._used_ports: set[int] = set()
        self._docker: Any | None = None
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize Docker SDK connection. Returns True if successful."""
        try:
            import docker
            self._docker = docker.from_env()
            # Test connection
            self._docker.ping()
            self._initialized = True
            logger.info("Docker connection established")
            return True
        except Exception as e:
            logger.warning(f"Docker not available: {e}. Using subprocess fallback.")
            self._docker = None
            self._initialized = True  # Mark as initialized even without Docker
            return False

    def _get_free_port(self) -> int:
        """Allocate a free port from the preview range."""
        for port in range(PREVIEW_PORT_START, PREVIEW_PORT_END):
            if port not in self._used_ports:
                # Verify it's actually free
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        s.bind(("", port))
                        self._used_ports.add(port)
                        return port
                    except OSError:
                        continue
        raise RuntimeError("No free preview ports available")

    async def create(self, project_id: str, language: str = "node") -> SandboxInfo:
        """Create a sandboxed container for a project.

        If Docker is not available, creates a workspace directory only
        (commands will run via subprocess in the workspace).
        """
        workspace_path = os.path.join(WORKSPACES_ROOT, project_id)
        os.makedirs(workspace_path, exist_ok=True)

        info = SandboxInfo(
            project_id=project_id,
            language=language,
            workspace_path=workspace_path,
            preview_container_port=LANGUAGE_PREVIEW_PORTS.get(language, 3000),
        )

        if self._docker is None:
            # Fallback: no Docker, just use workspace directory
            info.status = "ready"
            self._projects[project_id] = info
            logger.info(f"Workspace created for {project_id} (no Docker)")
            return info

        image = LANGUAGE_IMAGES.get(language, LANGUAGE_IMAGES["node"])
        host_port = self._get_free_port()
        container_port = info.preview_container_port

        try:
            # Pull image if not present
            try:
                self._docker.images.get(image)
            except Exception:
                logger.info(f"Pulling {image}...")
                self._docker.images.pull(image)

            container = self._docker.containers.run(
                image,
                name=f"codetin-{project_id}",
                detach=True,
                volumes={workspace_path: {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                ports={f"{container_port}/tcp": host_port},
                mem_limit="512m",
                nano_cpus=1_000_000_000,  # 1 CPU
                network_disabled=False,
                stdin_open=True,
                tty=True,
                remove=False,
                command="tail -f /dev/null",  # Keep container alive
            )

            info.container_id = container.id
            info.preview_host_port = host_port
            info.status = "running"
            self._projects[project_id] = info

            logger.info(f"Sandbox created: {project_id} on port {host_port}")
            return info

        except Exception as e:
            logger.error(f"Failed to create sandbox for {project_id}: {e}")
            # Fallback to directory-only mode
            info.status = "ready"
            self._projects[project_id] = info
            return info

    async def exec(self, project_id: str, cmd: str | list[str]) -> ExecResult:
        """Execute a command in the sandbox container."""
        info = self._projects.get(project_id)
        if not info:
            return ExecResult(stderr=f"Project {project_id} not found", exit_code=1)

        if isinstance(cmd, str):
            cmd_str = cmd
        else:
            cmd_str = " ".join(cmd)

        if self._docker and info.container_id and info.status == "running":
            return await self._exec_docker(info, cmd_str)
        else:
            return await self._exec_subprocess(info, cmd_str)

    async def _exec_docker(self, info: SandboxInfo, cmd: str) -> ExecResult:
        """Execute command via Docker container exec."""
        try:
            container = self._docker.containers.get(info.container_id)
            result = container.exec_run(cmd, workdir="/workspace", demux=True)
            exit_code = result.exit_code or 0
            stdout = (result.output[0] or b"").decode("utf-8", errors="replace")
            stderr = (result.output[1] or b"").decode("utf-8", errors="replace")
            return ExecResult(stdout=stdout, stderr=stderr, exit_code=exit_code)
        except Exception as e:
            return ExecResult(stderr=f"Docker exec error: {e}", exit_code=1)

    async def _exec_subprocess(self, info: SandboxInfo, cmd: str) -> ExecResult:
        """Execute command via subprocess in workspace directory."""
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=info.workspace_path,
                env={**os.environ, "PATH": os.environ.get("PATH", "")},
            )
            stdout, stderr = await proc.communicate()
            return ExecResult(
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                exit_code=proc.returncode or 0,
            )
        except Exception as e:
            return ExecResult(stderr=f"Subprocess error: {e}", exit_code=1)

    async def exec_pty(self, project_id: str):
        """Return a PTY-compatible process for terminal streaming.

        Returns an asyncio subprocess with connected stdin/stdout/stderr.
        The caller is responsible for wiring it to a WebSocket.
        """
        info = self._projects.get(project_id)
        if not info:
            raise ValueError(f"Project {project_id} not found")

        if self._docker and info.container_id and info.status == "running":
            # Use docker exec with -it for PTY
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-i", info.container_id, "bash",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=info.workspace_path,
            )
            return proc
        else:
            # Subprocess fallback
            proc = await asyncio.create_subprocess_exec(
                "bash",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=info.workspace_path,
            )
            return proc

    def get_preview_port(self, project_id: str) -> int:
        """Return the host port for preview proxy."""
        info = self._projects.get(project_id)
        if info and info.preview_host_port:
            return info.preview_host_port
        return 0

    def get_preview_url(self, project_id: str) -> str | None:
        """Return the full preview URL for a project."""
        port = self.get_preview_port(project_id)
        if port:
            return f"http://localhost:{port}"
        return None

    async def destroy(self, project_id: str) -> bool:
        """Stop and remove a sandbox container."""
        info = self._projects.pop(project_id, None)
        if not info:
            return False

        if info.preview_host_port:
            self._used_ports.discard(info.preview_host_port)

        if self._docker and info.container_id:
            try:
                container = self._docker.containers.get(info.container_id)
                container.stop(timeout=5)
                container.remove(force=True)
                logger.info(f"Sandbox destroyed: {project_id}")
            except Exception as e:
                logger.warning(f"Error destroying sandbox for {project_id}: {e}")

        # Clean up workspace directory
        import shutil
        if info.workspace_path and os.path.exists(info.workspace_path):
            shutil.rmtree(info.workspace_path, ignore_errors=True)

        return True

    def get_project(self, project_id: str) -> SandboxInfo | None:
        """Get info for an active project."""
        return self._projects.get(project_id)

    def list_projects(self) -> list[SandboxInfo]:
        """List all active projects."""
        return list(self._projects.values())

    def to_dict(self, info: SandboxInfo) -> dict:
        """Convert SandboxInfo to serializable dict."""
        return {
            "project_id": info.project_id,
            "language": info.language,
            "workspace_path": info.workspace_path,
            "preview_host_port": info.preview_host_port,
            "preview_container_port": info.preview_container_port,
            "status": info.status,
            "preview_url": self.get_preview_url(info.project_id),
        }


# Singleton instance
manager = SandboxManager()
