"""WebSocket handlers for CodeTin Gateway.

Provides:
- /ws/terminal/{project_id} — xterm.js-compatible terminal stream
- /ws/agent-stream/{execution_id} — agent progress event stream
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from sandbox.manager import manager as sandbox_manager
from orchestrator.state import state as state_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/terminal/{project_id}")
async def terminal_websocket(websocket: WebSocket, project_id: str):
    """WebSocket endpoint for terminal I/O.

    Client (xterm.js) sends keystrokes → forwarded to PTY stdin.
    PTY stdout → forwarded to client as text.
    """
    await websocket.accept()

    # Get the sandbox process
    try:
        proc = await sandbox_manager.exec_pty(project_id)
    except (ValueError, Exception) as e:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"Failed to start terminal: {e}",
        }))
        await websocket.close()
        return

    # Create tasks for bidirectional streaming
    async def read_from_pty():
        """Read PTY output and send to WebSocket."""
        try:
            while proc.stdout and not proc.stdout.at_eof():
                data = await proc.stdout.read(4096)
                if data:
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
                else:
                    break
        except (ConnectionError, WebSocketDisconnect):
            pass

    async def write_to_pty():
        """Read WebSocket input and send to PTY stdin."""
        try:
            while True:
                data = await websocket.receive_text()
                if proc.stdin:
                    # Handle special commands
                    if data.startswith("\x04"):  # Ctrl+D
                        proc.stdin.close()
                        break
                    proc.stdin.write(data.encode("utf-8"))
                    await proc.stdin.drain()
        except (ConnectionError, WebSocketDisconnect):
            pass

    # Run both directions
    read_task = asyncio.create_task(read_from_pty())
    write_task = asyncio.create_task(write_to_pty())

    # Wait for either to complete
    done, pending = await asyncio.wait(
        [read_task, write_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()

    # Clean up
    try:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
    except Exception:
        pass


@router.websocket("/ws/agent-stream/{execution_id}")
async def agent_stream_websocket(websocket: WebSocket, execution_id: str):
    """WebSocket endpoint for agent progress events.

    Streams JSON events from the orchestrator as the agent builds.
    """
    await websocket.accept()

    # Get or create the stream queue
    queue = await state_manager.get_stream(execution_id)
    if queue is None:
        queue = await state_manager.create_stream(execution_id)

    try:
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=30.0)
            if event is None:  # Sentinel
                await websocket.send_text(json.dumps({"type": "stream_end"}))
                break
            await websocket.send_text(json.dumps(event))
    except (asyncio.TimeoutError, WebSocketDisconnect, ConnectionError):
        pass
    finally:
        state_manager.close_stream(execution_id)
