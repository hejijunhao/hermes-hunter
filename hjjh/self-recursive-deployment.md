# Self-Recursive Deployment — The Overseer Builds Its Own Hunter

## The Idea

The original architecture assumes a human builds the Hunter's capabilities (Phase 2–5), then the Overseer improves them over time. This document proposes a more recursive approach: **deploy the Overseer to the cloud, give it access to an empty repo and a blank machine, and let it build the Hunter from scratch.**

The Overseer already has every tool it needs to write code, commit, and redeploy. The architecture docs serve as its blueprint. The difference between "building the Hunter" and "improving the Hunter" is just the starting state — empty vs. partially built. The code evolution mechanism _is_ the development mechanism.

This makes the system more self-perpetuating. The human's role shifts from developer to **director** — setting goals, adjusting budget, reviewing reports, and providing strategic guidance. The Overseer handles everything from writing security tools to optimising model selection to refining its own intervention strategy.

### The Hierarchy

```
Creator (You)
  └→ CLI / Browser Terminal
       └→ Overseer (Machine A — persistent, self-aware, strategic)
            └→ tools / injection / code evolution
                 └→ Hunter (Machine B — ephemeral, tactical, replaceable)
                      └→ subagents (parallel analysis workers)
```

You are the meta-meta-agent. The Overseer is the meta-agent. The Hunter is the agent. Each level has full autonomy within the constraints set by the level above.

---

## 1. Cloud Architecture

### 1.1 Two Fly.io Machines

```
┌─────────────────────────────────────────────────────┐
│  FLY MACHINE A — OVERSEER                            │
│  App: hermes-overseer                                │
│  Region: <closest to you>                            │
│                                                      │
│  Processes:                                          │
│    1. ttyd (port 8080) → hermes CLI                  │
│       └→ Your browser interface                      │
│    2. OverseerLoop (background, persistent)           │
│       └→ Continuous monitoring + improvement loop     │
│                                                      │
│  Access:                                             │
│    - GitHub PAT → push to hunter-repo                │
│    - Fly API token → manage Machine B                │
│    - Elephantasm API key → memory + observability    │
│    - LLM API keys → OpenRouter / provider            │
│                                                      │
│  State:                                              │
│    - ~/.hermes/hunter/ (budget, spend ledger, config) │
│    - Local clone of hunter-repo (code workspace)     │
│    - Overseer session DB                             │
│                                                      │
│  Persistent volume: /data (survives restarts)        │
└──────────────────────┬──────────────────────────────┘
                       │
          Fly Machines API + git push
                       │
┌──────────────────────┴──────────────────────────────┐
│  FLY MACHINE B — HUNTER                              │
│  App: hermes-hunter                                  │
│  Region: same                                        │
│                                                      │
│  Lifecycle: ephemeral, recreated on each redeploy    │
│                                                      │
│  Boot sequence:                                      │
│    1. git pull hunter-repo (latest code)             │
│    2. pip install dependencies                       │
│    3. python -m hunter.runner --session-id ...       │
│                                                      │
│  Access:                                             │
│    - Elephantasm API key → event extraction          │
│    - LLM API keys → model provider                   │
│    - Network: restricted during PoC testing          │
│                                                      │
│  No direct human access needed.                      │
│  Overseer is the only operator.                      │
└─────────────────────────────────────────────────────┘
```

### 1.2 Why Two Machines, Not One

1. **Isolation.** The Hunter runs exploit code and spins up target applications. It must be sandboxed. The Overseer must not be affected by a crashing or misbehaving Hunter.
2. **Independent lifecycle.** The Overseer kills and recreates the Hunter machine on every redeploy. If they shared a machine, this would be impossible.
3. **Security boundary.** The Hunter machine has restricted network access during PoC testing. The Overseer needs unrestricted access to GitHub, Fly API, Elephantasm, and LLM providers.
4. **Resource isolation.** The Hunter may consume significant CPU/memory during analysis. The Overseer should remain responsive for human interaction.

### 1.3 Why Fly.io

