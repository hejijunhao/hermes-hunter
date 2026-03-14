# Phase D: Bootstrap Mode — Completion Report

## Goal

Enable the Overseer to detect an empty or minimal Hunter repository and enter a special "bootstrap mode" that guides it through building the Hunter from scratch. When the Hunter repo reaches a functionality threshold, the Overseer automatically transitions to normal improvement mode.

**Result:** 3 files created, 2 files modified, 32 new tests added (470 total, zero regressions).

---

## Task D1: Bootstrap Detection

**Problem:** The Overseer assumed a Hunter repo with existing code. An empty repo produced meaningless status checks and iteration prompts with no actionable options.

**File created:** `hunter/bootstrap.py` (348 lines total — shared across D1–D5)

**Change:** `BootstrapState` dataclass + `detect_bootstrap(worktree)` function.

Detection heuristic: calls `worktree.list_files(".", "*.py")` and `worktree.list_files("skills", "*.md")`. Bootstrap mode activates when the repo lacks **both** Python files **and** skill files. Three distinct trigger reasons:
- No Python and no skills → "repo is empty or minimal"
- Skills present but no Python → "no code"
- Python present but no skills → "no security skills"

Both Python **and** skills are required to exit bootstrap — having only one category is insufficient for a functional Hunter.

**File modified:** `hunter/overseer.py` — `_setup()` (line 219–223)

Inserted after worktree setup, before memory bridge init:
```python
from hunter.bootstrap import detect_bootstrap
self._bootstrap_state = detect_bootstrap(self._controller.worktree)
```

**Design choice — re-detect on every startup.** Detection is two `list_files()` calls (cheap). Re-detecting is inherently correct after Overseer restarts — no stale persisted state. The `_bootstrap_state` field is also initialised to `None` in `__init__` (line 128).

---

## Task D2: Bootstrap Prompt Augmentation

### D2a: Bootstrap prompt content

**File created:** `hunter/prompts/bootstrap.md`

Contains the full bootstrap instruction set:
- **Build order** — skills → system prompt → tools → wiring → deploy-and-test → iterate. Safest-first progression: Markdown skills are zero-risk, Python tools are medium-risk.
- **File creation guidance** — how to use `hunter_code_edit` with empty `old_string` to create new files (not obvious from the tool schema alone).
- **Architecture references** — pointers to `hjjh/architecture.md` §3.1 (toolset), §3.2 (workflow), §3.4 (skills), §8 (code evolution).
- **Testing targets** — inline table of 4 known-vulnerable repos (Juice Shop, DVWA, WebGoat, crAPI) with expected vulnerability types (shared with D4).
- **Transition criteria** — explicit thresholds so the Overseer knows what to aim for (shared with D5).

### D2b: Prompt loading

**File modified:** `hunter/bootstrap.py` — added `load_bootstrap_prompt()` (lines 210–220)

Reads `hunter/prompts/bootstrap.md` via `Path(__file__).parent / "prompts" / "bootstrap.md"`. Follows the existing pattern where prompt content lives in `.md` files, not inline strings.

### D2c: Runtime injection into system prompt

**File modified:** `hunter/overseer.py` — `_setup()` (lines 235–238) and `_iteration()` (lines 368–373)

**In `_setup()`:** When bootstrap is detected, load and cache the prompt as `self._bootstrap_prompt`.

**In `_iteration()`:** After memory context injection (step 2, line 366), append bootstrap prompt to `ephemeral_system_prompt` as a new section:
```python
if self._bootstrap_prompt:
    self._agent.ephemeral_system_prompt += (
        "\n\n---\n\n## Bootstrap Mode — ACTIVE\n\n" + self._bootstrap_prompt
    )
```

This follows the existing runtime augmentation pattern — base prompt is set first, memory context is conditionally prepended, bootstrap prompt is conditionally appended. The system prompt structure becomes:
1. Base system prompt + reference docs
2. Memory context (if Elephantasm available)
3. Bootstrap instructions (if bootstrap active)

