"""ControlBackend implementation for Fly.io remote machines.

Manages the Hunter as a Fly.io machine via the Machines REST API.
Implements the same interface as ``HunterController`` (local backend)
but delegates process lifecycle to Fly instead of local subprocess management.

Key differences from local:
    - ``spawn()`` creates a Fly machine instead of a subprocess
    - ``kill()`` stops + destroys the Fly machine
    - ``redeploy()`` pushes code to remote before respawning
    - ``inject()`` uses Elephantasm events (not file-based IPC)
    - ``interrupt()`` stops the Fly machine (hard interrupt)
    - ``recover()`` finds orphaned machines from previous Overseer sessions
"""

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Maximum number of historical machine runs to retain in memory.
_MAX_HISTORY = 100

from hunter.backends.fly_api import FlyAPIError, FlyMachinesClient
from hunter.backends.fly_config import FlyConfig
from hunter.backends.fly_worktree import FlyWorktreeManager
from hunter.control import HunterStatus

logger = logging.getLogger(__name__)


@dataclass
class FlyHunterProcess:
    """Represents a running Hunter on a Fly machine.

    Lightweight wrapper holding machine metadata — analogous to
    ``HunterProcess`` but without subprocess internals.
    """

    machine_id: str
    session_id: str
    model: str
    started_at: datetime
    fly_app: str

    @property
    def pid(self) -> str:
        """Machine ID as the remote equivalent of PID."""
        return self.machine_id

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()


