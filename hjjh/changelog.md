# Hermes Hunter — Changelog

- **1.9.0** — CLI integration: `hermes hunter` subcommand tree — setup, overseer, spawn, kill, status, budget, logs
- **1.8.0** — Overseer main loop: `OverseerLoop` — continuous monitoring, evaluation, and improvement of the Hunter agent
- **1.7.0** — Overseer system prompt + reference docs: identity, intervention strategy, budget management, decision framework
- **1.6.0** — Budget/model tools: `budget_status`, `hunter_model_set` — all 13 Overseer tools now complete
- **1.5.0** — Code modification tools: `hunter_code_read`, `hunter_code_edit`, `hunter_diff`, `hunter_rollback`, `hunter_redeploy`
- **1.4.0** — Overseer injection tools: `hunter_inject`, `hunter_interrupt`, `hunter_logs` registered in `hunter-overseer` toolset
- **1.3.0** — Overseer process tools: `hunter_spawn`, `hunter_kill`, `hunter_status` registered in `hunter-overseer` toolset
- **1.2.0** — Elephantasm memory integration: `AnimaManager`, `OverseerMemoryBridge`, `HunterMemoryBridge`
- **1.1.0** — Phase 1 foundation: package scaffolding, budget system, worktree manager, process controller
- **1.0.0** — Foundation fork of Hermes Agent + architecture design

---

## 1.9.0 — CLI Integration (Task 12)

**Date:** 2026-03-12

The final Phase 1 task — all Hunter subsystem functionality exposed via `hermes hunter` CLI subcommands.

### Task 12: CLI Entry Points

Registered 7 subcommands under `hermes hunter`, with PID file-based cross-process discovery for standalone CLI usage.

**What was built:**

- **`hunter/cli.py`** (~290 lines) — full CLI implementation:
  - `register_hunter_commands()` — argparse registration for all 7 subcommands, returns parser for `main.py` to set handler
  - `handle_hunter_command()` — dispatcher, defaults to `status` when no subcommand given
  - **PID/meta helpers** — `_write_pid_meta()`, `_read_pid_meta()`, `_clear_pid_meta()` for cross-process Hunter discovery via `~/.hermes/hunter/hunter.pid` + `hunter.meta.json`. Follows the `gateway/status.py` pattern with `os.kill(pid, 0)` liveness checks and auto-cleanup of stale PIDs.
  - `_cmd_setup` — idempotent one-time setup: `ensure_hunter_home()`, `WorktreeManager().setup()`, `BudgetManager().create_default_config()`, `AnimaManager.ensure_animas()` (non-fatal)
  - `_cmd_overseer` — creates `OverseerLoop(model=, check_interval=)` and calls `.run()`, blocks until Ctrl+C
  - `_cmd_spawn` — checks PID file for existing Hunter, creates `HunterController`, calls `spawn(detach=True)`, writes PID/meta files
  - `_cmd_kill` — three-stage kill (interrupt flag → SIGTERM → SIGKILL), clears PID file
  - `_cmd_status` — shows Hunter process (from PID file), budget (from `BudgetManager`), worktree (from `WorktreeManager`), all with graceful degradation
  - `_cmd_budget` — routes to status/set/history. Set uses `parse_budget_string()` + `BudgetManager.update_config()`
  - `_cmd_logs` — finds most recent `.log` file by mtime, supports `--tail N` and `--follow` (poll-based `tail -f`)