**Why runtime injection, not static load-time:** The base prompt is reloaded every iteration (line 360). If bootstrap were baked into the static prompt, transitioning mid-session would require restarting. Runtime injection via `_bootstrap_prompt` can be cleared instantly when transition fires.

### D2d: Bootstrap-specific task section

**File modified:** `hunter/overseer.py` — `_build_iteration_prompt()` (lines 466–497)

The standard "Your Task" section offers options like "Do nothing", "Inject instruction", "Spawn Hunter" — none of which make sense for an empty repo. When `self._bootstrap_state.is_bootstrap` is `True`, the task section is replaced with:

```
## Your Task — Bootstrap
The Hunter repository is empty or minimal. Build its capabilities.

1. Check what exists in the worktree (`hunter_code_read`)
2. Decide what to build next (follow the build order above)
3. Write it (`hunter_code_edit` with empty `old_string` creates new files)
4. Commit when a logical unit is complete
5. When enough exists for a test, `hunter_redeploy` and observe
```

Action-oriented rather than evaluative — every bootstrap iteration should produce something.

---

## Task D3: Architecture Docs in Elephantasm

**Problem:** The Overseer needs deep context about what the Hunter should look like. The architecture doc (`hjjh/architecture.md`, 877 lines) has this, but it's too large for a system prompt and needs to be queryable.

**File modified:** `hunter/bootstrap.py` — added `seed_architecture_knowledge()` (lines 237–301)

**Behaviour:**
1. Check `animas.json` for `"architecture_seeded": true`. If present, return `False` (idempotent).
2. Read `hjjh/architecture.md` via `Path(__file__).parent.parent / "hjjh" / "architecture.md"`. Works both locally and in the Docker image (where docs are baked in per Phase C).
3. Split document by `## ` headers using `_split_sections()` helper (lines 304–328).
4. Extract two key sections as high-importance Elephantasm events via `memory.extract_decision()`:
   - `## 3. Hunter Architecture` — the build blueprint (toolset, workflow, skills)
   - `## 8. Code Evolution` — the operating envelope (what the Overseer can modify)
5. Write `"architecture_seeded": true` to the anima cache.

**Why only two sections:** The full doc has 13 sections. Most describe the Overseer (which already knows itself) or deployment (already done). Sections 3 and 8 are the ones the Overseer needs to reference while building the Hunter.

**File modified:** `hunter/overseer.py` — `_setup()` (lines 240–247)

Wired after memory bridge init, inside the bootstrap-active check. Non-fatal — wrapped in `try/except` with warning log.

**Helpers added:**
- `_split_sections(content)` — splits Markdown by `## ` headers into a `{header: content}` dict
- `_load_json(path)` / `_save_json(path, data)` — mirror the pattern from `AnimaManager._load_cache()` / `._save_cache()` in `hunter/memory.py`

---

## Task D4: Testing Target List

**File modified:** `hunter/bootstrap.py` — added `BOOTSTRAP_TESTING_TARGETS` constant (lines 99–124) and `get_testing_targets()` accessor (lines 127–129)

Four known-vulnerable applications with repo URL, tech stack, and expected vulnerability types:

| Target | Stack | Expected Vulns |
|--------|-------|---------------|
| OWASP Juice Shop | Node.js/TypeScript | XSS, SQLi, IDOR, Auth Bypass |
| DVWA | PHP | SQLi, XSS, Command Injection, File Upload |
| WebGoat | Java/Spring | OWASP Top 10 |
| crAPI | Python/Java/Go | BOLA, Broken Auth, Excessive Data Exposure |

The targets appear in two places:
1. **As a constant** in `hunter/bootstrap.py` — accessible programmatically via `get_testing_targets()` for future config-file migration
2. **Inline in `hunter/prompts/bootstrap.md`** — visible to the Overseer in prompt context

