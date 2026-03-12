# Phase 1: Foundation — Detailed Implementation Plan

## Goal

The Overseer can spawn a Hunter agent as a separate OS process, monitor its activity via Elephantasm event streams, inject runtime instructions, interrupt and redeploy it, and respect dynamically adjustable budget constraints.

**By the end of Phase 1, we can run:**
```bash
hermes hunter overseer          # Start the Overseer loop
hermes hunter spawn             # Manually spawn a Hunter (for testing)
hermes hunter status            # Check Hunter health
hermes hunter budget set 15/day # Adjust budget on the fly
```

---

## Prerequisites

- Python 3.11+ with active venv
- Elephantasm account + API key (`sk_live_...`)
- At least one open-source LLM provider configured (e.g., OpenRouter with Qwen 3.5 access)
- Hermes repo with working `hermes` CLI

---

## Task Overview

| # | Task | Depends On | Estimated Complexity |
|---|------|------------|---------------------|
| 1 | Package scaffolding (`hunter/`) | — | Low |
| 2 | Budget system (`hunter/budget.py`) | 1 | Low |
| 3 | Git worktree manager (`hunter/worktree.py`) | 1 | Medium |
| 4 | Hunter process controller (`hunter/control.py`) | 1, 3 | High |
| 5 | Elephantasm integration layer (`hunter/memory.py`) | 1 | Medium |
| 6 | Overseer tools — process management | 1, 4 | Medium |
| 7 | Overseer tools — runtime injection | 4, 6 | Medium |
| 8 | Overseer tools — code editing & redeploy | 3, 4, 6 | Medium |
| 9 | Overseer tools — budget & memory | 2, 5 | Low |
| 10 | Overseer main loop (`hunter/overseer.py`) | 5, 6, 7, 8, 9 | High |
| 11 | Overseer system prompt & skills | 10 | Medium |
| 12 | CLI entry points (`hermes hunter ...`) | 2, 4, 10 | Medium |
| 13 | Integration testing | All | Medium |

---

## Task 1: Package Scaffolding

**Goal:** Create the `hunter/` package with proper module structure and register it in the project.

### Files to Create

```
hunter/
├── __init__.py           # Package init, version, public API
├── overseer.py           # Overseer main loop (Task 10)
├── control.py            # HunterProcess class, process lifecycle (Task 4)
├── worktree.py           # Git worktree management (Task 3)
├── budget.py             # Budget config loading, watching, enforcement (Task 2)
├── memory.py             # Elephantasm integration layer (Task 5)
├── tools/
│   ├── __init__.py
│   ├── process_tools.py  # hunter_spawn, hunter_kill, hunter_status (Task 6)
│   ├── inject_tools.py   # hunter_inject, hunter_interrupt, hunter_logs (Task 7)
│   ├── code_tools.py     # hunter_code_edit, hunter_code_read, hunter_diff, hunter_rollback, hunter_redeploy (Task 8)
│   └── budget_tools.py   # budget_status, hunter_model_set (Task 9)
└── config.py             # Hunter-specific config constants, paths, defaults
```

### `hunter/__init__.py`

```python
"""Hermes Hunter — autonomous bug bounty hunting system."""

__version__ = "0.1.0"
```

### `hunter/config.py`

Define constants and path helpers used across the package:

```python
from pathlib import Path
from hermes_cli.config import get_hermes_home

# --- Paths ---
def get_hunter_home() -> Path:
    """~/.hermes/hunter/ — root for all Hunter-specific state."""
    return get_hermes_home() / "hunter"

def get_hunter_worktree_path() -> Path:
    """Where the Hunter's git worktree lives."""
    return get_hunter_home() / "worktree"

def get_hunter_state_db_path() -> Path:
    """Local SQLite for operational state (targets, reports queue)."""
    return get_hunter_home() / "state.db"

def get_budget_config_path() -> Path:
    """~/.hermes/hunter/budget.yaml — watched config for budget constraints."""
    return get_hunter_home() / "budget.yaml"

def get_injection_path() -> Path:
    """File-based injection point for Overseer → Hunter runtime instructions."""
    return get_hunter_home() / "injections" / "current.md"

def get_hunter_log_path() -> Path:
    """Hunter process stdout/stderr capture."""
    return get_hunter_home() / "logs"

# --- Defaults ---
HUNTER_BRANCH = "hunter/live"
HUNTER_DEFAULT_TOOLSETS = ["hermes-cli"]  # Will be replaced with "hermes-hunter" in Phase 2
HUNTER_DEFAULT_MODEL = "qwen/qwen3.5-32b"  # Medium tier default
HUNTER_MAX_ITERATIONS = 200  # Per session

# --- Elephantasm ---
OVERSEER_ANIMA_NAME = "hermes-overseer"
HUNTER_ANIMA_NAME = "hermes-hunter"
```

### Registration

Add `hunter` to the project's package list in `pyproject.toml` (or `setup.py`) so it's importable. Also add `elephantasm` as a dependency.

### Acceptance Criteria

- [ ] `from hunter import __version__` works
- [ ] `from hunter.config import get_hunter_home` returns `~/.hermes/hunter/`
- [ ] `elephantasm` is installable via `uv pip install -e ".[all,dev]"`

---

## Task 2: Budget System

**Goal:** A watched YAML config file that the Overseer reads each loop iteration. The human can edit it at any time and changes take effect immediately.

### File: `hunter/budget.py`

### 2.1 Budget Config Schema

```yaml
# ~/.hermes/hunter/budget.yaml
budget:
  mode: "daily"             # "daily" or "total"

  # Mode: daily
  max_per_day: 15.00        # USD per calendar day (UTC)

  # Mode: total
  # max_total: 300.00       # USD total
  # min_days: 5             # Must last at least this many days

  currency: "USD"

  # Circuit breakers
  alert_at_percent: 80      # Notify human when this % of budget consumed
  hard_stop_at_percent: 100  # Kill Hunter when this % consumed

  # Model cost estimates ($/1M tokens, updated by Overseer as it learns)
  model_costs:
    "qwen/qwen3.5-72b": 1.20   # Heavy tier
    "qwen/qwen3.5-32b": 0.60   # Medium tier
    "qwen/qwen3.5-7b": 0.15    # Light tier
```

### 2.2 BudgetManager Class

```python
class BudgetManager:
    """Loads budget config, tracks spend, enforces limits."""

    def __init__(self, config_path: Path = None):
        self.config_path = config_path or get_budget_config_path()
        self._config: dict = {}
        self._last_mtime: float = 0.0
        self._spend_today: float = 0.0
        self._spend_total: float = 0.0
        self._day_start: str = ""  # UTC date string for daily reset
        self.reload()

    def reload(self) -> bool:
        """Reload config from disk if file changed. Returns True if reloaded."""
        # Check mtime, only re-parse if changed
        # Parse YAML, validate schema, update self._config
        # Reset _spend_today if day rolled over

    def record_spend(self, amount_usd: float, model: str = None):
        """Record LLM API spend. Called after each API call."""
        # Add to _spend_today and _spend_total
        # Persist to a small spend ledger file (hunter/spend.jsonl)

    def check_budget(self) -> BudgetStatus:
        """Check current budget status. Called by Overseer each loop."""
        # Returns BudgetStatus with: allowed, remaining, percent_used, alert, hard_stop

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost for a planned API call."""

    def get_status_summary(self) -> str:
        """Human-readable budget status string."""

    def create_default_config(self):
        """Write default budget.yaml if it doesn't exist."""

@dataclass
class BudgetStatus:
    allowed: bool           # Can we keep spending?
    remaining_usd: float    # How much budget is left
    percent_used: float     # 0.0 - 100.0
    alert: bool             # Past alert threshold?
    hard_stop: bool         # Past hard stop threshold?
    mode: str               # "daily" or "total"
    spend_today: float
    spend_total: float
    daily_limit: Optional[float]
    total_limit: Optional[float]
```

### 2.3 Spend Tracking

Spend is tracked in a simple append-only JSONL file at `~/.hermes/hunter/spend.jsonl`:

```json
{"timestamp": "2026-03-11T14:23:01Z", "model": "qwen/qwen3.5-32b", "input_tokens": 4500, "output_tokens": 800, "cost_usd": 0.0032, "agent": "hunter"}
{"timestamp": "2026-03-11T14:23:45Z", "model": "qwen/qwen3.5-32b", "input_tokens": 2100, "output_tokens": 1200, "cost_usd": 0.0020, "agent": "overseer"}
```

On startup, `BudgetManager` replays the ledger to rebuild `_spend_today` and `_spend_total`. Daily spend resets at UTC midnight.

### 2.4 CLI Interface

```bash
hermes hunter budget                    # Show current status
hermes hunter budget set 20/day         # Set daily rate
hermes hunter budget set 300/5days      # Set total with minimum duration
hermes hunter budget history            # Show spend history
```

These commands write to `~/.hermes/hunter/budget.yaml` — the Overseer picks up changes on its next loop iteration.

### Acceptance Criteria

- [ ] `BudgetManager` loads and watches `budget.yaml`
- [ ] `reload()` detects file changes by mtime
- [ ] `record_spend()` appends to spend ledger
- [ ] `check_budget()` returns correct status for daily and total modes
- [ ] Day rollover resets daily spend correctly
- [ ] Default config is created on first run
- [ ] Unit tests for all budget scenarios (under limit, at alert, at hard stop, day rollover, mode switch)

---

## Task 3: Git Worktree Manager

**Goal:** Manage the `hunter/live` branch and git worktree that the Hunter runs from. The Overseer uses this to modify and redeploy the Hunter's code.

### File: `hunter/worktree.py`

### 3.1 WorktreeManager Class

```python
class WorktreeManager:
    """Manages the Hunter's git worktree and branch lifecycle."""

    def __init__(self, repo_root: Path = None, worktree_path: Path = None, branch: str = HUNTER_BRANCH):
        self.repo_root = repo_root or self._find_repo_root()
        self.worktree_path = worktree_path or get_hunter_worktree_path()
        self.branch = branch

    def setup(self) -> None:
        """One-time setup: create hunter/live branch + worktree if they don't exist."""
        # 1. Check if branch exists: git branch --list hunter/live
        # 2. If not, create from current HEAD: git branch hunter/live
        # 3. Check if worktree exists: git worktree list
        # 4. If not, create: git worktree add <worktree_path> hunter/live

    def teardown(self) -> None:
        """Remove worktree (but keep the branch)."""
        # git worktree remove <worktree_path>

    def is_setup(self) -> bool:
        """Check if worktree exists and is valid."""

    def is_clean(self) -> bool:
        """Check if worktree has no uncommitted changes."""
        # git -C <worktree_path> status --porcelain

    def get_head_commit(self) -> str:
        """Get current HEAD commit hash of the worktree."""
        # git -C <worktree_path> rev-parse HEAD

    def get_recent_commits(self, n: int = 10) -> List[Dict[str, str]]:
        """Get last N commits on hunter/live branch."""
        # git -C <worktree_path> log --oneline -n N

    def commit(self, message: str, files: List[str] = None) -> str:
        """Stage files (or all changes) and commit. Returns commit hash."""
        # git -C <worktree_path> add <files or -A>
        # git -C <worktree_path> commit -m <message>

    def rollback(self, commit_hash: str) -> None:
        """Reset worktree to a specific commit."""
        # git -C <worktree_path> reset --hard <commit_hash>

    def diff(self, staged: bool = False) -> str:
        """Show current changes in the worktree."""
        # git -C <worktree_path> diff [--staged]

    def diff_since(self, commit_hash: str) -> str:
        """Show changes between a commit and current HEAD."""
        # git -C <worktree_path> diff <commit_hash>..HEAD

    def read_file(self, relative_path: str) -> str:
        """Read a file from the worktree."""
        # (self.worktree_path / relative_path).read_text()

    def write_file(self, relative_path: str, content: str) -> None:
        """Write a file to the worktree (does NOT auto-commit)."""
        # (self.worktree_path / relative_path).write_text(content)

    def edit_file(self, relative_path: str, old_str: str, new_str: str) -> bool:
        """Find-and-replace edit in a worktree file. Returns success."""

    def _run_git(self, *args, cwd: Path = None) -> subprocess.CompletedProcess:
        """Run a git command. Raises on non-zero exit."""
        # subprocess.run(["git"] + list(args), cwd=cwd or self.worktree_path, ...)

    @staticmethod
    def _find_repo_root() -> Path:
        """Find the Hermes repo root from CWD."""
        # git rev-parse --show-toplevel
```

### 3.2 Worktree Lifecycle

```
First run (hermes hunter overseer):
  1. WorktreeManager.setup() called
  2. Creates hunter/live branch from current main HEAD
  3. Creates worktree at ~/.hermes/hunter/worktree/
  4. Hunter process will run from this path

Subsequent runs:
  1. WorktreeManager.is_setup() → True
  2. Overseer uses existing worktree
  3. Overseer can modify files, commit, rollback as needed

Overseer modifies Hunter code:
  1. Verify is_clean() — if not, commit or stash first
  2. write_file() or edit_file() to make changes
  3. commit("feat(hunter): add IDOR detection skill")
  4. Trigger redeploy (Task 4)

Rollback:
  1. rollback(previous_commit_hash)
  2. Trigger redeploy
```

### 3.3 Important Considerations

- **All git operations use `-C <worktree_path>`** to ensure they target the worktree, not the main repo.
- **The main repo is NEVER modified** by the Overseer's code tools. Only the worktree.
- **Branch sync**: the worktree starts as a fork of main. The Overseer can optionally merge upstream changes from main into `hunter/live` (but this is not a Phase 1 priority).
- **Concurrent access**: the Overseer is the only writer. The Hunter only reads its own codebase at startup. No locking needed.

### Acceptance Criteria

- [ ] `setup()` creates branch and worktree from scratch
- [ ] `setup()` is idempotent (safe to call multiple times)
- [ ] `commit()` returns a valid commit hash
- [ ] `rollback()` resets to the specified commit
- [ ] `read_file()` / `write_file()` / `edit_file()` operate on the worktree only
- [ ] `is_clean()` correctly detects uncommitted changes
- [ ] `_run_git()` raises informative errors on failure
- [ ] Unit tests using a temporary git repo

---

## Task 4: Hunter Process Controller

**Goal:** Spawn the Hunter as a separate OS process, capture its I/O, monitor its health, and support graceful interrupt + restart.

This is the most complex component in Phase 1. It bridges the Overseer's control loop with a live Hunter agent process.

### File: `hunter/control.py`

### 4.1 HunterProcess Class

```python
class HunterProcess:
    """Manages the lifecycle of a single Hunter agent process."""

    def __init__(
        self,
        worktree_path: Path,
        model: str = HUNTER_DEFAULT_MODEL,
        toolsets: List[str] = None,
        max_iterations: int = HUNTER_MAX_ITERATIONS,
        session_id: str = None,
        resume_session: bool = False,
        elephantasm_anima_id: str = None,
    ):
        self.worktree_path = worktree_path
        self.model = model
        self.toolsets = toolsets or HUNTER_DEFAULT_TOOLSETS
        self.max_iterations = max_iterations
        self.session_id = session_id or f"hunter-{uuid.uuid4().hex[:8]}"
        self.resume_session = resume_session
        self.elephantasm_anima_id = elephantasm_anima_id

        # Process state
        self._process: Optional[subprocess.Popen] = None
        self._pid: Optional[int] = None
        self._started_at: Optional[float] = None
        self._exited: bool = False
        self._exit_code: Optional[int] = None

        # I/O capture
        self._stdout_buffer: str = ""
        self._stderr_buffer: str = ""
        self._output_thread: Optional[threading.Thread] = None

        # Interrupt mechanism
        self._interrupt_file: Path = get_hunter_home() / "interrupt.flag"

    def spawn(self) -> None:
        """Start the Hunter as a subprocess."""
        # Build the command to run the Hunter agent
        # The Hunter runs from its worktree, using the hermes CLI entry point
        # Key: the subprocess runs with CWD = worktree_path
        #
        # Command structure:
        #   cd <worktree_path> && python -m hermes_cli.main chat \
        #     --model <model> \
        #     --toolsets <toolsets> \
        #     --max-turns <max_iterations> \
        #     -q "<initial_instruction>"
        #
        # OR: we invoke run_agent.py directly for more control (see §4.3)

    def kill(self, timeout: float = 10.0) -> bool:
        """Send SIGTERM, wait for graceful exit, SIGKILL if needed."""
        # 1. Write interrupt flag file
        # 2. Send SIGTERM
        # 3. Wait up to timeout
        # 4. SIGKILL if still alive
        # 5. Clean up interrupt flag

    def poll(self) -> HunterStatus:
        """Non-blocking health check."""
        # Check if process is alive
        # Return HunterStatus with: running, pid, uptime, last_output, iteration_count

    def wait(self, timeout: float = None) -> int:
        """Block until process exits. Returns exit code."""

    def get_logs(self, tail: int = 100) -> str:
        """Get the last N lines of Hunter output."""
        # Read from _stdout_buffer (rolling window)

    def get_full_log_path(self) -> Path:
        """Path to the Hunter's log file on disk."""

    def is_alive(self) -> bool:
        """Quick check if process is running."""

    @property
    def uptime_seconds(self) -> float:
        """How long the Hunter has been running."""

    def _capture_output(self):
        """Background thread that reads subprocess stdout/stderr."""
        # Runs in a daemon thread
        # Appends to _stdout_buffer (capped at 1MB)
        # Writes to log file for full history
        # Extracts structured events (tool calls, findings) for monitoring

    def _build_hunter_command(self) -> List[str]:
        """Build the subprocess command line."""
        # See §4.3 for details

@dataclass
class HunterStatus:
    running: bool
    pid: Optional[int]
    session_id: str
    model: str
    uptime_seconds: float
    exit_code: Optional[int]
    last_output_line: str
    error: Optional[str]
```

