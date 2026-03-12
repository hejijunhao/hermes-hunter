"""Tests for the Hunter CLI entry points (Task 12).

All tests mock infrastructure classes to avoid real git, subprocess, or API calls.
PID/meta tests use tmp_path for file isolation.
"""

import json
import os
import signal
import argparse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from hunter.cli import (
    _get_pid_path,
    _get_meta_path,
    _write_pid_meta,
    _read_pid_meta,
    _clear_pid_meta,
    _is_process_alive,
    register_hunter_commands,
    handle_hunter_command,
    _cmd_setup,
    _cmd_overseer,
    _cmd_spawn,
    _cmd_kill,
    _cmd_status,
    _cmd_budget,
    _cmd_logs,
    _tail_log,
)
from hunter.budget import BudgetStatus


# =============================================================================
# Test data
# =============================================================================

def _make_budget_status(
    allowed=True, remaining=10.0, percent=33.3,
    alert=False, hard_stop=False,
    spend_today=5.0, spend_total=50.0,
) -> BudgetStatus:
    return BudgetStatus(
        allowed=allowed,
        remaining_usd=remaining,
        percent_used=percent,
        alert=alert,
        hard_stop=hard_stop,
        mode="daily",
        spend_today=spend_today,
        spend_total=spend_total,
        daily_limit=15.0,
        total_limit=None,
        daily_rate_limit=None,
    )


def _make_args(**kwargs):
    """Create a mock argparse.Namespace with given attributes."""
    return argparse.Namespace(**kwargs)


# =============================================================================
# Tests — PID / metadata helpers
# =============================================================================

class TestPidMeta:

    def test_write_and_read_roundtrip(self, tmp_path):
        """Write PID + meta, read them back."""
        with patch("hunter.cli.get_hunter_home", return_value=tmp_path), \
             patch("hunter.cli.ensure_hunter_home"):
            _write_pid_meta(12345, "hunter-abc", "qwen/qwen3.5-32b", "/tmp/log.log")
            # Verify files exist
            assert (tmp_path / "hunter.pid").exists()
            assert (tmp_path / "hunter.meta.json").exists()

            # Mock os.kill to report process alive
            with patch("hunter.cli.os.kill"):
                pid, meta = _read_pid_meta()
            assert pid == 12345
            assert meta["session_id"] == "hunter-abc"
            assert meta["model"] == "qwen/qwen3.5-32b"
            assert meta["log_file"] == "/tmp/log.log"
            assert "started_at" in meta

    def test_read_no_pid_file(self, tmp_path):
        """Returns (None, {}) when no PID file exists."""
        with patch("hunter.cli.get_hunter_home", return_value=tmp_path):
            pid, meta = _read_pid_meta()
        assert pid is None
        assert meta == {}

    def test_read_stale_pid_auto_cleans(self, tmp_path):
        """Stale PID (process dead) gets cleaned up automatically."""
        pid_path = tmp_path / "hunter.pid"
        pid_path.write_text("99999")
        meta_path = tmp_path / "hunter.meta.json"
        meta_path.write_text('{"session_id": "old"}')

        with patch("hunter.cli.get_hunter_home", return_value=tmp_path), \
             patch("hunter.cli.os.kill", side_effect=ProcessLookupError):
            pid, meta = _read_pid_meta()
        assert pid is None
        assert meta == {}
        # Files should be cleaned up
        assert not pid_path.exists()
        assert not meta_path.exists()

    def test_read_invalid_pid_content(self, tmp_path):
        """Non-integer PID content gets cleaned up."""
        (tmp_path / "hunter.pid").write_text("not-a-number")
        with patch("hunter.cli.get_hunter_home", return_value=tmp_path):
            pid, meta = _read_pid_meta()
        assert pid is None

    def test_clear_pid_meta(self, tmp_path):
        """Clears both files."""
        (tmp_path / "hunter.pid").write_text("123")
        (tmp_path / "hunter.meta.json").write_text("{}")
        with patch("hunter.cli.get_hunter_home", return_value=tmp_path):
            _clear_pid_meta()
        assert not (tmp_path / "hunter.pid").exists()
        assert not (tmp_path / "hunter.meta.json").exists()

    def test_clear_pid_meta_missing_ok(self, tmp_path):
        """Clearing non-existent files doesn't raise."""
        with patch("hunter.cli.get_hunter_home", return_value=tmp_path):
            _clear_pid_meta()  # Should not raise

    def test_is_process_alive_true(self):
        with patch("hunter.cli.os.kill"):
            assert _is_process_alive(os.getpid()) is True

    def test_is_process_alive_false(self):
        with patch("hunter.cli.os.kill", side_effect=ProcessLookupError):
            assert _is_process_alive(99999) is False