**Why `get_testing_targets()` returns a copy:** Prevents callers from mutating the module-level constant.

---

## Task D5: Bootstrap Transition Logic

**File modified:** `hunter/bootstrap.py` — added `TransitionResult` dataclass (lines 141–158), `check_transition()` function (lines 161–203), and threshold constants (lines 136–138)

**Transition criteria (configurable as module constants):**

| Criterion | Threshold | Checked via |
|-----------|-----------|-------------|
| Security skills | `TRANSITION_MIN_SKILLS = 5` | `worktree.list_files("skills", "*.md")` |
| Python files | `TRANSITION_MIN_PYTHON_FILES = 3` | `worktree.list_files(".", "*.py")` |
| Commits | `TRANSITION_MIN_COMMITS = 10` | `worktree.get_recent_commits(10)` |

`TransitionResult` includes a `missing` list describing which criteria are unmet (e.g. `"skills: 2/5"`), an `as_dict()` method for Elephantasm metadata, and individual counts for logging.

**File modified:** `hunter/overseer.py` — `_iteration()` (lines 313–331)

Transition check runs at the **start** of each iteration, before budget check. When all criteria are met:
1. `self._bootstrap_state.is_bootstrap` set to `False`
2. `self._bootstrap_prompt` set to `None`
3. Transition event extracted to Elephantasm with counts as metadata

The transition is immediate — the same iteration that triggers it uses the normal task section. No Overseer restart required.

**Why not require "Hunter produces a finding":** Automatically detecting a successful finding requires parsing Elephantasm events or logs, which is fragile. The file-count thresholds are a minimum bar. The Overseer's own LLM judgment handles the nuance of "is the Hunter actually working?" — it can stay in bootstrap mode beyond the threshold if it decides more building is needed.

**Why threshold constants, not YAML config:** These are implementation details that don't need runtime adjustment. If they need changing, a code change is appropriate.

---

## Task D6: Tests

### `tests/test_hunter_bootstrap.py` (new — 26 tests)

| Class | Tests | What it verifies |
|-------|-------|-----------------|
| `TestDetectBootstrap` | 6 | Empty, README-only, functional, Python-only, skills-only, dataclass return type |
| `TestCheckTransition` | 6 | Empty (3 missing), partial, exact threshold, exceeds threshold, `as_dict()`, shortfall descriptions |
| `TestLoadBootstrapPrompt` | 4 | Non-empty return, contains build order, testing targets, transition criteria |
| `TestTestingTargets` | 3 | Non-empty list, expected dict fields, returns copy |
| `TestSeedArchitectureKnowledge` | 4 | Skips if seeded, extracts sections + writes flag, idempotent on second call, skips if doc missing |
| `TestSplitSections` | 3 | Basic split, empty content, no headers |

**Mock strategy:** `_make_worktree()` helper builds a `MagicMock` with `list_files.side_effect` that dispatches on `pattern` and `relative_dir` args. `_make_commits(n)` builds mock `CommitInfo` lists. `seed_architecture_knowledge` tests use `tmp_path` for cache isolation.

### `tests/test_hunter_overseer.py` — new `TestBootstrap` class (6 tests)

| Test | What it verifies |
|------|-----------------|
| `test_setup_detects_bootstrap_empty_repo` | `_setup()` with empty worktree → `_bootstrap_state.is_bootstrap=True`, `_bootstrap_prompt` loaded |
| `test_setup_no_bootstrap_functional_repo` | `_setup()` with Python + skills → no bootstrap, no prompt |
| `test_iteration_injects_bootstrap_prompt` | `ephemeral_system_prompt` contains "Bootstrap Mode" + prompt content during iteration |
| `test_iteration_uses_bootstrap_task` | `_build_iteration_prompt()` returns bootstrap task, not normal "Do nothing" options |
| `test_normal_mode_task` | Non-bootstrap state → standard task section with "Do nothing" |
| `test_transition_clears_bootstrap` | Worktree meets thresholds → state cleared, prompt removed, Elephantasm event with correct metadata |

