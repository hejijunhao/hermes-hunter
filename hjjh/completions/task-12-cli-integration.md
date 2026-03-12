# Task 12: CLI Integration — Completion

**Date:** 2026-03-12
**Status:** DONE — 51/51 tests passing, 0 regressions across 337 hunter tests
**Depends on:** Tasks 1–11 (all DONE)
**Blocks:** Task 13 (integration testing)

---

## Summary

Implemented the `hermes hunter` CLI subcommand tree — 7 subcommands exposing all Phase 1 functionality from the terminal. This is the final Phase 1 task; all 12 tasks are now complete with 337 tests.

The key design challenge was cross-process discovery: `hermes hunter spawn` in one CLI invocation needs to leave breadcrumbs for `hermes hunter kill`/`status`/`logs` in later invocations. Solved with PID file + metadata JSON following the existing `gateway/status.py` pattern.

---

## What Was Built

### Files Modified/Created

| File | Lines | Action |
|------|-------|--------|
| `hunter/cli.py` | ~290 | Replaced 19-line stub with full implementation |
| `hunter/control.py` | +15 | Added `detach` parameter to `HunterProcess.spawn()` |
| `hermes_cli/main.py` | +8 | Registered `hunter` subcommand with `ImportError` guard |
| `tests/test_hunter_cli.py` | ~310 | New test file — 51 tests |

### Subcommands

```
hermes hunter                     → defaults to status
hermes hunter setup               → one-time infrastructure setup
hermes hunter overseer             → start the Overseer control loop (blocks)
hermes hunter spawn                → spawn a Hunter subprocess (detached)
hermes hunter kill                 → three-stage kill (flag → SIGTERM → SIGKILL)
hermes hunter status               → show Hunter + budget + worktree state
hermes hunter budget               → show budget details
hermes hunter budget set 20/day    → update budget config
hermes hunter budget history       → show spend history + daily breakdown
hermes hunter logs                 → tail most recent log file
hermes hunter logs --follow        → tail -f equivalent with 0.5s poll
```

### `hunter/cli.py` — Implementation Details

**PID/meta helpers** — cross-process Hunter discovery:
- `_get_pid_path()` → `~/.hermes/hunter/hunter.pid`
- `_get_meta_path()` → `~/.hermes/hunter/hunter.meta.json`
- `_write_pid_meta(pid, session_id, model, log_path)` — written after spawn
- `_read_pid_meta()` → `(Optional[int], dict)` — auto-cleans stale PIDs via `os.kill(pid, 0)`
- `_clear_pid_meta()` — called after kill

**`register_hunter_commands(subparsers)`** — argparse registration:
- Returns the `hunter_parser` so `main.py` can call `set_defaults(func=handle_hunter_command)`
- 7 subcommands with appropriate arguments (`--model`, `--interval`, `--instruction`, `--resume`, `--follow`, `--tail`, budget `set` positional value)

**`handle_hunter_command(args)`** — dispatcher:
- Routes `args.hunter_command` to `_cmd_*` handlers via dict lookup
- Defaults to `_cmd_status` when no subcommand given
- Top-level try/except for user-friendly error messages (prints to stderr, `sys.exit(1)`)
- Catches `KeyboardInterrupt` for clean Ctrl+C handling

**Handler implementations:**

| Handler | Key detail |
|---------|------------|
| `_cmd_setup` | Calls `ensure_hunter_home()`, `WorktreeManager().setup()`, `BudgetManager().create_default_config()`, `AnimaManager.ensure_animas()`. All idempotent. Elephantasm failure is non-fatal. |
| `_cmd_overseer` | `OverseerLoop(model=, check_interval=).run()`. Blocks until Ctrl+C (handled by OverseerLoop internally). |
| `_cmd_spawn` | Checks PID file first (refuses if already running). Creates `HunterController`, calls `spawn(detach=True)`, writes PID/meta. |
| `_cmd_kill` | Three-stage: interrupt flag (5s wait) → SIGTERM (5s wait) → SIGKILL. Clears PID file after. |
| `_cmd_status` | Reads PID file for Hunter state, `BudgetManager` for spend, `WorktreeManager` for worktree. Each section wrapped in try/except for graceful degradation. |
| `_cmd_budget` | Routes to status (default) / set / history. Set uses `parse_budget_string()` + `update_config()`. History shows daily summary + recent entries. |
| `_cmd_logs` | Finds most recent `.log` file by mtime. `--tail N` prints last N lines. `--follow` uses readline poll loop (0.5s sleep) until KeyboardInterrupt. |

### `hunter/control.py` — `detach` Parameter

Added `detach: bool = False` to `HunterProcess.spawn()`:

