"""Tests for Overseer process management tools (hunter/tools/process_tools.py).

Tests the three tools registered in the hunter-overseer toolset:
    - hunter_spawn: deploy a new Hunter agent process
    - hunter_kill: terminate the running Hunter process
    - hunter_status: get Hunter health status

All tests use a mock HunterController — no real subprocesses or git repos.
"""

import json
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from hunter.control import HunterStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the controller singleton before each test."""
    import hunter.tools.process_tools as mod
    original = mod._controller
    mod._controller = None
    yield
    mod._controller = original


@pytest.fixture
def mock_controller():
    """Provide a mock HunterController and inject it as the singleton."""
    import hunter.tools.process_tools as mod

    controller = MagicMock()
    controller.is_running = False
    mod._controller = controller
    return controller


@pytest.fixture
def mock_process():
    """A mock HunterProcess returned by controller.spawn()."""
    proc = MagicMock()
    proc.session_id = "hunter-test1234"
    proc.model = "qwen/qwen3.5-32b"
    proc._pid = 42
    return proc


# ---------------------------------------------------------------------------
# _get_controller tests
# ---------------------------------------------------------------------------

class TestGetController:
    """Lazy singleton initialisation."""

    def test_creates_controller_on_first_call(self):
        """_get_controller lazily creates a HunterController with default managers."""
        import hunter.tools.process_tools as mod

        mock_wt = MagicMock()
        mock_bm = MagicMock()
        mock_hc = MagicMock()

        with patch("hunter.worktree.WorktreeManager", return_value=mock_wt) as PWT, \
             patch("hunter.budget.BudgetManager", return_value=mock_bm) as PBM, \
             patch("hunter.control.HunterController", return_value=mock_hc) as PHC:
            result = mod._get_controller()
            assert result is mock_hc
            PWT.assert_called_once()
            PBM.assert_called_once()
            PHC.assert_called_once_with(worktree=mock_wt, budget=mock_bm)

    def test_returns_same_instance_on_second_call(self, mock_controller):
        """Subsequent calls return the cached singleton."""
        import hunter.tools.process_tools as mod

        result1 = mod._get_controller()
        result2 = mod._get_controller()
        assert result1 is result2
        assert result1 is mock_controller

    def test_set_controller_overrides_singleton(self):
        """_set_controller allows tests to inject a mock."""
        import hunter.tools.process_tools as mod

        fake = MagicMock()
        mod._set_controller(fake)
        assert mod._get_controller() is fake


# ---------------------------------------------------------------------------
# hunter_spawn tests
# ---------------------------------------------------------------------------

class TestHunterSpawn:
    """hunter_spawn tool handler."""

    def test_spawn_default(self, mock_controller, mock_process):
        """Spawn with no arguments uses defaults."""
        from hunter.tools.process_tools import _handle_hunter_spawn

        mock_controller.spawn.return_value = mock_process
        result = json.loads(_handle_hunter_spawn({}))

        assert result["status"] == "spawned"
        assert result["session_id"] == "hunter-test1234"
        assert result["model"] == "qwen/qwen3.5-32b"
        assert result["pid"] == 42

        mock_controller.spawn.assert_called_once_with(
            model=None,
            initial_instruction=None,
            resume_session=False,
        )

    def test_spawn_with_model(self, mock_controller, mock_process):
        """Spawn with explicit model."""
        from hunter.tools.process_tools import _handle_hunter_spawn

        mock_process.model = "qwen/qwen3.5-72b"
        mock_controller.spawn.return_value = mock_process

        result = json.loads(_handle_hunter_spawn({"model": "qwen/qwen3.5-72b"}))

        assert result["model"] == "qwen/qwen3.5-72b"
        mock_controller.spawn.assert_called_once_with(
            model="qwen/qwen3.5-72b",
            initial_instruction=None,
            resume_session=False,
        )

    def test_spawn_with_instruction(self, mock_controller, mock_process):
        """Spawn with an initial instruction."""
        from hunter.tools.process_tools import _handle_hunter_spawn

        mock_controller.spawn.return_value = mock_process
        result = json.loads(_handle_hunter_spawn({
            "instruction": "Hunt for IDOR vulnerabilities."
        }))

        assert result["status"] == "spawned"
        mock_controller.spawn.assert_called_once_with(
            model=None,
            initial_instruction="Hunt for IDOR vulnerabilities.",
            resume_session=False,
        )

    def test_spawn_with_resume(self, mock_controller, mock_process):
        """Spawn with resume=True continues the last session."""
        from hunter.tools.process_tools import _handle_hunter_spawn

        mock_controller.spawn.return_value = mock_process
        result = json.loads(_handle_hunter_spawn({"resume": True}))

        assert result["status"] == "spawned"
        mock_controller.spawn.assert_called_once_with(
            model=None,
            initial_instruction=None,
            resume_session=True,
        )

    def test_spawn_all_args(self, mock_controller, mock_process):
        """Spawn with all arguments provided."""
        from hunter.tools.process_tools import _handle_hunter_spawn

        mock_process.model = "qwen/qwen3.5-7b"
        mock_controller.spawn.return_value = mock_process

        result = json.loads(_handle_hunter_spawn({
            "model": "qwen/qwen3.5-7b",
            "instruction": "Focus on auth bypass.",
            "resume": True,
        }))

        assert result["status"] == "spawned"
        assert result["model"] == "qwen/qwen3.5-7b"
        mock_controller.spawn.assert_called_once_with(
            model="qwen/qwen3.5-7b",
            initial_instruction="Focus on auth bypass.",
            resume_session=True,
        )

    def test_spawn_budget_exhausted(self, mock_controller):
        """Spawn returns error when budget is exhausted."""
        from hunter.tools.process_tools import _handle_hunter_spawn

        mock_controller.spawn.side_effect = RuntimeError(
            "Budget exhausted (100% used). Cannot spawn Hunter."
        )

        result = json.loads(_handle_hunter_spawn({}))
        assert "error" in result
        assert "Budget exhausted" in result["error"]

    def test_spawn_other_error(self, mock_controller):
        """Spawn returns error for other RuntimeErrors."""
        from hunter.tools.process_tools import _handle_hunter_spawn

        mock_controller.spawn.side_effect = RuntimeError("Worktree not found")

        result = json.loads(_handle_hunter_spawn({}))
        assert "error" in result
        assert "Worktree not found" in result["error"]


# ---------------------------------------------------------------------------
# hunter_kill tests
# ---------------------------------------------------------------------------

class TestHunterKill:
    """hunter_kill tool handler."""

    def test_kill_running_hunter(self, mock_controller):
        """Kill returns 'killed' when a Hunter was running."""
        from hunter.tools.process_tools import _handle_hunter_kill

        mock_controller.kill.return_value = True
        result = json.loads(_handle_hunter_kill({}))

        assert result["status"] == "killed"
        mock_controller.kill.assert_called_once()

    def test_kill_no_hunter(self, mock_controller):
        """Kill returns 'no_hunter_running' when nothing to kill."""
        from hunter.tools.process_tools import _handle_hunter_kill

        mock_controller.kill.return_value = False
        result = json.loads(_handle_hunter_kill({}))

        assert result["status"] == "no_hunter_running"


# ---------------------------------------------------------------------------
# hunter_status tests
# ---------------------------------------------------------------------------

class TestHunterStatus:
    """hunter_status tool handler."""

    def test_status_running(self, mock_controller):
        """Status returns full details for a running Hunter."""
        from hunter.tools.process_tools import _handle_hunter_status

        status = HunterStatus(
            running=True,
            pid=99,
            session_id="hunter-abc12345",
            model="qwen/qwen3.5-32b",
            uptime_seconds=120.5,
            exit_code=None,
            last_output_line="Analysing target...",
            error=None,
        )
        mock_controller.get_status.return_value = status

        result = json.loads(_handle_hunter_status({}))

        assert result["running"] is True
        assert result["pid"] == 99
        assert result["session_id"] == "hunter-abc12345"
        assert result["model"] == "qwen/qwen3.5-32b"
        assert result["uptime_seconds"] == 120.5
        assert result["exit_code"] is None
        assert result["last_output_line"] == "Analysing target..."
        assert result["error"] is None
        assert "summary" in result
        assert "running" in result["summary"].lower()

    def test_status_stopped(self, mock_controller):
        """Status returns exit info for a stopped Hunter."""
        from hunter.tools.process_tools import _handle_hunter_status

        status = HunterStatus(
            running=False,
            pid=99,
            session_id="hunter-abc12345",
            model="qwen/qwen3.5-32b",
            uptime_seconds=0.0,
            exit_code=0,
            last_output_line="Done.",
            error=None,
        )
        mock_controller.get_status.return_value = status

        result = json.loads(_handle_hunter_status({}))

        assert result["running"] is False
        assert result["exit_code"] == 0
        assert "summary" in result

    def test_status_not_started(self, mock_controller):
        """Status returns placeholder when no Hunter has been spawned."""
        from hunter.tools.process_tools import _handle_hunter_status

        status = HunterStatus(
            running=False,
            pid=None,
            session_id="",
            model="qwen/qwen3.5-32b",
            uptime_seconds=0.0,
            exit_code=None,
            last_output_line="",
            error="No Hunter has been spawned.",
        )
        mock_controller.get_status.return_value = status

        result = json.loads(_handle_hunter_status({}))

        assert result["running"] is False
        assert result["pid"] is None
        assert result["error"] == "No Hunter has been spawned."
        assert "summary" in result

    def test_status_with_error(self, mock_controller):
        """Status includes error info for a crashed Hunter."""
        from hunter.tools.process_tools import _handle_hunter_status

        status = HunterStatus(
            running=False,
            pid=88,
            session_id="hunter-crash001",
            model="qwen/qwen3.5-72b",
            uptime_seconds=5.2,
            exit_code=1,
            last_output_line="Traceback ...",
            error="Process exited with code 1",
        )
        mock_controller.get_status.return_value = status

        result = json.loads(_handle_hunter_status({}))

        assert result["running"] is False
        assert result["exit_code"] == 1
        assert result["error"] == "Process exited with code 1"


# ---------------------------------------------------------------------------
# Tool registration tests
# ---------------------------------------------------------------------------

class TestToolRegistration:
    """Verify tools are properly registered with the Hermes registry."""

    def test_tools_are_registered(self):
        """All three tools should be in the registry after import."""
        from tools.registry import registry

        # Importing the module triggers registration
        import hunter.tools.process_tools  # noqa: F401

        names = registry.get_all_tool_names()
        assert "hunter_spawn" in names
        assert "hunter_kill" in names
        assert "hunter_status" in names

    def test_tools_in_correct_toolset(self):
        """All tools belong to the hunter-overseer toolset."""
        from tools.registry import registry

        import hunter.tools.process_tools  # noqa: F401

        assert registry.get_toolset_for_tool("hunter_spawn") == "hunter-overseer"
        assert registry.get_toolset_for_tool("hunter_kill") == "hunter-overseer"
        assert registry.get_toolset_for_tool("hunter_status") == "hunter-overseer"

    def test_spawn_schema_has_optional_params(self):
        """hunter_spawn has model, instruction, resume as optional params."""
        from tools.registry import registry

        import hunter.tools.process_tools  # noqa: F401

        entry = registry._tools["hunter_spawn"]
        props = entry.schema["parameters"]["properties"]

        assert "model" in props
        assert "instruction" in props
        assert "resume" in props
        assert entry.schema["parameters"]["required"] == []

    def test_kill_schema_has_no_params(self):
        """hunter_kill has no parameters."""
        from tools.registry import registry

        import hunter.tools.process_tools  # noqa: F401

        entry = registry._tools["hunter_kill"]
        assert entry.schema["parameters"]["properties"] == {}

    def test_status_schema_has_no_params(self):
        """hunter_status has no parameters."""
        from tools.registry import registry

        import hunter.tools.process_tools  # noqa: F401

        entry = registry._tools["hunter_status"]
        assert entry.schema["parameters"]["properties"] == {}

    def test_schemas_are_valid_openai_format(self):
        """Schemas have the required top-level 'name' field for OpenAI tool format."""
        from tools.registry import registry

        import hunter.tools.process_tools  # noqa: F401

        for tool_name in ["hunter_spawn", "hunter_kill", "hunter_status"]:
            entry = registry._tools[tool_name]
            assert "name" in entry.schema
            assert "description" in entry.schema
            assert "parameters" in entry.schema
            assert entry.schema["parameters"]["type"] == "object"


# ---------------------------------------------------------------------------
# Toolset registration tests
# ---------------------------------------------------------------------------

class TestToolsetRegistration:
    """Verify the hunter-overseer toolset is defined in toolsets.py."""

    def test_toolset_exists(self):
        """hunter-overseer is a valid toolset."""
        from toolsets import get_toolset

        ts = get_toolset("hunter-overseer")
        assert ts is not None
        assert "tools" in ts

    def test_toolset_contains_process_tools(self):
        """hunter-overseer includes the three process management tools."""
        from toolsets import resolve_toolset

        tools = resolve_toolset("hunter-overseer")
        assert "hunter_spawn" in tools
        assert "hunter_kill" in tools
        assert "hunter_status" in tools

    def test_toolset_contains_future_tools(self):
        """hunter-overseer lists all planned tools (stubs registered later)."""
        from toolsets import get_toolset

        ts = get_toolset("hunter-overseer")
        expected = [
            "hunter_spawn", "hunter_kill", "hunter_status",
            "hunter_logs", "hunter_inject", "hunter_interrupt",
            "hunter_code_edit", "hunter_code_read", "hunter_diff",
            "hunter_rollback", "hunter_redeploy",
            "hunter_model_set", "budget_status",
        ]
        for name in expected:
            assert name in ts["tools"], f"{name} not in hunter-overseer toolset"


# ---------------------------------------------------------------------------
# Integration via registry.dispatch tests
# ---------------------------------------------------------------------------

class TestDispatchIntegration:
    """Verify tools work through the registry dispatch path."""

    def test_dispatch_hunter_spawn(self, mock_controller, mock_process):
        """registry.dispatch('hunter_spawn', ...) calls the handler."""
        from tools.registry import registry

        import hunter.tools.process_tools  # noqa: F401

        mock_controller.spawn.return_value = mock_process

        raw = registry.dispatch("hunter_spawn", {"model": "qwen/qwen3.5-7b"})
        result = json.loads(raw)

        assert result["status"] == "spawned"

    def test_dispatch_hunter_kill(self, mock_controller):
        """registry.dispatch('hunter_kill', ...) calls the handler."""
        from tools.registry import registry

        import hunter.tools.process_tools  # noqa: F401

        mock_controller.kill.return_value = True

        raw = registry.dispatch("hunter_kill", {})
        result = json.loads(raw)

        assert result["status"] == "killed"

    def test_dispatch_hunter_status(self, mock_controller):
        """registry.dispatch('hunter_status', ...) calls the handler."""
        from tools.registry import registry

        import hunter.tools.process_tools  # noqa: F401

        mock_controller.get_status.return_value = HunterStatus(
            running=False, pid=None, session_id="", model="test",
            uptime_seconds=0.0, exit_code=None, last_output_line="", error=None,
        )

        raw = registry.dispatch("hunter_status", {})
        result = json.loads(raw)

        assert "running" in result
        assert "summary" in result

    def test_dispatch_catches_unexpected_exception(self, mock_controller):
        """Unexpected exceptions in handlers are caught by registry.dispatch."""
        from tools.registry import registry

        import hunter.tools.process_tools  # noqa: F401

        mock_controller.spawn.side_effect = ValueError("unexpected")

        raw = registry.dispatch("hunter_spawn", {})
        result = json.loads(raw)

        assert "error" in result
        assert "ValueError" in result["error"]
