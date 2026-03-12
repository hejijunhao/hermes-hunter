"""CLI entry points for ``hermes hunter`` subcommands.

Registers the ``hunter`` subcommand tree with the main Hermes CLI parser
and dispatches to the appropriate handler.

Commands::

    hermes hunter                     Show system status (default)
    hermes hunter setup               One-time infrastructure setup
    hermes hunter overseer             Start the Overseer control loop
    hermes hunter spawn                Manually spawn a Hunter
    hermes hunter kill                 Kill the running Hunter
    hermes hunter status               Show system status
    hermes hunter budget               Budget management
    hermes hunter logs                 Show Hunter logs
"""

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from hunter.config import ensure_hunter_home, get_hunter_home, get_hunter_log_dir


# =============================================================================
# PID / metadata helpers
# =============================================================================

def _get_pid_path() -> Path:
    """Path to the Hunter PID file."""
    return get_hunter_home() / "hunter.pid"


def _get_meta_path() -> Path:
    """Path to the Hunter metadata JSON (session, model, log path)."""
    return get_hunter_home() / "hunter.meta.json"


def _write_pid_meta(
    pid: int, session_id: str, model: str, log_path: str,
) -> None:
    """Write PID file and metadata JSON after spawning."""
    ensure_hunter_home()
    _get_pid_path().write_text(str(pid))
    meta = {
        "session_id": session_id,
        "model": model,
        "log_file": log_path,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _get_meta_path().write_text(json.dumps(meta))


def _read_pid_meta() -> Tuple[Optional[int], dict]:
    """Read PID and metadata. Returns ``(None, {})`` if stale or missing.

    Auto-cleans stale PID files where the process no longer exists.
    """
    pid_path = _get_pid_path()
    if not pid_path.exists():
        return None, {}
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)  # Existence check — no signal sent
    except (ValueError, ProcessLookupError, PermissionError):
        _clear_pid_meta()
        return None, {}

    meta = {}
    meta_path = _get_meta_path()
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return pid, meta


def _clear_pid_meta() -> None:
    """Remove PID and metadata files."""
    for p in (_get_pid_path(), _get_meta_path()):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID exists."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# =============================================================================
# Argparse registration
# =============================================================================

def register_hunter_commands(subparsers) -> "argparse.ArgumentParser":
    """Register all ``hermes hunter *`` subcommands.

    Returns the ``hunter`` parser so the caller can set its default handler.
    """
    hunter_parser = subparsers.add_parser(
        "hunter",
        help="Bug bounty hunting system — Overseer + Hunter agents",
        description=(
            "Autonomous bug bounty hunting system. The Overseer monitors and "
            "improves a Hunter agent that finds vulnerabilities."
        ),
    )
    hunter_sub = hunter_parser.add_subparsers(dest="hunter_command")

    # hermes hunter setup
    hunter_sub.add_parser(
        "setup",
        help="One-time setup (worktree, Elephantasm, budget config)",
    )

    # hermes hunter overseer
    overseer_parser = hunter_sub.add_parser(
        "overseer",
        help="Start the Overseer control loop",
    )
    overseer_parser.add_argument(
        "--model", default=None,
        help="LLM model for the Overseer (default: anthropic/claude-opus-4.6)",
    )
    overseer_parser.add_argument(
        "--interval", type=float, default=30.0,
        help="Seconds between check iterations (default: 30)",
    )

    # hermes hunter spawn
    spawn_parser = hunter_sub.add_parser(
        "spawn",
        help="Manually spawn a Hunter agent",
    )
    spawn_parser.add_argument(
        "--model", default=None,
        help="LLM model for the Hunter (default: qwen/qwen3.5-32b)",
    )
    spawn_parser.add_argument(
        "--instruction", default=None,
        help="Initial instruction for the Hunter",
    )
    spawn_parser.add_argument(
        "--resume", action="store_true",
        help="Resume from the Hunter's last session",
    )

    # hermes hunter kill
    hunter_sub.add_parser("kill", help="Kill the running Hunter")

    # hermes hunter status
    hunter_sub.add_parser("status", help="Show system status")

    # hermes hunter budget
    budget_parser = hunter_sub.add_parser("budget", help="Budget management")
    budget_sub = budget_parser.add_subparsers(dest="budget_command")
    set_parser = budget_sub.add_parser("set", help="Set budget limit")
    set_parser.add_argument(
        "value",
        help="Budget value (e.g., '20/day', '300/5days', '15')",
    )
    budget_sub.add_parser("history", help="Show spend history")

    # hermes hunter logs
    logs_parser = hunter_sub.add_parser("logs", help="Show Hunter logs")
    logs_parser.add_argument(
        "--follow", "-f", action="store_true",
        help="Follow logs in real-time (like tail -f)",
    )
    logs_parser.add_argument(
        "--tail", type=int, default=50,
        help="Number of lines to show (default: 50)",
    )

    return hunter_parser