- **`detach=False` (default):** Existing behaviour — `Popen(stdout=subprocess.PIPE)` + background capture thread. Used by the Overseer which keeps the process in memory.
- **`detach=True`:** `Popen(stdout=log_fh)` — stdout redirected directly to the log file. No pipe, no capture thread. The subprocess survives the parent exiting without SIGPIPE. Used by CLI `hermes hunter spawn`.

The `_detach_log_fh` attribute holds the file handle reference to prevent garbage collection closing it.

Passed through in `HunterController.spawn(detach=)`.

### `hermes_cli/main.py` — Registration

```python
try:
    from hunter.cli import register_hunter_commands, handle_hunter_command
    hunter_parser = register_hunter_commands(subparsers)
    hunter_parser.set_defaults(func=handle_hunter_command)
except ImportError:
    pass  # hunter package not available
```

Inserted after the `uninstall` command, before the "Parse and execute" section. The `ImportError` guard means the hunter package is optional — standard Hermes users are unaffected.

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Cross-process discovery | PID file + meta JSON | Follows existing `gateway/status.py` pattern. `os.kill(pid, 0)` for liveness, auto-cleanup of stale files. |
| CLI spawn survival | `detach=True` → stdout to file | Eliminates SIGPIPE when CLI exits. Hunter subprocess writes independently to its log file. |
| Default subcommand | `status` | Matches user expectation ("what's going on?"). `hermes hunter` alone gives useful info. |
| Graceful degradation | try/except per section in status | `hermes hunter status` works even before `hermes hunter setup`. Shows "not configured" instead of crashing. |
| Error output | `print(f"Error: {e}", file=sys.stderr)` + `sys.exit(1)` | Clean error messages, no tracebacks. Non-zero exit code for scripting. |
| Registration guard | `try/except ImportError` | Hunter is an optional add-on to Hermes. Missing package doesn't break the CLI. |

---

## Tests — 51/51 Passing

| Class | Count | Coverage |
|-------|-------|----------|
| `TestPidMeta` | 8 | write/read roundtrip, stale PID cleanup, missing file, invalid content, clear, process alive/dead |
| `TestRegisterCommands` | 12 | all 7 subcommands parse correctly, argument defaults, argument overrides, no subcommand |
| `TestDispatch` | 4 | default → status, routes setup/spawn correctly, exception → stderr + exit(1) |
| `TestCmdSetup` | 3 | all components called, worktree already set up skips, Elephantasm failure non-fatal |
| `TestCmdOverseer` | 3 | default args, model + interval override, startup message printed |
| `TestCmdSpawn` | 3 | success + PID write, already-running guard, budget exhausted error |
| `TestCmdKill` | 3 | no process, graceful exit via flag, SIGTERM escalation |
| `TestCmdStatus` | 4 | running, not running, degraded (no budget/worktree), budget alert shown |
| `TestCmdBudget` | 5 | default shows status, set valid, set invalid, history with data, history empty |
| `TestCmdLogs` | 5 | no log dir, no log files, tail N lines, picks most recent by mtime, tail helper |
| **Total** | **51** | |

**Mock strategy:** Infrastructure classes (`WorktreeManager`, `BudgetManager`, `HunterController`, `OverseerLoop`, `AnimaManager`) mocked to avoid real git/subprocess/API calls. `tmp_path` for PID/meta/log files. `capsys` for output verification.

---

## Phase 1 Summary

All 12 implementation tasks are complete:

| Task | Component | File(s) | Tests |
|------|-----------|---------|-------|
| 1 | Package scaffolding | `hunter/config.py` | — |
| 2 | Budget system | `hunter/budget.py` | 9 |
| 3 | Worktree manager | `hunter/worktree.py` | 20 |
| 4 | Process controller | `hunter/control.py`, `hunter/runner.py` | 35 |
| 5 | Elephantasm memory | `hunter/memory.py` | 42 |
| 6 | Process tools | `hunter/tools/process_tools.py` | 29 |
| 7 | Inject tools | `hunter/tools/inject_tools.py` | 33 |
| 8 | Code tools | `hunter/tools/code_tools.py` | 49 |
| 9 | Budget tools | `hunter/tools/budget_tools.py` | 27 |
| 10 | Overseer loop | `hunter/overseer.py` | 53 |
| 11 | System prompt | `hunter/prompts/overseer_system.md` + references | 18 |
| 12 | CLI integration | `hunter/cli.py`, `hermes_cli/main.py` | 51 |
| **Total** | **~3,200 lines** | | **337** |

**Next:** Task 13 — integration testing (end-to-end with real agents against deliberately vulnerable repos).
