"""Hunter process controller — spawn, kill, poll, redeploy.

Manages the Hunter as a separate OS process via hunter/runner.py. Provides
HunterProcess (single process lifecycle) and HunterController (singleton
ensuring one Hunter at a time).

Architecture:
    The Overseer calls HunterController methods, which delegate to HunterProcess.
    HunterProcess spawns `python -m hunter.runner` as a subprocess, captures its
    output, and communicates via flag files (interrupt) and injection files.

    Overseer (this process)          Hunter (subprocess)
    ┌──────────────────┐             ┌──────────────────┐
    │ HunterController │──spawn()──→ │ hunter.runner    │
    │                  │──kill()───→ │  ↳ AIAgent       │
    │                  │←─poll()───  │  ↳ step_callback │
    │                  │             │    checks flags   │
    └──────────────────┘             └──────────────────┘
          │  ▲                              │  ▲
          │  │                              │  │
          ▼  │                              ▼  │
    interrupt.flag                    injection/current.md
    (stop signal)                     (runtime instructions)
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hunter.config import (
    HUNTER_DEFAULT_MODEL,
    HUNTER_DEFAULT_TOOLSETS,
    HUNTER_MAX_ITERATIONS,
    ensure_hunter_home,
    get_hunter_log_dir,
    get_interrupt_flag_path,
)

logger = logging.getLogger(__name__)


# Maximum bytes to keep in the in-memory output buffer (1 MB).
_MAX_BUFFER_BYTES = 1_048_576

# How long kill() waits for graceful exit before escalating to SIGKILL.
_KILL_GRACE_SECONDS = 10.0


# =============================================================================
# Data classes
# =============================================================================

@dataclass
class HunterStatus:
    """Snapshot of the Hunter process state."""

    running: bool
    pid: Optional[int]
    session_id: str
    model: str
    uptime_seconds: float
    exit_code: Optional[int]
    last_output_line: str
    error: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        if self.running:
            return (
                f"Hunter running (pid={self.pid}, session={self.session_id}, "
                f"model={self.model}, uptime={self.uptime_seconds:.0f}s)"
            )
        if self.exit_code is not None:
            return f"Hunter stopped (exit_code={self.exit_code}, session={self.session_id})"
        return f"Hunter not started (session={self.session_id})"


# =============================================================================
# HunterProcess — single process lifecycle
# =============================================================================

class HunterProcess:
    """Manages the lifecycle of a single Hunter agent subprocess.

    Spawns ``python -m hunter.runner`` as a child process, captures its
    stdout/stderr to a rolling in-memory buffer and a persistent log file,
    and supports graceful interrupt via a flag file.

    Usage::

        proc = HunterProcess(worktree_path=Path("/path/to/worktree"))
        proc.spawn(initial_instruction="Analyse the target repo.")
        status = proc.poll()
        logs = proc.get_logs(tail=50)
        proc.kill()
    """

    def __init__(
        self,
        worktree_path: Path,
        model: str = HUNTER_DEFAULT_MODEL,
        toolsets: List[str] = None,
        max_iterations: int = HUNTER_MAX_ITERATIONS,
        session_id: str = None,
        resume_session: bool = False,
    ):
        self.worktree_path = worktree_path
        self.model = model
        self.toolsets = toolsets or list(HUNTER_DEFAULT_TOOLSETS)
        self.max_iterations = max_iterations
        self.session_id = session_id or f"hunter-{uuid.uuid4().hex[:8]}"
        self.resume_session = resume_session

        # Process state
        self._process: Optional[subprocess.Popen] = None
        self._pid: Optional[int] = None
        self._started_at: Optional[float] = None
        self._exited: bool = False
        self._exit_code: Optional[int] = None
        self._error: Optional[str] = None

        # I/O capture
        self._stdout_lines: List[str] = []
        self._lock = threading.Lock()
        self._capture_thread: Optional[threading.Thread] = None
        self._log_file_path: Optional[Path] = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    def spawn(self, initial_instruction: str = None, detach: bool = False) -> None:
        """Start the Hunter as a subprocess.

        Args:
            initial_instruction: The first user message the Hunter receives.
                Defaults to a generic hunting directive.
            detach: If True, redirect stdout directly to the log file instead
                of piping through the parent process. Use this when the parent
                process will exit after spawning (e.g., CLI ``hermes hunter spawn``).
                The subprocess continues running independently.

        Raises:
            RuntimeError: If the process is already running.
        """
        if self._process is not None and self._process.poll() is None:
            raise RuntimeError(
                f"Hunter process already running (pid={self._pid}). "
                "Call kill() first."
            )

        ensure_hunter_home()
        self._clear_interrupt_flag()

        cmd = self._build_command(initial_instruction)
        env = self._build_env()

        # Prepare log file
        log_dir = get_hunter_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self._log_file_path = log_dir / f"{self.session_id}-{ts}.log"

        logger.info(
            "Spawning Hunter: session=%s model=%s worktree=%s",
            self.session_id, self.model, self.worktree_path,
        )
        logger.debug("Hunter command: %s", " ".join(cmd))

        if detach:
            # CLI mode: write directly to log file so the subprocess survives
            # the parent exiting (no pipe to break, no SIGPIPE).
            self._detach_log_fh = open(self._log_file_path, "a", encoding="utf-8")
            self._process = subprocess.Popen(
                cmd,
                cwd=str(self.worktree_path),
                stdout=self._detach_log_fh,
                stderr=subprocess.STDOUT,
                env=env,
            )
        else:
            # Overseer mode: pipe stdout for in-memory capture + monitoring.
            self._process = subprocess.Popen(
                cmd,
                cwd=str(self.worktree_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                bufsize=1,  # Line-buffered
            )

        self._pid = self._process.pid
        self._started_at = time.monotonic()
        self._exited = False
        self._exit_code = None
        self._error = None

        if not detach:
            # Start background thread to capture output
            self._capture_thread = threading.Thread(
                target=self._capture_output,
                name=f"hunter-capture-{self.session_id}",
                daemon=True,
            )
            self._capture_thread.start()

        logger.info("Hunter spawned: pid=%d session=%s detach=%s",
                     self._pid, self.session_id, detach)

    def kill(self, timeout: float = _KILL_GRACE_SECONDS) -> bool:
        """Stop the Hunter process.

        Attempts graceful shutdown via interrupt flag file, then SIGTERM,
        then SIGKILL as escalating fallbacks.

        Args:
            timeout: Seconds to wait for graceful exit before escalating.

        Returns:
            True if the process was stopped, False if it wasn't running.
        """
        if not self.is_alive():
            return False

        # Step 1: Write interrupt flag (gives Hunter a chance to save session)
        self._write_interrupt_flag("Overseer requested shutdown.")
        if self._wait_for_exit(timeout / 3):
            self._clear_interrupt_flag()
            return True

        # Step 2: SIGTERM
        logger.info("Hunter did not exit via flag file; sending SIGTERM (pid=%d)", self._pid)
        try:
            self._process.terminate()
        except OSError:
            pass
        if self._wait_for_exit(timeout / 3):
            self._clear_interrupt_flag()
            return True

        # Step 3: SIGKILL
        logger.warning("Hunter did not exit via SIGTERM; sending SIGKILL (pid=%d)", self._pid)
        try:
            self._process.kill()
        except OSError:
            pass
        if self._wait_for_exit(timeout / 3):
            self._clear_interrupt_flag()
            return True

        logger.error("Failed to kill Hunter process (pid=%d)", self._pid)
        return False

    def wait(self, timeout: float = None) -> int:
        """Block until the process exits.

        Args:
            timeout: Maximum seconds to wait. None means wait forever.

        Returns:
            The process exit code.

        Raises:
            TimeoutError: If timeout is reached before the process exits.
            RuntimeError: If the process was never started.
        """
        if self._process is None:
            raise RuntimeError("Hunter process was never started.")

        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise TimeoutError(
                f"Hunter process (pid={self._pid}) did not exit within {timeout}s"
            )

        self._mark_exited()
        return self._exit_code

    # ── Status ───────────────────────────────────────────────────────────

    def poll(self) -> HunterStatus:
        """Non-blocking health check. Returns current status snapshot."""
        if self._process is not None and not self._exited:
            rc = self._process.poll()
            if rc is not None:
                self._mark_exited()

        return HunterStatus(
            running=self.is_alive(),
            pid=self._pid,
            session_id=self.session_id,
            model=self.model,
            uptime_seconds=self.uptime_seconds,
            exit_code=self._exit_code,
            last_output_line=self._get_last_line(),
            error=self._error,
        )

    def is_alive(self) -> bool:
        """Quick check if the process is currently running."""
        if self._process is None:
            return False
        if self._exited:
            return False
        rc = self._process.poll()
        if rc is not None:
            self._mark_exited()
            return False
        return True

    @property
    def uptime_seconds(self) -> float:
        """How long the Hunter has been (or was) running."""
        if self._started_at is None:
            return 0.0
        if self.is_alive():
            return time.monotonic() - self._started_at
        # Process has exited — return total runtime
        return self._exit_uptime if hasattr(self, "_exit_uptime") else 0.0

    def get_logs(self, tail: int = 100) -> str:
        """Get the last N lines of Hunter output."""
        with self._lock:
            lines = self._stdout_lines[-tail:]
        return "\n".join(lines)

    def get_full_log_path(self) -> Optional[Path]:
        """Path to the Hunter's log file on disk, or None if never started."""
        return self._log_file_path

    # ── Internal ─────────────────────────────────────────────────────────

    def _build_command(self, initial_instruction: str = None) -> List[str]:
        """Build the subprocess command line for hunter.runner."""
        cmd = [
            sys.executable, "-m", "hunter.runner",
            "--session-id", self.session_id,
            "--model", self.model,
            "--toolsets", ",".join(self.toolsets),
            "--max-iterations", str(self.max_iterations),
        ]
        if self.resume_session:
            cmd.append("--resume")
        if initial_instruction:
            cmd.extend(["--instruction", initial_instruction])
        return cmd

    def _build_env(self) -> dict:
        """Build the environment for the Hunter subprocess."""
        env = dict(os.environ)
        # Ensure the worktree is on PYTHONPATH so the Hunter loads its own code
        pythonpath = env.get("PYTHONPATH", "")
        worktree_str = str(self.worktree_path)
        if worktree_str not in pythonpath:
            env["PYTHONPATH"] = (
                f"{worktree_str}{os.pathsep}{pythonpath}" if pythonpath
                else worktree_str
            )
        return env

    def _capture_output(self) -> None:
        """Background thread: read subprocess stdout line by line.

        Writes each line to:
        1. The in-memory rolling buffer (capped at _MAX_BUFFER_BYTES)
        2. The persistent log file on disk
        """
        log_fh = None
        try:
            if self._log_file_path:
                log_fh = open(self._log_file_path, "a", encoding="utf-8")

            for line in self._process.stdout:
                line = line.rstrip("\n")
                with self._lock:
                    self._stdout_lines.append(line)
                    # Trim buffer if it gets too large
                    while (
                        len(self._stdout_lines) > 100
                        and sum(len(l) for l in self._stdout_lines) > _MAX_BUFFER_BYTES
                    ):
                        self._stdout_lines.pop(0)

                if log_fh:
                    log_fh.write(line + "\n")
                    log_fh.flush()
        except (OSError, ValueError):
            # Pipe closed or process exited — expected
            pass
        finally:
            if log_fh:
                log_fh.close()

    def _mark_exited(self) -> None:
        """Record that the process has exited."""
        if self._exited:
            return
        self._exited = True
        self._exit_code = self._process.returncode if self._process else None
        self._exit_uptime = (
            time.monotonic() - self._started_at if self._started_at else 0.0
        )
        if self._exit_code and self._exit_code != 0:
            self._error = f"Process exited with code {self._exit_code}"
        self._clear_interrupt_flag()
        logger.info(
            "Hunter exited: pid=%s exit_code=%s uptime=%.0fs",
            self._pid, self._exit_code, self._exit_uptime,
        )

    def _get_last_line(self) -> str:
        """Get the most recent output line."""
        with self._lock:
            return self._stdout_lines[-1] if self._stdout_lines else ""

    def _wait_for_exit(self, timeout: float) -> bool:
        """Wait up to timeout seconds for the process to exit. Returns True if exited."""
        if self._process is None:
            return True
        try:
            self._process.wait(timeout=timeout)
            self._mark_exited()
            return True
        except subprocess.TimeoutExpired:
            return False

    def _write_interrupt_flag(self, message: str) -> None:
        """Write the interrupt flag file that the Hunter polls."""
        flag = get_interrupt_flag_path()
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(message, encoding="utf-8")

    def _clear_interrupt_flag(self) -> None:
        """Remove the interrupt flag file if it exists."""
        flag = get_interrupt_flag_path()
        try:
            flag.unlink(missing_ok=True)
        except OSError:
            pass

    def __repr__(self) -> str:
        state = "running" if self.is_alive() else "stopped"
        return (
            f"HunterProcess(session={self.session_id}, pid={self._pid}, "
            f"state={state}, model={self.model})"
        )