class FlyHunterController:
    """Manages the Hunter as a Fly.io machine.

    Implements the same interface as HunterController (ControlBackend protocol)
    but delegates process lifecycle to the Fly Machines API instead of local
    subprocess management.
    """

    def __init__(
        self,
        worktree: FlyWorktreeManager,
        budget: "BudgetManager",
        fly_client: FlyMachinesClient,
        fly_config: FlyConfig,
    ):
        self._worktree = worktree
        self._budget = budget
        self._fly = fly_client
        self._config = fly_config
        self._current: Optional[FlyHunterProcess] = None
        self._history: deque[Dict[str, Any]] = deque(maxlen=_MAX_HISTORY)
        # TTL cache for is_running to avoid hammering the Fly API.
        self._is_running_cache: Optional[bool] = None
        self._is_running_cache_ts: float = 0.0

    def close(self) -> None:
        """Release resources (close underlying HTTP client)."""
        self._fly.close()

    # -- ControlBackend protocol (properties) --------------------------------

    @property
    def worktree(self) -> FlyWorktreeManager:
        return self._worktree

    @property
    def budget(self) -> "BudgetManager":
        return self._budget

    # Seconds to cache is_running result before querying the API again.
    _IS_RUNNING_TTL = 30.0

    @property
    def is_running(self) -> bool:
        """Check if the Hunter machine is in 'started' state.

        Result is cached for ``_IS_RUNNING_TTL`` seconds to avoid
        hammering the Fly API on every Overseer loop iteration.
        """
        if self._current is None:
            return False
        now = time.monotonic()
        if (
            self._is_running_cache is not None
            and (now - self._is_running_cache_ts) < self._IS_RUNNING_TTL
        ):
            return self._is_running_cache
        try:
            machine = self._fly.get_machine(self._current.machine_id)
            result = machine.get("state") == "started"
        except FlyAPIError:
            result = False
        self._is_running_cache = result
        self._is_running_cache_ts = now
        return result

    @property
    def current(self) -> Optional[FlyHunterProcess]:
        return self._current

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    # -- ControlBackend protocol (lifecycle) ---------------------------------

    def spawn(
        self,
        model: str = None,
        initial_instruction: str = None,
        resume_session: bool = False,
        session_id: str = None,
        detach: bool = False,
    ) -> FlyHunterProcess:
        """Create and start a new Fly machine running the Hunter.

        Args:
            model: LLM model for the Hunter. Defaults to config default.
            initial_instruction: First instruction for the Hunter.
            resume_session: Resume the previous session.
            session_id: Explicit session ID. Auto-generated if omitted.
            detach: Ignored for Fly backend (machines are always detached).

        Returns:
            The newly created FlyHunterProcess.

        Raises:
            RuntimeError: If budget is exhausted or machine creation fails.
        """
        # Check budget
        self._budget.reload()
        status = self._budget.check_budget()
        if status.hard_stop:
            raise RuntimeError(
                f"Budget exhausted ({status.percent_used:.0f}% used). "
                "Cannot spawn Hunter."
            )
        if status.alert:
            logger.warning("Budget alert: %s", status.summary())

        # Kill existing machine if running
        if self._current is not None:
            self._record_history(self._current)
            self.kill()

        # Ensure worktree is ready
        if not self._worktree.is_setup():
            self._worktree.setup()

        # Determine session ID
        if resume_session and self._current is not None and session_id is None:
            session_id = self._current.session_id
        elif resume_session and self._current is None and session_id is None:
            logger.warning("resume_session=True but no current machine — starting new session")
        if session_id is None:
            session_id = f"hunter-{uuid.uuid4().hex[:8]}"

        model = model or "qwen/qwen3.5-32b"

        # Build machine config and create
        config = self._config.to_machine_config(
            model=model,
            session_id=session_id,
            instruction=initial_instruction,
            resume=resume_session,
        )

        logger.info(
            "Creating Fly machine: session=%s model=%s app=%s",
            session_id, model, self._config.hunter_app_name,
        )

        try:
            machine = self._fly.create_machine(config)
        except FlyAPIError as exc:
            raise RuntimeError(f"Failed to create Fly machine: {exc}") from exc

        machine_id = machine["id"]

        # Wait for the machine to start
        try:
            self._fly.wait_for_state(machine_id, "started", timeout=60)
        except FlyAPIError as exc:
            logger.error("Machine %s did not reach 'started': %s", machine_id, exc)
            # Clean up the failed machine
            try:
                self._fly.destroy_machine(machine_id, force=True)
            except FlyAPIError:
                pass
            raise RuntimeError(
                f"Fly machine {machine_id} failed to start: {exc}"
            ) from exc

        proc = FlyHunterProcess(
            machine_id=machine_id,
            session_id=session_id,
            model=model,
            started_at=datetime.now(timezone.utc),
            fly_app=self._config.hunter_app_name,
        )
        self._current = proc
        self._invalidate_running_cache()

        logger.info(
            "Hunter spawned on Fly: machine_id=%s session=%s",
            machine_id, session_id,
        )
        return proc

    def kill(self) -> bool:
        """Stop and destroy the current Hunter machine.

        Returns:
            True if a machine was stopped, False if none was running.
        """
        if self._current is None:
            return False

        machine_id = self._current.machine_id

        try:
            self._fly.stop_machine(machine_id, timeout=30)
        except FlyAPIError as exc:
            logger.warning("Failed to stop machine %s: %s", machine_id, exc)

        try:
            self._fly.wait_for_state(machine_id, "stopped", timeout=30)
        except FlyAPIError as exc:
            logger.warning("Machine %s did not reach 'stopped' state: %s", machine_id, exc)

        try:
            self._fly.destroy_machine(machine_id)
        except FlyAPIError as exc:
            logger.warning("Failed to destroy machine %s: %s", machine_id, exc)

        self._record_history(self._current)
        self._current = None
        self._invalidate_running_cache()

        logger.info("Hunter machine killed: %s", machine_id)
        return True

    def redeploy(
        self,
        resume_session: bool = True,
        model: str = None,
    ) -> FlyHunterProcess:
        """Push code changes to remote, then kill and respawn.

        Args:
            resume_session: Resume the previous session.
            model: Optional model change for the new instance.

        Returns:
            The newly spawned FlyHunterProcess.
        """
        # Push code before redeploying
        self._worktree.push()

        old_session_id = None
        if self._current is not None:
            old_session_id = self._current.session_id
            self.kill()

        return self.spawn(
            model=model,
            resume_session=resume_session,
            session_id=old_session_id if resume_session else None,
        )

    # -- ControlBackend protocol (status & monitoring) -----------------------

    def get_status(self) -> HunterStatus:
        """Query Fly machine state and build HunterStatus."""
        if self._current is None:
            return HunterStatus(
                running=False,
                pid=None,
                session_id="",
                model="",
                uptime_seconds=0.0,
                exit_code=None,
                last_output_line="",
                error="No Hunter has been spawned.",
            )

        try:
            machine = self._fly.get_machine(self._current.machine_id)
            state = machine.get("state", "unknown")
            running = state == "started"

            # Synchronise the is_running cache with the fresh API response.
            if not running:
                self._is_running_cache = False
                self._is_running_cache_ts = time.monotonic()

            return HunterStatus(
                running=running,
                pid=self._current.machine_id,
                session_id=self._current.session_id,
                model=self._current.model,
                uptime_seconds=self._current.uptime_seconds,
                exit_code=None if running else 0,
                last_output_line=f"Fly machine state: {state}",
                error=None if running else f"Machine state: {state}",
            )
        except FlyAPIError as exc:
            return HunterStatus(
                running=False,
                pid=self._current.machine_id,
                session_id=self._current.session_id,
                model=self._current.model,
                uptime_seconds=self._current.uptime_seconds,
                exit_code=None,
                last_output_line="",
                error=f"Failed to query machine: {exc}",
            )

    def get_logs(self, tail: int = 100) -> str:
        """Fetch recent logs from the Fly machine."""
        if self._current is None:
            return ""

        try:
            entries = self._fly.get_logs(self._current.machine_id, tail=tail)
            return "\n".join(
                entry.get("message", str(entry)) for entry in entries
            )
        except FlyAPIError:
            return ""

    # -- ControlBackend protocol (injection & interrupt) ---------------------

    def inject(self, instruction: str, priority: str = "normal") -> None:
        """Send injection via Elephantasm event.

        The Hunter's step_callback queries for recent injection events
        instead of reading a file.
        """
        # Write to Elephantasm as a structured event
        try:
            from hunter.memory import OverseerMemoryBridge, AnimaManager
            from hunter.config import OVERSEER_ANIMA_NAME

            anima_id = AnimaManager.get_anima_id(OVERSEER_ANIMA_NAME)
            if anima_id:
                bridge = OverseerMemoryBridge(anima_id=anima_id)
                try:
                    bridge.extract_decision(
                        f"INJECTION [{priority.upper()}]: {instruction}",
                        meta={
                            "type": "injection",
                            "priority": priority,
                            "target": "hunter",
                        },
                    )
                finally:
                    bridge.close()
                logger.info("Injected via Elephantasm: priority=%s", priority)
                return
        except Exception as exc:
            logger.debug("Elephantasm injection failed: %s", exc)

        # Fallback: log the injection (Hunter won't receive it until
        # Elephantasm is available)
        logger.warning(
            "Injection could not be delivered (Elephantasm unavailable): %s",
            instruction[:200],
        )

    def interrupt(self) -> None:
        """Stop the Fly machine (hard interrupt).

        For soft interrupt, use inject() with CRITICAL priority instead.
        """
        if self._current is not None:
            try:
                self._fly.stop_machine(self._current.machine_id)
                logger.info("Interrupted Hunter: stopped machine %s",
                            self._current.machine_id)
            except FlyAPIError as exc:
                logger.warning("Failed to interrupt machine: %s", exc)

    # -- Recovery ------------------------------------------------------------

    def recover(self) -> Optional[FlyHunterProcess]:
        """Check for an existing Hunter machine from a previous Overseer session.

        Called during startup to reconnect to a running Hunter
        instead of orphaning it.

        Returns:
            The recovered FlyHunterProcess, or None if no running machines.
        """
        try:
            machines = self._fly.list_machines()
        except FlyAPIError as exc:
            logger.warning("Failed to list machines for recovery: %s", exc)
            return None

        running = [m for m in machines if m.get("state") == "started"]
        if not running:
            return None

        m = running[0]
        env = m.get("config", {}).get("env", {})
        created_at = m.get("created_at", "")

        try:
            started_at = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            started_at = datetime.now(timezone.utc)

        proc = FlyHunterProcess(
            machine_id=m["id"],
            session_id=env.get("SESSION_ID", "unknown"),
            model=env.get("HUNTER_MODEL", "unknown"),
            started_at=started_at,
            fly_app=self._config.hunter_app_name,
        )
        self._current = proc

        logger.info(
            "Recovered Hunter machine: id=%s session=%s",
            proc.machine_id, proc.session_id,
        )
        return proc

    # -- Internal ------------------------------------------------------------

    def _invalidate_running_cache(self) -> None:
        """Force the next ``is_running`` call to query the API."""
        self._is_running_cache = None
        self._is_running_cache_ts = 0.0

    def _record_history(self, proc: FlyHunterProcess) -> None:
        """Save a summary of a process run to history."""
        self._history.append({
            "machine_id": proc.machine_id,
            "session_id": proc.session_id,
            "model": proc.model,
            "uptime_seconds": proc.uptime_seconds,
            "ended_at": datetime.now(timezone.utc).isoformat(),
        })

    def __repr__(self) -> str:
        state = "running" if self._current is not None else "stopped"
        session = self._current.session_id if self._current else "none"
        return (
            f"FlyHunterController(state={state}, session={session}, "
            f"app={self._config.hunter_app_name})"
        )
