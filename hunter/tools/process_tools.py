"""Overseer tools for Hunter process management.

Registers: hunter_spawn, hunter_kill, hunter_status

These are the first three tools in the ``hunter-overseer`` toolset. They wrap
HunterController methods to give the Overseer LLM the ability to deploy,
terminate, and inspect the Hunter agent subprocess.

The controller is lazily initialised as a module-level singleton — one
WorktreeManager + BudgetManager shared by all hunter-overseer tools.
"""

import json
import logging
from dataclasses import asdict
from typing import Optional

from tools.registry import registry

logger = logging.getLogger(__name__)

# =============================================================================
# Controller singleton
# =============================================================================

# Single-threaded assumption: this global is only safe because the Overseer
# loop runs synchronously.  If concurrent access becomes possible, guard with
# a threading.Lock.
_controller = None


def _get_controller():
    """Lazily initialise and return the shared HunterController.

    The controller is created once per process via the backend factory.
    It wires together the WorktreeManager (git worktree for the Hunter's
    codebase) and the BudgetManager (spend tracking + enforcement).

    Deferred imports avoid circular dependencies and allow hunter/ to be
    an optional package — if the imports fail, the tools simply won't be
    available.
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
# Handlers
# =============================================================================

def _handle_hunter_spawn(args: dict, **kwargs) -> str:
    """Spawn a new Hunter agent process.

    Kills any existing Hunter first. Checks budget before spawning — returns
    an error if the budget is exhausted.
    """
    controller = _get_controller()

    try:
        process = controller.spawn(
            model=args.get("model"),
            initial_instruction=args.get("instruction"),
            resume_session=args.get("resume", False),
        )
    except RuntimeError as e:
        return json.dumps({"error": str(e)})

    return json.dumps({
        "status": "spawned",
        "session_id": process.session_id,
        "model": process.model,
        "pid": process._pid,
    })


def _handle_hunter_kill(args: dict, **kwargs) -> str:
    """Terminate the running Hunter process."""
    controller = _get_controller()
    success = controller.kill()
    return json.dumps({
        "status": "killed" if success else "no_hunter_running",
    })


def _handle_hunter_status(args: dict, **kwargs) -> str:
    """Get Hunter health: running/stopped, PID, session, model, uptime, errors."""
    controller = _get_controller()
    status = controller.get_status()
    result = status.to_dict()
    result["summary"] = status.summary()
    return json.dumps(result)


# =============================================================================
# Tool registration
# =============================================================================

# -- hunter_spawn --

HUNTER_SPAWN_SCHEMA = {
    "name": "hunter_spawn",
    "description": (
        "Deploy a new Hunter agent instance from the current hunter/live worktree. "
        "Kills any existing Hunter first. The Hunter will begin autonomous "
        "vulnerability hunting. Returns an error if the budget is exhausted."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": (
                    "LLM model for the Hunter (e.g., 'qwen/qwen3.5-32b'). "
                    "Defaults to the medium tier."
                ),
            },
            "instruction": {
                "type": "string",
                "description": (
                    "Initial instruction for the Hunter — its first user message. "
                    "Defaults to a general hunting directive if omitted."
                ),
            },
            "resume": {
                "type": "boolean",
                "description": (
                    "Resume from the Hunter's last session instead of starting fresh."
                ),
                "default": False,
            },
        },
        "required": [],
    },
}

registry.register(
    name="hunter_spawn",
    toolset="hunter-overseer",
    schema=HUNTER_SPAWN_SCHEMA,
    handler=_handle_hunter_spawn,
    description="Spawn a new Hunter agent process",
)


# -- hunter_kill --

HUNTER_KILL_SCHEMA = {
    "name": "hunter_kill",
    "description": (
        "Terminate the running Hunter process. Uses a three-stage shutdown: "
        "interrupt flag → SIGTERM → SIGKILL. Returns whether a Hunter was "
        "actually killed or none was running."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

registry.register(
    name="hunter_kill",
    toolset="hunter-overseer",
    schema=HUNTER_KILL_SCHEMA,
    handler=_handle_hunter_kill,
    description="Terminate the running Hunter process",
)


# -- hunter_status --

HUNTER_STATUS_SCHEMA = {
    "name": "hunter_status",
    "description": (
        "Get the Hunter's current health status: whether it's running or stopped, "
        "its PID, session ID, model, uptime, exit code, last output line, and "
        "any error messages. Also includes a human-readable summary."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

registry.register(
    name="hunter_status",
    toolset="hunter-overseer",
    schema=HUNTER_STATUS_SCHEMA,
    handler=_handle_hunter_status,
    description="Get Hunter health status",
)
