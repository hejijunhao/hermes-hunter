# Phase B: Fly.io Remote Backend — Implementation Plan

## Goal

Implement the Fly.io backend so the Overseer can manage a remote Hunter machine via the Fly Machines API and push code to a remote GitHub repo. All existing tool handlers continue working unchanged — the backend swap is transparent via the `ControlBackend` / `WorktreeBackend` protocols established in Phase A.

**Entry state:** Phase A complete. Protocols defined, factory in place, `create_controller(mode="fly")` raises `NotImplementedError`.

**Exit state:** `create_controller(mode="fly")` returns a working `FlyHunterController` backed by `FlyWorktreeManager`. The Overseer can spawn a Hunter on a separate Fly machine, push code changes to its repo, inject instructions, read logs, and kill it — all through the same tool handlers that work locally.

---

## Architecture Overview

```
Overseer (Fly Machine A)
  │
  ├─ FlyHunterController (implements ControlBackend)
  │    ├─ spawn()  → POST /v1/apps/{app}/machines  (create + start)
  │    ├─ kill()   → POST /v1/apps/{app}/machines/{id}/stop
  │    ├─ status() → GET  /v1/apps/{app}/machines/{id}
  │    └─ logs()   → GET  /v1/apps/{app}/machines/{id}/logs (Fly Logs Nats endpoint)
  │
  ├─ FlyWorktreeManager (implements WorktreeBackend)
  │    ├─ Local git clone of Hunter repo (same file ops as WorktreeManager)
  │    ├─ commit() → stages + commits locally
  │    └─ push()   → git push origin main (triggers Hunter to pull on next boot)
  │
  └─ Injection adapter
       ├─ inject()    → Elephantasm event (Hunter polls via inject())
       └─ interrupt() → Fly Machines API stop (hard stop, no flag file needed)
```

### Key Design Decision: Clone, Not API

The `FlyWorktreeManager` works on a **local clone** of the Hunter's GitHub repo, not via the GitHub API. This means all existing file operations (`read_file`, `write_file`, `edit_file`, `diff`, `rollback`, etc.) work identically — they're just `git -C <clone_path>` commands, same as the local `WorktreeManager`. The only difference is that `push()` actually pushes to the remote, and the Hunter pulls on boot.

This avoids reimplementing every file operation against a REST API and reuses the battle-tested `WorktreeManager` logic.

### Key Design Decision: Injection via Elephantasm, Not Files

Locally, injection works via a shared filesystem (`~/.hermes/hunter/injections/current.md`). Remotely, there's no shared filesystem. Two options:

1. **Elephantasm events** — the Overseer writes an injection event; the Hunter's `step_callback` queries for recent injections instead of reading a file. This is the architecturally correct approach — Elephantasm is already the cross-machine communication layer.
2. **HTTP endpoint on the Hunter** — the Hunter exposes a small HTTP server for injection. Adds complexity, attack surface.

**Decision:** Elephantasm. It's already in the architecture, both agents have API keys, and the Dreamer can synthesise injection history into knowledge.

### Key Design Decision: Interrupt via Machine Stop

Locally, interrupt writes a flag file the Hunter polls. Remotely, we just stop the machine via the Fly Machines API. The Hunter doesn't need to gracefully wind down — it's ephemeral by design. Session state is preserved in Elephantasm.

For cases where we want a *soft* interrupt (let the Hunter finish its current analysis step), we use Elephantasm injection with `CRITICAL` priority. The Hunter's `step_callback` checks for critical injections and can self-terminate.

---

## Prerequisites

- Phase A complete (349 tests passing)
- Fly.io account with billing enabled
- `httpx` available (already a Hermes dependency)
- GitHub PAT with repo scope
- Elephantasm API key

---

## Task B1: Fly Machines API Client

**Goal:** Thin, typed wrapper around the Fly Machines REST API. No external SDK — raw `httpx` calls.

**File:** `hunter/backends/fly_api.py`

### API Surface