# =============================================================================
# Top-level dispatcher
# =============================================================================

def handle_hunter_command(args) -> None:
    """Dispatch ``hermes hunter`` subcommands."""
    cmd = getattr(args, "hunter_command", None)

    handlers = {
        "setup": _cmd_setup,
        "overseer": _cmd_overseer,
        "spawn": _cmd_spawn,
        "kill": _cmd_kill,
        "status": _cmd_status,
        "budget": _cmd_budget,
        "logs": _cmd_logs,
    }

    # Default to status when no subcommand given
    handler = handlers.get(cmd, _cmd_status)

    try:
        handler(args)
    except KeyboardInterrupt:
        print()  # Clean line after ^C
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


# =============================================================================
# Command handlers
# =============================================================================

def _cmd_setup(args) -> None:
    """One-time infrastructure setup."""
    print("Setting up Hermes Hunter...\n")

    # 1. Directory structure
    ensure_hunter_home()
    print("  [ok] Directory structure (~/.hermes/hunter/)")

    # 2. Git worktree
    from hunter.worktree import WorktreeManager
    wt = WorktreeManager()
    if wt.is_setup():
        print("  [ok] Git worktree (already set up)")
    else:
        wt.setup()
        print("  [ok] Git worktree (hunter/live branch created)")

    # 3. Budget config
    from hunter.budget import BudgetManager
    mgr = BudgetManager()
    if mgr.create_default_config():
        print("  [ok] Budget config (default created)")
    else:
        print("  [ok] Budget config (already exists)")

    # 4. Elephantasm Animas (non-fatal)
    try:
        from hunter.memory import AnimaManager
        result = AnimaManager.ensure_animas()
        if result:
            print("  [ok] Elephantasm Animas")
        else:
            print("  [--] Elephantasm Animas (no SDK or no API key)")
    except Exception as e:
        print(f"  [--] Elephantasm Animas (skipped: {e})")

    print("\nSetup complete.")


def _cmd_overseer(args) -> None:
    """Start the Overseer control loop."""
    from hunter.overseer import OverseerLoop

    kwargs = {"check_interval": args.interval}
    if args.model:
        kwargs["model"] = args.model

    model_display = args.model or "default"
    print(f"Starting Overseer (model={model_display}, interval={args.interval}s)")
    print("Press Ctrl+C to stop.\n")

    loop = OverseerLoop(**kwargs)
    loop.run()


def _cmd_spawn(args) -> None:
    """Spawn a Hunter agent subprocess."""
    # Check if already running
    pid, meta = _read_pid_meta()
    if pid is not None:
        print(
            f"Hunter already running "
            f"(pid={pid}, session={meta.get('session_id', '?')})"
        )
        print("Use 'hermes hunter kill' first.")
        sys.exit(1)

    from hunter.budget import BudgetManager
    from hunter.control import HunterController
    from hunter.worktree import WorktreeManager

    wt = WorktreeManager()
    budget = BudgetManager()
    controller = HunterController(worktree=wt, budget=budget)

    try:
        proc = controller.spawn(
            model=args.model,
            initial_instruction=args.instruction,
            resume_session=args.resume,
            detach=True,
        )
    except RuntimeError as e:
        print(f"Cannot spawn: {e}")
        sys.exit(1)

    # Write PID file so other CLI commands can find the process
    log_path = str(proc.get_full_log_path() or "")
    _write_pid_meta(proc._pid, proc.session_id, proc.model, log_path)

    print(f"Hunter spawned:")
    print(f"  PID:     {proc._pid}")
    print(f"  Session: {proc.session_id}")
    print(f"  Model:   {proc.model}")
    if log_path:
        print(f"  Logs:    {log_path}")


def _cmd_kill(args) -> None:
    """Kill the running Hunter process."""
    pid, meta = _read_pid_meta()
    if pid is None:
        print("No Hunter process running.")
        return

    session = meta.get("session_id", "?")
    print(f"Killing Hunter (pid={pid}, session={session})...")

    # Stage 1: Write interrupt flag (graceful)
    from hunter.config import get_interrupt_flag_path
    flag = get_interrupt_flag_path()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("CLI kill requested.", encoding="utf-8")

    for _ in range(10):  # Wait up to 5 seconds
        time.sleep(0.5)
        if not _is_process_alive(pid):
            _clear_pid_meta()
            _clear_interrupt_flag(flag)
            print("Hunter stopped gracefully.")
            return

    # Stage 2: SIGTERM
    print("  Sending SIGTERM...")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _clear_pid_meta()
        _clear_interrupt_flag(flag)
        print("Hunter already stopped.")
        return

    for _ in range(10):  # Wait up to 5 seconds
        time.sleep(0.5)
        if not _is_process_alive(pid):
            _clear_pid_meta()
            _clear_interrupt_flag(flag)
            print("Hunter terminated (SIGTERM).")
            return

    # Stage 3: SIGKILL
    print("  Sending SIGKILL...")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

    _clear_pid_meta()
    _clear_interrupt_flag(flag)
    print("Hunter killed (SIGKILL).")


