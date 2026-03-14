"""Overseer tools for runtime injection and monitoring.

Registers: hunter_inject, hunter_interrupt, hunter_logs

These tools give the Overseer the ability to steer the Hunter at runtime:
    - hunter_inject: push an instruction into the Hunter's next iteration
    - hunter_interrupt: signal the Hunter to stop gracefully
    - hunter_logs: retrieve recent Hunter stdout/stderr output

The injection mechanism uses file-based IPC:
    1. Overseer writes to ~/.hermes/hunter/injections/current.md
    2. Hunter's step_callback (runner.py) reads it, renames to .consumed
    3. Content is appended to the Hunter's ephemeral system prompt
"""

import json
import logging
from typing import Optional

from tools.registry import registry

logger = logging.getLogger(__name__)


# =============================================================================
# Controller singleton (same pattern as process_tools.py)
# =============================================================================

# Single-threaded assumption: this global is only safe because the Overseer
# loop runs synchronously.  If concurrent access becomes possible, guard with
# a threading.Lock.
_controller = None


def _get_controller():
    """Lazily initialise and return the shared HunterController.

    Deferred imports avoid circular dependencies and allow hunter/ to be
    an optional package.
    """
    global _controller
    if _controller is None:
        from hunter.backends import create_controller
        _controller = create_controller()
    return _controller


def _set_controller(controller):
    """Override the controller singleton (for testing)."""
    global _controller
    _controller = controller


# =============================================================================
# Elephantasm helper (best-effort, never crashes)
# =============================================================================

def _extract_overseer_event(text: str, meta: Optional[dict] = None):
    """Record an Overseer action to Elephantasm memory.

    Non-fatal — if Elephantasm is unavailable, the event is silently dropped.
    """
    try:
        from hunter.memory import OverseerMemoryBridge, AnimaManager
        from hunter.config import OVERSEER_ANIMA_NAME

        anima_id = AnimaManager.get_anima_id(OVERSEER_ANIMA_NAME)
        if not anima_id:
            return
        bridge = OverseerMemoryBridge(anima_id=anima_id)
        try:
            bridge.extract_decision(text, meta=meta)
        finally:
            bridge.close()
    except Exception as exc:
        logger.debug("Elephantasm extract skipped: %s", exc)


# =============================================================================
# Priority prefixes
# =============================================================================

_PRIORITY_PREFIXES = {
    "normal": "",
    "high": "HIGH PRIORITY: ",
    "critical": "CRITICAL — DROP CURRENT TASK: ",
}

_VALID_PRIORITIES = set(_PRIORITY_PREFIXES.keys())


# =============================================================================
# Handlers
# =============================================================================

def _handle_hunter_inject(args: dict, **kwargs) -> str:
    """Inject a runtime instruction into the Hunter via the controller.

    The controller's ``inject()`` method handles the IPC mechanism
    (file-based for local, Elephantasm for remote).
    """
    controller = _get_controller()

    instruction = args.get("instruction", "")
    if not instruction:
        return json.dumps({"error": "instruction is required"})

    priority = args.get("priority", "normal")
    if priority not in _VALID_PRIORITIES:
        return json.dumps({
            "error": f"Invalid priority '{priority}'. Must be one of: {sorted(_VALID_PRIORITIES)}"
        })

    try:
        controller.inject(instruction, priority)
    except OSError as e:
        return json.dumps({"error": f"Failed to inject: {e}"})

    # Best-effort Elephantasm logging
    _extract_overseer_event(
        f"Injected {priority} instruction: {instruction[:200]}",
        meta={"type": "injection", "priority": priority},
    )

    return json.dumps({
        "status": "injected",
        "priority": priority,
        "instruction_length": len(instruction),
    })