### 4.2 HunterController (Singleton)

Wraps `HunterProcess` with higher-level lifecycle management. Ensures only one Hunter runs at a time.

```python
class HunterController:
    """Singleton controller for the Hunter process. Ensures one Hunter at a time."""

    def __init__(self, worktree: WorktreeManager, budget: BudgetManager):
        self.worktree = worktree
        self.budget = budget
        self._current: Optional[HunterProcess] = None
        self._history: List[Dict[str, Any]] = []  # Past process runs

    def spawn(
        self,
        model: str = None,
        initial_instruction: str = None,
        resume_session: bool = False,
    ) -> HunterProcess:
        """Spawn a new Hunter. Kills existing one if running."""
        # 1. If _current is alive, raise or kill
        # 2. Check budget — refuse if hard_stop
        # 3. Ensure worktree is set up
        # 4. Create HunterProcess
        # 5. Call .spawn()
        # 6. Record in _history

    def kill(self) -> bool:
        """Kill the current Hunter."""

    def redeploy(self, resume_session: bool = True) -> HunterProcess:
        """Kill current Hunter, spawn new one from (potentially modified) worktree."""
        # 1. Save current session_id
        # 2. Kill current
        # 3. Spawn new with resume_session=True

    def get_status(self) -> HunterStatus:
        """Get current Hunter status."""

    def get_logs(self, tail: int = 100) -> str:
        """Get Hunter logs."""

    @property
    def is_running(self) -> bool:
        """Is the Hunter currently alive?"""
```

### 4.3 How the Hunter Process is Launched

The Hunter is a Hermes agent running from the worktree. There are two approaches:

**Option A: CLI subprocess (simpler, more isolated)**
```python
cmd = [
    sys.executable, "-m", "hermes_cli.main", "chat",
    "--model", self.model,
    "--toolsets", ",".join(self.toolsets),
    "--max-turns", str(self.max_iterations),
    "-q", initial_instruction or "Begin autonomous vulnerability hunting.",
]
env = {
    **os.environ,
    "PYTHONPATH": str(self.worktree_path),
    "HERMES_HOME": str(get_hunter_home()),
    "ELEPHANTASM_ANIMA_ID": self.elephantasm_anima_id or HUNTER_ANIMA_NAME,
    # Model/provider config
}
self._process = subprocess.Popen(
    cmd,
    cwd=self.worktree_path,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=env,
)
```

**Option B: Direct AIAgent in subprocess (more control)**
```python
# hunter/runner.py — entry point for the Hunter subprocess
# The Overseer spawns: python -m hunter.runner --session-id <id> --model <model> ...
#
# This gives us full control over:
# - Elephantasm extract() calls wired into the agent loop
# - Injection file polling between iterations
# - Custom system prompt building
# - Interrupt flag checking
```

**Decision: Option B.** We need deep integration with Elephantasm and the injection mechanism. A thin `hunter/runner.py` entry point gives us that control while still running as a separate process.

### 4.4 Hunter Runner Entry Point

```python
# hunter/runner.py
"""Entry point for the Hunter subprocess. Invoked by the Overseer."""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--model", default=HUNTER_DEFAULT_MODEL)
    parser.add_argument("--toolsets", default=",".join(HUNTER_DEFAULT_TOOLSETS))
    parser.add_argument("--max-iterations", type=int, default=HUNTER_MAX_ITERATIONS)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--instruction", default="Begin autonomous vulnerability hunting.")
    args = parser.parse_args()

    # Set up Elephantasm
    from hunter.memory import HunterMemoryBridge
    memory_bridge = HunterMemoryBridge(anima_name=HUNTER_ANIMA_NAME)

    # Inject Elephantasm memory into system prompt
    memory_context = memory_bridge.inject(query="vulnerability hunting strategy and patterns")

    # Build ephemeral system prompt with memory + injections
    ephemeral = _build_hunter_ephemeral_prompt(memory_context)

    # Check for Overseer injections
    injection = _read_injection_file()
    if injection:
        ephemeral += f"\n\n## Overseer Instruction\n{injection}"

    # Create and run the agent
    agent = AIAgent(
        model=args.model,
        enabled_toolsets=args.toolsets.split(","),
        max_iterations=args.max_iterations,
        ephemeral_system_prompt=ephemeral,
        session_id=args.session_id,
        # ... other config
    )

    # Install iteration hook for Elephantasm extraction + injection polling
    original_step = agent.step_callback
    def instrumented_step(step_info):
        memory_bridge.extract_step(step_info)
        _check_interrupt_flag(agent)
        _check_injection_file(agent)
        if original_step:
            original_step(step_info)
    agent.step_callback = instrumented_step

    # Run
    result = agent.run_conversation(args.instruction)

    # Extract final result to Elephantasm
    memory_bridge.extract_result(result)

if __name__ == "__main__":
    main()
```

### 4.5 Interrupt Mechanism

The Overseer signals the Hunter to stop via a flag file:

```
Overseer calls hunter_interrupt:
  1. Write "INTERRUPT: <message>" to ~/.hermes/hunter/interrupt.flag
  2. The Hunter's step_callback checks for this file each iteration
  3. If found, calls agent.interrupt(message)
  4. Hunter finishes current tool, saves session, exits
  5. Overseer's kill() waits for process exit, cleans up flag file
```

This is cleaner than SIGTERM because:
- The Hunter exits gracefully (saves session state)
- The Overseer can pass a message explaining why
- No platform-specific signal handling needed

SIGTERM is the fallback if the flag file approach hangs.

### 4.6 Injection Polling

Between each iteration, the Hunter checks for new instructions:

```
~/.hermes/hunter/injections/current.md exists?
  → Read content
  → Append to ephemeral_system_prompt for next API call
  → Rename to current.md.consumed (so it's not re-read)
  → Overseer knows injection was consumed when .consumed exists
```

### Acceptance Criteria

- [ ] `HunterProcess.spawn()` launches a subprocess that runs an AIAgent from the worktree
- [ ] `HunterProcess.kill()` gracefully stops the Hunter (flag file → SIGTERM → SIGKILL)
- [ ] `HunterProcess.poll()` returns accurate health status
- [ ] `HunterProcess.get_logs()` returns recent output
- [ ] `HunterController` enforces single-Hunter constraint
- [ ] `HunterController.redeploy()` kills and restarts with session resume
- [ ] `hunter/runner.py` runs as a standalone entry point
- [ ] Interrupt flag file mechanism works end-to-end
- [ ] Injection file polling works between iterations
- [ ] Integration test: spawn Hunter, inject instruction, verify it's consumed, interrupt, verify clean exit

