"""Overseer tools for editing the Hunter's source code.

Registers: hunter_code_read, hunter_code_edit, hunter_diff, hunter_rollback, hunter_redeploy

These tools implement the Overseer's "hard intervention" mechanism. They wrap
WorktreeManager (for file and git operations) and HunterController.redeploy()
to let the Overseer LLM modify the Hunter's codebase and restart it.

Flow:
    1. hunter_code_read  → inspect code before editing
    2. hunter_code_edit  → find-and-replace + auto-commit
    3. hunter_diff       → verify changes
    4. hunter_redeploy   → kill + restart with new code
    5. hunter_rollback   → revert if things went wrong
"""

import json
import logging
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
# Handlers
# =============================================================================

def _handle_hunter_code_read(args: dict, **kwargs) -> str:
    """Read a file from the Hunter's worktree."""
    path = args.get("path", "")
    if not path:
        return json.dumps({"error": "path is required"})

    controller = _get_controller()
    worktree = controller.worktree

    try:
        content = worktree.read_file(path)
    except FileNotFoundError:
        return json.dumps({"error": f"File not found in worktree: {path}"})
    except Exception as e:
        return json.dumps({"error": str(e)})

    return json.dumps({
        "path": path,
        "content": content,
        "size_bytes": len(content.encode("utf-8")),
    })


def _handle_hunter_code_edit(args: dict, **kwargs) -> str:
    """Find-and-replace edit in the Hunter's worktree, with auto-commit.

    Special case: empty old_string creates/overwrites a file.
    """
    path = args.get("path", "")
    if not path:
        return json.dumps({"error": "path is required"})

    old_string = args.get("old_string")
    new_string = args.get("new_string")

    if old_string is None or new_string is None:
        return json.dumps({"error": "old_string and new_string are required"})

    if old_string == new_string:
        return json.dumps({"error": "old_string and new_string are identical — no change"})

    controller = _get_controller()
    worktree = controller.worktree

    # Special case: empty old_string → create/overwrite file
    if old_string == "":
        try:
            worktree.write_file(path, new_string)
        except Exception as e:
            return json.dumps({"error": str(e)})
    else:
        # Standard find-and-replace
        try:
            found = worktree.edit_file(path, old_string, new_string)
        except FileNotFoundError:
            return json.dumps({"error": f"File not found in worktree: {path}"})
        except Exception as e:
            # Ambiguous edit (old_string appears multiple times) or other error
            return json.dumps({"error": str(e)})

        if not found:
            return json.dumps({"error": "old_string not found in file"})

    # Auto-commit
    commit_msg = args.get("commit_message", f"overseer: edit {path}")
    try:
        commit_hash = worktree.commit(commit_msg, files=[path])
    except Exception as e:
        return json.dumps({"error": f"Edit succeeded but commit failed: {e}"})

    # Elephantasm logging
    _extract_overseer_event(
        f"Code edit: {path} (commit {commit_hash[:8]})",
        meta={"type": "code_edit", "path": path, "commit": commit_hash},
    )

    return json.dumps({
        "status": "edited_and_committed",
        "path": path,
        "commit": commit_hash,
    })


def _handle_hunter_diff(args: dict, **kwargs) -> str:
    """View uncommitted changes or compare against a previous commit."""
    controller = _get_controller()
    worktree = controller.worktree

    since_commit = args.get("since_commit")

    try:
        if since_commit:
            diff_output = worktree.diff_since(since_commit)
        else:
            staged = args.get("staged", False)
            diff_output = worktree.diff(staged=staged)
    except Exception as e:
        return json.dumps({"error": str(e)})

    return json.dumps({
        "diff": diff_output,
        "empty": diff_output.strip() == "",
    })


def _handle_hunter_rollback(args: dict, **kwargs) -> str:
    """Reset the Hunter's worktree to a previous commit."""
    commit_hash = args.get("commit", "")
    if not commit_hash:
        return json.dumps({"error": "commit hash is required"})

    controller = _get_controller()
    worktree = controller.worktree

    try:
        worktree.rollback(commit_hash)
    except Exception as e:
        return json.dumps({"error": str(e)})

    # Get the new HEAD for confirmation
    try:
        new_head = worktree.get_head_commit()
    except Exception:
        new_head = commit_hash  # Fallback

    # Elephantasm logging
    _extract_overseer_event(
        f"Rolled back worktree to {commit_hash[:8]}",
        meta={"type": "rollback", "target_commit": commit_hash},
    )

    return json.dumps({
        "status": "rolled_back",
        "to_commit": new_head,
    })


