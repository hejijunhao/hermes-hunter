# Task 6: Overseer Tools — Process Management — Completion

**Status:** DONE
**Date:** 2026-03-11
**File:** `hunter/tools/process_tools.py` (~175 lines)
**Tests:** `tests/test_hunter_process_tools.py` — 29/29 passing

---

## What Was Built

Three Overseer tools registered in the `hunter-overseer` toolset, wrapping `HunterController` methods for LLM-driven process management.

### hunter_spawn

Deploys a new Hunter agent instance from the `hunter/live` worktree.

- **Parameters:** `model` (optional string), `instruction` (optional string), `resume` (optional bool, default false)
- **Behaviour:** Calls `controller.spawn()`. Kills any existing Hunter first. Budget-gated — returns `{"error": "..."}` if budget exhausted.
- **Returns:** `{"status": "spawned", "session_id": "...", "model": "...", "pid": N}`

### hunter_kill

Terminates the running Hunter process.

- **Parameters:** None
- **Behaviour:** Calls `controller.kill()`. Three-stage shutdown (flag → SIGTERM → SIGKILL).
- **Returns:** `{"status": "killed"}` or `{"status": "no_hunter_running"}`

### hunter_status

Gets Hunter health information.

- **Parameters:** None
- **Behaviour:** Calls `controller.get_status()` and serialises the HunterStatus dataclass.
- **Returns:** Full status dict + human-readable `summary` field.

### Controller Singleton

A module-level `_controller` lazily initialises on first tool call:
- Creates `WorktreeManager()` + `BudgetManager()` with default paths
- Wraps them in `HunterController(worktree, budget)`
- Deferred imports avoid circular dependencies
- `_set_controller()` exposed for test injection

---

## Integration Points

### toolsets.py

Added `hunter-overseer` toolset listing all 13 planned Overseer tools:
```python
"hunter-overseer": {
    "tools": [
        "hunter_spawn", "hunter_kill", "hunter_status",
        "hunter_logs", "hunter_inject", "hunter_interrupt",
        "hunter_code_edit", "hunter_code_read", "hunter_diff",
        "hunter_rollback", "hunter_redeploy",
        "hunter_model_set", "budget_status",
    ],
}
```

### model_tools.py

Added `hunter.tools.process_tools` to the `_modules` discovery list. Import errors are silently ignored (standard pattern for optional tools).

---

## Design Decisions

- **Lazy singleton over dependency injection:** The controller is created once per process. Tools in Hermes are stateless functions — the singleton bridges them to the stateful controller. `_set_controller()` enables test injection without import-time side effects.
- **RuntimeError → JSON error:** `controller.spawn()` raises on budget exhaustion. The handler catches this and returns it as a JSON error dict, keeping the LLM in the loop rather than crashing the tool dispatch.
- **Schema includes `summary` field:** `hunter_status` adds a human-readable summary to the status dict, making it easier for the Overseer LLM to interpret at a glance without parsing every field.

---

## Test Coverage (29 tests)

| Group | Tests | Coverage |
|-------|-------|----------|
| _get_controller | 3 | Lazy creation (mocked deps), singleton caching, test override |
| hunter_spawn | 7 | Default args, model, instruction, resume, all args, budget exhausted, other error |
| hunter_kill | 2 | Running hunter, no hunter |
| hunter_status | 4 | Running, stopped, not started, crashed with error |
| Tool registration | 6 | Names in registry, correct toolset, spawn params, kill/status no params, OpenAI schema format |
| Toolset registration | 3 | Toolset exists, contains process tools, contains all planned tools |
| Dispatch integration | 4 | Spawn via dispatch, kill via dispatch, status via dispatch, exception handling |

---

## Acceptance Criteria

- [x] `hunter_spawn` creates a running Hunter process
- [x] `hunter_kill` stops the Hunter cleanly
- [x] `hunter_status` returns accurate health info
- [x] Tools are registered in the `hunter-overseer` toolset
- [x] Controller singleton is properly initialised
- [x] Budget check prevents spawn when budget exhausted