- **Machines API** — programmatic start/stop/destroy of VMs, perfect for the Overseer to manage the Hunter's lifecycle.
- **Persistent volumes** — Overseer state survives restarts.
- **HTTPS out of the box** — browser terminal access without configuring TLS.
- **Pay-per-use** — Hunter machine only runs (and costs money) when actively hunting.
- **Simple networking** — machines in the same org can communicate via internal DNS.

---

## 2. Human Interface — Browser Terminal

### 2.1 ttyd: Terminal in the Browser

The Creator interacts with the Overseer via a browser-based terminal. [ttyd](https://github.com/tsl0922/ttyd) serves a full terminal emulator (xterm.js) over HTTPS/WebSocket. The Hermes CLI — prompt_toolkit UI, spinners, colors, everything — renders natively.

```
Browser: https://hermes-overseer.fly.dev
  ┌─────────────────────────────────────────────┐
  │ $ hermes                                     │
  │ 🔮 Hermes Agent v2.x                       │
  │ > What's the Hunter doing right now?         │
  │                                              │
  │ Overseer: The Hunter is analysing acme-api,  │
  │ currently running static analysis with       │
  │ semgrep. Found one potential IDOR so far.    │
  │ Budget: $8.50 remaining ($15/day).           │
  │                                              │
  │ > Focus it on auth bypass patterns instead.  │
  │                                              │
  │ Overseer: Injecting redirect now. I'll also  │
  │ check if the auth-bypass skill needs         │
  │ improvement based on recent results.         │
  └─────────────────────────────────────────────┘
```

### 2.2 Two Interaction Modes

**Structured commands** — operational control:
```bash
hermes hunter status              # What's happening?
hermes hunter logs -f             # Watch Hunter work in real-time
hermes hunter budget              # Check spend
hermes hunter budget set 25/day   # Adjust budget
hermes hunter kill                # Emergency stop
hermes hunter review              # Review a report awaiting approval
```

**Direct conversation** — strategic direction:
```bash
hermes
> The Hunter isn't finding anything on web targets.
> Pivot to open-source Go projects with auth modules.
> And write it a skill for detecting race conditions.
```

In conversation mode, the Creator talks to the Overseer the same way the Overseer talks to the Hunter. The Overseer has the tools to act on strategic direction immediately — inject instructions, write skills, change targets, adjust models.

### 2.3 Multi-Session

ttyd supports multiple concurrent sessions. The Creator can have several browser tabs open:
- Tab 1: `hermes hunter logs -f` (watching the Hunter)
- Tab 2: `hermes` (chatting with the Overseer)
- Tab 3: `hermes hunter budget` (monitoring costs)

### 2.4 Authentication

Basic auth via ttyd's `--credential` flag, with the password set as a Fly secret:

```bash
fly secrets set AUTH_PASSWORD="<strong-password>" -a hermes-overseer
```

For additional security: Fly.io private networking + WireGuard, or Fly Proxy with OAuth (Tailscale, Cloudflare Access, etc.).

### 2.5 Telegram as Secondary Channel

The existing Hermes `send_message` tool provides a secondary notification channel. The Overseer sends alerts to Telegram for:
- Report ready for review (with approve/reject inline buttons)
- Budget alerts (80% threshold)
- Hunter crash / error state
- Significant findings

The Creator can respond via Telegram for simple approvals, or open the browser terminal for deeper interaction.

---

## 3. Remote Control Layer

### 3.1 Abstraction: Control Backend Interface

The existing `HunterController` and `WorktreeManager` are designed for local subprocess + local worktree. Rather than rewriting them, we introduce a **backend abstraction** that the tools call through. The Overseer's tools don't know or care whether the Hunter is local or remote.

```python
# hunter/backends/base.py

class ControlBackend(Protocol):
    """Interface for Hunter process lifecycle management."""

    def spawn(self, model: str, instruction: str, session_id: str, resume: bool) -> dict: ...
    def kill(self, timeout: float) -> bool: ...
    def get_status(self) -> HunterStatus: ...
    def get_logs(self, tail: int) -> str: ...
    def is_alive(self) -> bool: ...


class WorktreeBackend(Protocol):
    """Interface for Hunter code management."""

    def setup(self) -> None: ...
    def read_file(self, path: str) -> str: ...
    def write_file(self, path: str, content: str) -> None: ...
    def edit_file(self, path: str, old_str: str, new_str: str) -> bool: ...
    def commit(self, message: str, files: list) -> str: ...
    def push(self) -> None: ...  # No-op for local backend
    def rollback(self, commit: str) -> None: ...
    def diff(self, staged: bool) -> str: ...
    def diff_since(self, commit: str) -> str: ...
    def get_head_commit(self) -> str: ...
    def is_clean(self) -> bool: ...
```

### 3.2 Local Backend (Existing — Development / Testing)

```python
# hunter/backends/local.py

class LocalControlBackend(ControlBackend):
    """Manages the Hunter as a local subprocess. Wraps existing HunterProcess."""
    # Delegates to hunter/control.py — the existing implementation


class LocalWorktreeBackend(WorktreeBackend):
    """Manages a local git worktree. Wraps existing WorktreeManager."""
    # Delegates to hunter/worktree.py — the existing implementation
    # push() is a no-op
```

### 3.3 Fly.io Backend (Production)

```python
# hunter/backends/fly.py

class FlyControlBackend(ControlBackend):
    """Manages the Hunter as a Fly.io machine via the Machines API."""

    def __init__(self, app_name: str, fly_token: str, machine_config: dict):
        self.app_name = app_name
        self.fly_token = fly_token
        self.machine_config = machine_config  # CPU, memory, image, env vars
        self._machine_id: Optional[str] = None

    def spawn(self, model, instruction, session_id, resume) -> dict:
        """Create or start a Fly machine running the Hunter."""
        # POST https://api.machines.dev/v1/apps/{app}/machines
        # Machine runs: git pull && python -m hunter.runner ...
        # Environment vars pass model, session_id, instruction, API keys
        # Returns machine ID, status

    def kill(self, timeout: float) -> bool:
        """Stop the Fly machine."""
        # POST https://api.machines.dev/v1/apps/{app}/machines/{id}/stop

    def get_status(self) -> HunterStatus:
        """Query machine state + last Elephantasm event."""
        # GET https://api.machines.dev/v1/apps/{app}/machines/{id}
        # Combine with Elephantasm query for last Hunter event timestamp

    def get_logs(self, tail: int) -> str:
        """Fetch logs from Fly's log aggregator."""
        # GET https://api.machines.dev/v1/apps/{app}/machines/{id}/logs
        # OR: query Elephantasm for recent Hunter events (richer than raw logs)

    def is_alive(self) -> bool:
        # Machine state == "started"


class FlyWorktreeBackend(WorktreeBackend):
    """Local clone of a remote repo. Push triggers Hunter redeploy."""

    def __init__(self, repo_url: str, local_path: Path, pat: str):
        self.repo_url = f"https://{pat}@github.com/{repo_url}.git"
        self.local_path = local_path

    def setup(self):
        """Clone if not exists, pull if exists."""
        if not self.local_path.exists():
            # git clone <repo_url> <local_path>
        else:
            # git -C <local_path> pull

    def push(self):
        """Push committed changes to remote. Hunter pulls on next deploy."""
        # git -C <local_path> push origin main

    def commit(self, message, files) -> str:
        """Stage and commit locally."""
        # Same as existing WorktreeManager.commit()

    # read_file, write_file, edit_file, diff, rollback — same as local
```

### 3.4 Backend Selection

```python
# hunter/backends/__init__.py

def get_backends(mode: str = "auto") -> Tuple[ControlBackend, WorktreeBackend]:
    """Factory for control + worktree backends."""
    if mode == "auto":
        mode = "fly" if os.environ.get("FLY_APP_NAME") else "local"

    if mode == "local":
        return LocalControlBackend(...), LocalWorktreeBackend(...)
    elif mode == "fly":
        return FlyControlBackend(...), FlyWorktreeBackend(...)
```

The `HunterController` and all tool handlers use backends via this factory. Zero changes to tool code when switching from local to cloud.

---

## 4. The Self-Build Bootstrap

### 4.1 The Core Insight

The Overseer's existing tools — `hunter_code_edit`, `hunter_code_read`, `hunter_diff`, `hunter_redeploy` — are sufficient to build the Hunter from an empty repo. The architecture docs (`hjjh/architecture.md`, `hjjh/phase1-implementation.md`) serve as the specification.

**Building** the Hunter and **improving** the Hunter are the same operation at different starting states.

### 4.2 Bootstrap Sequence

```
Phase 0: EMPTY REPO
  │
  │  Overseer reads architecture.md, understands what to build
  │
  ├─ Step 1: Write security skills (Markdown — zero risk)
  │   ├─ skills/security/owasp-top-10/SKILL.md
  │   ├─ skills/security/idor-hunting/SKILL.md
  │   ├─ skills/security/auth-bypass/SKILL.md
  │   ├─ skills/security/injection-patterns/SKILL.md
  │   └─ ... (all from architecture §3.4)
  │
  ├─ Step 2: Write Hunter system prompt
  │   └─ hunter/prompts/hunter_system.md
  │
  ├─ Step 3: Write security tools (Python — medium risk)
  │   ├─ tools/target_clone.py (clone a repo)
  │   ├─ tools/target_scan.py (run semgrep, bandit)
  │   ├─ tools/attack_surface_map.py (endpoint mapping)
  │   ├─ tools/vuln_assess.py (structured assessment)
  │   ├─ tools/report_draft.py (report generation)
  │   └─ tools/dedup_check.py (Elephantasm query)
  │
  ├─ Step 4: Wire up toolset + registration
  │   ├─ toolsets.py entry for "hermes-hunter"
  │   └─ model_tools.py imports
  │
  ├─ Step 5: Deploy and test
  │   ├─ Push to hunter-repo
  │   ├─ Start Hunter machine
  │   ├─ Point Hunter at a deliberately vulnerable target (DVWA, Juice Shop)
  │   ├─ Watch Elephantasm events
  │   └─ Evaluate: did it find known vulns?
  │
  ├─ Step 6: Iterate
  │   ├─ Fix bugs in tools
  │   ├─ Improve skills based on what the Hunter missed
  │   ├─ Add missing tools (poc_build, poc_verify, target_dast)
  │   ├─ Redeploy, test again
  │   └─ Repeat until Hunter is functional
  │
  │  ── TRANSITION: Hunter can autonomously produce a finding ──
  │
  └─ Normal mode: continuous improvement loop (Phase 5 of original plan)
```

### 4.3 Bootstrap Prompt Augmentation

The Overseer's system prompt gains a conditional section when the Hunter repo is empty or minimal:

```markdown
## Bootstrap Mode — Active

The Hunter repository is currently empty (or has minimal capabilities).
Your primary task right now is to BUILD the Hunter's capabilities before
you can improve them.

### Your Blueprint

The architecture specification is in hjjh/architecture.md. Key sections:
- §3.1: Hunter toolset (what tools to build)
- §3.2: Hunter workflow (the analysis pipeline)
- §3.4: Hunter skills (what security knowledge to write)

### Build Order (safest first)

1. **Skills first.** Write Markdown files — zero risk, immediate value.
   The Hunter can use these even before it has custom tools.
2. **System prompt.** Define the Hunter's identity and methodology.
3. **Simple tools.** Start with target_clone (git clone wrapper),
   target_scan (semgrep invocation), report_draft (template filling).
4. **Wire up.** Register tools in toolset, add imports.
5. **Deploy and test.** Push, start Hunter, point at a known-vulnerable
   target (like DVWA or Juice Shop). Watch what happens.
6. **Iterate.** Fix what's broken, add what's missing.

### Testing Targets for Bootstrap

Use these deliberately vulnerable applications to validate:
- OWASP Juice Shop (Node.js — broad vulnerability coverage)
- DVWA (PHP — classic web vulns)
- WebGoat (Java — OWASP Top 10)
- Damn Vulnerable GraphQL Application (GraphQL-specific)
- crAPI (API security)

Point the Hunter at these and verify it finds known vulnerabilities.
When it can reliably produce findings against test targets, transition
to real bounty targets.

### Transition Criteria

Exit bootstrap mode when:
- Hunter has at least 5 security skills
- Hunter has at least 3 working tools (clone, scan, report)
- Hunter can autonomously produce a structured finding against a test target
- You've verified at least one complete clone → analyse → report cycle
```

### 4.4 The Overseer Reads Its Own Docs

A critical enabler: the Overseer needs access to `hjjh/architecture.md` and related docs on Machine A. These are the blueprint it follows during bootstrap. Options:

1. **Include in the Overseer's Docker image** (simplest — bake docs into the image at build time)
2. **Overseer has read access to its own repo** via the GitHub PAT (can clone and read)
3. **Load into Elephantasm** as high-importance knowledge (persistent, queryable)

Option 1 for initial deployment, option 3 for long-term (the Dreamer synthesises the architecture into usable knowledge).

---

## 5. The Two Repos

### 5.1 Repo Structure

```
REPO 1: hermes-hunter (the Overseer's repo — your main repo)
  ├─ hunter/                    # Overseer subsystem (Phase 1 code)
  ├─ hjjh/                      # Architecture docs, plans
  ├─ run_agent.py, cli.py, ...  # Core Hermes agent
  ├─ tools/, skills/, agent/    # Hermes infrastructure
  └─ Dockerfile.overseer        # Overseer machine image

REPO 2: hermes-hunter-live (the Hunter's repo — starts EMPTY)
  ├─ (Overseer writes everything here)
  ├─ tools/                     # Security tools (written by Overseer)
  ├─ skills/security/           # Security skills (written by Overseer)
  ├─ hunter/                    # Hunter-specific code (written by Overseer)
  ├─ prompts/                   # Hunter system prompt (written by Overseer)
  └─ Dockerfile.hunter          # Hunter machine image (may also be written by Overseer)
```

### 5.2 Why Two Repos

The original architecture uses a git worktree (same repo, different branch). For cloud deployment, two repos is cleaner:

1. **No branch confusion.** The Overseer pushes to `main` on the Hunter repo. No worktree management needed.
2. **Independent CI.** The Hunter repo can have its own Dockerfile, its own dependencies, its own build pipeline.
3. **Access control.** The Overseer has write access to both repos. The Hunter has read access to its own repo only.
4. **Clear audit trail.** The Hunter repo's git log is a complete history of every change the Overseer has ever made.
5. **The Hunter repo IS the deliverable.** If the system works, the Hunter repo contains a fully autonomous security analysis agent — built entirely by another agent.

### 5.3 The Hunter Repo as Artifact

This is the philosophically interesting part. The Hunter repo starts empty. Over time, the Overseer fills it with tools, skills, prompts, and logic. The git history tells the story of an agent building another agent:

```
commit abc123  "feat(skills): add OWASP Top 10 detection skill"
commit def456  "feat(tools): implement target_clone — git clone wrapper"
commit ghi789  "feat(tools): implement target_scan — semgrep integration"
commit jkl012  "fix(tools): target_scan was missing --config=auto flag"
commit mno345  "feat(skills): add IDOR hunting patterns"
commit pqr678  "feat(prompt): initial Hunter system prompt"
commit stu901  "feat(tools): implement report_draft — structured vuln reporting"
commit vwx234  "test: deployed against Juice Shop, found XSS in search — but missed SQLi"
commit yza567  "feat(skills): improve injection-patterns skill with SQLi examples"
commit bcd890  "perf: switch subagent model to light tier for recon phase"
...
```

---

## 6. Self-Recursion Properties

### 6.1 What Makes This Self-Recursive

The system has multiple feedback loops operating at different timescales:

```
Loop 1: TACTICAL (seconds–minutes)
  Hunter analyses code → finds (or doesn't find) vulnerability
  → Elephantasm captures the event
  → Overseer reads event on next iteration
  → Overseer injects guidance ("try IDOR on that endpoint")
  → Hunter adjusts

Loop 2: STRUCTURAL (minutes–hours)
  Overseer notices the Hunter repeatedly misses a vuln class
  → Writes a new skill or tool
  → Commits, pushes, redeploys Hunter
  → Hunter gains new capability
  → Overseer monitors whether it helped

Loop 3: STRATEGIC (hours–days)
  Overseer's Elephantasm memory accumulates intervention outcomes
  → Dreamer synthesises: "skill additions improve vuln detection 40%,
    model switches during analysis cause context loss, recon doesn't
    need heavy compute"
  → Overseer's inject() retrieves this knowledge
  → Overseer's intervention strategy evolves

Loop 4: META-STRATEGIC (days–weeks)
  Creator reviews Elephantasm dashboard, Overseer reports, bounty outcomes
  → Talks to Overseer: "You're spending too much on recon. Also,
    the Hunter needs to learn about GraphQL-specific vulns."
  → Overseer adjusts its own strategy, writes new skills, changes
    model allocation
```

### 6.2 Self-Perpetuation

Once deployed, the system operates autonomously within budget constraints:

1. **The Overseer never stops.** It runs as a persistent process, monitoring and improving.
2. **The Hunter is ephemeral.** Killed and redeployed as needed. Each version is potentially better than the last.
3. **Knowledge compounds.** Elephantasm's Dreamer synthesises events into long-term knowledge. Both agents get smarter over time.
4. **Budget is the only external constraint.** Set a daily limit, and the system self-regulates within it. Increase the budget, and it does more.
5. **Human input is optional.** The Creator can go days without interacting. The Overseer manages everything. The Creator is needed only for report approval (legal requirement) and strategic direction.

### 6.3 What the Overseer Cannot Do

Hard boundaries that prevent runaway self-modification:

1. **Cannot modify its own code.** The Overseer's repo is read-only to itself. Only the Creator (via git push to hermes-hunter) can change the Overseer.
2. **Cannot exceed budget.** Hard stop is absolute.
3. **Cannot submit reports without human approval.** Legal and ethical requirement.
4. **Cannot attack live systems.** Hunter only analyses source code and tests against sandboxed instances.
5. **Cannot access the Overseer machine from the Hunter machine.** Network isolation.

---

## 7. Implementation Plan

### Phase A: Backend Abstraction (Prerequisite — can be done now)

**Goal:** Introduce the `ControlBackend` / `WorktreeBackend` protocol so all tool handlers go through it. Ship with only the local backend, but the abstraction is in place.

| # | Task | Description |
|---|------|-------------|
| A1 | Define backend protocols | `hunter/backends/base.py` — `ControlBackend`, `WorktreeBackend` |
| A2 | Wrap existing code as local backend | `hunter/backends/local.py` — delegates to existing `HunterController`, `WorktreeManager` |
| A3 | Add backend factory | `hunter/backends/__init__.py` — `get_backends(mode)` |
| A4 | Update tool handlers | All 4 tool modules use `get_backends()` instead of direct instantiation |
| A5 | Verify existing tests still pass | No behaviour change, just indirection |

**Deliverable:** All existing Phase 1 code works identically, but tools now go through a swappable backend. No Fly.io dependency yet.

### Phase B: Fly.io Remote Backend

**Goal:** Implement the Fly.io backend so the Overseer can manage a remote Hunter machine and push code to a remote repo.

| # | Task | Description |
|---|------|-------------|
| B1 | Fly Machines API client | `hunter/backends/fly_api.py` — thin wrapper around Fly Machines REST API (create, start, stop, destroy, status, logs). No external SDK dependency — raw `httpx` calls. |
| B2 | FlyControlBackend | `hunter/backends/fly.py` — implements `ControlBackend` using `fly_api.py`. Maps spawn/kill/status/logs to Machines API calls. |
| B3 | FlyWorktreeBackend | `hunter/backends/fly.py` — implements `WorktreeBackend` using a local git clone + push. `push()` sends commits to the Hunter repo. |
| B4 | Environment config | Fly secrets: `FLY_API_TOKEN`, `GITHUB_PAT`, `HUNTER_REPO`, `HUNTER_FLY_APP`, `ELEPHANTASM_API_KEY`, LLM API keys. Loaded in `hunter/config.py`. |
| B5 | Auto-detection | `get_backends("auto")` checks for `FLY_APP_NAME` env var (set by Fly runtime) to pick the right backend. |
| B6 | Integration test | Start a Fly machine via API, verify it boots and runs a simple command, stop it. |

**Deliverable:** The Overseer can manage a Hunter on a separate Fly machine. All existing tools work without modification.

### Phase C: Browser Terminal + Deployment

**Goal:** Package the Overseer for Fly.io deployment with browser-based terminal access.

| # | Task | Description |
|---|------|-------------|
| C1 | Overseer Dockerfile | `Dockerfile.overseer` — Python 3.11, ttyd, Hermes + hunter package, architecture docs baked in. Two processes: ttyd (foreground, port 8080) + OverseerLoop (background). |
| C2 | fly.toml for Overseer | App config: HTTP service on 8080, persistent volume at `/data`, secrets, health check. |
| C3 | Hunter Dockerfile | `Dockerfile.hunter` — Python 3.11, git, semgrep, node (for JS targets). Boot script: `git pull && pip install -r requirements.txt && python -m hunter.runner`. |
| C4 | fly.toml for Hunter | App config: no public HTTP, machine auto-stop on exit, resource limits (CPU, memory, network policy). |
| C5 | Process supervisor | Simple wrapper in `hunter/supervisor.py` that runs OverseerLoop as a background thread/process alongside the ttyd foreground process. Handles graceful shutdown. |
| C6 | Auth configuration | ttyd `--credential` with password from Fly secret. Document optional Tailscale/Cloudflare Access setup for stronger auth. |
| C7 | Deploy script | `scripts/deploy-overseer.sh` — builds image, deploys to Fly, sets secrets, creates persistent volume. One-command setup. |
| C8 | Hunter machine template | Store the Hunter machine config (image, size, env) as a JSON template the Overseer reads when spawning. |

**Deliverable:** Run `./scripts/deploy-overseer.sh`, open browser, interact with the Overseer. It can spawn and manage the Hunter on a separate Fly machine.

### Phase D: Bootstrap Mode

**Goal:** The Overseer can build the Hunter from an empty repo.

| # | Task | Description |
|---|------|-------------|
| D1 | Bootstrap detection | On startup, Overseer checks if the Hunter repo is empty (or has < N files). If so, activate bootstrap mode. |
| D2 | Bootstrap prompt section | Conditional system prompt augmentation with the build plan, testing targets, and transition criteria (see §4.3). |
| D3 | Architecture docs in Elephantasm | On first run, extract the architecture doc's key sections to the Overseer's Anima as high-importance knowledge. The Overseer can query them during bootstrap. |
| D4 | Testing target list | A config file or Elephantasm memory entry listing known-vulnerable repos (Juice Shop, DVWA, etc.) for the Overseer to use during bootstrap testing. |
| D5 | Bootstrap transition logic | After each deploy-and-test cycle, evaluate whether transition criteria are met. When they are, remove bootstrap prompt section and switch to normal improvement mode. |
| D6 | End-to-end test | Start Overseer against an empty Hunter repo. Verify it writes at least one skill, one tool, commits, pushes, and deploys. |

**Deliverable:** The Overseer bootstraps a functional Hunter from an empty repository, using the architecture docs as its blueprint.

### Phase E: Human Approval Flow

**Goal:** Reports go through human review via browser terminal and/or Telegram.

| # | Task | Description |
|---|------|-------------|
| E1 | `hermes hunter review` CLI command | Interactive report display with approve/revise/deny options. Reads from a reports queue (SQLite or file-based). |
| E2 | Report queue | Simple file-based or SQLite queue: Hunter produces a report → Overseer reviews → queues for human approval. |
| E3 | Telegram notifications | Overseer calls `send_message` to notify Creator when a report is ready. Include summary + "open terminal to review" prompt. |
| E4 | Telegram approval (stretch) | Inline approve/reject via Telegram reply. Uses existing `gateway/run.py` approval pattern. |

**Deliverable:** Reports flow from Hunter → Overseer review → human approval → submission.

---

## 8. Deployment Checklist

### Prerequisites

- [ ] Fly.io account with billing enabled
- [ ] GitHub account with a new empty repo (`hermes-hunter-live`)
- [ ] GitHub PAT with repo write access to both repos
- [ ] Elephantasm account + API key
- [ ] LLM provider account (OpenRouter recommended for model variety)

### First Deployment

```bash
# 1. Create Fly apps
fly apps create hermes-overseer
fly apps create hermes-hunter

# 2. Create persistent volume for Overseer
fly volumes create overseer_data --app hermes-overseer --size 10 --region <region>

# 3. Set secrets
fly secrets set -a hermes-overseer \
  AUTH_PASSWORD="<password>" \
  GITHUB_PAT="<pat>" \
  HUNTER_REPO="<user>/hermes-hunter-live" \
  HUNTER_FLY_APP="hermes-hunter" \
  FLY_API_TOKEN="<token>" \
  ELEPHANTASM_API_KEY="<key>" \
  OPENROUTER_API_KEY="<key>"

fly secrets set -a hermes-hunter \
  ELEPHANTASM_API_KEY="<key>" \
  OPENROUTER_API_KEY="<key>"

# 4. Deploy Overseer
fly deploy --app hermes-overseer

# 5. Open browser
open https://hermes-overseer.fly.dev

# 6. In the browser terminal:
hermes hunter setup     # Initialize Elephantasm Animas, budget config
hermes hunter budget set 15/day
hermes hunter overseer  # Start the loop — it will bootstrap the Hunter
```

---

## 9. Cost Estimate

### Fly.io Compute

| Resource | Spec | Est. Monthly Cost |
|----------|------|-------------------|
| Overseer machine | shared-cpu-2x, 512MB, always-on | ~$10–15 |
| Hunter machine | performance-2x, 2GB, on-demand | ~$5–20 (depends on uptime) |
| Persistent volume | 10GB | ~$1.50 |
| Bandwidth | Minimal (mostly API calls) | ~$0 |
| **Total Fly.io** | | **~$17–37/month** |

### LLM Costs (Managed by Budget System)

Set via `hermes hunter budget set <amount>/day`. The Overseer manages model selection within this constraint. At $15/day, that's ~$450/month for LLM inference — enough for significant analysis with medium-tier open-source models.

### Elephantasm

Depends on plan. Event volume from two agents is moderate.

### Total System Cost

**~$500–600/month** at $15/day LLM budget. The system needs to find roughly one $500–$1000 bounty per month to break even, or one $5000 bounty every 6–12 months to be highly profitable.

---

## 10. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Overseer writes buggy Hunter tools | High (early) | Medium | Bootstrap testing against known-vulnerable targets; automatic rollback on crash |
| LLM context limits during code generation | Medium | Medium | Overseer can spawn subagents for development tasks; keep individual files small |
| Fly machine costs exceed budget | Low | Low | Budget system tracks all costs; hard stop is absolute |
| Hunter produces false-positive reports | High (early) | Low | Overseer reviews before presenting to human; human reviews before submission |
| Network security during PoC testing | Medium | High | Hunter machine has restricted outbound network; PoC runs only against local sandbox |
| Overseer gets stuck in a loop | Medium | Medium | Elephantasm memory tracks intervention outcomes; self-regulation mechanism |
| ttyd security (browser terminal exposed) | Low | High | Strong auth password; optional Tailscale/CF Access; Fly private networking |

---

## 11. Open Questions

| # | Question | Options | Leaning |
|---|----------|---------|---------|
| 1 | Should the Hunter Dockerfile be written by the Overseer too, or pre-built? | Pre-built (simpler) vs. Overseer-managed (more self-recursive) | Pre-built for v1, Overseer-managed later |
| 2 | How does the Hunter install its own dependencies? | requirements.txt in hunter-repo (Overseer can add deps) vs. fat base image | requirements.txt — more flexible |
| 3 | Should the Overseer's bootstrap use subagents for parallel code writing? | Single-threaded (simpler, easier to debug) vs. subagents (faster) | Single-threaded for bootstrap, subagents once stable |
| 4 | How to handle Overseer restarts? | Resume from Elephantasm memory vs. fresh start with memory injection | Memory injection — stateless Overseer, memory in Elephantasm |
| 5 | Should the system support multiple Hunter machines? | One (simpler) vs. many (parallel target analysis) | One for v1, many as a future enhancement |
| 6 | What's the minimum viable bootstrap? | Skills only (Hunter uses stock Hermes tools) vs. skills + custom tools | Skills first — the Hunter can already use `terminal`, `browser`, `web_search` from stock Hermes |