def _handle_hunter_redeploy(args: dict, **kwargs) -> str:
    """Kill the current Hunter and restart with updated code.

    Differs from hunter_spawn: defaults to resume_session=True (continuity)
    and always kills the current Hunter first.
    """
    controller = _get_controller()

    resume = args.get("resume_session", True)
    model = args.get("model")

    try:
        process = controller.redeploy(
            resume_session=resume,
            model=model,
        )
    except RuntimeError as e:
        return json.dumps({"error": str(e)})

    # Elephantasm logging
    _extract_overseer_event(
        f"Redeployed Hunter (session={process.session_id}, model={process.model}, resumed={resume})",
        meta={"type": "redeploy", "model": process.model, "resumed": resume},
    )

    return json.dumps({
        "status": "redeployed",
        "session_id": process.session_id,
        "model": process.model,
        "pid": process._pid,
        "resumed": resume,
    })


# =============================================================================
# Tool registration
# =============================================================================

# -- hunter_code_read --

HUNTER_CODE_READ_SCHEMA = {
    "name": "hunter_code_read",
    "description": (
        "Read a file from the Hunter's worktree. Use this to inspect the "
        "Hunter's source code before deciding what to change. Paths are "
        "relative to the worktree root (e.g., 'skills/security/idor/SKILL.md', "
        "'hunter/tools/process_tools.py', 'agent/prompt_builder.py')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Relative path within the worktree (e.g., 'tools/web_tools.py')."
                ),
            },
        },
        "required": ["path"],
    },
}

registry.register(
    name="hunter_code_read",
    toolset="hunter-overseer",
    schema=HUNTER_CODE_READ_SCHEMA,
    handler=_handle_hunter_code_read,
    description="Read a file from the Hunter's worktree",
)


# -- hunter_code_edit --

HUNTER_CODE_EDIT_SCHEMA = {
    "name": "hunter_code_edit",
    "description": (
        "Edit a file in the Hunter's worktree using find-and-replace, then "
        "auto-commit. The old_string must appear exactly once in the file. "
        "To create a new file, pass old_string as an empty string and the "
        "full content in new_string. Each edit is a separate git commit."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path within the worktree.",
            },
            "old_string": {
                "type": "string",
                "description": (
                    "Text to find (must appear exactly once). "
                    "Empty string means create/overwrite the file."
                ),
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text.",
            },
            "commit_message": {
                "type": "string",
                "description": (
                    "Git commit message. Defaults to 'overseer: edit {path}'."
                ),
            },
        },
        "required": ["path", "old_string", "new_string"],
    },
}

registry.register(
    name="hunter_code_edit",
    toolset="hunter-overseer",
    schema=HUNTER_CODE_EDIT_SCHEMA,
    handler=_handle_hunter_code_edit,
    description="Edit a file in the Hunter's worktree",
)


# -- hunter_diff --

HUNTER_DIFF_SCHEMA = {
    "name": "hunter_diff",
    "description": (
        "View changes in the Hunter's worktree. Without arguments, shows "
        "unstaged changes. Use 'staged' for staged-only or 'since_commit' "
        "to compare against a previous commit."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "staged": {
                "type": "boolean",
                "description": "If true, show only staged changes. Default false.",
                "default": False,
            },
            "since_commit": {
                "type": "string",
                "description": (
                    "Show changes between this commit and HEAD. Takes priority "
                    "over 'staged' if both are provided."
                ),
            },
        },
        "required": [],
    },
}

registry.register(
    name="hunter_diff",
    toolset="hunter-overseer",
    schema=HUNTER_DIFF_SCHEMA,
    handler=_handle_hunter_diff,
    description="View changes in the Hunter's worktree",
)


# -- hunter_rollback --

HUNTER_ROLLBACK_SCHEMA = {
    "name": "hunter_rollback",
    "description": (
        "Reset the Hunter's worktree to a previous commit (hard reset). "
        "Use this to undo a bad code change. All uncommitted changes are "
        "discarded. Use hunter_redeploy afterwards to restart with the "
        "rolled-back code."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "commit": {
                "type": "string",
                "description": "Full or short commit hash to reset to.",
            },
        },
        "required": ["commit"],
    },
}

registry.register(
    name="hunter_rollback",
    toolset="hunter-overseer",
    schema=HUNTER_ROLLBACK_SCHEMA,
    handler=_handle_hunter_rollback,
    description="Rollback the Hunter's worktree to a previous commit",
)


# -- hunter_redeploy --

HUNTER_REDEPLOY_SCHEMA = {
    "name": "hunter_redeploy",
    "description": (
        "Kill the current Hunter and restart from the updated worktree. "
        "Use this after code changes (hunter_code_edit) to deploy new code. "
        "Defaults to resuming the previous session for continuity. "
        "Unlike hunter_spawn, this is for code updates, not fresh starts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "resume_session": {
                "type": "boolean",
                "description": (
                    "Resume the previous session (default true). Set false "
                    "for a fresh start."
                ),
                "default": True,
            },
            "model": {
                "type": "string",
                "description": (
                    "Override the model for the new Hunter instance. "
                    "If omitted, keeps the current model."
                ),
            },
        },
        "required": [],
    },
}

registry.register(
    name="hunter_redeploy",
    toolset="hunter-overseer",
    schema=HUNTER_REDEPLOY_SCHEMA,
    handler=_handle_hunter_redeploy,
    description="Redeploy the Hunter with updated code",
)
