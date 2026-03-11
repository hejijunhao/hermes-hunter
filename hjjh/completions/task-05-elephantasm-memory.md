# Task 5: Elephantasm Integration Layer — Completion

**Status:** DONE
**Date:** 2026-03-11
**File:** `hunter/memory.py` (~405 lines)
**Tests:** `tests/test_hunter_memory.py` — 42/42 passing

---

## What Was Built

Three classes wrapping the Elephantasm SDK for both agents:

### AnimaManager

Static helper for one-time Anima creation and local ID caching.

- `ensure_animas(cache_path)` — creates both Animas (hermes-overseer, hermes-hunter) on the Elephantasm server, caches IDs to `~/.hermes/hunter/animas.json`. Idempotent: skips creation if cache already has both IDs, only creates missing ones on partial cache.
- `get_anima_id(name, cache_path)` — looks up an Anima ID by name from the local JSON cache.
- Handles: corrupt cache files, API failures during creation, missing elephantasm installation.

### OverseerMemoryBridge

Elephantasm integration for the Overseer agent's control loop.

- `inject(query)` — retrieves relevant memory context (learned strategies, past intervention outcomes) as a prompt-ready string. Returns None on empty or error.
- `extract_decision(decision, meta)` — records an intervention decision (model change, code edit, etc.) as a SYSTEM event.
- `extract_observation(observation, meta)` — records a Hunter monitoring observation, tagged with `type: observation` in meta.
- `extract_intervention_result(intervention_id, verdict, metrics_before, metrics_after)` — records whether an intervention helped, regressed, or was neutral. Non-neutral gets importance 0.9, neutral gets 0.5.
- Auto-generates session ID: `overseer-YYYYMMDD-HHMMSS`.

### HunterMemoryBridge

Elephantasm integration for the Hunter subprocess, wired into the step callback.

- `set_session(session_id)` — binds the bridge to a specific hunt session.
- `inject(query)` — retrieves relevant vulnerability patterns, similar past findings.
- `extract_step(step_info)` — called each iteration, captures tool calls (as TOOL_CALL events) and assistant messages (as MESSAGE_OUT events, truncated to 2000 chars).
- `extract_finding(finding)` — records a vulnerability with severity-mapped importance (critical=1.0, high=0.9, medium=0.7, low=0.5, info=0.3).
- `extract_result(result)` — records session summary, filtering meta to scalar values only.
- `check_duplicate(description)` — semantic search via inject(); returns matching memory summary if similarity > 0.85, else None.

### Error Handling

All Elephantasm calls go through `_safe_extract()` and `_safe_inject()` wrappers:
- Never raise — exceptions are logged at WARNING level
- Rate limit errors are detected and trigger a 5s sleep
- If elephantasm is not installed, `_HAS_ELEPHANTASM = False` disables all SDK features gracefully

---

## Design Decisions

- **Module-level imports with try/except:** `Elephantasm`, `EventType`, and `RateLimitError` are imported at module level (with fallback to None). This makes them patchable in tests and avoids repeated import overhead per method call.
- **JSON file cache for Anima IDs:** The SDK has no `list_animas` or `get_anima` API — only `create_anima`. We cache `{name: id}` in `animas.json` so we only call create once.
- **Non-fatal everywhere:** Both bridges are designed so the agents function identically whether Elephantasm is up, down, or uninstalled. Memory is a performance enhancer, not a hard dependency.
- **Scalar-only meta filtering in extract_result:** Complex nested dicts in meta could break Elephantasm's event storage. We filter to `str|int|float|bool` only.

---

## Test Coverage (42 tests)

| Group | Tests | Coverage |
|-------|-------|----------|
| AnimaManager | 9 | Create both, cache hit, partial cache, create failure, no SDK, get by name, missing, no file, corrupt cache |
| OverseerMemoryBridge | 11 | Init validation, inject (prompt/empty/error/no-content), extract decision/observation, intervention result (improvement/neutral), non-fatal extract, close, session ID format |
| HunterMemoryBridge | 19 | Init validation, set_session, inject (prompt/empty), extract_step (tool/message/both/truncation), extract_finding (high/critical), extract_result (scalar filter), check_duplicate (found/not found/no memories/null similarity/error), non-fatal extract, close |
| _severity_to_importance | 3 | All levels, case-insensitive, unknown default |

---

## Integration Points

- **runner.py** (Task 5 wiring): Lines 239-242 have the stub where `HunterMemoryBridge.inject()` will be wired in to provide `memory_context` for the ephemeral prompt. The step callback in `_make_step_callback()` is where `extract_step()` will be called.
- **overseer.py** (Task 10): The Overseer loop will instantiate `OverseerMemoryBridge` and call `inject()` at the start of each iteration, `extract_*()` after each decision.
- **budget_tools.py** (Task 9): `budget_status` may record spend events via the bridge.

---

## SDK Findings

The Elephantasm SDK (as installed) has these characteristics relevant to our implementation:
- `create_anima(name, description)` raises on conflict (no upsert) — hence the cache-first approach
- No `list_animas` or `get_anima_by_name` — cache is essential
- `RateLimitError` has no `retry_after` attribute — we use a fixed 5s backoff
- `MemoryPack.content` is the raw text; `.as_prompt()` formats it for injection
- `ScoredMemory` has `.similarity` (float or None) and `.summary` (str)