# =============================================================================
# Tests — register_hunter_commands
# =============================================================================

class TestRegisterCommands:

    def _parse(self, *args):
        """Parse args through a real argparse parser with hunter commands."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register_hunter_commands(sub)
        return parser.parse_args(["hunter"] + list(args))

    def test_setup_parses(self):
        args = self._parse("setup")
        assert args.hunter_command == "setup"

    def test_overseer_defaults(self):
        args = self._parse("overseer")
        assert args.hunter_command == "overseer"
        assert args.model is None
        assert args.interval == 30.0

    def test_overseer_with_args(self):
        args = self._parse("overseer", "--model", "qwen/qwen3.5-72b", "--interval", "60")
        assert args.model == "qwen/qwen3.5-72b"
        assert args.interval == 60.0

    def test_spawn_defaults(self):
        args = self._parse("spawn")
        assert args.hunter_command == "spawn"
        assert args.model is None
        assert args.instruction is None
        assert args.resume is False

    def test_spawn_with_args(self):
        args = self._parse("spawn", "--model", "qwen/qwen3.5-7b", "--instruction", "Hunt!", "--resume")
        assert args.model == "qwen/qwen3.5-7b"
        assert args.instruction == "Hunt!"
        assert args.resume is True

    def test_kill_parses(self):
        args = self._parse("kill")
        assert args.hunter_command == "kill"

    def test_status_parses(self):
        args = self._parse("status")
        assert args.hunter_command == "status"

    def test_budget_default(self):
        args = self._parse("budget")
        assert args.hunter_command == "budget"
        assert getattr(args, "budget_command", None) is None

    def test_budget_set(self):
        args = self._parse("budget", "set", "20/day")
        assert args.budget_command == "set"
        assert args.value == "20/day"

    def test_budget_history(self):
        args = self._parse("budget", "history")
        assert args.budget_command == "history"

    def test_logs_defaults(self):
        args = self._parse("logs")
        assert args.hunter_command == "logs"
        assert args.follow is False
        assert args.tail == 50

    def test_logs_with_follow(self):
        args = self._parse("logs", "-f", "--tail", "100")
        assert args.follow is True
        assert args.tail == 100

    def test_no_subcommand(self):
        args = self._parse()
        assert getattr(args, "hunter_command", None) is None


# =============================================================================
# Tests — handle_hunter_command (dispatch)
# =============================================================================

class TestDispatch:

    @patch("hunter.cli._cmd_status")
    def test_default_dispatches_to_status(self, mock_status):
        args = _make_args(hunter_command=None)
        handle_hunter_command(args)
        mock_status.assert_called_once_with(args)

    @patch("hunter.cli._cmd_setup")
    def test_dispatches_setup(self, mock_setup):
        args = _make_args(hunter_command="setup")
        handle_hunter_command(args)
        mock_setup.assert_called_once_with(args)

    @patch("hunter.cli._cmd_spawn")
    def test_dispatches_spawn(self, mock_spawn):
        args = _make_args(hunter_command="spawn")
        handle_hunter_command(args)
        mock_spawn.assert_called_once_with(args)

    @patch("hunter.cli._cmd_status")
    def test_exception_prints_error(self, mock_status, capsys):
        mock_status.side_effect = RuntimeError("Something broke")
        args = _make_args(hunter_command="status")
        with pytest.raises(SystemExit) as exc_info:
            handle_hunter_command(args)
        assert exc_info.value.code == 1
        assert "Something broke" in capsys.readouterr().err


# =============================================================================
# Tests — _cmd_setup
# =============================================================================

class TestCmdSetup:

    @patch("hunter.cli.ensure_hunter_home")
    @patch("hunter.worktree.WorktreeManager")
    @patch("hunter.budget.BudgetManager")
    @patch("hunter.memory.AnimaManager")
    def test_setup_calls_all_components(self, mock_anima, mock_budget_cls, mock_wt_cls, mock_ensure, capsys):
        mock_wt = mock_wt_cls.return_value
        mock_wt.is_setup.return_value = False
        mock_budget = mock_budget_cls.return_value
        mock_budget.create_default_config.return_value = True
        mock_anima.ensure_animas.return_value = {"hermes-overseer": "id1"}

        _cmd_setup(_make_args())

        mock_ensure.assert_called_once()
        mock_wt.setup.assert_called_once()
        mock_budget.create_default_config.assert_called_once()
        mock_anima.ensure_animas.assert_called_once()
        output = capsys.readouterr().out
        assert "Setup complete" in output

    @patch("hunter.cli.ensure_hunter_home")
    @patch("hunter.worktree.WorktreeManager")
    @patch("hunter.budget.BudgetManager")
    @patch("hunter.memory.AnimaManager")
    def test_setup_worktree_already_set_up(self, mock_anima, mock_budget_cls, mock_wt_cls, mock_ensure, capsys):
        mock_wt = mock_wt_cls.return_value
        mock_wt.is_setup.return_value = True
        mock_budget_cls.return_value.create_default_config.return_value = False
        mock_anima.ensure_animas.return_value = {}

        _cmd_setup(_make_args())
        mock_wt.setup.assert_not_called()
        output = capsys.readouterr().out
        assert "already set up" in output

    @patch("hunter.cli.ensure_hunter_home")
    @patch("hunter.worktree.WorktreeManager")
    @patch("hunter.budget.BudgetManager")
    @patch("hunter.memory.AnimaManager")
    def test_setup_elephantasm_failure_nonfatal(self, mock_anima, mock_budget_cls, mock_wt_cls, mock_ensure, capsys):
        mock_wt_cls.return_value.is_setup.return_value = True
        mock_budget_cls.return_value.create_default_config.return_value = False
        mock_anima.ensure_animas.side_effect = RuntimeError("No API key")

        _cmd_setup(_make_args())  # Should not raise
        output = capsys.readouterr().out
        assert "skipped" in output
        assert "Setup complete" in output


# =============================================================================
# Tests — _cmd_overseer
# =============================================================================

class TestCmdOverseer:

    @patch("hunter.overseer.OverseerLoop")
    def test_overseer_creates_loop_and_runs(self, mock_loop_cls, capsys):
        mock_loop = mock_loop_cls.return_value
        args = _make_args(model=None, interval=30.0)
        _cmd_overseer(args)
        mock_loop_cls.assert_called_once_with(check_interval=30.0)
        mock_loop.run.assert_called_once()

    @patch("hunter.overseer.OverseerLoop")
    def test_overseer_with_model(self, mock_loop_cls):
        mock_loop_cls.return_value.run = MagicMock()
        args = _make_args(model="qwen/qwen3.5-72b", interval=60.0)
        _cmd_overseer(args)
        mock_loop_cls.assert_called_once_with(
            check_interval=60.0, model="qwen/qwen3.5-72b",
        )

    @patch("hunter.overseer.OverseerLoop")
    def test_overseer_prints_startup_message(self, mock_loop_cls, capsys):
        mock_loop_cls.return_value.run = MagicMock()
        args = _make_args(model=None, interval=30.0)
        _cmd_overseer(args)
        output = capsys.readouterr().out
        assert "Overseer" in output
        assert "Ctrl+C" in output


# =============================================================================
# Tests — _cmd_spawn
# =============================================================================

class TestCmdSpawn:

    @patch("hunter.cli._write_pid_meta")
    @patch("hunter.cli._read_pid_meta", return_value=(None, {}))
    @patch("hunter.control.HunterController")
    @patch("hunter.worktree.WorktreeManager")
    @patch("hunter.budget.BudgetManager")
    def test_spawn_success(self, mock_budget_cls, mock_wt_cls, mock_ctrl_cls, mock_read, mock_write, capsys):
        mock_proc = MagicMock()
        mock_proc._pid = 42
        mock_proc.session_id = "hunter-xyz"
        mock_proc.model = "qwen/qwen3.5-32b"
        mock_proc.get_full_log_path.return_value = Path("/tmp/hunter.log")
        mock_ctrl_cls.return_value.spawn.return_value = mock_proc

        args = _make_args(model=None, instruction=None, resume=False)
        _cmd_spawn(args)

        mock_ctrl_cls.return_value.spawn.assert_called_once_with(
            model=None, initial_instruction=None,
            resume_session=False, detach=True,
        )
        mock_write.assert_called_once_with(42, "hunter-xyz", "qwen/qwen3.5-32b", "/tmp/hunter.log")
        output = capsys.readouterr().out
        assert "42" in output
        assert "hunter-xyz" in output

    @patch("hunter.cli._read_pid_meta", return_value=(999, {"session_id": "old"}))
    def test_spawn_already_running(self, mock_read, capsys):
        args = _make_args(model=None, instruction=None, resume=False)
        with pytest.raises(SystemExit) as exc_info:
            _cmd_spawn(args)
        assert exc_info.value.code == 1
        output = capsys.readouterr().out
        assert "already running" in output

    @patch("hunter.cli._read_pid_meta", return_value=(None, {}))
    @patch("hunter.control.HunterController")
    @patch("hunter.worktree.WorktreeManager")
    @patch("hunter.budget.BudgetManager")
    def test_spawn_budget_exhausted(self, mock_budget_cls, mock_wt_cls, mock_ctrl_cls, mock_read, capsys):
        mock_ctrl_cls.return_value.spawn.side_effect = RuntimeError("Budget exhausted (100% used).")
        args = _make_args(model=None, instruction=None, resume=False)
        with pytest.raises(SystemExit) as exc_info:
            _cmd_spawn(args)
        assert exc_info.value.code == 1
        output = capsys.readouterr().out
        assert "Cannot spawn" in output


# =============================================================================
# Tests — _cmd_kill
# =============================================================================

class TestCmdKill:

    @patch("hunter.cli._read_pid_meta", return_value=(None, {}))
    def test_kill_no_process(self, mock_read, capsys):
        _cmd_kill(_make_args())
        output = capsys.readouterr().out
        assert "No Hunter process running" in output

    @patch("hunter.cli._clear_interrupt_flag")
    @patch("hunter.cli._clear_pid_meta")
    @patch("hunter.cli._is_process_alive", return_value=False)
    @patch("hunter.cli._read_pid_meta", return_value=(123, {"session_id": "abc"}))
    @patch("hunter.config.get_interrupt_flag_path")
    @patch("hunter.cli.time.sleep")
    def test_kill_graceful(self, mock_sleep, mock_flag_path, mock_read, mock_alive, mock_clear_pid, mock_clear_flag, tmp_path, capsys):
        flag_file = tmp_path / "interrupt.flag"
        mock_flag_path.return_value = flag_file
        _cmd_kill(_make_args())
        output = capsys.readouterr().out
        assert "gracefully" in output
        mock_clear_pid.assert_called_once()

    @patch("hunter.cli._clear_interrupt_flag")
    @patch("hunter.cli._clear_pid_meta")
    @patch("hunter.cli.os.kill")
    @patch("hunter.cli._is_process_alive")
    @patch("hunter.cli._read_pid_meta", return_value=(123, {"session_id": "abc"}))
    @patch("hunter.config.get_interrupt_flag_path")
    @patch("hunter.cli.time.sleep")
    def test_kill_escalates_to_sigterm(self, mock_sleep, mock_flag_path, mock_read, mock_alive, mock_os_kill, mock_clear_pid, mock_clear_flag, tmp_path, capsys):
        flag_file = tmp_path / "interrupt.flag"
        mock_flag_path.return_value = flag_file
        # Process stays alive for first 10 checks (flag), dies on first SIGTERM check
        mock_alive.side_effect = [True] * 10 + [False]
        _cmd_kill(_make_args())
        output = capsys.readouterr().out
        assert "SIGTERM" in output
        mock_os_kill.assert_called_with(123, signal.SIGTERM)


# =============================================================================
# Tests — _cmd_status
# =============================================================================

class TestCmdStatus:

    @patch("hunter.worktree.WorktreeManager")
    @patch("hunter.budget.BudgetManager")
    @patch("hunter.cli._read_pid_meta", return_value=(None, {}))
    def test_status_not_running(self, mock_read, mock_budget_cls, mock_wt_cls, capsys):
        mock_budget_cls.return_value.check_budget.return_value = _make_budget_status()
        mock_wt_cls.return_value.is_setup.return_value = True
        mock_wt_cls.return_value.get_head_commit.return_value = "abc12345678"
        _cmd_status(_make_args())
        output = capsys.readouterr().out
        assert "not running" in output
        assert "$" in output  # Budget info
        assert "ready" in output  # Worktree

    @patch("hunter.worktree.WorktreeManager")
    @patch("hunter.budget.BudgetManager")
    @patch("hunter.cli._read_pid_meta")
    def test_status_running(self, mock_read, mock_budget_cls, mock_wt_cls, capsys):
        started = datetime.now(timezone.utc).isoformat()
        mock_read.return_value = (42, {"session_id": "hunter-abc", "model": "qwen/qwen3.5-32b", "started_at": started})
        mock_budget_cls.return_value.check_budget.return_value = _make_budget_status()
        mock_wt_cls.return_value.is_setup.return_value = True
        mock_wt_cls.return_value.get_head_commit.return_value = "abc12345678"
        _cmd_status(_make_args())
        output = capsys.readouterr().out
        assert "running" in output
        assert "42" in output
        assert "hunter-abc" in output

    @patch("hunter.cli._read_pid_meta", return_value=(None, {}))
    def test_status_degraded_no_budget(self, mock_read, capsys):
        """Status works even if budget/worktree aren't configured."""
        with patch("hunter.budget.BudgetManager", side_effect=Exception("no config")), \
             patch("hunter.worktree.WorktreeManager", side_effect=Exception("no git")):
            _cmd_status(_make_args())
        output = capsys.readouterr().out
        assert "not running" in output
        assert "not configured" in output or "not set up" in output

    @patch("hunter.worktree.WorktreeManager")
    @patch("hunter.budget.BudgetManager")
    @patch("hunter.cli._read_pid_meta", return_value=(None, {}))
    def test_status_budget_alert(self, mock_read, mock_budget_cls, mock_wt_cls, capsys):
        mock_budget_cls.return_value.check_budget.return_value = _make_budget_status(alert=True)
        mock_wt_cls.return_value.is_setup.return_value = True
        mock_wt_cls.return_value.get_head_commit.return_value = "abc12345678"
        _cmd_status(_make_args())
        output = capsys.readouterr().out
        assert "ALERT" in output


