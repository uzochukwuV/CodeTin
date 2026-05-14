"""Lobster Trap prompt inspection wrapper.

Calls the lobstertrap binary to inspect prompts for injection,
dangerous commands, and other security issues.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

LOBSTER_TRAP_BINARY = "/tmp/lobstertrap/lobstertrap"


async def inspect_prompt(prompt: str) -> dict[str, Any]:
    """Inspect a prompt through Lobster Trap.

    Returns:
        dict with keys:
            flagged: bool — whether the prompt was blocked
            risk_score: float — 0.0 to 1.0
            action: str — ALLOW, DENY, LOG, etc.
            rule: str — the rule that triggered the decision (if any)
            details: str — human-readable explanation
            metadata: dict — full inspection metadata
    """
    try:
        result = subprocess.run(
            [LOBSTER_TRAP_BINARY, "inspect", prompt],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="/tmp/lobstertrap",
        )

        output = result.stderr  # lobstertrap outputs to stderr
        metadata = _parse_inspect_output(output)

        action = metadata.get("action", "ALLOW")
        flagged = action == "DENY"

        return {
            "flagged": flagged,
            "risk_score": metadata.get("risk_score", 0.0),
            "action": action,
            "rule": metadata.get("rule", ""),
            "details": metadata.get("message", ""),
            "metadata": metadata,
        }

    except subprocess.TimeoutExpired:
        logger.warning("Lobster Trap inspection timed out")
        return {"flagged": False, "risk_score": 0, "action": "TIMEOUT", "details": "Inspection timed out"}
    except FileNotFoundError:
        logger.warning("Lobster Trap binary not found — skipping inspection")
        return {"flagged": False, "risk_score": 0, "action": "SKIP", "details": "Lobster Trap not installed"}
    except Exception as e:
        logger.warning(f"Lobster Trap inspection error: {e}")
        return {"flagged": False, "risk_score": 0, "action": "ERROR", "details": str(e)}


def _parse_inspect_output(output: str) -> dict:
    """Parse the stderr output of `lobstertrap inspect`."""
    result: dict = {}

    # Extract JSON metadata block
    in_json = False
    json_lines = []
    for line in output.split("\n"):
        if line.strip() == "{":
            in_json = True
        if in_json:
            json_lines.append(line)
        if in_json and line.strip() == "}":
            break

    if json_lines:
        try:
            metadata = json.loads("\n".join(json_lines))
            result.update(metadata)
            result["risk_score"] = metadata.get("risk_score", 0.0)
        except json.JSONDecodeError:
            pass

    # Extract policy decision
    for line in output.split("\n"):
        if line.strip().startswith("Action:"):
            result["action"] = line.split(":", 1)[1].strip()
        elif line.strip().startswith("Rule:"):
            result["rule"] = line.split(":", 1)[1].strip()
        elif line.strip().startswith("Message:"):
            result["message"] = line.split(":", 1)[1].strip()

    return result