def _clear_interrupt_flag(flag: Path) -> None:
    """Remove the interrupt flag file."""
    try:
        flag.unlink(missing_ok=True)
    except OSError:
        pass


def _cmd_status(args) -> None:
    """Show system status: Hunter process, budget, worktree."""
    # Hunter process
    pid, meta = _read_pid_meta()
    if pid is not None:
        uptime_str = ""
        started = meta.get("started_at")
        if started:
            try:
                start_dt = datetime.fromisoformat(started)
                delta = datetime.now(timezone.utc) - start_dt
                uptime_str = f", uptime={int(delta.total_seconds())}s"
            except ValueError:
                pass
        print(
            f"Hunter:   running (pid={pid}, session={meta.get('session_id', '?')}, "
            f"model={meta.get('model', '?')}{uptime_str})"
        )
    else:
        print("Hunter:   not running")

    # Budget
    try:
        from hunter.budget import BudgetManager
        mgr = BudgetManager()
        status = mgr.check_budget()
        line = f"Budget:   {status.summary()}"
        if status.alert:
            line += "  [ALERT]"
        if status.hard_stop:
            line += "  [HARD STOP]"
        print(line)
    except Exception:
        print("Budget:   not configured (run 'hermes hunter setup')")

    # Worktree
    try:
        from hunter.worktree import WorktreeManager
        wt = WorktreeManager()
        if wt.is_setup():
            head = wt.get_head_commit()[:8]
            print(f"Worktree: ready (head={head})")
        else:
            print("Worktree: not set up (run 'hermes hunter setup')")
    except Exception:
        print("Worktree: not set up (run 'hermes hunter setup')")


def _cmd_budget(args) -> None:
    """Budget management: show status, set limits, view history."""
    from hunter.budget import BudgetManager, parse_budget_string

    mgr = BudgetManager()
    subcmd = getattr(args, "budget_command", None)

    if subcmd is None:
        # Default: show budget status
        status = mgr.check_budget()
        print(f"Mode:        {status.mode}")
        print(f"Status:      {status.summary()}")
        if status.mode == "total" and status.daily_rate_limit:
            print(f"Rate limit:  ${status.daily_rate_limit:.2f}/day")
        print(f"Spend today: ${status.spend_today:.2f}")
        print(f"Spend total: ${status.spend_total:.2f}")
        if status.alert:
            print("\nWARNING: Alert threshold reached!")
        if status.hard_stop:
            print("HARD STOP: Budget exhausted!")

    elif subcmd == "set":
        try:
            kwargs = parse_budget_string(args.value)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)
        mgr.update_config(**kwargs)
        print(f"Budget updated: {args.value}")
        new_status = mgr.check_budget()
        print(f"New status: {new_status.summary()}")

    elif subcmd == "history":
        daily = mgr.get_daily_summary()
        if not daily:
            print("No spend history.")
            return

        print("Daily spend:")
        for day, total in daily.items():
            print(f"  {day}:  ${total:.4f}")

        entries = mgr.get_spend_history(limit=20)
        if entries:
            print(f"\nRecent entries ({len(entries)}):")
            for e in entries[:10]:
                ts = e.get("timestamp", "")[:19]
                cost = e.get("cost_usd", 0.0)
                model = e.get("model", "?")
                agent = e.get("agent", "?")
                print(f"  {ts}  ${cost:.4f}  {model}  ({agent})")


def _cmd_logs(args) -> None:
    """Show Hunter logs from the most recent log file."""
    log_dir = get_hunter_log_dir()

    if not log_dir.exists():
        print("No logs found. Has the Hunter been spawned?")
        return

    # Find most recent log file
    log_files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
    if not log_files:
        print("No log files found.")
        return

    log_file = log_files[-1]  # Most recent by mtime
    print(f"Log file: {log_file.name}\n")

    if args.follow:
        _follow_log(log_file, args.tail)
    else:
        _tail_log(log_file, args.tail)


def _tail_log(log_file: Path, n: int) -> None:
    """Print the last N lines of a log file."""
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    for line in lines[-n:]:
        print(line, end="")


def _follow_log(log_file: Path, initial_lines: int) -> None:
    """Follow a log file in real-time (like ``tail -f``)."""
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        # Show last N lines first
        lines = f.readlines()
        for line in lines[-initial_lines:]:
            print(line, end="")

        # Follow new content
        try:
            while True:
                line = f.readline()
                if line:
                    print(line, end="")
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            print()
