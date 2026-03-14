# Post-Phase-D Cleanup Plan

Code review of Phases A–D identified 14 issues across production code, deployment artifacts, and tests. Grouped by priority — fix HIGH items before deploying to Fly.io.

---

## HIGH — Fix before deploying

### H1: `httpx.Client` never closed in `FlyHunterController`

**File:** `hunter/backends/fly_api.py:45`, `hunter/backends/fly_control.py`

`FlyMachinesClient.__init__()` creates an `httpx.Client` and exposes `close()` on line 55, but `FlyHunterController` never calls it. In a long-running Overseer (days/weeks), this leaks connections from the pool.

**Fix:** Add `close()` to `FlyHunterController` that delegates to `self._fly.close()`. Call it from Overseer teardown. Alternatively, make `FlyMachinesClient` a context manager (`__enter__`/`__exit__`).

**Test:** Add a test verifying `FlyHunterController.close()` calls `fly_client.close()`.

---

### H2: Partial clone left on disk on `setup()` failure

**File:** `hunter/backends/fly_worktree.py:66-72`

If `git clone` times out (`subprocess.TimeoutExpired`) or fails partway, the partial directory persists. Next call to `setup()` sees the directory, enters the `is_setup()` → `True` branch, and tries `pull` on a corrupted repo — which also fails.

**Fix:** Wrap the clone in try/except for `subprocess.TimeoutExpired` and `subprocess.CalledProcessError`. On failure, `shutil.rmtree()` the partial clone directory, then re-raise as `WorktreeError`.

**Test:** Mock `subprocess.run` to raise `TimeoutExpired`, verify the clone dir is cleaned up and `WorktreeError` is raised.

---

### H3: Dockerfile.overseer silences install failures

**File:** `deploy/Dockerfile.overseer:17`

```dockerfile
RUN pip install --no-cache-dir -e ".[hunter]" 2>/dev/null || true
```

This swallows install errors. If a dependency is broken, the build succeeds but the image is broken at runtime.

**Fix:** Remove `2>/dev/null || true`. The first install (pyproject.toml only, no source) is expected to fail — restructure as:

```dockerfile
COPY pyproject.toml setup.cfg* setup.py* /app/
RUN pip install --no-cache-dir -e ".[hunter]" || echo "Pre-install skipped (source not yet copied)"
COPY . /app
RUN pip install --no-cache-dir -e ".[hunter]"
```

Or accept the first-layer miss and only install after the full `COPY . /app`.

---

### H4: `AUTH_PASSWORD` visible in process listing

**File:** `deploy/overseer-entrypoint.sh:32-33`

ttyd's `--credential` flag puts the password in the process argv, visible via `ps aux` inside the container.

**Fix:** Write credentials to a temp file and use ttyd's file-based auth, or document this as an accepted container-only risk:

```bash
CRED_FILE=$(mktemp)
echo "hermes:${AUTH_PASSWORD}" > "$CRED_FILE"
chmod 600 "$CRED_FILE"
TTYD_ARGS+=("--credential" "file:${CRED_FILE}")
```

Verify ttyd version supports `--credential` with file paths. If not, document the accepted risk.

---

## MEDIUM — Fix soon

### M1: No `subprocess.TimeoutExpired` handling in clone

**File:** `hunter/backends/fly_worktree.py:66`

`subprocess.run(..., timeout=120)` can raise `subprocess.TimeoutExpired`, which propagates raw instead of as a `WorktreeError`. Callers won't catch it correctly.

**Fix:** Catch `TimeoutExpired` alongside `CalledProcessError`, wrap as `WorktreeError("Clone timed out after 120s")`. (Can be combined with H2 fix.)

---

### M2: `kill()` silently swallows `wait_for_state` failure

**File:** `hunter/backends/fly_control.py` (kill method)

The stop and destroy steps log warnings on `FlyAPIError`, but the middle `wait_for_state("stopped")` catches the exception with a bare `pass` — no logging.

**Fix:** Add `logger.warning("Machine %s did not reach 'stopped' state: %s", machine_id, exc)` to match the pattern used by stop and destroy.