---

## Task 5: Elephantasm Integration Layer

**Goal:** A clean wrapper around the Elephantasm SDK that both the Overseer and Hunter use for memory extraction and injection.

### File: `hunter/memory.py`

### 5.1 Setup

```bash
pip install elephantasm
# Set environment variable:
export ELEPHANTASM_API_KEY="sk_live_..."
```

### 5.2 AnimalManager (One-Time Setup)

```python
class AnimaManager:
    """Manages Elephantasm Anima creation and configuration."""

    @staticmethod
    def ensure_animas() -> Dict[str, str]:
        """Create Overseer and Hunter Animas if they don't exist. Returns {name: id} map."""
        from elephantasm import create_anima

        animas = {}
        for name, description in [
            (OVERSEER_ANIMA_NAME, "Meta-agent that monitors and improves the Hunter"),
            (HUNTER_ANIMA_NAME, "Bug bounty Hunter agent that finds vulnerabilities"),
        ]:
            try:
                anima = create_anima(name, description=description)
                animas[name] = anima.id
            except Exception:
                # Already exists — look up by name (or cache locally)
                pass
        return animas

    @staticmethod
    def get_anima_id(name: str) -> str:
        """Get Anima ID by name from local cache."""
        # Cache in ~/.hermes/hunter/animas.json
```

### 5.3 OverseerMemoryBridge

```python
class OverseerMemoryBridge:
    """Elephantasm integration for the Overseer agent."""

    def __init__(self, anima_id: str = None):
        from elephantasm import Elephantasm
        self.anima_id = anima_id or AnimaManager.get_anima_id(OVERSEER_ANIMA_NAME)
        self.client = Elephantasm(anima_id=self.anima_id)
        self._session_id = f"overseer-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

    def inject(self, query: str = None) -> Optional[str]:
        """Get relevant memory context for the Overseer's current loop iteration."""
        pack = self.client.inject(query=query)
        return pack.as_prompt() if pack else None

    def extract_decision(self, decision: str, meta: dict = None):
        """Record an Overseer decision (intervention, model change, etc.)."""
        self.client.extract(
            EventType.SYSTEM,
            content=decision,
            session_id=self._session_id,
            meta=meta or {},
        )

    def extract_observation(self, observation: str, meta: dict = None):
        """Record an Overseer observation about the Hunter."""
        self.client.extract(
            EventType.SYSTEM,
            content=observation,
            session_id=self._session_id,
            meta={"type": "observation", **(meta or {})},
        )

    def extract_intervention_result(self, intervention_id: str, verdict: str, metrics_before: dict, metrics_after: dict):
        """Record the outcome of an intervention for learning."""
        self.client.extract(
            EventType.SYSTEM,
            content=f"Intervention {intervention_id} result: {verdict}",
            session_id=self._session_id,
            importance_score=0.9 if verdict != "neutral" else 0.5,
            meta={
                "type": "intervention_result",
                "intervention_id": intervention_id,
                "verdict": verdict,
                "metrics_before": metrics_before,
                "metrics_after": metrics_after,
            },
        )

    def close(self):
        self.client.close()
```

### 5.4 HunterMemoryBridge

```python
class HunterMemoryBridge:
    """Elephantasm integration for the Hunter agent. Used inside hunter/runner.py."""

    def __init__(self, anima_id: str = None):
        from elephantasm import Elephantasm
        self.anima_id = anima_id or AnimaManager.get_anima_id(HUNTER_ANIMA_NAME)
        self.client = Elephantasm(anima_id=self.anima_id)
        self._session_id: str = ""

    def set_session(self, session_id: str):
        self._session_id = session_id

    def inject(self, query: str = None) -> Optional[str]:
        """Get relevant memory for the Hunter's current task."""
        pack = self.client.inject(query=query)
        return pack.as_prompt() if pack else None

    def extract_step(self, step_info: dict):
        """Called by the Hunter's step_callback after each iteration."""
        # Extract tool calls
        if "tool_call" in step_info:
            self.client.extract(
                EventType.TOOL_CALL,
                content=f"{step_info['tool_call']['name']}({json.dumps(step_info['tool_call'].get('args', {}))})",
                session_id=self._session_id,
                meta=step_info.get("meta", {}),
            )
        # Extract assistant messages
        if "assistant_message" in step_info:
            self.client.extract(
                EventType.MESSAGE_OUT,
                content=step_info["assistant_message"][:2000],  # Truncate for event storage
                session_id=self._session_id,
                role="assistant",
            )

    def extract_finding(self, finding: dict):
        """Record a vulnerability finding."""
        self.client.extract(
            EventType.SYSTEM,
            content=f"Vulnerability found: {finding['title']} ({finding['severity']})",
            session_id=self._session_id,
            importance_score=_severity_to_importance(finding["severity"]),
            meta={
                "type": "finding",
                "cwe": finding.get("cwe"),
                "severity": finding["severity"],
                "target": finding.get("target"),
            },
        )

    def extract_result(self, result: dict):
        """Record the final result of a Hunter session."""
        self.client.extract(
            EventType.SYSTEM,
            content=f"Session complete. Findings: {result.get('findings_count', 0)}",
            session_id=self._session_id,
            meta={"type": "session_result", **{k: v for k, v in result.items() if isinstance(v, (str, int, float, bool))}},
        )

    def check_duplicate(self, description: str) -> Optional[str]:
        """Check if a similar finding exists in memory. Returns matching memory summary or None."""
        pack = self.client.inject(query=description)
        if pack and pack.long_term_memories:
            top = pack.long_term_memories[0]
            if top.similarity and top.similarity > 0.85:
                return top.summary
        return None

    def close(self):
        self.client.close()

def _severity_to_importance(severity: str) -> float:
    return {"critical": 1.0, "high": 0.9, "medium": 0.7, "low": 0.5, "info": 0.3}.get(severity.lower(), 0.5)
```

### 5.5 Error Handling

All Elephantasm calls should be **non-fatal**. If the API is down or rate-limited:
- `inject()` returns `None` — agent runs without memory context
- `extract()` failures are logged but don't stop the agent
- `RateLimitError` is caught and retried with `retry_after`

```python
def _safe_extract(client, *args, **kwargs):
    """Extract with error handling. Never raises."""
    try:
        return client.extract(*args, **kwargs)
    except RateLimitError as e:
        time.sleep(e.retry_after or 5)
        try:
            return client.extract(*args, **kwargs)
        except Exception:
            logger.warning("Elephantasm extract failed after retry", exc_info=True)
    except Exception:
        logger.warning("Elephantasm extract failed", exc_info=True)
    return None
```

### Acceptance Criteria

- [ ] `AnimaManager.ensure_animas()` creates both Animas (or handles "already exists")
- [ ] `OverseerMemoryBridge.inject()` returns a prompt string or None
- [ ] `OverseerMemoryBridge.extract_decision()` sends an event to Elephantasm
- [ ] `HunterMemoryBridge.extract_step()` handles the step_callback dict format
- [ ] `HunterMemoryBridge.check_duplicate()` returns a match summary for similar findings
- [ ] All Elephantasm calls are non-fatal (wrapped in error handling)
- [ ] Unit tests with mocked Elephantasm client
- [ ] Integration test with real Elephantasm API (can be marked as slow/optional)

---

## Task 6: Overseer Tools — Process Management

**Goal:** Register `hunter_spawn`, `hunter_kill`, `hunter_status` as Hermes tools the Overseer can call.

### File: `hunter/tools/process_tools.py`

### 6.1 Tool Schemas & Handlers