**Mock strategy:** Tests that exercise `_setup()` use the existing Phase A pattern: patch `ensure_hunter_home`, `create_controller`, `BudgetManager`, `AnimaManager`, `AIAgent`. The factory mock returns a controller whose `worktree.list_files` is configured per test. Tests that exercise `_iteration()` and `_build_iteration_prompt()` use the `overseer` fixture (pre-configured with mocked dependencies) and manually set `_bootstrap_state` / `_bootstrap_prompt`.

---

## Files Changed Summary

| File | Action | Lines | Purpose |
|------|--------|-------|---------|
| `hunter/bootstrap.py` | **Created** | 348 | Detection, transition, prompt loading, architecture seeding, testing targets |
| `hunter/prompts/bootstrap.md` | **Created** | 74 | Bootstrap mode prompt content |
| `hunter/overseer.py` | Modified | +45 net | Bootstrap detection in `_setup()`, prompt injection + transition in `_iteration()`, bootstrap task in `_build_iteration_prompt()` |
| `tests/test_hunter_bootstrap.py` | **Created** | 232 | 26 unit tests |
| `tests/test_hunter_overseer.py` | Modified | +115 | 6 integration tests in `TestBootstrap` class |

**Totals:** ~420 lines production code, ~350 lines tests. 32 new tests, 470 total passing.

---

## Design Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Bootstrap state persistence | Re-detect on every startup | Cheap (two `list_files()` calls), inherently correct after restarts, no stale-state risk |
| Bootstrap prompt location | Separate `.md` file, not inline string | Follows existing `hunter/prompts/` pattern, keeps `bootstrap.py` focused on logic |
| Prompt injection timing | Runtime per-iteration, not load-time | Allows mid-session transition without restart |
| Architecture seeding sections | §3 (Hunter Architecture) + §8 (Code Evolution) only | Other sections describe the Overseer itself or deployment — not needed for building the Hunter |
| Seeding idempotency | Cache flag in `animas.json` | Mirrors `AnimaManager`'s caching pattern, prevents re-extraction on every restart |
| Transition criteria | File-count thresholds, not "Hunter produces a finding" | Finding detection is fragile; thresholds are a minimum bar; Overseer's LLM judgment handles nuance |
| Testing targets storage | Module constant + inline in prompt | Accessible programmatically via `get_testing_targets()` and visible in prompt context |
| Bootstrap detection heuristic | Requires **both** Python files **and** skills | Either alone is insufficient for a functional Hunter |

---

## Verification

```
$ python -m pytest tests/test_hunter_bootstrap.py -q
26 passed in 0.09s

$ python -m pytest tests/test_hunter_overseer.py -q
59 passed in 0.81s

$ python -m pytest tests/test_hunter_*.py tests/test_fly_*.py -q
470 passed in 48.79s
```

| Test file | Count | Status |
|-----------|-------|--------|
| test_hunter_control.py | 35 | PASS |
| test_hunter_memory.py | 42 | PASS |
| test_hunter_process_tools.py | 29 | PASS |
| test_hunter_inject_tools.py | 33 | PASS |
| test_hunter_code_tools.py | 49 | PASS |
| test_hunter_budget_tools.py | 27 | PASS |
| test_hunter_overseer_prompts.py | 18 | PASS |
| test_hunter_overseer.py | 59 | PASS |
| test_hunter_cli.py | 51 | PASS |
| test_hunter_backends.py | 12 | PASS |
| **test_hunter_bootstrap.py** | **26** | **PASS** |
| test_fly_api.py | 19 | PASS |
| test_fly_config.py | 11 | PASS |
| test_fly_worktree.py | 13 | PASS |
| test_fly_control.py | 46 | PASS |
| **Total** | **470** | **ALL PASS** |

Live E2E deferred until Fly.io infrastructure is provisioned (same as B8/C).
