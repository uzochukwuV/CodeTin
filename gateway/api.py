"""REST API endpoints for CodeTin Gateway."""

from __future__ import annotations

import logging
import os
import shutil
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sandbox.manager import manager as sandbox_manager
from orchestrator.state import state as state_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

WORKSPACES_ROOT = os.environ.get("CODETIN_WORKSPACES", "/workspaces")


# ── Request/Response Models ─────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    name: str = ""
    language: str = "node"


class RunCommandRequest(BaseModel):
    project_id: str
    cmd: str


class WriteFileRequest(BaseModel):
    content: str


class ChatRequest(BaseModel):
    prompt: str
    project_id: str = ""


class ExecuteRequest(BaseModel):
    instruction: str
    project_id: str
    context_files: list[str] = []


class OrchestrateRequest(BaseModel):
    task: str
    project_id: str
    context_files: list[str] = []


# ── Orchestrate (5-Agent Pipeline) ──────────────────────────────────

@router.post("/orchestrate")
async def orchestrate(req: OrchestrateRequest):
    """Execute the full 5-agent pipeline: organise → research → code → review → test."""
    from orchestrator.agents import OrganiserAgent, SharedContext

    project = state_manager.get_project(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ctx = SharedContext(
        project_id=req.project_id,
        work_dir=project.workspace_path,
        language=project.language,
    )

    organiser = OrganiserAgent(client=None)  # TODO: wire AgentFieldClient
    result = await organiser.orchestrate(req.task, ctx)

    return {
        "success": result.success,
        "phases_completed": result.phases_completed,
        "output": result.output,
        "error": result.error,
        "duration_ms": round(result.duration_ms, 2),
    }


# ── Chat (Consumer Mode) ────────────────────────────────────────────

@router.post("/chat")
async def chat(req: ChatRequest):
    """Consumer mode: describe an app, AI builds it."""
    from orchestrator.consumer import build_from_prompt

    # Create project if not specified
    project_id = req.project_id
    if not project_id:
        import uuid as _uuid
        project_id = f"project-{_uuid.uuid4().hex[:8]}"
        await create_project(CreateProjectRequest(name=project_id, language="node"))

    project = state_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Start the build and return the first event (execution_id)
    events = []
    async for event in build_from_prompt(req.prompt, project_id, project.workspace_path):
        events.append(event)
        if event.get("type") in ("error", "build_start"):
            break

    return events[0] if events else {"status": "no_events"}


# ── Execute (Developer Mode) ────────────────────────────────────────

@router.post("/execute")
async def execute(req: ExecuteRequest):
    """Developer mode: AI assist on existing code."""
    from orchestrator.developer import assist

    project = state_manager.get_project(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    events = []
    async for event in assist(req.instruction, req.project_id, project.workspace_path, req.context_files):
        events.append(event)
        if event.get("type") in ("error", "build_start"):
            break

    return events[0] if events else {"status": "no_events"}


# ── Projects ────────────────────────────────────────────────────────────

@router.post("/projects")
async def create_project(req: CreateProjectRequest):
    """Create a new project with sandbox."""
    project_id = req.name.lower().replace(" ", "-") or f"project-{uuid.uuid4().hex[:8]}"

    # Create sandbox
    sandbox = await sandbox_manager.create(project_id, language=req.language)

    # Register in state
    state_manager.create_project(
        project_id=project_id,
        name=req.name or project_id,
        language=req.language,
        workspace_path=sandbox.workspace_path,
    )

    return {
        "project_id": project_id,
        "language": req.language,
        "workspace_path": sandbox.workspace_path,
        "preview_url": sandbox_manager.get_preview_url(project_id),
        "status": sandbox.status,
    }


@router.get("/projects")
async def list_projects():
    """List all active projects."""
    projects = state_manager.list_projects()
    return [
        {
            "project_id": p.project_id,
            "name": p.name,
            "language": p.language,
            "workspace_path": p.workspace_path,
            "preview_url": sandbox_manager.get_preview_url(p.project_id),
            "status": "active",
        }
        for p in projects
    ]


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    """Get project details."""
    project = state_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return {
        "project_id": project.project_id,
        "name": project.name,
        "language": project.language,
        "workspace_path": project.workspace_path,
        "preview_url": sandbox_manager.get_preview_url(project_id),
        "preview_port": sandbox_manager.get_preview_port(project_id),
        "active_executions": project.active_executions,
    }


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project and its sandbox."""
    state_manager.delete_project(project_id)
    await sandbox_manager.destroy(project_id)
    return {"status": "deleted", "project_id": project_id}


# ── Files ───────────────────────────────────────────────────────────────

@router.get("/files")
async def list_files(path: str = "", tree: bool = False):
    """List files in a project workspace.

    If `tree=true`, returns a recursive tree structure.
    """
    workspace = _resolve_workspace(path)
    if not os.path.isdir(workspace):
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    if tree:
        return _build_tree(workspace, os.path.basename(workspace) or path)
    else:
        try:
            entries = os.listdir(workspace)
            files = []
            for entry in sorted(entries):
                full = os.path.join(workspace, entry)
                files.append({
                    "name": entry,
                    "type": "directory" if os.path.isdir(full) else "file",
                    "size": os.path.getsize(full) if os.path.isfile(full) else 0,
                })
            return {"path": path or "/", "entries": files}
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied")


@router.get("/files/{path:path}")
async def read_file(path: str):
    """Read a file in a project workspace."""
    full_path = _resolve_file_path(path)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"path": path, "content": content}
    except UnicodeDecodeError:
        # Binary file
        return {"path": path, "content": "[binary file]", "binary": True}


@router.put("/files/{path:path}")
async def write_file(path: str, req: WriteFileRequest):
    """Write a file in a project workspace."""
    full_path = _resolve_file_path(path)

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(req.content)

    return {"path": path, "status": "written", "size": len(req.content)}


@router.delete("/files/{path:path}")
async def delete_file(path: str):
    """Delete a file or directory in a project workspace."""
    full_path = _resolve_file_path(path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    if os.path.isdir(full_path):
        shutil.rmtree(full_path, ignore_errors=True)
    else:
        os.remove(full_path)

    return {"path": path, "status": "deleted"}


# ── Command Execution ───────────────────────────────────────────────────

@router.post("/run")
async def run_command(req: RunCommandRequest):
    """Run a command in a project's sandbox."""
    result = await sandbox_manager.exec(req.project_id, req.cmd)
    return {
        "project_id": req.project_id,
        "cmd": req.cmd,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
    }


# ── Preview Proxy ───────────────────────────────────────────────────────

@router.get("/preview/{project_id}/{path:path}")
async def preview_proxy(project_id: str, path: str = ""):
    """Proxy requests to a project's preview server."""
    from fastapi.responses import RedirectResponse
    import httpx

    port = sandbox_manager.get_preview_port(project_id)
    if not port:
        raise HTTPException(status_code=404, detail="No preview available")

    url = f"http://localhost:{port}/{path}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, follow_redirects=False, timeout=10)
            headers = dict(resp.headers)
            headers.pop("content-length", None)
            headers.pop("transfer-encoding", None)
            return JSONResponse(
                content={"status": "ok", "preview_url": url},
                status_code=200,
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Preview unavailable: {e}")


# ── Security Scan ───────────────────────────────────────────────────────

@router.post("/scan")
async def scan_prompt(req: WriteFileRequest):
    """Scan a prompt through Lobster Trap."""
    from security.lobster_trap import inspect_prompt
    result = await inspect_prompt(req.content)
    return result


# ── Helpers ─────────────────────────────────────────────────────────────

def _resolve_workspace(path: str) -> str:
    """Resolve a workspace path from the request path parameter."""
    # If path looks like a project ID, use its workspace
    project = state_manager.get_project(path)
    if project:
        return project.workspace_path

    # Otherwise treat as direct path
    if path.startswith(WORKSPACES_ROOT):
        return path
    return os.path.join(WORKSPACES_ROOT, path)


def _resolve_file_path(path: str) -> str:
    """Resolve a file path within a workspace."""
    # Check if path contains a project ID prefix
    project = state_manager.get_project(path.split("/")[0])
    if project:
        relative = "/".join(path.split("/")[1:])
        return os.path.normpath(os.path.join(project.workspace_path, relative))

    # Direct path
    full = os.path.normpath(os.path.join(WORKSPACES_ROOT, path))
    if not full.startswith(WORKSPACES_ROOT):
        raise HTTPException(status_code=403, detail="Path traversal detected")
    return full


def _build_tree(dir_path: str, base_path: str = "") -> dict:
    """Build a recursive file tree."""
    tree = {"name": os.path.basename(dir_path) or base_path, "type": "directory", "children": []}
    try:
        for entry in sorted(os.listdir(dir_path)):
            if entry.startswith(".") and entry not in ("package.json", "requirements.txt", "go.mod", "Cargo.toml"):
                continue  # Skip hidden files except key config files
            full = os.path.join(dir_path, entry)
            if os.path.isdir(full):
                tree["children"].append(_build_tree(full, entry))
            else:
                tree["children"].append({
                    "name": entry,
                    "type": "file",
                    "size": os.path.getsize(full),
                })
    except PermissionError:
        pass
    return tree