```python
from tools.registry import registry

# --- hunter_spawn ---
registry.register(
    name="hunter_spawn",
    toolset="hunter-overseer",
    schema={
        "type": "function",
        "function": {
            "name": "hunter_spawn",
            "description": "Deploy a new Hunter agent instance from the current hunter/live worktree. "
                           "Kills any existing Hunter first. The Hunter will begin autonomous vulnerability hunting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "LLM model for the Hunter (e.g., 'qwen/qwen3.5-32b'). Defaults to medium tier.",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "Initial instruction for the Hunter. Defaults to general hunting directive.",
                    },
                    "resume": {
                        "type": "boolean",
                        "description": "Resume from the Hunter's last session instead of starting fresh.",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
    handler=_handle_hunter_spawn,
    description="Spawn a new Hunter agent process",
)

def _handle_hunter_spawn(args: dict, **kwargs) -> str:
    controller = _get_controller()
    process = controller.spawn(
        model=args.get("model"),
        initial_instruction=args.get("instruction"),
        resume_session=args.get("resume", False),
    )
    return json.dumps({
        "status": "spawned",
        "session_id": process.session_id,
        "model": process.model,
        "pid": process._pid,
    })


# --- hunter_kill ---
registry.register(
    name="hunter_kill",
    toolset="hunter-overseer",
    schema={...},  # No parameters needed
    handler=_handle_hunter_kill,
    description="Terminate the running Hunter process",
)

def _handle_hunter_kill(args: dict, **kwargs) -> str:
    controller = _get_controller()
    success = controller.kill()
    return json.dumps({"status": "killed" if success else "no_hunter_running"})


# --- hunter_status ---
registry.register(
    name="hunter_status",
    toolset="hunter-overseer",
    schema={...},  # No parameters
    handler=_handle_hunter_status,
    description="Get Hunter health status",
)

def _handle_hunter_status(args: dict, **kwargs) -> str:
    controller = _get_controller()
    status = controller.get_status()
    return json.dumps(asdict(status))
```

### 6.2 Controller Singleton

```python
_controller: Optional[HunterController] = None

def _get_controller() -> HunterController:
    global _controller
    if _controller is None:
        from hunter.worktree import WorktreeManager
        from hunter.budget import BudgetManager
        worktree = WorktreeManager()
        budget = BudgetManager()
        _controller = HunterController(worktree=worktree, budget=budget)
    return _controller
```

### 6.3 Toolset Registration

Add to `toolsets.py`:

```python
"hunter-overseer": {
    "description": "Tools for the Overseer to manage the Hunter agent",
    "tools": [
        "hunter_spawn", "hunter_kill", "hunter_status",
        "hunter_logs", "hunter_inject", "hunter_interrupt",
        "hunter_code_edit", "hunter_code_read", "hunter_diff",
        "hunter_rollback", "hunter_redeploy",
        "hunter_model_set", "budget_status",
    ],
    "includes": [],
},
```

And add the import to `model_tools.py` `_modules` list:
```python
"hunter.tools.process_tools",
"hunter.tools.inject_tools",
"hunter.tools.code_tools",
"hunter.tools.budget_tools",
```

### Acceptance Criteria

- [ ] `hunter_spawn` creates a running Hunter process
- [ ] `hunter_kill` stops the Hunter cleanly
- [ ] `hunter_status` returns accurate health info
- [ ] Tools are visible in `hermes --list-tools` when `hunter-overseer` toolset is enabled
- [ ] Controller singleton is properly initialised
- [ ] Budget check prevents spawn when budget exhausted

---

## Task 7: Overseer Tools — Runtime Injection

**Goal:** Register `hunter_inject`, `hunter_interrupt`, `hunter_logs` as tools.

### File: `hunter/tools/inject_tools.py`

### 7.1 Tool Definitions

```python
# --- hunter_inject ---
# Writes an instruction to the injection file. The Hunter reads it on its next iteration.
schema = {
    "parameters": {
        "properties": {
            "instruction": {
                "type": "string",
                "description": "The instruction to inject into the Hunter's next iteration. "
                               "This is appended to the Hunter's ephemeral system prompt.",
            },
            "priority": {
                "type": "string",
                "enum": ["normal", "high", "critical"],
                "description": "Priority level. 'critical' means the Hunter should drop what it's doing.",
                "default": "normal",
            },
        },
        "required": ["instruction"],
    },
}

def _handle_hunter_inject(args: dict, **kwargs) -> str:
    instruction = args["instruction"]
    priority = args.get("priority", "normal")

    injection_path = get_injection_path()
    injection_path.parent.mkdir(parents=True, exist_ok=True)

    prefix = {"normal": "", "high": "HIGH PRIORITY: ", "critical": "CRITICAL — DROP CURRENT TASK: "}
    content = f"{prefix[priority]}{instruction}"

    injection_path.write_text(content)

    # Extract to Elephantasm
    _extract_overseer_event(f"Injected {priority} instruction: {instruction[:200]}")

    return json.dumps({"status": "injected", "priority": priority})


# --- hunter_interrupt ---
# Signals the Hunter to stop gracefully.
def _handle_hunter_interrupt(args: dict, **kwargs) -> str:
    message = args.get("message", "Overseer requested interrupt.")
    controller = _get_controller()

    if not controller.is_running:
        return json.dumps({"status": "no_hunter_running"})

    # Write interrupt flag
    interrupt_path = get_hunter_home() / "interrupt.flag"
    interrupt_path.write_text(message)

    # Wait for graceful exit (up to 30s), then force kill
    try:
        controller._current.wait(timeout=30)
        status = "interrupted_gracefully"
    except TimeoutError:
        controller.kill()
        status = "force_killed"

    return json.dumps({"status": status, "message": message})


# --- hunter_logs ---
# Returns recent Hunter output.
def _handle_hunter_logs(args: dict, **kwargs) -> str:
    tail = args.get("tail", 100)
    controller = _get_controller()
    logs = controller.get_logs(tail=tail)
    return json.dumps({"logs": logs, "lines": tail})
```

### Acceptance Criteria

- [ ] `hunter_inject` writes instruction to injection file
- [ ] `hunter_interrupt` triggers graceful shutdown via flag file
- [ ] `hunter_interrupt` falls back to force kill after timeout
- [ ] `hunter_logs` returns recent output
- [ ] Injection file is consumed by Hunter on next iteration (tested end-to-end)

---

## Task 8: Overseer Tools — Code Editing & Redeploy

**Goal:** Register tools that let the Overseer modify the Hunter's source code and redeploy.

### File: `hunter/tools/code_tools.py`

### 8.1 Tool Definitions

```python
# --- hunter_code_read ---
def _handle_hunter_code_read(args: dict, **kwargs) -> str:
    path = args["path"]  # Relative to worktree root
    worktree = _get_controller().worktree
    try:
        content = worktree.read_file(path)
        return json.dumps({"path": path, "content": content})
    except FileNotFoundError:
        return json.dumps({"error": f"File not found: {path}"})

# --- hunter_code_edit ---
def _handle_hunter_code_edit(args: dict, **kwargs) -> str:
    path = args["path"]
    # Supports two modes: full write or find-and-replace
    worktree = _get_controller().worktree

    if "content" in args:
        # Full file write
        worktree.write_file(path, args["content"])
    elif "old_string" in args and "new_string" in args:
        # Find-and-replace
        success = worktree.edit_file(path, args["old_string"], args["new_string"])
        if not success:
            return json.dumps({"error": "old_string not found in file"})
    else:
        return json.dumps({"error": "Provide either 'content' (full write) or 'old_string'+'new_string' (edit)"})

    # Auto-commit
    commit_msg = args.get("commit_message", f"overseer: edit {path}")
    commit_hash = worktree.commit(commit_msg, files=[path])

    return json.dumps({"status": "edited_and_committed", "path": path, "commit": commit_hash})

# --- hunter_diff ---
def _handle_hunter_diff(args: dict, **kwargs) -> str:
    worktree = _get_controller().worktree
    since = args.get("since_commit")
    if since:
        diff = worktree.diff_since(since)
    else:
        diff = worktree.diff()
    return json.dumps({"diff": diff})

# --- hunter_rollback ---
def _handle_hunter_rollback(args: dict, **kwargs) -> str:
    commit = args["commit"]
    worktree = _get_controller().worktree
    worktree.rollback(commit)
    return json.dumps({"status": "rolled_back", "to_commit": commit})

# --- hunter_redeploy ---
def _handle_hunter_redeploy(args: dict, **kwargs) -> str:
    controller = _get_controller()
    resume = args.get("resume_session", True)
    model = args.get("model")  # Optional model change

    process = controller.redeploy(resume_session=resume)
    if model:
        # Model change applied via the new spawn
        pass

    return json.dumps({
        "status": "redeployed",
        "session_id": process.session_id,
        "model": process.model,
        "resumed": resume,
    })
```

### Acceptance Criteria

