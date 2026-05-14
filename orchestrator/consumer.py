"""Consumer pipeline: plain English → SWE-AF build → live app.

Streams progress events back to the frontend via async generator.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncGenerator

from orchestrator.state import state
from security.lobster_trap import inspect_prompt

logger = logging.getLogger(__name__)

NODE_ID = "swe-planner"


async def build_from_prompt(
    prompt: str,
    project_id: str,
    workspace_path: str,
) -> AsyncGenerator[dict, None]:
    """Build an app from a natural language description.

    Pipeline:
    1. Security scan prompt through Lobster Trap
    2. Call SWE-AF planning + execution pipeline
    3. Stream progress events
    4. Return result with preview URL
    """
    execution_id = f"exec_{uuid.uuid4().hex[:8]}"

    # Step 1: Security scan
    yield {"type": "security_scan", "status": "scanning", "message": "Scanning prompt for security..."}
    scan_result = await inspect_prompt(prompt)
    if scan_result.get("flagged"):
        yield {
            "type": "error",
            "message": f"Prompt blocked by security scan: {scan_result.get('details', 'injection detected')}",
            "risk_score": scan_result.get("risk_score", 0),
        }
        return

    yield {"type": "security_scan", "status": "passed", "message": "Prompt passed security scan"}

    # Step 2: Create stream for agent events
    stream = await state.create_stream(execution_id)

    # Step 3: Start SWE-AF build in background
    state.add_execution(project_id, execution_id, {
        "type": "consumer_build",
        "prompt": prompt,
        "status": "running",
    })

    yield {"type": "build_start", "execution_id": execution_id, "message": f"Building: {prompt[:80]}..."}

    # Launch the actual build
    build_task = asyncio.create_task(
        _run_swe_af_build(prompt, project_id, workspace_path, execution_id)
    )

    # Step 4: Stream progress events from SWE-AF
    while not build_task.done():
        try:
            event = await asyncio.wait_for(stream.get(), timeout=1.0)
            if event is None:  # Sentinel
                break
            yield event
        except asyncio.TimeoutError:
            continue

    # Step 5: Wait for build result
    try:
        result = await build_task
        yield {"type": "build_complete", "result": result, "execution_id": execution_id}
    except Exception as e:
        yield {"type": "error", "message": f"Build failed: {str(e)}", "execution_id": execution_id}
    finally:
        state.remove_execution(project_id, execution_id)
        state.close_stream(execution_id)


async def _run_swe_af_build(
    prompt: str,
    project_id: str,
    workspace_path: str,
    execution_id: str,
) -> dict:
    """Run SWE-AF build pipeline."""
    try:
        from agentfield import AgentFieldClient

        client = AgentFieldClient(base_url="http://localhost:8080")

        # Use the async execute API
        response = await client.execute_async(
            f"{NODE_ID}.build",
            input_data={
                "goal": prompt,
                "repo_path": workspace_path,
                "config": {
                    "enable_learning": True,
                    "ai_provider": "opencode",
                },
            },
        )

        return {
            "execution_id": response.get("execution_id"),
            "status": response.get("status", "queued"),
        }

    except Exception as e:
        # Fallback: simulate a build for demo purposes
        logger.warning(f"SWE-AF build failed, using fallback: {e}")
        await _demo_build(prompt, workspace_path, execution_id)
        return {"status": "completed", "fallback": True}


async def _demo_build(
    prompt: str,
    workspace_path: str,
    execution_id: str,
) -> None:
    """Demo build: creates a simple Node.js or Python app from the prompt."""
    from orchestrator.state import state as state_mgr

    # Detect if this is likely a web app
    is_web = any(kw in prompt.lower() for kw in ["app", "web", "site", "page", "react", "html", "express", "flask", "fastapi"])

    if is_web:
        # Create a simple Node.js Express app
        import os

        os.makedirs(workspace_path, exist_ok=True)

        # package.json
        with open(os.path.join(workspace_path, "package.json"), "w") as f:
            f.write('{"name":"app","version":"1.0.0","main":"index.js","scripts":{"start":"node index.js"},"dependencies":{"express":"^4.18.2"}}\n')

        # index.js
        with open(os.path.join(workspace_path, "index.js"), "w") as f:
            f.write(f"""const express = require('express');
const app = express();
const PORT = process.env.PORT || 3000;

app.get('/', (req, res) => {{
    res.send('<h1>{prompt}</h1><p>Built by CodeTin</p>');
}});

app.listen(PORT, () => {{
    console.log(`Server running on port ${{PORT}}`);
}});
""")

        await state_mgr.push_event(execution_id, {"type": "progress", "message": "Created Express.js app"})
        await state_mgr.push_event(execution_id, {"type": "progress", "message": "Running npm install..."})

        # Install deps
        import subprocess
        proc = subprocess.run(
            ["npm", "install"], cwd=workspace_path, capture_output=True, text=True, timeout=60
        )
        await state_mgr.push_event(execution_id, {"type": "progress", "message": f"npm install: {'done' if proc.returncode == 0 else 'failed'}"})

    else:
        # Create a simple Python script
        import os

        os.makedirs(workspace_path, exist_ok=True)

        with open(os.path.join(workspace_path, "main.py"), "w") as f:
            f.write(f"""# {prompt}
print("Hello from CodeTin!")
print("Project: {prompt}")
""")

        await state_mgr.push_event(execution_id, {"type": "progress", "message": "Created Python script"})
