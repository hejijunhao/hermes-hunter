# Hermes Hunter — Changelog

- **1.3.0** — Overseer process tools: `hunter_spawn`, `hunter_kill`, `hunter_status` registered in `hunter-overseer` toolset
- **1.2.0** — Elephantasm memory integration: `AnimaManager`, `OverseerMemoryBridge`, `HunterMemoryBridge`
- **1.1.0** — Phase 1 foundation: package scaffolding, budget system, worktree manager, process controller
- **1.0.0** — Foundation fork of Hermes Agent + architecture design

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