- [ ] `hunter_code_read` reads files from the worktree (not the main repo)
- [ ] `hunter_code_edit` supports both full write and find-replace modes
- [ ] `hunter_code_edit` auto-commits changes
- [ ] `hunter_diff` shows uncommitted changes or changes since a commit
- [ ] `hunter_rollback` resets the worktree to a specific commit
- [ ] `hunter_redeploy` kills + restarts the Hunter with updated code
- [ ] Integration test: edit a skill file → commit → redeploy → verify Hunter loads new skill

---

## Task 9: Overseer Tools — Budget & Model

**Goal:** Register `budget_status` and `hunter_model_set` tools.

### File: `hunter/tools/budget_tools.py`

### 9.1 Tool Definitions

```python
# --- budget_status ---
def _handle_budget_status(args: dict, **kwargs) -> str:
    budget = _get_controller().budget
    budget.reload()  # Pick up any config changes
    status = budget.check_budget()
    return json.dumps(asdict(status))

# --- hunter_model_set ---
def _handle_hunter_model_set(args: dict, **kwargs) -> str:
    model = args["model"]
    apply_to = args.get("apply_to", "hunter")  # "hunter" or "subagents" or "all"

    controller = _get_controller()

    if apply_to in ("hunter", "all"):
        # Requires redeploy to take effect
        # Store as pending model change; applied on next redeploy
        _store_pending_model(model)
        needs_redeploy = True
    if apply_to in ("subagents", "all"):
        # Can be injected at runtime (changes delegate_task model param)
        _inject_subagent_model(model)
        needs_redeploy = False

    return json.dumps({
        "status": "model_updated",
        "model": model,
        "apply_to": apply_to,
        "needs_redeploy": needs_redeploy,
    })
```

### Acceptance Criteria

- [ ] `budget_status` returns current budget info after reloading config
- [ ] `hunter_model_set` stores model preference
- [ ] Model change for Hunter requires redeploy (correctly flagged)
- [ ] Model change for subagents can be injected at runtime

---

## Task 10: Overseer Main Loop

**Goal:** The Overseer's continuous monitoring and improvement loop, implemented as a Hermes agent with a specialised system prompt and toolset.

### File: `hunter/overseer.py`

### 10.1 OverseerLoop Class

```python
class OverseerLoop:
    """The Overseer's main control loop. Wraps an AIAgent with the hunter-overseer toolset."""

    def __init__(
        self,
        model: str = "anthropic/claude-opus-4.6",  # Overseer uses best available
        budget: BudgetManager = None,
        memory: OverseerMemoryBridge = None,
        check_interval: float = 30.0,  # Seconds between loop iterations
    ):
        self.model = model
        self.budget = budget or BudgetManager()
        self.memory = memory or OverseerMemoryBridge()
        self.check_interval = check_interval
        self._running = False
        self._agent: Optional[AIAgent] = None

    def run(self) -> None:
        """Start the Overseer loop. Blocks until interrupted."""
        self._running = True
        self._setup()

        while self._running:
            try:
                self._iteration()
            except KeyboardInterrupt:
                self._shutdown()
                break
            except Exception as e:
                logger.error(f"Overseer loop error: {e}", exc_info=True)
                self.memory.extract_decision(f"Loop error: {e}", meta={"type": "error"})

            time.sleep(self.check_interval)

    def stop(self):
        """Signal the loop to stop after current iteration."""
        self._running = False

    def _setup(self):
        """One-time setup at loop start."""
        # 1. Ensure Elephantasm Animas exist
        AnimaManager.ensure_animas()

        # 2. Ensure budget config exists
        self.budget.create_default_config()

        # 3. Ensure worktree is set up
        worktree = WorktreeManager()
        worktree.setup()

        # 4. Build initial Overseer agent
        self._agent = self._create_agent()

    def _iteration(self):
        """Single iteration of the Overseer loop."""
        # 1. Reload budget (picks up human changes)
        self.budget.reload()
        budget_status = self.budget.check_budget()

        if budget_status.hard_stop:
            logger.warning("Budget exhausted. Killing Hunter.")
            _get_controller().kill()
            self.memory.extract_decision("Budget exhausted — killed Hunter")
            return

        # 2. Inject Overseer memory
        memory_context = self.memory.inject(
            query="recent interventions, hunter performance, current strategy"
        )

        # 3. Build the iteration prompt
        # This is the "user message" the Overseer agent processes each loop
        prompt = self._build_iteration_prompt(budget_status, memory_context)

        # 4. Run the Overseer agent for one turn
        # The agent has access to all hunter_* tools and will decide what to do
        result = self._agent.run_conversation(
            user_message=prompt,
            conversation_history=self._agent_history,
        )

        # 5. Extract the Overseer's actions to Elephantasm
        self.memory.extract_decision(
            f"Loop iteration complete. Action: {result.get('final_response', 'none')[:500]}",
            meta={"budget_remaining": budget_status.remaining_usd},
        )

        # 6. Record spend
        if "api_calls" in result:
            for call in result["api_calls"]:
                self.budget.record_spend(call.get("cost_usd", 0.0), model=self.model)

    def _build_iteration_prompt(self, budget: BudgetStatus, memory_context: Optional[str]) -> str:
        """Build the prompt for this loop iteration."""
        parts = []

        parts.append("## Current Status")
        parts.append(f"Budget: ${budget.remaining_usd:.2f} remaining ({budget.percent_used:.0f}% used)")

        # Hunter status
        controller = _get_controller()
        if controller.is_running:
            status = controller.get_status()
            parts.append(f"Hunter: running (pid {status.pid}, uptime {status.uptime_seconds:.0f}s)")
            parts.append(f"Hunter model: {status.model}")
            # Include recent logs for the Overseer to evaluate
            recent_logs = controller.get_logs(tail=50)
            if recent_logs:
                parts.append(f"\n## Recent Hunter Activity\n```\n{recent_logs}\n```")
        else:
            parts.append("Hunter: not running")

        if memory_context:
            parts.append(f"\n## Your Memory Context\n{memory_context}")

        parts.append("\n## Your Task")
        parts.append(
            "Review the Hunter's status and recent activity. Decide whether to:\n"
            "- Do nothing (Hunter is performing well)\n"
            "- Inject a runtime instruction (soft intervention)\n"
            "- Modify Hunter code and redeploy (hard intervention)\n"
            "- Change the Hunter's model (cost optimization)\n"
            "- Spawn the Hunter if it's not running\n"
            "- Check budget and adjust model tier if needed\n"
            "\n"
            "Use your tools to take action. If no action is needed, say so briefly."
        )

        return "\n".join(parts)

    def _create_agent(self) -> AIAgent:
        """Create the Overseer's AIAgent instance."""
        return AIAgent(
            model=self.model,
            enabled_toolsets=["hunter-overseer"],
            max_iterations=20,  # Per loop iteration, not total
            ephemeral_system_prompt=_load_overseer_system_prompt(),
            session_id=f"overseer-{datetime.utcnow().strftime('%Y%m%d')}",
            quiet_mode=True,
        )

    def _shutdown(self):
        """Clean shutdown."""
        logger.info("Overseer shutting down...")
        self.memory.extract_decision("Overseer shutting down (KeyboardInterrupt)")
        self.memory.close()
```

### 10.2 Conversation History Management

The Overseer runs continuously, but context windows are finite. Strategy:

- Each loop iteration is a **new conversation turn** (user message = status update, assistant response = action taken)
- The conversation history grows over the session
- When the context approaches the limit, use the existing `context_compressor` to summarise and continue
- Alternatively, start a fresh conversation every N iterations, relying on Elephantasm memory for continuity

```python
# In _iteration():
self._agent_history.append({"role": "user", "content": prompt})
result = self._agent.run_conversation(prompt, conversation_history=self._agent_history)
self._agent_history.append({"role": "assistant", "content": result["final_response"]})

# Trim history if it gets too long (keep last 20 turns + rely on Elephantasm for older context)
if len(self._agent_history) > 40:
    self._agent_history = self._agent_history[-20:]
```

### 10.3 First-Run Behaviour

On the very first run (no Hunter has ever been spawned):