# =============================================================================
# Tests — _cmd_budget
# =============================================================================

class TestCmdBudget:

    @patch("hunter.budget.BudgetManager")
    def test_budget_default_shows_status(self, mock_cls, capsys):
        mock_cls.return_value.check_budget.return_value = _make_budget_status()
        _cmd_budget(_make_args(budget_command=None))
        output = capsys.readouterr().out
        assert "daily" in output
        assert "$5.00" in output  # spend_today

    @patch("hunter.budget.BudgetManager")
    def test_budget_set_valid(self, mock_cls, capsys):
        mock = mock_cls.return_value
        mock.check_budget.return_value = _make_budget_status()
        _cmd_budget(_make_args(budget_command="set", value="20/day"))
        mock.update_config.assert_called_once_with(mode="daily", max_per_day=20.0)
        output = capsys.readouterr().out
        assert "updated" in output.lower()

    def test_budget_set_invalid(self, capsys):
        with patch("hunter.budget.BudgetManager"):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_budget(_make_args(budget_command="set", value="invalid!"))
            assert exc_info.value.code == 1

    @patch("hunter.budget.BudgetManager")
    def test_budget_history_with_data(self, mock_cls, capsys):
        mock = mock_cls.return_value
        mock.get_daily_summary.return_value = {"2026-03-11": 5.5, "2026-03-12": 3.2}
        mock.get_spend_history.return_value = [
            {"timestamp": "2026-03-12T10:00:00", "cost_usd": 0.003, "model": "qwen/qwen3.5-32b", "agent": "hunter"},
        ]
        _cmd_budget(_make_args(budget_command="history"))
        output = capsys.readouterr().out
        assert "2026-03-11" in output
        assert "2026-03-12" in output

    @patch("hunter.budget.BudgetManager")
    def test_budget_history_empty(self, mock_cls, capsys):
        mock_cls.return_value.get_daily_summary.return_value = {}
        _cmd_budget(_make_args(budget_command="history"))
        output = capsys.readouterr().out
        assert "No spend history" in output


