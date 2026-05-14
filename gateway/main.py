"""CodeTin Gateway — FastAPI entry point.

Starts the API gateway on port 3000, serving:
- REST API at /api/*
- WebSocket at /ws/*
- Static HTML UI at /
- Preview proxy at /preview/*
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from gateway.api import router as api_router
from gateway.ws import router as ws_router
from sandbox.manager import manager as sandbox_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="CodeTin Gateway", version="0.1.0")

# Mount routers
app.include_router(api_router)
app.include_router(ws_router)

# Serve static files
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def startup():
    """Initialize sandbox manager and start AgentField if needed."""
    logger.info("Starting CodeTin Gateway...")

    # Ensure workspaces directory exists
    workspaces = os.environ.get("CODETIN_WORKSPACES", "/workspaces")
    os.makedirs(workspaces, exist_ok=True)

    # Initialize sandbox manager
    docker_available = await sandbox_manager.initialize()
    if docker_available:
        logger.info("Docker sandbox mode enabled")
    else:
        logger.info("Subprocess mode (no Docker)")


@app.get("/")
async def index():
    """Serve the main HTML UI."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>CodeTin Gateway</h1><p>API running. See /docs for API docs.</p>")


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "ok",
        "docker": sandbox_manager._docker is not None,
        "projects": len(sandbox_manager.list_projects()),
    }


def main():
    """Entry point for `python -m gateway`."""
    import uvicorn

    port = int(os.environ.get("CODETIN_PORT", "3000"))
    host = os.environ.get("CODETIN_HOST", "0.0.0.0")

    logger.info(f"Starting gateway on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