1. Overseer sees "Hunter: not running"
2. Overseer's natural response (given its system prompt) should be to call `hunter_spawn`
3. If the worktree doesn't have any Hunter-specific tools yet (Phase 2), the Hunter will just be a vanilla Hermes agent — that's fine for testing the control loop

### Acceptance Criteria

- [ ] `OverseerLoop.run()` starts and runs continuously
- [ ] Each iteration reloads budget, checks Hunter status, and lets the agent decide
- [ ] Budget hard stop kills the Hunter
- [ ] Memory context is injected each iteration
- [ ] Agent has access to all hunter-overseer tools
- [ ] KeyboardInterrupt triggers clean shutdown
- [ ] Conversation history is managed (doesn't grow unbounded)
- [ ] Integration test: start Overseer → it spawns Hunter → monitor for 3 iterations → shut down

---

## Task 11: Overseer System Prompt & Skills

**Goal:** Create the Overseer's system prompt and initial skills that guide its behaviour.

### 11.1 System Prompt

File: `hunter/prompts/overseer_system.md`

```markdown
You are the Overseer — a meta-agent responsible for continuously improving
a bug-bounty Hunter agent. Your Hunter is a separate Hermes agent instance
running from a git worktree you control.

Your job is NOT to find vulnerabilities yourself. Your job is to make the
Hunter better at finding them.

## What You Do

1. **Monitor** the Hunter's activity, decisions, and output
2. **Evaluate** whether the Hunter is producing high-quality vulnerability
   reports that are likely to earn bounty payouts
3. **Intervene** when you see problems — either soft (runtime instruction)
   or hard (code change + redeploy)
4. **Optimize** model selection and resource allocation within budget
5. **Learn** from your own intervention history (your memory helps here)

## Intervention Modes

- **SOFT** — `hunter_inject`: Send a runtime instruction. Use for tactical
  steering ("focus on the auth module", "try IDOR on /api/users/{id}").
  Low risk, immediate effect.

- **HARD** — `hunter_code_edit` + `hunter_redeploy`: Modify the Hunter's
  source code. Use for systemic improvements ("add a new security skill",
  "improve the report template"). Medium risk, requires monitoring.

- **MODEL** — `hunter_model_set`: Change the LLM model. Use for cost
  optimization ("recon doesn't need the heavy model").

Always prefer soft over hard. Always prefer small changes over large ones.

## Decision Framework

Each iteration, ask yourself:
1. Is the Hunter running? If not, should it be?
2. Is it stuck or looping? → Inject guidance or interrupt
3. Is it finding real vulnerabilities? → If not, why? What skill is missing?
4. Is report quality high enough to earn payouts? → If not, improve the
   report-writing skill
5. Are we on budget? → Adjust model tier if needed
6. Did my last intervention help? → If not, what should I try differently?

## What "Good" Looks Like

The ultimate metric is: **high-quality vulnerability reports that earn
bounty payouts.** Everything else is a supporting signal.

A good report has:
- Clear title and accurate severity (CVSS)
- Correct CWE classification
- Detailed steps to reproduce
- Working proof of concept
- Realistic impact assessment
- Actionable remediation advice

## Rules

- NEVER modify your own code or system prompt
- Always commit changes with clear messages before redeploying
- Monitor the impact of every hard intervention
- If a change causes regression, rollback immediately
- Respect budget constraints absolutely — hard stop means hard stop
- When in doubt, observe rather than intervene
```

### 11.2 Overseer Skills

File: `skills/overseer/intervention-strategy/SKILL.md`

```markdown
---
name: intervention-strategy
description: How to decide when and how to intervene with the Hunter
category: overseer
---

# Intervention Strategy

## When to Intervene

### Do Nothing When:
- Hunter is making steady progress through a target
- Recent findings are legitimate and well-documented
- No errors or repeated failures in logs
- Budget usage is on track

### Soft Intervention When:
- Hunter is spending too long on one area (redirect focus)
- Hunter missed an obvious attack vector you can see in the logs
- A new target or priority has been identified
- Hunter's approach is correct but needs tactical refinement

### Hard Intervention When:
- Hunter repeatedly fails at a specific vulnerability class
- Report quality is consistently low (missing sections, weak PoCs)
- A new skill or tool would systematically improve performance
- Hunter's workflow ordering is inefficient

### Model Change When:
- Budget is running low but Hunter is in a phase that doesn't need heavy compute
- Hunter is in deep analysis phase and would benefit from a heavier model
- Subagents are doing simple tasks on an expensive model

## Intervention Sizing

- **Skill addition/edit**: Safest. Just Markdown. Do this first.
- **Prompt tweak**: Low risk. Changes how Hunter thinks, not what it can do.
- **Tool parameter change**: Medium risk. Test mentally before deploying.
- **Tool logic change**: Higher risk. Keep changes small. Monitor closely.
- **Core agent change**: Highest risk. Only if absolutely necessary.

## Post-Intervention Monitoring

After any hard intervention:
1. Watch the next 3-5 Hunter iterations closely
2. Compare output quality before vs. after
3. If regression → rollback immediately, don't try to fix forward
4. Record the outcome in your decision log (Elephantasm handles this)
```

File: `skills/overseer/budget-management/SKILL.md`

```markdown
---
name: budget-management
description: How to manage LLM costs within budget constraints
category: overseer
---

# Budget Management

## Model Tier Selection

| Phase | Recommended Tier | Rationale |
|-------|-----------------|-----------|
| Recon (cloning, doc reading) | Light | Low reasoning needed |
| Attack surface mapping | Medium | Some analysis needed |
| Static analysis interpretation | Medium-Heavy | Complex reasoning |
| Novel vulnerability hunting | Heavy | Maximum capability |
| PoC building | Medium | Mostly code generation |
| Report writing | Heavy | Quality matters most here |
| Subagent bulk tasks | Light | Parallel + cheap |

## Budget Strategies

### Comfortable (< 50% used)
- Use heavy model for Hunter main
- Medium for subagents
- Don't optimize — focus on quality

### Cautious (50-80% used)
- Drop subagents to light
- Keep Hunter on medium unless in deep analysis
- Start being selective about targets

### Critical (> 80% used)
- Switch Hunter to medium
- Subagents to light only
- Focus on finishing current target, don't start new ones
- Consider if remaining budget is better spent on reports than new hunting
```

### 11.3 Prompt Loading

```python
# In hunter/overseer.py
def _load_overseer_system_prompt() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "overseer_system.md"
    return prompt_path.read_text()
```

### Acceptance Criteria

- [ ] System prompt file exists and loads correctly
- [ ] Skills are in the standard Hermes skill format (YAML frontmatter + Markdown)
- [ ] Overseer agent uses the system prompt when created
- [ ] Skills are discoverable by the Overseer agent

---

## Task 12: CLI Entry Points

**Goal:** Add `hermes hunter` subcommands for starting the Overseer, checking status, and managing budget.

### File: Modify `hermes_cli/main.py`

### 12.1 Subcommand Structure

```
hermes hunter overseer              # Start the Overseer loop
hermes hunter overseer --model X    # Start with specific Overseer model
hermes hunter spawn                 # Manually spawn a Hunter (testing)
hermes hunter spawn --model X       # Spawn with specific model
hermes hunter kill                  # Kill the running Hunter
hermes hunter status                # Show Hunter + Overseer status
hermes hunter budget                # Show budget status
hermes hunter budget set 20/day     # Set daily budget
hermes hunter budget set 300/5days  # Set total budget with min duration
hermes hunter budget history        # Show spend history
hermes hunter logs                  # Tail Hunter logs
hermes hunter logs --follow         # Follow Hunter logs (like tail -f)
hermes hunter setup                 # One-time setup (worktree, Elephantasm Animas, budget config)
```

### 12.2 Implementation

```python
# hunter/cli.py
"""CLI entry points for `hermes hunter` subcommands."""

import argparse

def register_hunter_commands(subparsers):
    """Register all `hermes hunter *` subcommands."""
    hunter_parser = subparsers.add_parser("hunter", help="Bug bounty hunting system")
    hunter_sub = hunter_parser.add_subparsers(dest="hunter_command")

    # hermes hunter overseer
    overseer_parser = hunter_sub.add_parser("overseer", help="Start the Overseer control loop")
    overseer_parser.add_argument("--model", default=None, help="Overseer LLM model")
    overseer_parser.add_argument("--interval", type=float, default=30.0, help="Check interval in seconds")

    # hermes hunter spawn
    spawn_parser = hunter_sub.add_parser("spawn", help="Manually spawn a Hunter")
    spawn_parser.add_argument("--model", default=None, help="Hunter LLM model")
    spawn_parser.add_argument("--instruction", default=None, help="Initial instruction")
    spawn_parser.add_argument("--resume", action="store_true", help="Resume previous session")

    # hermes hunter kill
    hunter_sub.add_parser("kill", help="Kill the running Hunter")

    # hermes hunter status
    hunter_sub.add_parser("status", help="Show system status")

    # hermes hunter budget ...
    budget_parser = hunter_sub.add_parser("budget", help="Budget management")
    budget_sub = budget_parser.add_subparsers(dest="budget_command")
    budget_sub.add_parser("status", help="Show budget status")  # default if no subcommand
    set_parser = budget_sub.add_parser("set", help="Set budget")
    set_parser.add_argument("value", help="Budget value (e.g., '20/day', '300/5days')")
    budget_sub.add_parser("history", help="Show spend history")

    # hermes hunter logs
    logs_parser = hunter_sub.add_parser("logs", help="Show Hunter logs")
    logs_parser.add_argument("--follow", "-f", action="store_true", help="Follow logs")
    logs_parser.add_argument("--tail", type=int, default=50, help="Number of lines")

    # hermes hunter setup
    hunter_sub.add_parser("setup", help="One-time setup")


def handle_hunter_command(args):
    """Dispatch hermes hunter subcommands."""
    cmd = args.hunter_command

    if cmd == "overseer":
        from hunter.overseer import OverseerLoop
        loop = OverseerLoop(model=args.model, check_interval=args.interval)
        loop.run()

    elif cmd == "spawn":
        from hunter.control import HunterController
        # ... spawn and print status

    elif cmd == "kill":
        # ... kill and print status

    elif cmd == "status":
        # ... print Hunter + budget status

    elif cmd == "budget":
        _handle_budget_command(args)

    elif cmd == "logs":
        # ... tail or follow logs

    elif cmd == "setup":
        from hunter.worktree import WorktreeManager
        from hunter.memory import AnimaManager
        from hunter.budget import BudgetManager
        WorktreeManager().setup()
        AnimaManager.ensure_animas()
        BudgetManager().create_default_config()
        print("Setup complete.")
```

### 12.3 Integration with Main CLI

In `hermes_cli/main.py`, add the hunter subcommand registration:

```python
# In the argument parser setup
from hunter.cli import register_hunter_commands, handle_hunter_command
register_hunter_commands(subparsers)

# In the dispatch logic
if args.command == "hunter":
    handle_hunter_command(args)
```

### Acceptance Criteria

- [ ] `hermes hunter setup` creates worktree, Animas, and budget config
- [ ] `hermes hunter overseer` starts the Overseer loop
- [ ] `hermes hunter spawn` manually spawns a Hunter
- [ ] `hermes hunter kill` kills the Hunter
- [ ] `hermes hunter status` shows current system state
- [ ] `hermes hunter budget` shows budget info
- [ ] `hermes hunter budget set 20/day` updates budget config
- [ ] `hermes hunter logs` shows recent Hunter output
- [ ] All commands have `--help` text

---

## Task 13: Integration Testing

**Goal:** Verify the full Phase 1 system works end-to-end.

### 13.1 Test Scenarios

**Test 1: Setup & Teardown**
```
1. hermes hunter setup
   → Creates ~/.hermes/hunter/ directory structure
   → Creates hunter/live branch and worktree
   → Creates Elephantasm Animas
   → Creates default budget.yaml
2. Verify all paths exist
3. Clean up
```

**Test 2: Manual Hunter Lifecycle**
```
1. hermes hunter spawn --model qwen/qwen3.5-7b --instruction "List the files in the current directory and report what you see."
2. hermes hunter status → running
3. hermes hunter logs → see Hunter output
4. hermes hunter kill → clean exit
5. hermes hunter status → not running
```

**Test 3: Injection Flow**
```
1. Spawn Hunter with a long-running instruction
2. hermes hunter inject "Stop what you're doing and summarise your progress so far."
3. Verify Hunter receives and acts on the injection
4. Kill Hunter
```

**Test 4: Code Edit & Redeploy**
```
1. Spawn Hunter
2. Use hunter_code_edit to add a test file to the worktree
3. Verify file exists in worktree (not in main repo)
4. Verify commit was created on hunter/live
5. Redeploy Hunter
6. Verify new Hunter process started
```

**Test 5: Budget Enforcement**
```
1. Set budget to $0.01/day
2. Record $0.02 of spend
3. Verify check_budget() returns hard_stop=True
4. Verify Overseer kills Hunter when budget exhausted
```

**Test 6: Overseer Loop (Short Run)**
```
1. Start Overseer with check_interval=5
2. Verify it spawns a Hunter on first iteration
3. Let it run for 3 iterations
4. Verify Elephantasm events are being captured
5. Ctrl+C → verify clean shutdown
```

**Test 7: Rollback**
```
1. Get current HEAD of hunter/live
2. Make a code edit (hunter_code_edit)
3. Verify new commit
4. Rollback to original HEAD
5. Verify code is reverted
```

### 13.2 Test Infrastructure

```python
# tests/hunter/conftest.py

@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary git repo for testing."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True)
    return repo

@pytest.fixture
def worktree_manager(temp_repo, tmp_path):
    """WorktreeManager configured to use temp paths."""
    wt_path = tmp_path / "worktree"
    return WorktreeManager(repo_root=temp_repo, worktree_path=wt_path)

@pytest.fixture
def budget_manager(tmp_path):
    """BudgetManager with temp config path."""
    return BudgetManager(config_path=tmp_path / "budget.yaml")

@pytest.fixture
def mock_elephantasm(monkeypatch):
    """Mock Elephantasm client for unit tests."""
    # Patch elephantasm.Elephantasm with a mock that records calls
    ...
```

### Acceptance Criteria

- [ ] All 7 test scenarios pass
- [ ] Unit tests exist for BudgetManager, WorktreeManager, HunterProcess
- [ ] Integration tests can run with mocked Elephantasm (fast) or real API (slow)
- [ ] No test writes to `~/.hermes/` (uses temp directories)
- [ ] Tests are in `tests/hunter/` directory

---

## Implementation Order

The recommended build order, accounting for dependencies:

```
Week 1:
  Task 1  → Package scaffolding (1-2 hours)
  Task 2  → Budget system (half day)
  Task 3  → Git worktree manager (half day)

Week 2:
  Task 5  → Elephantasm integration (half day)
  Task 4  → Hunter process controller (1-2 days) ← hardest task
  Task 6  → Process management tools (half day)

Week 3:
  Task 7  → Injection tools (half day)
  Task 8  → Code editing tools (half day)
  Task 9  → Budget & model tools (2-3 hours)

Week 4:
  Task 10 → Overseer main loop (1 day)
  Task 11 → System prompt & skills (half day)
  Task 12 → CLI entry points (half day)
  Task 13 → Integration testing (1 day)
```

Tasks 1-3 and 5 can be built in parallel (no interdependencies). Task 4 depends on 1 and 3. Tasks 6-9 depend on 4. Task 10 depends on everything.

---

## Definition of Done (Phase 1 Complete)

- [ ] `hermes hunter setup` creates all infrastructure from scratch
- [ ] `hermes hunter overseer` starts a loop that spawns and monitors a Hunter
- [ ] The Overseer can inject instructions that the Hunter receives
- [ ] The Overseer can edit Hunter code, commit, and redeploy
- [ ] Budget constraints are enforced (hard stop kills the Hunter)
- [ ] Budget can be changed at runtime by editing the YAML file
- [ ] Both agents capture events to Elephantasm
- [ ] Both agents inject Elephantasm memory at iteration start
- [ ] The Overseer's event stream is visible on the Elephantasm dashboard
- [ ] The Hunter's event stream is visible on the Elephantasm dashboard
- [ ] All unit tests pass
- [ ] Integration test demonstrates full spawn → inject → edit → redeploy → kill cycle