- **`hunter/control.py`** — added `detach: bool = False` parameter to `HunterProcess.spawn()`:
  - When `detach=True`: stdout goes directly to log file (`Popen(stdout=log_fh)`), no pipe, no capture thread. The subprocess survives the parent process exiting.
  - When `detach=False`: existing behavior unchanged (pipe + capture thread for Overseer's in-memory log buffer).
  - Passed through in `HunterController.spawn()`.

- **`hermes_cli/main.py`** — added hunter subcommand registration with `ImportError` guard for optional hunter package

**Design decisions:**
- PID file for cross-process discovery: `hermes hunter spawn` in one CLI invocation, `hermes hunter kill` in another. Follows the existing `gateway/status.py` pattern.
- Detached spawn: eliminates SIGPIPE risk when CLI exits after spawning. The Hunter subprocess writes directly to its log file, independent of the parent process.
- Default to `status` when no subcommand given: matches user expectation ("what's going on?")
- Graceful degradation in `_cmd_status`: budget/worktree sections wrapped in try/except, so status works even before `hermes hunter setup`

**Tests:** 51/51 passing — PID/meta helpers (8: roundtrip, stale cleanup, missing, invalid, clear, alive/dead), argparse registration (12: all subcommands, arguments, defaults), dispatch (4: default to status, routing, error handling), setup (3: all components, already set up, Elephantasm failure), overseer (3: default args, model override, startup message), spawn (3: success + PID write, already running, budget exhausted), kill (3: no process, graceful, SIGTERM escalation), status (4: running, not running, degraded, alert), budget (5: default, set valid, set invalid, history with data, history empty), logs (5: no dir, no files, tail N, picks most recent, tail helper).

---

## Phase 1 Complete

All 12 tasks (+ Task 13 integration testing remaining) are now implemented:

| Task | Component | Tests |
|------|-----------|-------|
| 1 | Package scaffolding | — |
| 2 | Budget system | 9 |
| 3 | Worktree manager | 20 |
| 4 | Process controller | 35 |
| 5 | Elephantasm memory | 42 |
| 6 | Process tools | 29 |
| 7 | Inject tools | 33 |
| 8 | Code tools | 49 |
| 9 | Budget tools | 27 |
| 10 | Overseer loop | 53 |
| 11 | System prompt | 18 |
| 12 | CLI integration | 51 |
| **Total** | | **337** |

---

## 1.8.0 — Overseer Main Loop (Task 10)

**Date:** 2026-03-12

The Overseer's brain — a continuous control loop that monitors, evaluates, and improves the Hunter agent using the 13 tools from Tasks 6–9.

### Task 10: Overseer Main Loop

Implemented `OverseerLoop` in `hunter/overseer.py` — a `while`-loop wrapper around `AIAgent` that gives the Overseer full autonomy over the Hunter's lifecycle.

**What was built:**

- **`hunter/overseer.py`** (~435 lines, replaced 12-line stub) — `OverseerLoop` class + `_load_overseer_system_prompt()`:
  - `run()` — main loop, blocks until `KeyboardInterrupt` or `stop()`. Catches per-iteration errors without crashing — resilience is critical for an autonomous long-running process.
  - `stop()` — signals loop exit after current iteration.
  - `_setup()` — one-time init: creates shared `HunterController` and injects it into all 4 tool modules (`process_tools`, `inject_tools`, `code_tools`, `budget_tools`) via `_set_controller()`. Ensures Animas, budget config, worktree, and memory bridge are all initialised (all non-fatal on failure).
  - `_create_agent()` — builds `AIAgent` with `enabled_toolsets=["hunter-overseer"]`, `max_iterations=20`, `quiet_mode=True`, `skip_context_files=True`, `skip_memory=True`. Session IDs are timestamped (`overseer-YYYYMMDD-HHMMSS`).
  - `_iteration()` — single loop step: reload budget → hard stop check → inject Elephantasm memory → build iteration prompt → `run_conversation()` → append user/assistant pair to history → trim if over threshold → extract decision to Elephantasm → record Overseer's own API spend.
  - `_build_iteration_prompt()` — builds the user message with budget summary, alert warnings, Hunter status (running/stopped/crashed), recent logs, iteration count, and a task description listing the agent's intervention options.
  - `_shutdown()` — extracts final event to Elephantasm, closes memory bridge.

**Constructor parameters:**
- `model` — LLM the Overseer itself uses (default: `anthropic/claude-opus-4.6`)
- `budget` / `memory` / `controller` — optional pre-configured dependencies (created in `_setup()` if None)
- `check_interval` — seconds between iterations (default 30)
- `history_max_messages` / `history_keep_messages` — conversation trim thresholds (40/20)

**Critical design decision — shared controller injection:**

Each tool module has its own lazy `_controller` singleton. Without intervention, `hunter_spawn` via the tool registry creates a Hunter tracked by `process_tools._controller`, but the loop's `_controller.get_status()` would report "no Hunter spawned" — a split-brain problem. **Solution:** `_setup()` creates one shared `HunterController` and injects it into all four tool modules via their `_set_controller()` functions.

**Conversation history management:** Only user/assistant message pairs are tracked — not internal tool_call/tool messages from `result["messages"]`. This prevents rapid context inflation (a single agent turn with tool calls can produce 10–20 internal messages). History is trimmed to the last `history_keep_messages` (20) when it exceeds `history_max_messages` (40). Elephantasm memory provides long-term continuity beyond the trim window.

**Spend tracking:** Overseer records its own API spend using rough token estimates (4000 input + 1000 output per API call). Actual token tracking would require `AIAgent` to expose usage data (future improvement).

**Design decisions:**
- Non-fatal everything: Elephantasm, Anima setup, memory bridge — all fail gracefully. The loop continues even if memory is down.
- Prompt reloaded each iteration: ensures reference doc changes take effect without restart.
- Budget checked first each iteration: hard stop kills the Hunter and skips the agent turn entirely — no wasted API spend.
- Error resilience in `run()`: per-iteration exceptions are caught, logged, extracted to Elephantasm, and the loop continues.

**Tests:** 53/53 passing — setup (10: ensure_hunter_home, budget config, worktree setup/already-setup, animas ensure/failure, agent creation, controller injection into 4 tool modules, memory bridge failure non-fatal, provided controller), iteration (16: budget reload/check, hard stop kills Hunter + skips agent + extracts memory, memory inject, ephemeral prompt update with/without memory, run_conversation called, history append, decision extract, spend recording, count increment, no-memory mode, zero-spend on zero API calls, zero-spend on zero cost), history management (4: grows across iterations, trimmed at threshold, keeps recent messages, passed to agent), prompt building (8: budget summary with $, alert warning, no alert when OK, Hunter not running/running, logs when running, task section with tool names, iteration number), run/shutdown (7: stop flag, shutdown extracts final event with count, shutdown closes memory, shutdown without memory OK, shutdown sets running false, iteration error does not crash loop, iteration error extracted to Elephantasm), agent creation (6: correct toolsets, quiet mode, skip context files, skip memory, session ID format, model passthrough), first run (2: "not running" in prompt, spawn suggestion).

---

## 1.7.0 — Overseer System Prompt (Task 11)

**Date:** 2026-03-12

The Overseer's personality and decision-making framework — a structured system prompt with modular reference documents that guide intervention strategy and budget management.

### Task 11: System Prompt & Reference Documents

Created the Overseer's system prompt and supporting reference docs as bundled Markdown files in `hunter/prompts/`.

**What was built:**

- **`hunter/prompts/overseer_system.md`** (~95 lines) — main system prompt:
  - **Identity:** "You are the Overseer — a meta-agent responsible for continuously improving a bug-bounty Hunter agent."
  - **Three intervention modes:** SOFT (`hunter_inject` — tactical steering), HARD (`hunter_code_edit` + `hunter_redeploy` — systemic improvements with 6-step workflow), MODEL (`hunter_model_set` — cost optimisation). Includes the principle: "Always prefer the least invasive intervention."
  - **Decision framework:** 6 evaluation questions per iteration — is the Hunter running? Stuck? Finding vulns? Report quality? On budget? Did last intervention help?
  - **Quality definition:** "High-quality vulnerability reports that earn bounty payouts" as the ultimate metric, with 7 criteria (title, CVSS, CWE, reproduction steps, PoC, impact, remediation).
  - **Tool quick reference:** All 13 tools grouped by category (process management, runtime injection, code modification, budget & model).
  - **8 rules:** Never modify own code, always commit before redeploying, one change per commit, monitor after hard interventions, rollback on regression, respect budget absolutely, observe rather than intervene when in doubt, prefer skills over code changes.

- **`hunter/prompts/references/budget-management.md`** (~48 lines) — model tier selection + budget strategies:
  - **Model tier table:** Maps Hunter phases (recon → analysis → PoC → reporting) to recommended model sizes (7B → 32B → 72B) with rationale.
  - **Three budget strategy tiers:** Comfortable (<50% — heavy model, don't optimise), Cautious (50–80% — drop subagents to light, selective targeting), Critical (>80% — medium tier only, finish current targets, consider polishing over hunting).
  - **Cost tracking guidance:** Check budget at start of each iteration, proactively switch before hard stop forces a kill.

- **`hunter/prompts/references/intervention-strategy.md`** (~58 lines) — when and how to intervene:
  - **Four decision categories:** Do nothing (steady progress, positive outcomes), Soft intervention (redirect focus, nudge quality), Hard intervention (repeated failures, systemic gaps), Model change (budget pressure, phase transitions).
  - **Intervention sizing ladder:** Skill addition (safest) → system prompt tweak → tool parameter change → tool logic change → core agent change (riskiest). "Do this first" for skills.
  - **Post-intervention monitoring protocol:** Watch 3–5 iterations, compare before/after, rollback immediately on regression, don't stack changes.
  - **Common anti-patterns:** Over-intervention (prevents momentum), thrashing (alternating strategies), large rewrites (when targeted edits suffice), ignoring rollback (fixing forward on broken changes).

- **`_load_overseer_system_prompt()`** in `hunter/overseer.py`:
  - Reads main prompt from `hunter/prompts/overseer_system.md`
  - Appends all `.md` files from `hunter/prompts/references/` sorted alphabetically with `---` dividers
  - Raises `FileNotFoundError` if main prompt missing, tolerates missing `references/` dir

**Design decision — bundled files, not skills:** Reference docs are loaded directly from the package directory rather than using the Hermes skills system. This avoids adding the `skills` toolset to the Overseer's `enabled_toolsets`, which would pull in 16+ extra tools and break the focused `hunter-overseer` tool surface. The trade-off is that reference docs don't benefit from the skills system's platform filtering or frontmatter features, but the Overseer doesn't need those.

**Tests:** 18/18 passing — real prompt files (4: main prompt exists, references dir exists, budget reference exists, intervention reference exists), prompt content (8: contains role/identity, soft/hard/model intervention modes, rules section, decision framework questions, tool quick reference, references appended to main prompt, dividers between references), load function (2: returns non-empty string, includes reference content), edge cases (4: missing references dir OK, missing main prompt raises FileNotFoundError, references sorted alphabetically/deterministically, empty references dir OK).

---

## 1.6.0 — Budget & Model Tools (Task 9)

**Date:** 2026-03-11

Budget visibility and model tier control — the Overseer can now check spending and switch the Hunter's LLM model for cost optimisation.

### Task 9: Overseer Tools — Budget & Model Management

Registered `budget_status` and `hunter_model_set` as Hermes tools in the `hunter-overseer` toolset.

**What was built:**

- **`hunter/tools/budget_tools.py`** (~215 lines) — two tool handlers + controller singleton + Elephantasm helper + model path helper:
  - `budget_status` — reloads config (picks up human edits), returns full `BudgetStatus` dict with `summary`, `recent_spend` (last 5 entries), and `daily_breakdown`. Single call gives the Overseer full context for model-switching decisions.
  - `hunter_model_set` — persists model to `~/.hermes/hunter/model_override.txt` (survives restarts). Optional `apply_immediately` triggers redeploy with the new model. Graceful failure: if redeploy fails, model file is still written and `redeployment_error` is returned. Contextual `note` field tells the Overseer when the change takes effect.
  - `_get_model_override_path()` — returns `~/.hermes/hunter/model_override.txt`.

- **`model_tools.py`** — added `hunter.tools.budget_tools` to the `_modules` discovery list

**Design decisions:**
- File-based model persistence: survives both Hunter and Overseer restarts
- Proactive response enrichment: budget_status returns spend history + daily breakdown in one call
- Graceful redeploy failure: model file written even if redeploy fails on budget exhaustion

**Tests:** 27/27 passing — controller singleton (3), budget_status (6: normal/exhausted/alert/spend/daily/no-ledger), hunter_model_set (8: basic/missing/empty/old-model/immediate-running/immediate-not-running/budget-error/note/elephantasm), model override path (2), tool registration (4), dispatch integration (3).

---

## 1.5.0 — Code Modification Tools (Task 8)

**Date:** 2026-03-11

The Overseer's "hard intervention" mechanism — read, edit, diff, rollback, and redeploy the Hunter's codebase.

### Task 8: Overseer Tools — Code Modification

Registered `hunter_code_read`, `hunter_code_edit`, `hunter_diff`, `hunter_rollback`, `hunter_redeploy` as Hermes tools in the `hunter-overseer` toolset.

**What was built:**

- **`hunter/tools/code_tools.py`** (~310 lines) — five tool handlers + controller singleton + Elephantasm helper:
  - `hunter_code_read` — reads a file from the worktree. Returns `{path, content, size_bytes}`. Handles `FileNotFoundError` and `WorktreeError`.
  - `hunter_code_edit` — find-and-replace + auto-commit. `old_string` must appear exactly once (ambiguous edits rejected). Empty `old_string` creates a new file via `write_file()`. Each edit is a separate git commit with default message `"overseer: edit {path}"` or custom `commit_message`.
  - `hunter_diff` — shows unstaged changes by default. `staged=true` for staged only. `since_commit` for historical comparison (takes priority over `staged`). Includes `empty` boolean for LLM convenience.
  - `hunter_rollback` — hard-resets worktree to a specified commit. Returns the new HEAD. Falls back to input hash if `get_head_commit()` fails after reset.
  - `hunter_redeploy` — kills current Hunter and restarts from updated worktree. Defaults to `resume_session=true` (preserves continuity, unlike `hunter_spawn`). Accepts optional `model` override.

- **`model_tools.py`** — added `hunter.tools.code_tools` to the `_modules` discovery list

**Design decisions:**
- Edit-only (no full-file write): safer, auditable, mirrors Claude Code's Edit tool
- New files via `old_string=""`: keeps one tool with clear semantics
- `since_commit` priority: avoids confusing `staged + since_commit` combination
- Broad exception handling: catches `Exception` not just `WorktreeError` for robustness

**Tests:** 49/49 passing — controller singleton (3), hunter_code_read (6: normal/missing/empty/not-found/worktree-error/utf8-size), hunter_code_edit (13: normal/create/missing-path/missing-old/missing-new/identical/not-found/ambiguous/file-not-found/custom-commit/default-commit/elephantasm/commit-failure), hunter_diff (6: unstaged/staged/since-commit/empty/worktree-error/invalid-commit), hunter_rollback (6: valid/missing/empty/invalid/elephantasm/head-failure), hunter_redeploy (5: defaults/no-resume/model/budget-error/elephantasm), tool registration (5), dispatch integration (5).

---

## 1.4.0 — Overseer Injection Tools (Task 7)

**Date:** 2026-03-11

Runtime injection and monitoring tools — the Overseer can now steer the Hunter at runtime without redeploying.

### Task 7: Overseer Tools — Runtime Injection

Registered `hunter_inject`, `hunter_interrupt`, `hunter_logs` as Hermes tools in the `hunter-overseer` toolset.

**What was built:**

- **`hunter/tools/inject_tools.py`** (~240 lines) — three tool handlers + lazy controller singleton + Elephantasm helper:
  - `hunter_inject` — writes an instruction to `~/.hermes/hunter/injections/current.md` with priority prefix (normal/high/critical). The Hunter's step_callback consumes it on its next iteration. Validates instruction presence and priority value.
  - `hunter_interrupt` — writes an interrupt flag file, waits up to 30s for graceful exit via the Hunter's step_callback, then falls back to `controller.kill()`. Handles race condition where `controller.current` becomes None between checks.
  - `hunter_logs` — returns recent Hunter output from the in-memory rolling buffer via `controller.get_logs()`. Includes `hunter_running` status in response.
  - `_extract_overseer_event()` — best-effort Elephantasm logging. Creates a temporary `OverseerMemoryBridge`, extracts, closes. All exceptions caught at debug level.

- **`model_tools.py`** — added `hunter.tools.inject_tools` to the `_modules` discovery list

**Design decisions:**
- File-based IPC for injection: Overseer writes, Hunter polls each iteration. Simpler than sockets/pipes, survives process restarts
- Priority validation in handler: invalid values return structured error, keeping the LLM in control
- Interrupt uses `controller.current` property (public API) instead of `_current`
- Separate controller singleton per tool module: consistent pattern, clean test isolation

**Tests:** 33/33 passing — controller singleton (3: lazy init, caching, test override), hunter_inject (10: all priorities, missing/empty/invalid args, dir creation, overwrite, Elephantasm logging + error resilience), hunter_interrupt (6: no hunter, graceful exit, force kill, default message, flag file write, race condition), hunter_logs (4: default tail, custom tail, empty, JSON structure), tool registration (6: registry presence, toolset, schemas, OpenAI format), dispatch integration (4: inject/interrupt/logs via dispatch, exception handling).

---

## 1.3.0 — Overseer Process Tools (Task 6)

**Date:** 2026-03-11

First Overseer tools registered in the Hermes tool registry — the Overseer can now spawn, kill, and inspect the Hunter via LLM tool calls.

### Task 6: Overseer Tools — Process Management

Registered `hunter_spawn`, `hunter_kill`, `hunter_status` as Hermes tools in the new `hunter-overseer` toolset.

**What was built:**

- **`hunter/tools/process_tools.py`** (~175 lines) — three tool handlers + lazy controller singleton:
  - `hunter_spawn` — deploys a new Hunter from the `hunter/live` worktree. Accepts optional `model`, `instruction`, and `resume` parameters. Budget-gated: returns `{"error": "..."}` if budget exhausted. Kills any existing Hunter first.
  - `hunter_kill` — terminates the running Hunter via three-stage shutdown (flag → SIGTERM → SIGKILL). Returns `killed` or `no_hunter_running`.
  - `hunter_status` — returns full health snapshot (`running`, `pid`, `session_id`, `model`, `uptime_seconds`, `exit_code`, `error`) plus a human-readable `summary` string.
  - `_get_controller()` — lazily creates a shared `HunterController` with `WorktreeManager` + `BudgetManager`. Deferred imports avoid circular deps. `_set_controller()` exposed for test injection.

- **`toolsets.py`** — added `hunter-overseer` toolset listing all 13 planned Overseer tools (3 registered now, 10 stubs for Tasks 7–9)
- **`model_tools.py`** — added `hunter.tools.process_tools` to the `_modules` discovery list (import errors silently ignored)

**Design decisions:**
- Lazy singleton: created once per process, bridging stateless Hermes tool handlers to the stateful `HunterController`
- RuntimeError → JSON error: budget exhaustion returns a structured error dict rather than crashing the registry dispatch, keeping the Overseer LLM in the loop
- `summary` field in status output: human-readable string so the LLM can interpret health at a glance without parsing every field

**Tests:** 29/29 passing — controller singleton (lazy init, caching, test override), hunter_spawn (7: defaults, model, instruction, resume, all args, budget exhausted, other error), hunter_kill (2: running, none), hunter_status (4: running, stopped, not started, crashed), tool registration (6: names, toolset, schema params, OpenAI format), toolset registration (3: exists, contains process tools, contains all planned tools), dispatch integration (4: spawn/kill/status via dispatch, exception handling).

---

## 1.2.0 — Elephantasm Memory Integration (Task 5)

**Date:** 2026-03-11

Connected both agents to Elephantasm for long-term agentic memory and observability — the foundation for cross-target learning and self-improving intervention strategies.

### Task 5: Elephantasm Integration Layer

Implemented the Elephantasm SDK wrapper providing long-term agentic memory for both agents.

**What was built:**

- **`hunter/memory.py`** (~405 lines) — three classes + safety wrappers:
  - `AnimaManager` — one-time Anima creation + local JSON ID cache (`animas.json`). Idempotent: handles partial cache, API failures, and missing SDK gracefully.
  - `OverseerMemoryBridge` — `inject()` retrieves learned strategies as prompt-ready text; `extract_decision()`, `extract_observation()`, and `extract_intervention_result()` record Overseer actions with importance scoring (non-neutral interventions get 0.9). Auto-generated session IDs (`overseer-YYYYMMDD-HHMMSS`).
  - `HunterMemoryBridge` — `inject()` retrieves vulnerability patterns and similar past findings; `extract_step()` captures tool calls and messages each iteration; `extract_finding()` records vulnerabilities with severity-mapped importance (critical=1.0 → info=0.3); `extract_result()` records session summaries with scalar-only meta filtering; `check_duplicate()` does semantic dedup (similarity >0.85).
  - `_safe_extract()` / `_safe_inject()` — non-fatal wrappers. All Elephantasm errors are logged at WARNING but never propagated. Rate-limit errors trigger 5s backoff.

**Design decisions:**
- Module-level imports with `try/except` (fallback to None when SDK not installed) — makes the module patchable in tests and avoids repeated import overhead
- JSON file cache for Anima IDs — the SDK has no `list_animas` API, only `create_anima`, so we cache `{name: id}` locally
- Non-fatal everywhere — agents function identically whether Elephantasm is up, down, or uninstalled. Memory is a performance enhancer, not a hard dependency.
- Scalar-only meta filtering in `extract_result()` — prevents nested dicts breaking event storage

**SDK findings:**
- `create_anima(name, description)` raises on conflict (no upsert) — hence the cache-first approach
- No `list_animas` or `get_anima_by_name` endpoint — local cache is essential
- `RateLimitError` has no `retry_after` attribute — we use a fixed 5s backoff
- `MemoryPack.content` is raw text; `.as_prompt()` formats for injection
- `ScoredMemory` has `.similarity` (float or None) and `.summary` (str)

**Tests:** 42/42 passing — AnimaManager (9: create/cache/partial/failure/no-SDK/get/missing/no-file/corrupt), OverseerMemoryBridge (11: init, inject prompt/empty/error/no-content, extract decision/observation, intervention result improvement/neutral, non-fatal extract, close, session ID format), HunterMemoryBridge (19: init, set_session, inject prompt/empty, extract_step tool/message/both/truncation, extract_finding high/critical, extract_result scalar filter, check_duplicate found/not-found/no-memories/null-similarity/error, non-fatal extract, close), _severity_to_importance (3: all levels, case-insensitive, unknown default).

---

## 1.1.0 — Phase 1 Foundation (Tasks 1–4)

**Date:** 2026-03-11

Phase 1 goal: the Overseer can spawn, monitor, interrupt, and redeploy a Hunter instance within budget constraints. Tasks 1–4 establish the foundational infrastructure — all subsequent tasks build on these.

### Task 1: Package Scaffolding

Created the `hunter/` Python package with the full module structure for Phase 1.

**What was built:**

- **`hunter/__init__.py`** — package init with version string (`0.1.0`)
- **`hunter/config.py`** (120 lines) — single source of truth for all Hunter paths and constants:
  - 10 path functions: `get_hunter_home()`, `get_budget_config_path()`, `get_spend_ledger_path()`, `get_injection_path()`, `get_interrupt_flag_path()`, `get_hunter_log_dir()`, etc.
  - 8 constants: `HUNTER_BRANCH` (`hunter/live`), `HUNTER_DEFAULT_MODEL` (`qwen/qwen3.5-32b`), `HUNTER_MAX_ITERATIONS` (200), `OVERSEER_DEFAULT_CHECK_INTERVAL` (30s), Elephantasm anima names, etc.
  - `ensure_hunter_home()` — creates the directory tree at `~/.hermes/hunter/`
  - Path functions use runtime evaluation (not module-level constants) so tests can override `HERMES_HOME`
- **12 stub modules** for Tasks 2–12, each with docstrings documenting planned classes/functions
- **`hunter/prompts/`** directory for Overseer system prompt (Task 11)
- **`pyproject.toml`** updated: `hunter = ["elephantasm"]` optional dep, added to `all` extras, setuptools discovery

**Design decisions:**
- `~/.hermes/hunter/` subdirectory (not `~/.hermes/`) for isolation — cleanup is `rm -rf ~/.hermes/hunter/`
- `elephantasm` as optional dependency — standard Hermes users don't need it
- Stubs created upfront so tasks can be worked in parallel without import errors

### Task 2: Budget System

Implemented the full budget management system with config loading, spend tracking, and enforcement.

**What was built:**

- **`hunter/budget.py`** (~300 lines) — three classes:
  - `BudgetManager` — loads `budget.yaml`, watches for config changes via mtime, tracks spend via JSONL ledger, enforces limits
  - `BudgetStatus` — dataclass snapshot: `allowed`, `remaining_usd`, `percent_used`, `alert`, `hard_stop`, `mode`, `spend_today`, `spend_total`, limits
  - `SpendEntry` — dataclass for a single ledger row (timestamp, model, tokens, cost, agent)
  - `parse_budget_string()` — parses CLI shorthand: `"20/day"` → daily mode, `"300/5days"` → total mode with rate limit

**How it works:**

- **Config watching:** `reload()` checks `budget.yaml` mtime each Overseer loop iteration — if unchanged, no I/O. Human can `vim` the file and changes take effect within seconds.
- **Spend tracking:** append-only JSONL ledger (`spend.jsonl`). On startup, replays the ledger to rebuild in-memory totals. Daily spend resets at UTC midnight.
- **Two budget modes:**
  - *Daily* — tracks spend per UTC day against `max_per_day`, alerts at 80%, hard stop at 100%
  - *Total* — tracks all-time spend against `max_total`, also enforces daily rate limit (`max_total / min_days`)
- **Cost estimation:** `estimate_cost()` predicts spend using configured per-model rates (e.g. Qwen 3.5 72B: $1.20/1M tokens, 32B: $0.60, 7B: $0.15)

**Design decisions:**
- JSONL over SQLite: append-only is crash-safe (no transactions, no WAL, no corruption risk), human-readable (`cat | jq`), and months of data stays under 1MB
- mtime polling over inotify: cross-platform (macOS/Linux/Windows), no dependencies, negligible overhead at 30s intervals
- UTC midnight for daily reset: deterministic regardless of timezone, no daylight saving edge cases

**Tests:** 9/9 passing — default config creation, daily and total mode enforcement, config reload, ledger persistence, spend history, cost estimation, CLI string parsing, daily summary aggregation.

### Task 3: Git Worktree Manager

Implemented full git worktree lifecycle management for the Hunter's isolated codebase.

**What was built:**

- **`hunter/worktree.py`** (~280 lines) — three types:
  - `WorktreeManager` — manages the `hunter/live` branch and worktree at `~/.hermes/hunter/worktree/`
  - `CommitInfo` — dataclass: `hash`, `short_hash`, `message`
  - `WorktreeError` — custom exception for git failures
  - Setup/teardown: `setup()` creates branch from HEAD + worktree (idempotent), `teardown()` removes worktree but preserves branch history
  - Status: `is_setup()`, `is_clean()`, `get_head_commit()`, `get_recent_commits(n)`
  - File ops: `read_file()`, `write_file()`, `edit_file()` (unique match required), `delete_file()`, `list_files()`
  - Git ops: `commit()`, `rollback()`, `diff()`, `diff_since()`

**Safety invariants:**
- All git commands default to `cwd=worktree_path` — they target the worktree, never the main repo
- `edit_file()` requires a unique match — ambiguous edits raise `WorktreeError` instead of silently replacing the wrong instance
- `commit()` refuses empty commits
- `_find_repo_root()` walks up from `__file__`, not CWD — works regardless of invocation directory
- 30-second timeout on all git commands — fail fast on hangs

**Design decisions:**
- `write_file()` doesn't auto-commit — allows batching multiple changes into one logical commit, inspecting with `diff()` before committing, or discarding via `rollback()`
- Teardown keeps the branch — commit history is the complete evolution log of the Hunter's codebase, preserved across teardown/re-setup cycles

**Tests:** 20/20 passing — setup, idempotent setup, clean detection, file read/write/edit/delete, commit, head commit, recent commits, edit uniqueness enforcement, diff, diff_since, rollback, empty commit rejection, list_files, teardown, re-setup with history preservation. All tests use temporary git repos.

### Task 4: Hunter Process Controller

Implemented the Hunter process lifecycle manager and subprocess entry point — the core IPC bridge between Overseer and Hunter.

**What was built:**

- **`hunter/control.py`** (~370 lines) — three types:
  - `HunterProcess` — single process lifecycle: `spawn()`, `kill()` (three-stage), `wait()`, `poll()`, `is_alive()`, `get_logs()`, `get_full_log_path()`
  - `HunterController` — singleton ensuring one Hunter at a time: `spawn()` (budget-gated), `kill()`, `redeploy()`, `get_status()`, `get_logs()`, `history` of past runs
  - `HunterStatus` — dataclass: `running`, `pid`, `session_id`, `model`, `uptime_seconds`, `exit_code`, `error`

- **`hunter/runner.py`** (~230 lines) — subprocess entry point (`python -m hunter.runner`):
  - Parses CLI args (session-id, model, toolsets, max-iterations, resume, instruction)
  - Reads initial injection file and builds ephemeral system prompt
  - Creates and runs an AIAgent with a step callback that polls for interrupts and injections each iteration
  - Injection consumption: reads `current.md`, renames to `.consumed`, appends to ephemeral prompt
  - Interrupt detection: reads `interrupt.flag`, calls `agent.interrupt()`

**How the three-stage kill works:**
1. Write `interrupt.flag` → Hunter's step callback sees it → `agent.interrupt()` → graceful exit. Wait `timeout/3`.
2. `SIGTERM` → OS-level graceful termination. Wait `timeout/3`.
3. `SIGKILL` → force kill (last resort). Wait `timeout/3`.

**How injection works:**
1. Overseer writes `~/.hermes/hunter/injections/current.md`
2. Hunter's step callback reads it, renames to `.consumed`, appends content to ephemeral system prompt
3. Next API call includes the instruction (never persisted to conversation history)
4. Overseer sees `.consumed` → knows injection was received

**Design decisions:**
- Subprocess (not in-process): code evolution requires kill/modify/restart with new Python imports
- Merged stdout/stderr: one stream, one capture thread, one buffer — Overseer doesn't need to distinguish for monitoring
- Line-buffered output (`bufsize=1`): real-time monitoring, not chunked
- Rolling buffer (~1MB cap) for quick `get_logs()` + persistent log file for post-mortem
- PYTHONPATH prepends worktree: after code evolution, the subprocess loads the modified code, not the main repo's version

**Tests:** 35/35 passing — HunterStatus serialisation, subprocess spawn/poll, three-stage kill, exit detection, output capture, timeout handling, command building, PYTHONPATH setup, controller budget-gating, controller kill/redeploy, interrupt flag round-trip, injection read/consume, ephemeral prompt assembly (memory + injection combinations), step callback interrupt/injection detection, end-to-end interrupt via flag file with real subprocess. All tests use temporary repos and mock paths.

### Summary of Phase 1 progress

| Component | File | Lines | Tests |
|-----------|------|-------|-------|
| Config & paths | `hunter/config.py` | ~120 | — |
| Budget system | `hunter/budget.py` | ~300 | 9 |
| Worktree manager | `hunter/worktree.py` | ~280 | 20 |
| Process controller | `hunter/control.py` | ~370 | 35 |
| Runner entry point | `hunter/runner.py` | ~230 | (covered by control tests) |
| **Total** | | **~1,300** | **64** |

**Remaining Phase 1 tasks (7–12):** Overseer tool implementations (injection, code editing, budget/model), Overseer main loop, system prompts, and CLI integration.

---

## 1.0.0 — Foundation Fork

**Date:** 2026-03-10

### Context

Hermes Hunter is built on top of **Hermes Agent** by [Nous Research](https://nousresearch.com/) — an open-source, model-agnostic AI agent framework (MIT licensed, Python 3.11+, ~2.7k GitHub stars). We forked the Hermes Agent codebase as the foundation for an autonomous bug bounty hunting system.

### Why Hermes Agent?

Hermes Agent provides the infrastructure we need out of the box, so we can focus on the hunting architecture rather than rebuilding agent fundamentals:

- **AIAgent core loop** (`run_agent.py`) — synchronous conversation loop with tool dispatch, iteration budgets, and session persistence via SQLite. This becomes the runtime for both the Overseer and Hunter agents.
- **Tool registry** (`tools/registry.py`) — centralised registration with schema discovery, handler dispatch, and availability checking. We register our Overseer tools (process control, code editing, budget management) through the same system.
- **Skill system** (`skills/`) — Markdown files loaded into system prompts automatically. Security analysis skills are the primary target for Overseer improvement — the safest and most frequent type of code evolution.
- **40+ built-in tools** — terminal execution, web search, browser automation, file operations, code execution, task delegation. The Hunter inherits all of these for vulnerability analysis.
- **Subagent delegation** (`delegate_task`) — the Hunter spawns subagents for parallel reconnaissance, analysis, and PoC building. The Overseer refines this strategy over time.
- **Multi-platform messaging** — Telegram, Discord, Slack, WhatsApp, Signal. Used for human review notifications and approval flows.
- **6 terminal backends** — local, Docker, SSH, Modal, Daytona, Singularity. The Hunter runs in Docker/Modal for PoC isolation; the Overseer runs locally or on a dedicated VM.
- **Session persistence** (`hermes_state.py`) — SQLite + FTS5 session storage. Enables Hunter session resume after redeploy.
- **Context compression** (`agent/context_compressor.py`) — auto-summarisation near token limits. Critical for long-running Hunter analysis sessions.
- **Process registry** — managed background processes. The Hunter runs as a separate OS process from the Overseer.
- **Interrupt mechanism** (`_interrupt_requested`) — graceful agent shutdown. Repurposed for the Overseer's redeploy protocol.
- **Ephemeral system prompt** — prompt fragments appended at API-call time but never persisted to conversation history. The mechanism for Overseer runtime injection (soft interventions).

### What the fork contains

The full Hermes Agent codebase as of commit `2a062e2`, unmodified except for:

- **`.gitignore`** — minor additions for hunter-specific paths
- **`pyproject.toml`** — added `hunter` optional dependency group (`elephantasm`), added `hermes-agent[hunter]` to the `all` extras, added `hunter` and `hunter.tools` to setuptools package discovery

### Architecture designed

The two-agent meta-architecture was designed and documented:

- **`hjjh/vision.md`** — strategic vision, feasibility assessment, market tier analysis (mid-tier $500–$5K bounties as the primary target), and the case for self-improvement as the competitive edge
- **`hjjh/architecture.md`** — full technical design: system topology, Overseer control loop, Hunter workflow, Elephantasm integration, communication protocols, budget system, performance metrics, code evolution tiers, implementation plan (5 phases, 12 tasks), deployment architecture, and safety/legal guardrails

### Key architectural decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Process model | Separate OS processes | Code evolution requires kill/modify/restart independently |
| Parallelism | One Hunter, unlimited subagents | Hunter manages its own parallelism; Overseer refines strategy |
| Code isolation | Git worktree (`hunter/live` branch) | Shared repo, easy branching, Overseer code unaffected by Hunter changes |
| Budget model | Independent, time-based, dynamically adjustable | Human sets constraints via watched config file |
| LLM selection | Open-source models (Qwen 3.5, Kimi K2.5) | Tiered heavy/medium/light; Overseer selects within budget |
| Memory & observability | Elephantasm | Dual-purpose: long-term agentic memory + event stream monitoring |
| Self-regulation | Overseer learns its own intervention cadence | Via Elephantasm memory of what strategies worked/failed |
| Human involvement | Minimal — review reports only | Overseer reviews first, then presents to human for approval |
| Primary metric | $$$ — reports that earn bounty payouts | Everything else is a supporting signal |
