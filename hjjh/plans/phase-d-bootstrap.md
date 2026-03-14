# Phase D: Bootstrap Mode — Implementation Plan

## Context

Phases A–C + pre-Phase-D hardening are complete. The Overseer can manage a remote Hunter via the Fly Machines API, push code to a remote repo, and deploy to Fly.io. But it assumes a Hunter repo that already has code. Phase D makes the Overseer capable of **building the Hunter from an empty repo** — detecting the empty state, entering a special bootstrap mode with build instructions, seeding architecture knowledge into Elephantasm, and automatically transitioning to normal mode when the Hunter is functional.

---

## New Files

| File | Purpose |
|------|---------|
| `hunter/bootstrap.py` | Detection, transition logic, architecture seeding, testing targets |
| `hunter/prompts/bootstrap.md` | Bootstrap mode prompt content |
| `tests/test_hunter_bootstrap.py` | Unit tests for bootstrap module |

## Modified Files

| File | Changes |
|------|---------|
| `hunter/overseer.py` | `_setup()`: detect bootstrap + seed architecture. `_iteration()`: inject bootstrap prompt + check transition. `_build_iteration_prompt()`: bootstrap-specific task section |
| `tests/test_hunter_overseer.py` | New `TestBootstrap` class |

---

## Task D1: Bootstrap Detection

**File:** `hunter/bootstrap.py` (new)

Create `BootstrapState` dataclass and `detect_bootstrap()` function:

```python
@dataclass
class BootstrapState:
    is_bootstrap: bool
    reason: str
    python_count: int
    skills_count: int

def detect_bootstrap(worktree: WorktreeBackend) -> BootstrapState:
```

**Detection heuristic:** Call `worktree.list_files(".", "*.py")` and `worktree.list_files("skills", "*.md")`. If both return empty lists → bootstrap mode. This handles empty repos, repos with only README, and repos without real Hunter code.

**Wire into `hunter/overseer.py` `_setup()`** — after worktree setup (line 215), before memory bridge init:

```python
from hunter.bootstrap import detect_bootstrap
self._bootstrap_state = detect_bootstrap(self._controller.worktree)
if self._bootstrap_state.is_bootstrap:
    logger.info("Bootstrap mode ACTIVE: %s", self._bootstrap_state.reason)
```

**Design choice — re-detect, don't persist.** Detection is two `list_files()` calls. Re-detecting on every startup is inherently correct after restarts, with no stale-state risk.

---

## Task D2: Bootstrap Prompt Augmentation

### D2a: Bootstrap prompt content

**File:** `hunter/prompts/bootstrap.md` (new)