The Fly Machines API (https://docs.machines.dev) is a REST API. We need a small subset:

```python
class FlyMachinesClient:
    """Thin client for the Fly.io Machines REST API.

    Base URL: https://api.machines.dev/v1
    Auth: Bearer token via Authorization header.
    """

    def __init__(self, app_name: str, api_token: str):
        self.app_name = app_name
        self.api_token = api_token
        self._base_url = "https://api.machines.dev/v1"

    # -- Machine lifecycle --

    def create_machine(self, config: dict) -> dict:
        """POST /apps/{app}/machines
        Creates AND starts a machine. Returns machine dict with 'id'.
        Config includes: image, env, guest (CPU/mem), auto_destroy, restart.
        """

    def start_machine(self, machine_id: str) -> None:
        """POST /apps/{app}/machines/{id}/start
        Starts an existing stopped machine.
        """

    def stop_machine(self, machine_id: str, timeout: int = 30) -> None:
        """POST /apps/{app}/machines/{id}/stop
        Sends signal, waits up to timeout seconds.
        """

    def destroy_machine(self, machine_id: str, force: bool = False) -> None:
        """DELETE /apps/{app}/machines/{id}?force={force}
        Permanently removes the machine.
        """

    def wait_for_state(self, machine_id: str, state: str, timeout: int = 60) -> dict:
        """GET /apps/{app}/machines/{id}/wait?state={state}&timeout={timeout}
        Blocks until machine reaches target state. Returns machine dict.
        """

    # -- Status --

    def get_machine(self, machine_id: str) -> dict:
        """GET /apps/{app}/machines/{id}
        Returns full machine state including status, config, events.
        """

    def list_machines(self) -> list[dict]:
        """GET /apps/{app}/machines
        Returns all machines for the app. Used to find existing Hunter machines.
        """

    # -- Logs --

    def get_logs(self, machine_id: str, tail: int = 100) -> list[dict]:
        """GET /apps/{app}/machines/{id}/logs (Fly Logs Nats endpoint)
        Returns recent log entries. Each entry has 'message' and 'timestamp'.
        """
```

### Error Handling

```python
class FlyAPIError(Exception):
    """Raised when the Fly Machines API returns a non-2xx response."""
    def __init__(self, status_code: int, message: str, response_body: str = ""):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"Fly API error {status_code}: {message}")
```

All methods raise `FlyAPIError` on non-2xx responses. The caller (`FlyHunterController`) translates these into user-friendly messages.

### Implementation Notes

- Use `httpx.Client` (sync), not async. The Overseer loop is synchronous.
- Set reasonable timeouts: 30s for create/start/stop, 10s for status/list.
- Include `User-Agent: hermes-prime/1.0` header.
- Log all API calls at DEBUG level for audit trail.
- Machine config uses Fly's JSON schema — `guest` for CPU/RAM, `env` for environment variables, `services` for networking.

### Tests (in `tests/test_fly_api.py`)

- Mock `httpx.Client` — no real API calls in unit tests.
- Test each method maps to correct HTTP verb + URL + headers.
- Test `FlyAPIError` raised on 4xx/5xx responses.
- Test `wait_for_state` timeout behavior.
- ~10–12 tests.

---

## Task B2: Fly Configuration

**Goal:** Define all Fly-specific configuration in one place. Load from environment variables (set as Fly secrets in production).

**File:** `hunter/backends/fly_config.py`

### Configuration

```python
@dataclass
class FlyConfig:
    """Configuration for the Fly.io remote backend.

    All values come from environment variables (set as Fly secrets).
    """
    # Fly API
    fly_api_token: str          # FLY_API_TOKEN — Machines API auth
    hunter_app_name: str        # HUNTER_FLY_APP — e.g. "hermes-prime-hunter"

    # GitHub
    github_pat: str             # GITHUB_PAT — repo write access
    hunter_repo: str            # HUNTER_REPO — e.g. "user/hermes-prime-hunter"

    # Hunter machine spec
    machine_image: str          # HUNTER_FLY_IMAGE — Docker image ref
    machine_cpu_kind: str       # "shared" or "performance" (default: "shared")
    machine_cpus: int           # CPU count (default: 2)
    machine_memory_mb: int      # RAM in MB (default: 2048)
    machine_region: str         # Fly region (default: auto / same as Overseer)

    # Hunter environment (passed as env vars to the Hunter machine)
    elephantasm_api_key: str    # ELEPHANTASM_API_KEY
    openrouter_api_key: str     # OPENROUTER_API_KEY

    @classmethod
    def from_env(cls) -> "FlyConfig":
        """Load from environment variables. Raises ValueError if required vars missing."""

    def to_machine_config(self, model: str, session_id: str,
                          instruction: str = None, resume: bool = False) -> dict:
        """Build the Fly Machines API config dict for creating a Hunter machine."""
        # Returns:
        # {
        #   "config": {
        #     "image": self.machine_image,
        #     "env": { model, session_id, API keys, instruction, ... },
        #     "guest": { "cpu_kind": ..., "cpus": ..., "memory_mb": ... },
        #     "auto_destroy": True,
        #     "restart": { "policy": "no" },
        #   }
        # }
```

### Why a Separate Config Module

- Keeps `fly_api.py` pure (no env var reading).
- Keeps `FlyHunterController` focused on lifecycle logic.
- Single place to document what environment variables are needed.
- Easy to test — mock `os.environ`, verify correct loading and defaults.

### Tests (in `tests/test_fly_config.py`)

- Test `from_env()` with all vars set.
- Test `from_env()` raises `ValueError` when required vars missing.
- Test `to_machine_config()` produces correct structure.
- Test defaults (cpu, memory, region).
- ~6–8 tests.

---

## Task B3: FlyWorktreeManager

**Goal:** Implement `WorktreeBackend` using a local git clone of the Hunter's GitHub repo, with a real `push()`.

**File:** `hunter/backends/fly_worktree.py`

### Strategy: Subclass WorktreeManager

The `FlyWorktreeManager` needs the exact same file and git operations as `WorktreeManager`, just with:
1. A different `setup()` — clone from GitHub instead of creating a worktree.
2. A real `push()` — `git push origin main`.
3. A different path — `/data/hunter-repo/` (Fly persistent volume) instead of `~/.hermes/hunter/worktree/`.

**Two approaches:**

**Option A — Subclass `WorktreeManager`:**
Override `setup()`, `push()`, and path resolution. Inherit everything else. Risk: `WorktreeManager` internals change and break the subclass.

**Option B — Composition with shared helpers:**
`FlyWorktreeManager` wraps a local clone path and reimplements file/git ops using the same `git -C` pattern. More duplication, but no coupling to `WorktreeManager` internals.

**Decision: Option A (subclass).** The `WorktreeManager` is stable (Phase 1 complete, 35 tests), and the operations are genuinely identical — same `git -C <path>` commands. The subclass only overrides initialization and push. If we need to diverge later, we extract a base class.

```python
class FlyWorktreeManager(WorktreeManager):
    """WorktreeBackend backed by a local clone of a remote GitHub repo.

    File operations work identically to WorktreeManager (same path, same
    git commands). The differences:
    - setup() clones from GitHub instead of creating a worktree
    - push() actually pushes to origin
    - branch is always 'main' (no worktree branch)
    """

    def __init__(self, repo_url: str, clone_path: Path, github_pat: str):
        # DON'T call super().__init__() — we set up paths differently
        self._repo_url = f"https://{github_pat}@github.com/{repo_url}.git"
        self._worktree_path = clone_path
        self._branch = "main"
        # Set attributes that WorktreeManager methods expect:
        # self.worktree_path, self.branch, self._repo_root

    def setup(self) -> None:
        """Clone the repo if not present, pull if it exists."""
        if not self._worktree_path.exists():
            # git clone <url> <path>
        else:
            # git -C <path> pull --ff-only origin main

    def teardown(self) -> None:
        """Remove the local clone. Leaves the remote repo intact."""
        # shutil.rmtree(self._worktree_path)

    def is_setup(self) -> bool:
        """Check if the clone exists and is a valid git repo."""
        return (self._worktree_path / ".git").exists()

    def push(self) -> None:
        """Push committed changes to the remote Hunter repo."""
        # git -C <path> push origin main
        logger.info("Pushed to remote Hunter repo")
```

### Integration with Code Tools

The code tools flow is: `edit_file()` → `commit()` → (later) `redeploy()`. Currently, `commit()` doesn't trigger `push()`. For the remote backend, the Overseer must push before redeploying, because the Hunter machine pulls from GitHub on boot.

**Two options:**
1. Auto-push in `commit()` — every commit immediately pushes.
2. Push in `redeploy()` — batch commits, push once before restarting the machine.

**Decision: Push in `redeploy()`.** The Overseer often makes multiple edits before redeploying (edit skill → edit tool → commit each → redeploy). Pushing after each commit wastes time. The `FlyHunterController.redeploy()` calls `self.worktree.push()` before spawning the new machine.

### Tests (in `tests/test_fly_worktree.py`)

- Test `setup()` clones when directory doesn't exist (mock `subprocess.run`).
- Test `setup()` pulls when directory exists.
- Test `push()` runs `git push origin main`.
- Test `is_setup()` checks for `.git` directory.
- Test inherited methods (`read_file`, `write_file`, `edit_file`, `commit`) work with the clone path.
- Test `teardown()` removes directory.
- ~10–12 tests.

---

## Task B4: FlyHunterController

**Goal:** Implement `ControlBackend` using the Fly Machines API for Hunter lifecycle management.

**File:** `hunter/backends/fly_control.py`

### Class Design

```python
class FlyHunterController:
    """Manages the Hunter as a Fly.io machine.

    Implements the same interface as HunterController (ControlBackend protocol)
    but delegates process lifecycle to the Fly Machines API instead of local
    subprocess management.
    """

    def __init__(self, worktree: FlyWorktreeManager, budget: BudgetManager,
                 fly_client: FlyMachinesClient, fly_config: FlyConfig):
        self._worktree = worktree
        self._budget = budget
        self._fly = fly_client
        self._config = fly_config
        self._current: Optional[FlyHunterProcess] = None
        self._history: list = []

    # -- ControlBackend protocol --

    @property
    def worktree(self) -> FlyWorktreeManager:
        return self._worktree

    @property
    def budget(self) -> BudgetManager:
        return self._budget

    @property
    def is_running(self) -> bool:
        """Check if the Hunter machine is in 'started' state."""

    @property
    def current(self) -> Optional["FlyHunterProcess"]:
        return self._current

    @property
    def history(self) -> list:
        return list(self._history)

    def spawn(self, model=None, initial_instruction=None,
              resume_session=False, session_id=None, detach=False) -> "FlyHunterProcess":
        """Create and start a new Fly machine running the Hunter.

        Steps:
        1. Check budget (same as local controller)
        2. Kill existing machine if running
        3. Build machine config from FlyConfig.to_machine_config()
        4. Call fly_client.create_machine(config)
        5. Wait for 'started' state
        6. Return FlyHunterProcess wrapper
        """

    def kill(self) -> bool:
        """Stop and destroy the current Hunter machine.

        Steps:
        1. fly_client.stop_machine(id, timeout=30)
        2. fly_client.wait_for_state(id, 'stopped')
        3. fly_client.destroy_machine(id)
        4. Move current to history
        """

    def redeploy(self, resume_session=True, model=None) -> "FlyHunterProcess":
        """Push code changes to remote, then kill and respawn.

        Steps:
        1. self.worktree.push()  ← the key difference from local
        2. kill()
        3. spawn(resume_session=resume_session, model=model)
        """

    def get_status(self) -> HunterStatus:
        """Query Fly machine state and build HunterStatus.

        Maps Fly machine states to HunterStatus:
        - 'started' → running=True
        - 'stopped'/'destroyed' → running=False
        - Machine events → uptime, exit_code
        """

    def get_logs(self, tail: int = 100) -> str:
        """Fetch recent logs from the Fly machine."""
```

### FlyHunterProcess

A lightweight wrapper that holds machine metadata, analogous to `HunterProcess` but without subprocess internals:

```python
@dataclass
class FlyHunterProcess:
    """Represents a running Hunter on a Fly machine."""
    machine_id: str
    session_id: str
    model: str
    started_at: datetime
    fly_app: str

    @property
    def is_alive(self) -> bool:
        """Must query Fly API — can't check local process."""

    @property
    def pid(self) -> str:
        """Returns machine_id as the remote equivalent of PID."""

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now() - self.started_at).total_seconds()
```

### Budget Enforcement

Same logic as local `HunterController.spawn()`:

```python
def spawn(self, ...):
    # Budget check — identical to local
    if not self._budget.can_spend():
        raise BudgetExhaustedError("Daily budget exhausted")
    # ... proceed with machine creation
```

### Machine Recovery on Overseer Restart

When the Overseer restarts (Fly machine reboots, crash, deploy), it needs to find any existing Hunter machine:

```python
def recover(self) -> Optional[FlyHunterProcess]:
    """Check for an existing Hunter machine from a previous Overseer session.

    Called during OverseerLoop._setup() to reconnect to a running Hunter
    instead of orphaning it.
    """
    machines = self._fly.list_machines()
    running = [m for m in machines if m["state"] == "started"]
    if running:
        # Reconnect to the most recent one
        m = running[0]
        self._current = FlyHunterProcess(
            machine_id=m["id"],
            session_id=m["config"]["env"].get("SESSION_ID", "unknown"),
            model=m["config"]["env"].get("HUNTER_MODEL", "unknown"),
            started_at=datetime.fromisoformat(m["created_at"]),
            fly_app=self._config.hunter_app_name,
        )
        return self._current
    return None
```

### Tests (in `tests/test_fly_control.py`)

- Test `spawn()` calls `create_machine` with correct config, waits for state.
- Test `spawn()` kills existing machine before creating new one.
- Test `spawn()` raises `BudgetExhaustedError` when budget exceeded.
- Test `kill()` calls stop → wait → destroy sequence.
- Test `redeploy()` calls `push()` then kill+spawn.
- Test `get_status()` maps Fly machine states to `HunterStatus`.
- Test `get_logs()` returns formatted log lines.
- Test `recover()` finds existing running machine.
- Test `recover()` returns None when no machines running.
- All tests mock `FlyMachinesClient` — no real API calls.
- ~14–16 tests.

---

## Task B5: Injection Adapter

**Goal:** Make `hunter_inject` and `hunter_interrupt` work remotely without file-based IPC.

### The Problem

The current injection flow:
```
inject_tools._handle_hunter_inject()
  → writes to get_injection_path()  (~/.hermes/hunter/injections/current.md)

Hunter's step_callback (in runner.py)
  → reads get_injection_path()
  → renames to .consumed
  → appends content to ephemeral prompt
```

This requires a shared filesystem. Remote machines don't share a filesystem.

### The Solution: Backend-Aware Injection

Add `inject()` and `interrupt()` methods to the `ControlBackend` protocol. The inject tools call these instead of writing files directly.

**Protocol extension in `hunter/backends/base.py`:**

```python
class ControlBackend(Protocol):
    # ... existing methods ...

    def inject(self, instruction: str, priority: str = "normal") -> None:
        """Send a runtime instruction to the Hunter."""

    def interrupt(self) -> None:
        """Signal the Hunter to stop gracefully."""
```

**Local implementation** (add to `HunterController`):

```python
def inject(self, instruction: str, priority: str = "normal") -> None:
    """Write injection to the shared filesystem (existing behavior)."""
    prefix = {"normal": "", "high": "HIGH PRIORITY: ",
              "critical": "CRITICAL — DROP CURRENT TASK: "}.get(priority, "")
    injection_path = get_injection_path()
    injection_path.parent.mkdir(parents=True, exist_ok=True)
    injection_path.write_text(f"{prefix}{instruction}")

def interrupt(self) -> None:
    """Write interrupt flag file (existing behavior)."""
    flag = get_interrupt_flag_path()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("interrupt")
```

**Remote implementation** (add to `FlyHunterController`):

```python
def inject(self, instruction: str, priority: str = "normal") -> None:
    """Send injection via Elephantasm event.

    The Hunter's step_callback queries for recent injection events
    instead of reading a file.
    """
    # Write to Elephantasm as a structured event on the Hunter's Anima
    # Event type: "injection", payload: { instruction, priority, timestamp }
    # The Hunter's inject() call retrieves this in its step_callback

def interrupt(self) -> None:
    """Stop the Fly machine (hard interrupt).

    For soft interrupt: inject with CRITICAL priority instead.
    """
    if self._current:
        self._fly.stop_machine(self._current.machine_id)
```

**Update `inject_tools.py`:**

```python
# Before (file-based):
def _handle_hunter_inject(args):
    instruction = args["instruction"]
    priority = args.get("priority", "normal")
    # ... build prefix, write to file ...

# After (backend-aware):
def _handle_hunter_inject(args):
    controller = _get_controller()
    instruction = args["instruction"]
    priority = args.get("priority", "normal")
    controller.inject(instruction, priority)
    return json.dumps({"status": "injected", "priority": priority})
```

This moves injection logic from the tool handler into the controller, where it can be backend-specific. The tool handler becomes a thin dispatcher.

### Impact on Existing Tests

The `inject_tools` tests currently verify file writes. They'll need updating to verify `controller.inject()` calls instead. The file-write behavior moves to `HunterController.inject()` tests.

### Tests

- Test `HunterController.inject()` writes file (preserves existing behavior).
- Test `HunterController.interrupt()` writes flag file.
- Test `FlyHunterController.inject()` calls Elephantasm (mocked).
- Test `FlyHunterController.interrupt()` calls `stop_machine`.
- Test `_handle_hunter_inject` calls `controller.inject()`.
- ~8–10 tests (some in existing test files, some new).

---

## Task B6: Wire Up the Factory

**Goal:** Update `create_controller()` to return `FlyHunterController` when `mode="fly"`.

**File:** `hunter/backends/__init__.py`

### Changes

```python
def create_controller(mode: str = "auto", budget: BudgetManager = None) -> ControlBackend:
    if mode == "auto":
        mode = "fly" if os.environ.get("FLY_APP_NAME") else "local"

    if mode == "fly":
        from hunter.backends.fly_config import FlyConfig
        from hunter.backends.fly_api import FlyMachinesClient
        from hunter.backends.fly_control import FlyHunterController
        from hunter.backends.fly_worktree import FlyWorktreeManager

        config = FlyConfig.from_env()
        fly_client = FlyMachinesClient(config.hunter_app_name, config.fly_api_token)
        worktree = FlyWorktreeManager(
            repo_url=config.hunter_repo,
            clone_path=Path("/data/hunter-repo"),  # Fly persistent volume
            github_pat=config.github_pat,
        )
        if budget is None:
            from hunter.budget import BudgetManager
            budget = BudgetManager()
        return FlyHunterController(
            worktree=worktree, budget=budget,
            fly_client=fly_client, fly_config=config,
        )

    # Local mode (existing)
    from hunter.budget import BudgetManager as BM
    from hunter.control import HunterController
    from hunter.worktree import WorktreeManager

    worktree = WorktreeManager()
    if budget is None:
        budget = BM()
    return HunterController(worktree=worktree, budget=budget)
```

### Return Type Change

The factory return type broadens from `HunterController` to `ControlBackend`. This is safe because all callers use the protocol interface. Update the type annotation and docstring.

### Tests

- Update existing `test_hunter_backends.py::TestCreateControllerFly` — no longer raises `NotImplementedError`, returns `FlyHunterController`.
- Test auto-detection with `FLY_APP_NAME` set returns `FlyHunterController`.
- ~3–4 test updates.

---

## Task B7: Protocol Extensions & Local Parity

**Goal:** Add `inject()` and `interrupt()` to the `ControlBackend` protocol and implement them on the local `HunterController`, then update the inject tools to use them.

This task ensures the local backend stays protocol-compliant after the protocol is extended in B5.

### Changes to `hunter/control.py`

Add `inject()` and `interrupt()` methods to `HunterController`. The logic currently lives in `inject_tools.py` — move it into the controller where it belongs.

### Changes to `hunter/tools/inject_tools.py`

The `_handle_hunter_inject` and `_handle_hunter_interrupt` handlers become thin wrappers that call `controller.inject()` and `controller.interrupt()`.

### Changes to `hunter/backends/base.py`

Add `inject()` and `interrupt()` to the `ControlBackend` protocol definition.

### Test Updates

- Update `tests/test_hunter_inject_tools.py` — mock `controller.inject()` instead of checking file writes.
- Add `inject()`/`interrupt()` tests to `tests/test_hunter_control.py`.
- Update `tests/test_hunter_backends.py` protocol satisfaction test to include new methods.
- ~6–8 test changes.

---

## Task B8: Integration Test

**Goal:** Verify the full remote lifecycle works end-to-end against real Fly.io infrastructure.

**File:** `tests/integration/test_fly_integration.py`

### Test Scope

```python
@pytest.mark.integration  # Excluded from default test runs
class TestFlyIntegration:

    def test_machine_lifecycle(self, fly_config):
        """Create a machine, verify it boots, check status, read logs, stop, destroy."""
        client = FlyMachinesClient(fly_config.hunter_app_name, fly_config.fly_api_token)

        # Create a simple machine (not a full Hunter — just verify API works)
        config = {"config": {"image": "alpine:latest", "cmd": ["sleep", "30"],
                             "guest": {"cpu_kind": "shared", "cpus": 1, "memory_mb": 256},
                             "auto_destroy": True}}
        machine = client.create_machine(config)
        assert "id" in machine

        # Wait for it to start
        client.wait_for_state(machine["id"], "started", timeout=30)

        # Check status
        status = client.get_machine(machine["id"])
        assert status["state"] == "started"

        # Stop it
        client.stop_machine(machine["id"])
        client.wait_for_state(machine["id"], "stopped", timeout=30)

        # Destroy it
        client.destroy_machine(machine["id"])

    def test_controller_spawn_and_kill(self, fly_config):
        """Full FlyHunterController spawn → status → kill cycle."""

    def test_worktree_clone_and_push(self, fly_config, tmp_github_repo):
        """Clone a test repo, make a change, commit, push, verify on remote."""
```

### Running Integration Tests

```bash
# Unit tests only (default — no Fly credentials needed)
python -m pytest tests/ -q

# Include integration tests (requires FLY_API_TOKEN etc.)
python -m pytest tests/ -q -m integration
```

Integration tests are excluded by default via pytest marker configuration in `pyproject.toml` or `pytest.ini`.

---

## Task Dependency Graph

```
B1 (Fly API client)
  │
  ├──→ B4 (FlyHunterController) ──→ B6 (Factory wiring)
  │                                       │
B2 (Fly config) ──────────────────────────┘
  │                                       │
B3 (FlyWorktreeManager) ─────────────────┘
                                          │
B5 (Injection adapter) ──→ B7 (Protocol extensions + local parity)
                                          │
                                          └──→ B8 (Integration test)
```

**Parallelizable:** B1, B2, B3 can be built simultaneously. B5 is independent of B1–B3.

**Sequential:** B4 depends on B1+B2. B6 depends on B2+B3+B4. B7 depends on B5. B8 depends on everything.

---

## Execution Order

| Order | Task | Est. Lines | Dependencies |
|-------|------|-----------|--------------|
| 1 | **B1** — Fly Machines API client | ~180 | None |
| 2 | **B2** — Fly configuration | ~100 | None |
| 3 | **B5** — Injection adapter (protocol design) | ~60 | None |
| 4 | **B3** — FlyWorktreeManager | ~120 | None |
| 5 | **B7** — Protocol extensions + local parity | ~80 | B5 |
| 6 | **B4** — FlyHunterController | ~250 | B1, B2, B5 |
| 7 | **B6** — Factory wiring | ~30 | B2, B3, B4 |
| 8 | **B8** — Integration test | ~120 | All |
| | **Tests total** | ~300 | Alongside each task |

**Estimated total:** ~940 lines of production code + ~300 lines of tests.

---

## Files Changed Summary (Projected)

| File | Action | Purpose |
|------|--------|---------|
| `hunter/backends/fly_api.py` | **Create** | Fly Machines REST API client |
| `hunter/backends/fly_config.py` | **Create** | Environment-based configuration |
| `hunter/backends/fly_worktree.py` | **Create** | WorktreeBackend via local clone + push |
| `hunter/backends/fly_control.py` | **Create** | ControlBackend via Fly Machines API |
| `hunter/backends/base.py` | Modify | Add `inject()`, `interrupt()` to ControlBackend |
| `hunter/backends/__init__.py` | Modify | Wire up Fly backend in factory |
| `hunter/control.py` | Modify | Add `inject()`, `interrupt()` methods |
| `hunter/tools/inject_tools.py` | Modify | Use `controller.inject()` instead of file writes |
| `tests/test_fly_api.py` | **Create** | API client unit tests |
| `tests/test_fly_config.py` | **Create** | Configuration tests |
| `tests/test_fly_worktree.py` | **Create** | FlyWorktreeManager tests |
| `tests/test_fly_control.py` | **Create** | FlyHunterController tests |
| `tests/test_hunter_backends.py` | Modify | Update factory + protocol tests |
| `tests/test_hunter_inject_tools.py` | Modify | Update for controller-based injection |
| `tests/test_hunter_control.py` | Modify | Add inject/interrupt tests |
| `tests/integration/test_fly_integration.py` | **Create** | End-to-end integration tests |

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Fly Machines API changes or has undocumented behavior | Medium | Medium | Pin to v1 API; integration test catches regressions |
| `WorktreeManager` subclass coupling breaks on internal changes | Low | Medium | Phase 1 is stable; if it breaks, extract a shared base class |
| Injection via Elephantasm adds latency vs. file-based | Medium | Low | Acceptable — injection is not time-critical. Can add HTTP fallback later |
| Machine recovery on Overseer restart misidentifies stale machines | Medium | Medium | Check machine created_at + session metadata; destroy orphans older than 24h |
| Git push conflicts if Hunter repo is modified externally | Low | Low | Force-push from Overseer (it's the only writer); or abort with clear error |
| `FlyConfig.from_env()` missing variables cause cryptic errors | High (first deploy) | Low | Clear error messages listing which vars are missing |

---

## Success Criteria

1. `create_controller(mode="fly")` returns a working `FlyHunterController`
2. All existing 349 tests pass (no behavior change for local mode)
3. New unit tests pass (~50–60 new tests, all mocked)
4. Integration test passes against real Fly.io (manual run)
5. The Overseer can spawn a Hunter on a Fly machine, push code to its repo, read logs, and kill it
6. Injection works via Elephantasm (or controller method) instead of file-based IPC
7. Overseer restart reconnects to existing Hunter machine (no orphans)

---

## What This Does NOT Cover

These remain for Phase C and beyond:

- **Dockerfile for the Hunter machine** — Phase C
- **Dockerfile for the Overseer machine** — Phase C
- **Browser terminal (ttyd) setup** — Phase C
- **Bootstrap mode (building Hunter from empty repo)** — Phase D
- **Human approval flow** — Phase E
- **Elephantasm integration for the Hunter's step_callback** — Phase D (the Hunter side of injection polling)
- **Multiple Hunter machines** — future enhancement
- **Telegram notifications** — Phase E