# =============================================================================
# Tests — _cmd_logs
# =============================================================================

class TestCmdLogs:

    def test_logs_no_log_dir(self, tmp_path, capsys):
        with patch("hunter.cli.get_hunter_log_dir", return_value=tmp_path / "nonexistent"):
            _cmd_logs(_make_args(follow=False, tail=50))
        output = capsys.readouterr().out
        assert "No logs found" in output

    def test_logs_no_log_files(self, tmp_path, capsys):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        with patch("hunter.cli.get_hunter_log_dir", return_value=log_dir):
            _cmd_logs(_make_args(follow=False, tail=50))
        output = capsys.readouterr().out
        assert "No log files found" in output

    def test_logs_tail(self, tmp_path, capsys):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "hunter-abc-20260312-100000.log"
        log_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")

        with patch("hunter.cli.get_hunter_log_dir", return_value=log_dir):
            _cmd_logs(_make_args(follow=False, tail=3))
        output = capsys.readouterr().out
        assert "line 3" in output
        assert "line 4" in output
        assert "line 5" in output
        assert "line 1" not in output

    def test_logs_picks_most_recent(self, tmp_path, capsys):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        # Create two log files with different mtimes
        old_log = log_dir / "hunter-old-20260311.log"
        old_log.write_text("old content\n")
        import time as _time
        _time.sleep(0.05)  # Ensure different mtime
        new_log = log_dir / "hunter-new-20260312.log"
        new_log.write_text("new content\n")

        with patch("hunter.cli.get_hunter_log_dir", return_value=log_dir):
            _cmd_logs(_make_args(follow=False, tail=50))
        output = capsys.readouterr().out
        assert "new content" in output
        assert "hunter-new" in output  # Shows filename

    def test_tail_log_helper(self, tmp_path, capsys):
        log_file = tmp_path / "test.log"
        log_file.write_text("a\nb\nc\nd\ne\n")
        _tail_log(log_file, 2)
        output = capsys.readouterr().out
        assert "d\n" in output
        assert "e\n" in output
        assert "a" not in output
