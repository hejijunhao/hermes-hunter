# Post-Phase-D Cleanup — Completion Report

## Goal

Address 14 code-review issues identified across production code, deployment artifacts, and tests from Phases A–D. Grouped by priority: HIGH (deployment blockers), MEDIUM (robustness/security), LOW (polish).

**Result:** 10 files modified, 8 new tests added, 113 total tests passing (zero regressions).

---

## HIGH — H1: `httpx.Client` never closed in `FlyHunterController`

**Files:** `hunter/backends/fly_api.py`, `hunter/backends/fly_control.py`

**Change:** Added `__enter__`/`__exit__` context manager methods to `FlyMachinesClient`, and a `close()` method to `FlyHunterController` that delegates to `self._fly.close()`.

**Tests added:** `TestFlyMachinesClientInit.test_context_manager`, `TestFlyMachinesClientInit.test_close_delegates`, `TestClose.test_close_delegates_to_fly_client`.

---

## HIGH — H2 + MEDIUM — M1: Partial clone left on disk on `setup()` failure

**File:** `hunter/backends/fly_worktree.py`

**Change:** Wrapped the `subprocess.run` clone call in try/except for both `TimeoutExpired` and `CalledProcessError`. On failure, `shutil.rmtree()` removes the partial clone directory, then re-raises as `WorktreeError` with a descriptive message.

**Tests added:** `TestSetupCloneFailure.test_timeout_cleans_up_partial_clone`, `test_clone_error_cleans_up_partial_clone`, `test_timeout_no_dir_still_raises`.

---

## HIGH — H3: Dockerfile.overseer silences install failures

**File:** `deploy/Dockerfile.overseer`

**Change:** Replaced `2>/dev/null || true` with `|| echo "Pre-install skipped (source not yet copied)"`. Also added `setup.cfg*` and `setup.py*` to the COPY layer for better cache utilisation. The second install (after full source copy) has no fallback — failures correctly break the build.

---

## HIGH — H4: `AUTH_PASSWORD` visible in process listing

**File:** `deploy/overseer-entrypoint.sh`

**Change:** Credentials are now written to a `chmod 600` temp file. ttyd receives `--credential file:/path` instead of inline `hermes:password`, so the password no longer appears in `ps aux`.

---

## MEDIUM — M2: `kill()` silently swallows `wait_for_state` failure

**File:** `hunter/backends/fly_control.py`

**Change:** Replaced bare `pass` with `logger.warning("Machine %s did not reach 'stopped' state: %s", machine_id, exc)` to match the logging pattern used by `stop_machine` and `destroy_machine` handlers.

---

## MEDIUM — M3: Stale TTL cache when machine is externally destroyed

**File:** `hunter/backends/fly_control.py`

**Change:** `get_status()` now synchronises the `is_running` cache when it observes a non-running state from the API. This means if a machine is externally destroyed, the next `get_status()` call (which bypasses the cache) will update the cache for subsequent `is_running` checks.

**Test added:** `TestGetStatusCacheSync.test_get_status_invalidates_cache_on_non_running`.

---

## MEDIUM — M4: `resume_session=True` silently ignored when no current machine

**File:** `hunter/backends/fly_control.py`

**Change:** Added a warning log when `resume_session=True` but `self._current is None` and no explicit `session_id` is provided.

**Test added:** `TestResumeSessionWarning.test_warns_when_no_current_machine`.

---

## MEDIUM — M5: Hunter Dockerfile pipes untrusted script to bash

**File:** `deploy/Dockerfile.hunter`

**Change:** Replaced `curl | bash` NodeSource install with a multi-stage build. Node.js binary is copied from the official `node:20-slim` image via `COPY --from=node-bin`, eliminating the supply-chain risk.

---

## MEDIUM — M6: Semgrep installed without version pinning

**File:** `deploy/Dockerfile.hunter` (same edit as M5)

**Change:** `pip install semgrep` → `pip install 'semgrep>=1.45.0,<2.0'` for reproducible builds.

---

## LOW — L1: Deploy script uses `grep` for JSON parsing

**File:** `scripts/deploy-overseer.sh`

**Change:** Replaced `grep -q` JSON checks with `jq -e` queries for both app and volume existence checks. Added a `jq` prerequisite check at the top of the script.

---

## LOW — L2: `_split_sections()` is naive about code blocks

**File:** `hunter/bootstrap.py`

**Change:** Added `in_code_block` tracking — lines starting with `` ``` `` toggle the flag, and `## ` header detection is skipped inside fenced code blocks.

**Test added:** `TestSplitSections.test_ignores_headers_inside_code_blocks`.

---

## LOW — L3: No thread safety on tool module singletons

**Files:** `hunter/tools/process_tools.py`, `code_tools.py`, `inject_tools.py`, `budget_tools.py`

**Change:** Added a comment above each `_controller = None` global documenting the single-threaded assumption and noting that a `threading.Lock` should be added if concurrent access becomes possible.

---

## LOW — L4: `get_testing_targets()` return type is untyped

**File:** `hunter/bootstrap.py`

**Change:** `def get_testing_targets() -> list:` → `def get_testing_targets() -> list[dict[str, Any]]:`. Added `Any` to the typing imports.

---

## Test coverage gaps closed

| Gap | Fix | Test file |
|-----|-----|-----------|
| `subprocess.TimeoutExpired` during clone | H2+M1 | `tests/test_fly_worktree.py` |
| `FlyHunterController.close()` lifecycle | H1 | `tests/test_fly_control.py` |
| `resume_session=True` when `_current is None` | M4 | `tests/test_fly_control.py` |
| `get_status()` cache sync on non-running | M3 | `tests/test_fly_control.py` |
| Context manager for `FlyMachinesClient` | H1 | `tests/test_fly_api.py` |
| `_split_sections` code block awareness | L2 | `tests/test_hunter_bootstrap.py` |
| Incomplete `test_skips_if_arch_doc_missing` | — | `tests/test_hunter_bootstrap.py` |

Also extracted `_ARCHITECTURE_DOC_PATH` as a module-level constant in `hunter/bootstrap.py` to make the arch-doc-missing test patchable without complex Path mocking.
