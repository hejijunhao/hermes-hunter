"""Overseer tools for budget and model management.

Registers: budget_status, hunter_model_set

These tools give the Overseer visibility into spending and control over
the Hunter's model tier — the primary levers for cost optimisation.

    - budget_status:    full snapshot of budget state + spend history
    - hunter_model_set: change the Hunter's LLM model (persist to file)
"""

import json
import logging
from pathlib import Path
from typing import Optional

from tools.registry import registry

logger = logging.getLogger(__name__)


# =============================================================================
# Controller singleton (same pattern as process_tools.py / inject_tools.py)
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
# Helpers
# =============================================================================

def _get_model_override_path() -> Path:
    """Path to the persistent model override file.

    The runner reads this on startup to pick up model changes.
    """
    from hunter.config import get_hunter_home
    return get_hunter_home() / "model_override.txt"


# =============================================================================
# Handlers
# =============================================================================

def _handle_budget_status(args: dict, **kwargs) -> str:
    """Get full budget snapshot including spend history and daily breakdown."""
    controller = _get_controller()
    budget = controller.budget

    # Reload to pick up any human config changes
    budget.reload()
    status = budget.check_budget()

    result = status.to_dict()
    result["summary"] = status.summary()

    # Add spend history context (last 5 entries for quick reference)
    recent = budget.get_spend_history(limit=5)
    result["recent_spend"] = recent

    # Add daily summary
    daily = budget.get_daily_summary()
    result["daily_breakdown"] = daily

    return json.dumps(result)


def _handle_hunter_model_set(args: dict, **kwargs) -> str:
    """Change the Hunter's LLM model tier.

    Persists the model to a file so it survives restarts. Optionally
    triggers an immediate redeploy if the Hunter is running.
    """
    model = args.get("model", "")
    if not model:
        return json.dumps({"error": "model is required"})

    apply_immediately = args.get("apply_immediately", False)

    controller = _get_controller()

    # Get old model for logging
    old_model = None
    if controller.current:
        old_model = controller.current.model

    # Persist model override to file
    model_path = _get_model_override_path()
    try:
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text(model, encoding="utf-8")
    except OSError as e:
        return json.dumps({"error": f"Failed to persist model override: {e}"})

    result = {
        "status": "model_updated",
        "old_model": old_model,
        "new_model": model,
        "apply_immediately": apply_immediately,
    }

    # Optionally trigger redeploy
    if apply_immediately and controller.is_running:
        try:
            process = controller.redeploy(resume_session=True, model=model)
            result["redeployed"] = True
            result["session_id"] = process.session_id
            result["pid"] = process._pid
        except RuntimeError as e:
            result["redeployment_error"] = str(e)
            result["redeployed"] = False
    else:
        result["redeployed"] = False
        if controller.is_running:
            result["note"] = "Model change takes effect on next redeploy or spawn."
        else:
            result["note"] = "Model change takes effect on next spawn."

    # Elephantasm logging
    _extract_overseer_event(
        f"Model changed: {old_model} → {model} (immediate={apply_immediately})",
        meta={"type": "model_change", "old_model": old_model, "new_model": model},
    )

    return json.dumps(result)


# =============================================================================
# Tool registration
# =============================================================================

# -- budget_status --

BUDGET_STATUS_SCHEMA = {
    "name": "budget_status",
    "description": (
        "Get current budget status: remaining funds, spend rate, alerts, and "
        "daily breakdown. Reloads the budget config to pick up any human "
        "changes to the budget.yaml file. Use this to decide whether to "
        "switch model tiers or adjust hunting strategy."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

registry.register(
    name="budget_status",
    toolset="hunter-overseer",
    schema=BUDGET_STATUS_SCHEMA,
    handler=_handle_budget_status,
    description="Get current budget and spend status",
)


# -- hunter_model_set --

HUNTER_MODEL_SET_SCHEMA = {
    "name": "hunter_model_set",
    "description": (
        "Change the Hunter's LLM model tier. The change is persisted to disk "
        "so it survives restarts. By default takes effect on next spawn or "
        "redeploy. Set apply_immediately=true to trigger a redeploy now. "
        "Use lighter models (7B) for recon, heavier (72B) for deep analysis."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": (
                    "Model identifier (e.g., 'qwen/qwen3.5-7b', "
                    "'qwen/qwen3.5-32b', 'qwen/qwen3.5-72b')."
                ),
            },
            "apply_immediately": {
                "type": "boolean",
                "description": (
                    "If true and Hunter is running, trigger a redeploy with "
                    "the new model. Default false."
                ),
                "default": False,
            },
        },
        "required": ["model"],
    },
}

registry.register(
    name="hunter_model_set",
    toolset="hunter-overseer",
    schema=HUNTER_MODEL_SET_SCHEMA,
    handler=_handle_hunter_model_set,
    description="Change the Hunter's LLM model",
)