# =============================================================================
# HunterController — singleton, ensures one Hunter at a time
# =============================================================================

class HunterController:
    """High-level controller for the Hunter process.

    Wraps HunterProcess with lifecycle management: ensures only one Hunter
    runs at a time, checks budget before spawning, and supports redeploy
    (kill + restart with new code).

    Usage::

        from hunter.worktree import WorktreeManager
        from hunter.budget import BudgetManager

        controller = HunterController(
            worktree=WorktreeManager(),
            budget=BudgetManager(),
        )
        controller.spawn(instruction="Hunt for IDOR vulnerabilities.")
        status = controller.get_status()
        controller.redeploy()
        controller.kill()
    """

    def __init__(self, worktree: "WorktreeManager", budget: "BudgetManager"):
        self.worktree = worktree
        self.budget = budget
        self._current: Optional[HunterProcess] = None
        self._history: List[Dict[str, Any]] = []

    def spawn(
        self,
        model: str = None,
        initial_instruction: str = None,
        resume_session: bool = False,
        session_id: str = None,
        detach: bool = False,
    ) -> HunterProcess:
        """Spawn a new Hunter. Kills any existing one first.

        Args:
            model: LLM model override. Defaults to config default.
            initial_instruction: First message for the Hunter.
            resume_session: Resume the previous session's conversation.
            session_id: Explicit session ID. Auto-generated if omitted.
            detach: If True, the subprocess writes directly to its log file
                instead of piping through this process. Use for CLI spawns
                where the parent process will exit.

        Returns:
            The newly spawned HunterProcess.

        Raises:
            RuntimeError: If budget is exhausted.
        """
        # Check budget
        self.budget.reload()
        status = self.budget.check_budget()
        if status.hard_stop:
            raise RuntimeError(
                f"Budget exhausted ({status.percent_used:.0f}% used). "
                "Cannot spawn Hunter."
            )
        if status.alert:
            logger.warning("Budget alert: %s", status.summary())

        # Kill existing Hunter if running
        if self._current is not None and self._current.is_alive():
            logger.info("Killing existing Hunter before spawning new one.")
            self._record_history(self._current)
            self._current.kill()

        # Ensure worktree is ready
        if not self.worktree.is_setup():
            self.worktree.setup()

        # Determine session ID for resume
        if resume_session and self._current is not None and session_id is None:
            session_id = self._current.session_id

        # Create and spawn
        proc = HunterProcess(
            worktree_path=self.worktree.worktree_path,
            model=model or HUNTER_DEFAULT_MODEL,
            session_id=session_id,
            resume_session=resume_session,
        )
        proc.spawn(initial_instruction=initial_instruction, detach=detach)
        self._current = proc
        return proc

    def kill(self) -> bool:
        """Kill the current Hunter process.

        Returns:
            True if a Hunter was killed, False if none was running.
        """
        if self._current is None:
            return False
        if not self._current.is_alive():
            return False
        self._record_history(self._current)
        return self._current.kill()

    def redeploy(
        self,
        resume_session: bool = True,
        model: str = None,
    ) -> HunterProcess:
        """Kill the current Hunter and spawn a new one from the (potentially modified) worktree.

        This is the core of the Overseer's "hard intervention" flow:
        1. Overseer edits code in the worktree
        2. Overseer commits
        3. Overseer calls redeploy() — Hunter restarts with new code

        Args:
            resume_session: If True, the new Hunter continues the previous session.
            model: Optional model change applied to the new instance.

        Returns:
            The newly spawned HunterProcess.
        """
        old_session_id = None
        if self._current is not None:
            old_session_id = self._current.session_id
            if self._current.is_alive():
                self._record_history(self._current)
                self._current.kill()

        return self.spawn(
            model=model,
            resume_session=resume_session,
            session_id=old_session_id if resume_session else None,
        )

    def get_status(self) -> HunterStatus:
        """Get the current Hunter's status.

        Returns a "not started" status if no Hunter has ever been spawned.
        """
        if self._current is None:
            return HunterStatus(
                running=False,
                pid=None,
                session_id="",
                model=HUNTER_DEFAULT_MODEL,
                uptime_seconds=0.0,
                exit_code=None,
                last_output_line="",
                error="No Hunter has been spawned.",
            )
        return self._current.poll()

    def get_logs(self, tail: int = 100) -> str:
        """Get recent Hunter output."""
        if self._current is None:
            return ""
        return self._current.get_logs(tail=tail)

    @property
    def is_running(self) -> bool:
        """Is the Hunter currently alive?"""
        return self._current is not None and self._current.is_alive()

    @property
    def current(self) -> Optional[HunterProcess]:
        """The current (or most recent) HunterProcess, or None."""
        return self._current

    @property
    def history(self) -> List[Dict[str, Any]]:
        """List of past Hunter process run summaries."""
        return list(self._history)

    def _record_history(self, proc: HunterProcess) -> None:
        """Save a summary of a process run to history."""
        status = proc.poll()
        self._history.append({
            "session_id": status.session_id,
            "model": status.model,
            "pid": status.pid,
            "uptime_seconds": status.uptime_seconds,
            "exit_code": status.exit_code,
            "ended_at": datetime.now(timezone.utc).isoformat(),
        })

    def __repr__(self) -> str:
        state = "running" if self.is_running else "stopped"
        session = self._current.session_id if self._current else "none"
        return f"HunterController(state={state}, session={session}, history_len={len(self._history)})"