def _handle_hunter_interrupt(args: dict, **kwargs) -> str:
    """Signal the Hunter to stop gracefully.

    Uses the controller's ``interrupt()`` method (file-based for local,
    machine stop for remote), then waits for graceful exit before
    falling back to force-kill.
    """
    controller = _get_controller()
    message = args.get("message", "Overseer requested interrupt.")

    if not controller.is_running:
        return json.dumps({"status": "no_hunter_running"})

    # Signal interrupt via the controller
    try:
        controller.interrupt()
    except OSError as e:
        return json.dumps({"error": f"Failed to signal interrupt: {e}"})

    # Wait for graceful exit, then force-kill
    current = controller.current
    if current is None:
        return json.dumps({"status": "no_hunter_running"})

    try:
        current.wait(timeout=30)
        status = "interrupted_gracefully"
    except TimeoutError:
        controller.kill()
        status = "force_killed"

    # Best-effort Elephantasm logging
    _extract_overseer_event(
        f"Interrupted Hunter ({status}): {message[:200]}",
        meta={"type": "interrupt", "result": status},
    )

    return json.dumps({"status": status, "message": message})


def _handle_hunter_logs(args: dict, **kwargs) -> str:
    """Return recent Hunter stdout/stderr output from the in-memory buffer."""
    controller = _get_controller()
    tail = args.get("tail", 100)

    logs = controller.get_logs(tail=tail)
    return json.dumps({
        "logs": logs,
        "lines": tail,
        "hunter_running": controller.is_running,
    })


# =============================================================================
# Tool registration
# =============================================================================

# -- hunter_inject --

HUNTER_INJECT_SCHEMA = {
    "name": "hunter_inject",
    "description": (
        "Push a runtime instruction into the Hunter's next iteration. The "
        "instruction is written to a file that the Hunter reads and consumes "
        "on its next step. Use this for tactical steering without redeploying. "
        "The instruction is appended to the Hunter's ephemeral system prompt."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "instruction": {
                "type": "string",
                "description": (
                    "The instruction to inject. Examples: 'Focus on SQL injection "
                    "in the /api/users endpoint', 'Try IDOR on the invoice API'."
                ),
            },
            "priority": {
                "type": "string",
                "enum": ["normal", "high", "critical"],
                "description": (
                    "Priority level. 'normal' is appended as-is. 'high' adds a "
                    "priority prefix. 'critical' tells the Hunter to drop its "
                    "current task immediately."
                ),
                "default": "normal",
            },
        },
        "required": ["instruction"],
    },
}

registry.register(
    name="hunter_inject",
    toolset="hunter-overseer",
    schema=HUNTER_INJECT_SCHEMA,
    handler=_handle_hunter_inject,
    description="Inject a runtime instruction into the Hunter",
)


# -- hunter_interrupt --

HUNTER_INTERRUPT_SCHEMA = {
    "name": "hunter_interrupt",
    "description": (
        "Signal the Hunter to stop gracefully. Writes an interrupt flag file "
        "that the Hunter checks each iteration. Waits up to 30 seconds for "
        "graceful shutdown, then falls back to force-kill. Use this before "
        "code modifications that require a redeploy."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": (
                    "Message explaining why the Hunter is being interrupted. "
                    "Defaults to 'Overseer requested interrupt.'"
                ),
                "default": "Overseer requested interrupt.",
            },
        },
        "required": [],
    },
}

registry.register(
    name="hunter_interrupt",
    toolset="hunter-overseer",
    schema=HUNTER_INTERRUPT_SCHEMA,
    handler=_handle_hunter_interrupt,
    description="Interrupt the Hunter for redeploy or shutdown",
)


# -- hunter_logs --

HUNTER_LOGS_SCHEMA = {
    "name": "hunter_logs",
    "description": (
        "Get recent Hunter output (stdout + stderr). Returns the last N lines "
        "from the Hunter's in-memory output buffer. Useful for monitoring "
        "what the Hunter is doing without querying Elephantasm."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tail": {
                "type": "integer",
                "description": "Number of recent lines to return. Defaults to 100.",
                "default": 100,
            },
        },
        "required": [],
    },
}

registry.register(
    name="hunter_logs",
    toolset="hunter-overseer",
    schema=HUNTER_LOGS_SCHEMA,
    handler=_handle_hunter_logs,
    description="Get recent Hunter output logs",
)