Contains the full bootstrap instruction set:
- Build order (skills → system prompt → tools → wiring → deploy-and-test → iterate)
- Architecture section references (§3.1 Hunter toolset, §3.2 Hunter workflow, §3.4 Hunter skills)
- How to use `hunter_code_edit` with empty `old_string` to create new files
- Testing targets (from D4, inline in the prompt)
- Transition criteria (from D5, so the Overseer knows when it's done)

### D2b: Prompt loading

**File:** `hunter/bootstrap.py` — add:

```python
def load_bootstrap_prompt() -> str:
    path = Path(__file__).parent / "prompts" / "bootstrap.md"
    return path.read_text(encoding="utf-8")
```

### D2c: Runtime injection

**File:** `hunter/overseer.py`

**In `_setup()`** — cache the prompt if bootstrap is active:

```python
self._bootstrap_prompt: Optional[str] = None
if self._bootstrap_state.is_bootstrap:
    from hunter.bootstrap import load_bootstrap_prompt
    self._bootstrap_prompt = load_bootstrap_prompt()
```

**In `_iteration()` (after line 324, where `ephemeral_system_prompt` is set)** — append bootstrap prompt:

```python
if self._bootstrap_prompt:
    self._agent.ephemeral_system_prompt += (
        "\n\n---\n\n## Bootstrap Mode — ACTIVE\n\n" + self._bootstrap_prompt
    )
```

This follows the existing pattern: base prompt is set, then memory context is conditionally appended. Bootstrap prompt appends after memory context.

### D2d: Bootstrap-specific task section

**In `_build_iteration_prompt()`** — when `self._bootstrap_state.is_bootstrap`, replace the standard "Your Task" section with:

```markdown
## Your Task — Bootstrap

The Hunter repository is empty or minimal. Build its capabilities.

1. Check what exists in the worktree (`hunter_code_read`)
2. Decide what to build next (follow the build order above)
3. Write it (`hunter_code_edit` with empty `old_string` creates new files)
4. Commit when a logical unit is complete
5. When enough exists for a test, `hunter_redeploy` and observe
```

The standard task section (do nothing / inject / modify / spawn) doesn't make sense for an empty repo. The bootstrap task is action-oriented: build something every iteration.

---

## Task D3: Architecture Docs in Elephantasm

**File:** `hunter/bootstrap.py` — add:

```python
def seed_architecture_knowledge(
    memory: OverseerMemoryBridge,
    cache_path: Path | None = None,
) -> bool:
```

**Behaviour:**
1. Check `animas.json` for `"architecture_seeded": true`. If present, return `False`.
2. Read `hjjh/architecture.md` from the Overseer's own repo (via `Path(__file__).parent.parent / "hjjh" / "architecture.md"`)
3. Split by `## ` headers into sections
4. Extract the most relevant sections as high-importance Elephantasm events:
   - §3 Hunter Architecture (toolset, workflow, skills — the build blueprint)
   - §8 Code Evolution (what the Overseer can modify — the operating envelope)
5. Write `"architecture_seeded": true` to the anima cache
6. Return `True`

**Wire into `hunter/overseer.py` `_setup()`** — after memory bridge init, non-fatal:

```python
if self._bootstrap_state.is_bootstrap and self.memory:
    try:
        from hunter.bootstrap import seed_architecture_knowledge
        seed_architecture_knowledge(self.memory)
    except Exception as e:
        logger.warning("Architecture seeding failed (non-fatal): %s", e)
```

**Uses `_safe_extract()` internally** — follows existing pattern where Elephantasm failures never crash the system.

---

## Task D4: Testing Target List

**File:** `hunter/bootstrap.py` — add constant:

```python
BOOTSTRAP_TESTING_TARGETS = [
    {"name": "OWASP Juice Shop", "repo": "juice-shop/juice-shop",
     "stack": "Node.js/TypeScript", "vulns": ["XSS", "SQLi", "IDOR", "Auth Bypass"]},
    {"name": "DVWA", "repo": "digininja/DVWA",
     "stack": "PHP", "vulns": ["SQLi", "XSS", "Command Injection", "File Upload"]},
    {"name": "WebGoat", "repo": "WebGoat/WebGoat",
     "stack": "Java/Spring", "vulns": ["OWASP Top 10"]},
    {"name": "crAPI", "repo": "OWASP/crAPI",
     "stack": "Python/Java/Go", "vulns": ["BOLA", "Broken Auth", "Excessive Data Exposure"]},
]
```

Also exposed via `get_testing_targets() -> list` for future config-file migration.

These targets are **also listed inline in `hunter/prompts/bootstrap.md`** so the Overseer sees them in prompt context without needing to call a function.

---

## Task D5: Bootstrap Transition Logic

**File:** `hunter/bootstrap.py` — add:

```python
TRANSITION_MIN_SKILLS = 5
TRANSITION_MIN_PYTHON_FILES = 3
TRANSITION_MIN_COMMITS = 10

@dataclass
class TransitionResult:
    ready: bool
    skills_count: int
    python_count: int
    commits_count: int
    missing: list[str]

def check_transition(worktree: WorktreeBackend) -> TransitionResult:
```

Checks `list_files("skills", "*.md")`, `list_files(".", "*.py")`, and `get_recent_commits(TRANSITION_MIN_COMMITS)`. Returns `ready=True` when all thresholds are met, with `missing` listing what's still needed.

**Wire into `hunter/overseer.py` `_iteration()`** — at the top, after `self._iteration_count += 1`:

```python
if self._bootstrap_state and self._bootstrap_state.is_bootstrap:
    from hunter.bootstrap import check_transition
    transition = check_transition(self._controller.worktree)
    if transition.ready:
        logger.info("Bootstrap complete — transitioning to normal mode")
        self._bootstrap_state.is_bootstrap = False
        self._bootstrap_prompt = None
        if self.memory:
            self.memory.extract_decision(
                "Bootstrap mode complete. Transitioning to normal improvement mode.",
                meta={"type": "bootstrap_transition",
                      "skills": transition.skills_count,
                      "python_files": transition.python_count,
                      "commits": transition.commits_count},
            )
```

When transition fires, `_bootstrap_prompt` is cleared and subsequent iterations use the standard prompt and task section. No restart needed.

---

## Task D6: Tests

### `tests/test_hunter_bootstrap.py` (new)

| Test | Verifies |
|------|----------|
| `test_detect_empty_repo` | No Python, no skills → `is_bootstrap=True` |
| `test_detect_readme_only` | No Python, no skills (README doesn't count) → `is_bootstrap=True` |
| `test_detect_functional_repo` | Has Python + skills → `is_bootstrap=False` |
| `test_detect_partial_repo` | Has Python but no skills → `is_bootstrap=True` |
| `test_transition_not_ready` | Below thresholds → `ready=False`, `missing` populated |
| `test_transition_ready` | Meets all thresholds → `ready=True`, `missing=[]` |
| `test_load_bootstrap_prompt` | Returns non-empty string from the `.md` file |
| `test_seed_architecture_skips_if_seeded` | Cached flag → returns `False`, no extraction |
| `test_seed_architecture_extracts` | No flag → extracts sections, writes flag, returns `True` |
| `test_get_testing_targets` | Returns non-empty list with expected fields |

### `tests/test_hunter_overseer.py` — new `TestBootstrap` class

| Test | Verifies |
|------|----------|
| `test_setup_detects_bootstrap` | `_setup()` with empty worktree → `_bootstrap_state.is_bootstrap=True` |
| `test_setup_loads_bootstrap_prompt` | `_bootstrap_prompt` is non-None when bootstrap detected |
| `test_iteration_injects_bootstrap_prompt` | `ephemeral_system_prompt` contains "Bootstrap Mode" during iteration |
| `test_iteration_uses_bootstrap_task` | `_build_iteration_prompt()` output contains "Bootstrap" task section |
| `test_iteration_transitions_clears_prompt` | After transition, `_bootstrap_prompt` is `None` |
| `test_normal_mode_no_bootstrap` | Non-empty worktree → no bootstrap state, standard prompt |

---

## Implementation Order

1. **D1 + D4** — `hunter/bootstrap.py` with `BootstrapState`, `detect_bootstrap()`, `BOOTSTRAP_TESTING_TARGETS`
2. **D5** — Add `TransitionResult`, `check_transition()` to same file
3. **D2a** — Create `hunter/prompts/bootstrap.md`
4. **D2b** — Add `load_bootstrap_prompt()` to `hunter/bootstrap.py`
5. **D3** — Add `seed_architecture_knowledge()` to `hunter/bootstrap.py`
6. **D2c+D2d** — Modify `hunter/overseer.py`: `_setup()`, `_iteration()`, `_build_iteration_prompt()`
7. **D6** — Tests

Steps 1–5 are pure additions with no existing code changes. Step 6 is the integration point. Step 7 validates everything.

---

## Verification

1. `python -m pytest tests/test_hunter_bootstrap.py -v` — all bootstrap unit tests pass
2. `python -m pytest tests/test_hunter_overseer.py -v` — existing + new bootstrap tests pass
3. `python -m pytest tests/test_hunter_*.py tests/test_fly_*.py -q` — full suite, zero regressions
4. `python -c "from hunter.bootstrap import detect_bootstrap, check_transition, load_bootstrap_prompt; print('OK')"` — imports work
5. Live E2E deferred until Fly.io infrastructure is provisioned (same as B8/C)