---

### M3: Stale TTL cache when machine is externally destroyed

**File:** `hunter/backends/fly_control.py:97-119`

If someone destroys the Fly machine outside the Overseer (via dashboard or API), the `is_running` cache returns `True` for up to 30 seconds. `self._current` is not None, so the early-return on line 104 doesn't fire.

**Fix:** Two options:
1. Accept the 30s window and document it.
2. Have `get_status()` also invalidate the cache when it sees a terminal state.

**Test:** Add test where cache says "running" but API returns "destroyed" after TTL expires — verify it updates.

---

### M4: `resume_session=True` silently ignored when no current machine

**File:** `hunter/backends/fly_control.py:175-178`

If `resume_session=True` but `self._current is None`, a new session ID is generated without warning. The caller believes they're resuming but they're not.

**Fix:** Add a log warning:
```python
if resume_session and self._current is None:
    logger.warning("resume_session=True but no current machine — starting new session")
```

---

### M5: Hunter Dockerfile pipes untrusted script to bash

**File:** `deploy/Dockerfile.hunter:9`

```dockerfile
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
```

Supply-chain risk — a compromised NodeSource endpoint runs arbitrary code during the build.

**Fix:** Use GPG key verification for the NodeSource apt repo, or switch to a multi-stage build using an official `node:20-slim` image to copy the Node binary.

---

### M6: Semgrep installed without version pinning

**File:** `deploy/Dockerfile.hunter:14`

`pip install semgrep` with no version constraint means builds are non-reproducible. A breaking semgrep release could silently break Hunter scans.

**Fix:**
```dockerfile
RUN pip install --no-cache-dir 'semgrep>=1.45.0,<2.0'
```

---

## LOW — Improve when convenient

### L1: Deploy script uses `grep` for JSON parsing

**File:** `scripts/deploy-overseer.sh:25,34`

`fly apps list --json | grep -q "\"$app\""` is fragile — substring matches can produce false positives if one app name is a prefix of another.

**Fix:** Use `jq` for reliable JSON queries:
```bash
if ! fly apps list --json | jq -e ".[] | select(.Name == \"$app\")" >/dev/null 2>&1; then
```

Add `jq` as a prerequisite check at the top of the script.

---

### L2: `_split_sections()` is naive about code blocks

**File:** `hunter/bootstrap.py:314-315`

Any line starting with `## ` is treated as a section header, including lines inside fenced code blocks. With the current `hjjj/architecture.md` this is safe, but it's fragile.

**Fix:** Track fenced code blocks (` ``` `) and skip header detection inside them:
```python
in_code_block = False
for line in content.split("\n"):
    if line.startswith("```"):
        in_code_block = not in_code_block
    if not in_code_block and line.startswith("## "):
        # ... header logic
```

---

### L3: No thread safety on tool module singletons

**Files:** `hunter/tools/process_tools.py:29`, `inject_tools.py:32`, `code_tools.py:33`, `budget_tools.py:29`

`_get_controller()` uses a global `_controller` without a lock. Two concurrent calls could both see `None` and create separate instances.

**Fix:** Safe while the Overseer loop is synchronous. If concurrent access becomes possible, add a `threading.Lock`. For now, add a comment documenting the single-threaded assumption.

---

### L4: `get_testing_targets()` return type is untyped

**File:** `hunter/bootstrap.py:127`

```python
def get_testing_targets() -> list:
```

Should be `list[dict[str, Any]]` for IDE/type-checker support.

---

## Test coverage gaps to close

| Gap | Related item | File to add test in |
|-----|-------------|---------------------|
| `subprocess.TimeoutExpired` during clone | H2, M1 | `tests/test_fly_worktree.py` |
| `FlyHunterController.close()` lifecycle | H1 | `tests/test_fly_control.py` |
| `recover()` with multiple running machines | — | `tests/test_fly_control.py` |
| `resume_session=True` when `_current is None` | M4 | `tests/test_fly_control.py` |
| Incomplete `test_skips_if_arch_doc_missing` (contains `pass`) | — | `tests/test_hunter_bootstrap.py` |
