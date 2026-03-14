"""Tests for the Overseer main loop (Task 10).

All tests mock AIAgent to avoid actual LLM calls. Mock fixtures for
BudgetManager, OverseerMemoryBridge, and HunterController are used
throughout.
"""

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from hunter.budget import BudgetManager, BudgetStatus
from hunter.control import HunterStatus
from hunter.overseer import OverseerLoop, _load_overseer_system_prompt


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


def _make_hunter_status(
    running=False, pid=None, session_id="",
    model="qwen/qwen3.5-32b", uptime=0.0,
    exit_code=None, error="No Hunter has been spawned.",
) -> HunterStatus:
    return HunterStatus(
        running=running,
        pid=pid,
        session_id=session_id,
        model=model,
        uptime_seconds=uptime,
        exit_code=exit_code,
        last_output_line="",
        error=error,
    )


def _make_agent_result(response="No action needed.", api_calls=1):
    return {
        "final_response": response,
        "messages": [],
        "api_calls": api_calls,
        "completed": True,
    }


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_budget():
    budget = MagicMock(spec=BudgetManager)
    budget.reload.return_value = False
    budget.check_budget.return_value = _make_budget_status()
    budget.create_default_config.return_value = False
    budget.estimate_cost.return_value = 0.01
    return budget


@pytest.fixture
def mock_memory():
    memory = MagicMock()
    memory.inject.return_value = None
    memory.extract_decision = MagicMock()
    memory.close = MagicMock()
    return memory


@pytest.fixture
def mock_controller():
    controller = MagicMock()
    controller.is_running = False
    controller.get_status.return_value = _make_hunter_status()
    controller.get_logs.return_value = ""
    controller.kill.return_value = False
    return controller


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.run_conversation.return_value = _make_agent_result()
    agent.ephemeral_system_prompt = None
    return agent


@pytest.fixture
def mock_worktree():
    wt = MagicMock()
    wt.is_setup.return_value = True
    return wt


@pytest.fixture
def overseer(mock_budget, mock_memory, mock_controller, mock_agent):
    """Pre-configured OverseerLoop with all dependencies mocked."""
    loop = OverseerLoop(
        budget=mock_budget,
        memory=mock_memory,
        controller=mock_controller,
        check_interval=0.0,
    )
    # Simulate post-setup state
    loop._controller = mock_controller
    loop._agent = mock_agent
    loop._running = True
    return loop


# =============================================================================
# Tests — Setup
# =============================================================================

class TestSetup:
    """Tests for OverseerLoop._setup().

    After Phase A, the overseer uses ``create_controller()`` from
    ``hunter.backends`` instead of constructing WorktreeManager and
    HunterController directly. Tests patch the factory accordingly.
    """

    # Helper to build a mock controller whose .worktree behaves correctly.
    @staticmethod
    def _make_mock_controller(worktree_is_setup=True):
        ctrl = MagicMock()
        ctrl.worktree.is_setup.return_value = worktree_is_setup
        return ctrl

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    def test_setup_ensures_hunter_home(self, _agent, _anima, _budget, _factory, mock_ensure):
        loop = OverseerLoop()
        loop._setup()
        mock_ensure.assert_called_once()

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    def test_setup_creates_default_budget(self, _agent, _anima, mock_budget_cls, _factory, _ensure):
        mock_budget = mock_budget_cls.return_value
        loop = OverseerLoop()
        loop._setup()
        mock_budget.create_default_config.assert_called_once()

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    def test_setup_ensures_worktree(self, _agent, _anima, _budget, mock_factory, _ensure):
        mock_ctrl = self._make_mock_controller(worktree_is_setup=False)
        mock_factory.return_value = mock_ctrl
        loop = OverseerLoop()
        loop._setup()
        mock_ctrl.worktree.setup.assert_called_once()

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    def test_setup_worktree_already_setup(self, _agent, _anima, _budget, mock_factory, _ensure):
        mock_ctrl = self._make_mock_controller(worktree_is_setup=True)
        mock_factory.return_value = mock_ctrl
        loop = OverseerLoop()
        loop._setup()
        mock_ctrl.worktree.setup.assert_not_called()

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    def test_setup_ensures_animas(self, _agent, mock_anima, _budget, _factory, _ensure):
        loop = OverseerLoop()
        loop._setup()
        mock_anima.ensure_animas.assert_called_once()

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    def test_setup_anima_failure_nonfatal(self, _agent, mock_anima, _budget, _factory, _ensure):
        mock_anima.ensure_animas.side_effect = RuntimeError("API down")
        loop = OverseerLoop()
        loop._setup()  # Should not raise
        assert loop._agent is not None

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    def test_setup_creates_agent(self, mock_agent_cls, _anima, _budget, _factory, _ensure):
        loop = OverseerLoop()
        loop._setup()
        assert loop._agent is not None
        mock_agent_cls.assert_called_once()

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    def test_setup_injects_controller_into_tool_modules(
        self, _agent, _anima, _budget, mock_factory, _ensure,
    ):
        mock_ctrl = self._make_mock_controller()
        mock_factory.return_value = mock_ctrl
        loop = OverseerLoop()

        with patch("hunter.tools.process_tools._set_controller") as p_set, \
             patch("hunter.tools.inject_tools._set_controller") as i_set, \
             patch("hunter.tools.code_tools._set_controller") as c_set, \
             patch("hunter.tools.budget_tools._set_controller") as b_set:
            loop._setup()
            for setter in (p_set, i_set, c_set, b_set):
                setter.assert_called_once_with(mock_ctrl)

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    @patch("hunter.overseer.OverseerMemoryBridge")
    def test_setup_memory_bridge_failure_nonfatal(
        self, mock_bridge, _agent, _anima, _budget, _factory, _ensure,
    ):
        mock_bridge.side_effect = ValueError("No Anima ID")
        loop = OverseerLoop()
        loop._setup()  # Should not raise
        assert loop.memory is None

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    def test_setup_uses_provided_controller(self, _agent, _anima, _budget, _factory, _ensure):
        custom_controller = MagicMock()
        custom_controller.worktree.is_setup.return_value = True
        loop = OverseerLoop(controller=custom_controller)
        loop._setup()
        assert loop._controller is custom_controller
        # Factory should NOT have been called — we provided our own controller
        _factory.assert_not_called()


# =============================================================================
# Tests — Iteration
# =============================================================================

class TestIteration:

    def test_iteration_reloads_budget(self, overseer, mock_budget):
        overseer._iteration()
        mock_budget.reload.assert_called_once()

    def test_iteration_checks_budget(self, overseer, mock_budget):
        overseer._iteration()
        mock_budget.check_budget.assert_called_once()

    def test_iteration_hard_stop_kills_hunter(self, overseer, mock_budget, mock_controller):
        mock_budget.check_budget.return_value = _make_budget_status(
            hard_stop=True, percent=100.0,
        )
        overseer._iteration()
        mock_controller.kill.assert_called_once()

    def test_iteration_hard_stop_skips_agent(self, overseer, mock_budget, mock_agent):
        mock_budget.check_budget.return_value = _make_budget_status(hard_stop=True)
        overseer._iteration()
        mock_agent.run_conversation.assert_not_called()

    def test_iteration_hard_stop_extracts_memory(self, overseer, mock_budget, mock_memory):
        mock_budget.check_budget.return_value = _make_budget_status(hard_stop=True)
        overseer._iteration()
        mock_memory.extract_decision.assert_called_once()
        args = mock_memory.extract_decision.call_args
        assert "exhausted" in args[0][0].lower() or "Budget" in args[0][0]

    def test_iteration_injects_memory(self, overseer, mock_memory):
        overseer._iteration()
        mock_memory.inject.assert_called_once()

    def test_iteration_updates_ephemeral_prompt_with_memory(self, overseer, mock_memory, mock_agent):
        mock_memory.inject.return_value = "Previous intervention improved IDOR detection by 40%."
        overseer._iteration()
        prompt = mock_agent.ephemeral_system_prompt
        assert "Previous intervention improved" in prompt

    def test_iteration_no_memory_context_ok(self, overseer, mock_memory, mock_agent):
        mock_memory.inject.return_value = None
        overseer._iteration()
        prompt = mock_agent.ephemeral_system_prompt
        assert "Memory Context" not in prompt

    def test_iteration_calls_agent_run_conversation(self, overseer, mock_agent):
        overseer._iteration()
        mock_agent.run_conversation.assert_called_once()
        kwargs = mock_agent.run_conversation.call_args
        assert "user_message" in kwargs.kwargs or len(kwargs.args) > 0

    def test_iteration_appends_to_history(self, overseer):
        assert len(overseer._conversation_history) == 0
        overseer._iteration()
        assert len(overseer._conversation_history) == 2
        assert overseer._conversation_history[0]["role"] == "user"
        assert overseer._conversation_history[1]["role"] == "assistant"

    def test_iteration_extracts_decision(self, overseer, mock_memory):
        overseer._iteration()
        # extract_decision called for the iteration result (not hard stop)
        assert mock_memory.extract_decision.call_count == 1
        args = mock_memory.extract_decision.call_args[0][0]
        assert "Iteration 1" in args

    def test_iteration_records_spend(self, overseer, mock_budget):
        overseer._iteration()
        mock_budget.estimate_cost.assert_called_once()
        mock_budget.record_spend.assert_called_once()
        call_kwargs = mock_budget.record_spend.call_args
        assert call_kwargs.kwargs.get("agent") == "overseer" or "overseer" in str(call_kwargs)

    def test_iteration_increments_count(self, overseer):
        assert overseer._iteration_count == 0
        overseer._iteration()
        assert overseer._iteration_count == 1
        overseer._iteration()
        assert overseer._iteration_count == 2

    def test_iteration_without_memory_bridge(self, overseer, mock_agent):
        overseer.memory = None
        overseer._iteration()  # Should not raise
        mock_agent.run_conversation.assert_called_once()

    def test_iteration_no_spend_when_zero_api_calls(self, overseer, mock_agent, mock_budget):
        mock_agent.run_conversation.return_value = _make_agent_result(api_calls=0)
        overseer._iteration()
        mock_budget.record_spend.assert_not_called()

    def test_iteration_no_spend_when_zero_cost(self, overseer, mock_budget):
        mock_budget.estimate_cost.return_value = 0.0
        overseer._iteration()
        mock_budget.record_spend.assert_not_called()


# =============================================================================
# Tests — History management
# =============================================================================

class TestHistoryManagement:

    def test_history_grows_across_iterations(self, overseer):
        overseer._iteration()
        overseer._iteration()
        overseer._iteration()
        assert len(overseer._conversation_history) == 6

    def test_history_trimmed_at_threshold(self, overseer):
        overseer.history_max_messages = 6
        overseer.history_keep_messages = 4
        for _ in range(5):  # 10 messages > 6
            overseer._iteration()
        assert len(overseer._conversation_history) <= 6

    def test_history_keeps_recent(self, overseer):
        overseer.history_max_messages = 4
        overseer.history_keep_messages = 2
        for _ in range(5):
            overseer._iteration()
        # After trimming, the last 2 messages should be from the most recent iteration
        assert overseer._conversation_history[-1]["role"] == "assistant"
        assert overseer._conversation_history[-2]["role"] == "user"

    def test_history_passed_to_agent(self, overseer, mock_agent):
        # Capture history length at each call (list is mutable, so we
        # can't inspect it after the fact — it will have grown).
        captured_lens = []
        orig_return = _make_agent_result()

        def capture_history(**kwargs):
            captured_lens.append(len(kwargs.get("conversation_history", [])))
            return orig_return

        mock_agent.run_conversation.side_effect = capture_history
        overseer._iteration()  # Passes empty history (0 messages)
        overseer._iteration()  # Should pass 2 messages from 1st iteration
        assert captured_lens == [0, 2]


# =============================================================================
# Tests — Iteration prompt building
# =============================================================================

class TestIterationPrompt:

    def test_prompt_includes_budget_summary(self, overseer):
        status = _make_budget_status(spend_today=5.0, remaining=10.0)
        prompt = overseer._build_iteration_prompt(status)
        assert "$" in prompt

    def test_prompt_includes_budget_alert_warning(self, overseer):
        status = _make_budget_status(alert=True)
        prompt = overseer._build_iteration_prompt(status)
        assert "WARNING" in prompt

    def test_prompt_no_alert_when_ok(self, overseer):
        status = _make_budget_status(alert=False)
        prompt = overseer._build_iteration_prompt(status)
        assert "WARNING" not in prompt

    def test_prompt_includes_hunter_not_running(self, overseer, mock_controller):
        mock_controller.get_status.return_value = _make_hunter_status(running=False)
        status = _make_budget_status()
        prompt = overseer._build_iteration_prompt(status)
        assert "not" in prompt.lower()

    def test_prompt_includes_hunter_running(self, overseer, mock_controller):
        mock_controller.get_status.return_value = _make_hunter_status(
            running=True, pid=12345, uptime=120.0, session_id="hunter-abc",
        )
        status = _make_budget_status()
        prompt = overseer._build_iteration_prompt(status)
        assert "12345" in prompt or "running" in prompt.lower()

    def test_prompt_includes_logs_when_running(self, overseer, mock_controller):
        mock_controller.get_status.return_value = _make_hunter_status(running=True, pid=1)
        mock_controller.get_logs.return_value = "Scanning /api/users endpoint..."
        status = _make_budget_status()
        prompt = overseer._build_iteration_prompt(status)
        assert "Scanning /api/users" in prompt

    def test_prompt_includes_task_section(self, overseer):
        status = _make_budget_status()
        prompt = overseer._build_iteration_prompt(status)
        assert "Your Task" in prompt
        assert "hunter_inject" in prompt

    def test_prompt_includes_iteration_number(self, overseer):
        overseer._iteration_count = 42
        status = _make_budget_status()
        prompt = overseer._build_iteration_prompt(status)
        assert "42" in prompt


# =============================================================================
# Tests — Run and shutdown
# =============================================================================

class TestRunAndShutdown:

    def test_stop_sets_running_false(self, overseer):
        overseer._running = True
        overseer.stop()
        assert overseer._running is False

    def test_shutdown_extracts_final_event(self, overseer, mock_memory):
        overseer._iteration_count = 5
        overseer._shutdown()
        mock_memory.extract_decision.assert_called_once()
        assert "5 iterations" in mock_memory.extract_decision.call_args[0][0]

    def test_shutdown_closes_memory(self, overseer, mock_memory):
        overseer._shutdown()
        mock_memory.close.assert_called_once()

    def test_shutdown_without_memory(self, overseer):
        overseer.memory = None
        overseer._shutdown()  # Should not raise

    def test_shutdown_sets_running_false(self, overseer):
        overseer._running = True
        overseer._shutdown()
        assert overseer._running is False

    def test_iteration_error_does_not_crash(self, overseer, mock_agent, mock_budget):
        """If _iteration() raises, the loop should continue."""
        # First call raises, second succeeds
        mock_budget.check_budget.side_effect = [
            RuntimeError("Transient error"),
            _make_budget_status(),
        ]
        # Run two iterations manually
        error_caught = False
        try:
            overseer._iteration()
        except RuntimeError:
            error_caught = True
        # The loop's try/except in run() catches this, but we test _iteration directly
        assert error_caught
        # Second iteration should succeed
        overseer._iteration()
        assert overseer._iteration_count == 2

    def test_iteration_error_extracted_to_memory(self, overseer, mock_memory, mock_agent):
        """When run() catches an iteration error, it extracts to memory."""
        mock_agent.run_conversation.side_effect = RuntimeError("LLM timeout")

        # Track iterations so we stop after 1 — use try/finally to ensure
        # stop() is called even when the iteration raises an exception.
        original_iteration = overseer._iteration

        def one_iteration_then_stop():
            try:
                original_iteration()
            finally:
                overseer.stop()

        overseer._iteration = one_iteration_then_stop

        # Mock _setup() since run() calls it and it would create real infrastructure.
        # The overseer fixture already has _controller, _agent, etc. pre-populated.
        with patch.object(overseer, "_setup"), \
             patch("hunter.overseer.time.sleep"):
            overseer.run()

        # Memory should have been called for the error
        # (extract_decision is called in the except block of run())
        error_calls = [
            c for c in mock_memory.extract_decision.call_args_list
            if "error" in str(c).lower() or "shutting" in str(c).lower()
        ]
        assert len(error_calls) > 0


# =============================================================================
# Tests — Agent creation
# =============================================================================

class TestCreateAgent:

    @patch("run_agent.AIAgent")
    def test_create_agent_uses_correct_toolsets(self, mock_agent_cls):
        loop = OverseerLoop()
        loop.budget = MagicMock()
        loop._controller = MagicMock()
        loop._create_agent()
        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["enabled_toolsets"] == ["hunter-overseer"]

    @patch("run_agent.AIAgent")
    def test_create_agent_quiet_mode(self, mock_agent_cls):
        loop = OverseerLoop()
        loop.budget = MagicMock()
        loop._controller = MagicMock()
        loop._create_agent()
        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["quiet_mode"] is True

    @patch("run_agent.AIAgent")
    def test_create_agent_skips_context_files(self, mock_agent_cls):
        loop = OverseerLoop()
        loop.budget = MagicMock()
        loop._controller = MagicMock()
        loop._create_agent()
        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["skip_context_files"] is True

    @patch("run_agent.AIAgent")
    def test_create_agent_skips_memory(self, mock_agent_cls):
        loop = OverseerLoop()
        loop.budget = MagicMock()
        loop._controller = MagicMock()
        loop._create_agent()
        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["skip_memory"] is True

    @patch("run_agent.AIAgent")
    def test_create_agent_session_id_format(self, mock_agent_cls):
        loop = OverseerLoop()
        loop.budget = MagicMock()
        loop._controller = MagicMock()
        loop._create_agent()
        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["session_id"].startswith("overseer-")

    @patch("run_agent.AIAgent")
    def test_create_agent_uses_loop_model(self, mock_agent_cls):
        loop = OverseerLoop(model="qwen/qwen3.5-72b")
        loop.budget = MagicMock()
        loop._controller = MagicMock()
        loop._create_agent()
        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["model"] == "qwen/qwen3.5-72b"


# =============================================================================
# Tests — First run
# =============================================================================

class TestFirstRun:

    def test_first_run_hunter_not_running(self, overseer, mock_controller):
        mock_controller.get_status.return_value = _make_hunter_status(running=False)
        status = _make_budget_status()
        prompt = overseer._build_iteration_prompt(status)
        assert "not" in prompt.lower()

    def test_first_run_suggests_spawn(self, overseer):
        status = _make_budget_status()
        prompt = overseer._build_iteration_prompt(status)
        assert "Spawn" in prompt


# =============================================================================
# Tests — Bootstrap mode (Phase D)
# =============================================================================

class TestBootstrap:
    """Tests for bootstrap detection, prompt injection, and transition."""

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    def test_setup_detects_bootstrap_empty_repo(
        self, _agent, _anima, _budget, mock_factory, _ensure,
    ):
        mock_ctrl = MagicMock()
        mock_ctrl.worktree.is_setup.return_value = True
        mock_ctrl.worktree.list_files.return_value = []
        mock_factory.return_value = mock_ctrl

        loop = OverseerLoop()
        loop._setup()

        assert loop._bootstrap_state is not None
        assert loop._bootstrap_state.is_bootstrap is True
        assert loop._bootstrap_prompt is not None

    @patch("hunter.overseer.ensure_hunter_home")
    @patch("hunter.backends.create_controller")
    @patch("hunter.overseer.BudgetManager")
    @patch("hunter.overseer.AnimaManager")
    @patch("run_agent.AIAgent")
    def test_setup_no_bootstrap_functional_repo(
        self, _agent, _anima, _budget, mock_factory, _ensure,
    ):
        mock_ctrl = MagicMock()
        mock_ctrl.worktree.is_setup.return_value = True

        def list_files(relative_dir=".", pattern="*"):
            if pattern == "*.py":
                return ["runner.py", "tools/scan.py", "tools/clone.py"]
            if relative_dir == "skills" and pattern == "*.md":
                return ["skills/owasp.md", "skills/idor.md"]
            return []
        mock_ctrl.worktree.list_files.side_effect = list_files
        mock_factory.return_value = mock_ctrl

        loop = OverseerLoop()
        loop._setup()

        assert loop._bootstrap_state is not None
        assert loop._bootstrap_state.is_bootstrap is False
        assert loop._bootstrap_prompt is None

    def test_iteration_injects_bootstrap_prompt(
        self, overseer, mock_controller, mock_agent,
    ):
        from hunter.bootstrap import BootstrapState
        overseer._bootstrap_state = BootstrapState(
            is_bootstrap=True, reason="empty", python_count=0, skills_count=0,
        )
        overseer._bootstrap_prompt = "BUILD THE HUNTER"

        # Mock list_files for transition check (not ready yet)
        mock_controller.worktree.list_files.return_value = []
        mock_controller.worktree.get_recent_commits.return_value = []

        overseer._iteration()

        # The bootstrap prompt should have been appended to system prompt
        prompt_set = mock_agent.ephemeral_system_prompt
        assert "Bootstrap Mode" in prompt_set
        assert "BUILD THE HUNTER" in prompt_set

    def test_iteration_uses_bootstrap_task(self, overseer, mock_controller):
        from hunter.bootstrap import BootstrapState
        overseer._bootstrap_state = BootstrapState(
            is_bootstrap=True, reason="empty", python_count=0, skills_count=0,
        )

        status = _make_budget_status()
        prompt = overseer._build_iteration_prompt(status)
        assert "Bootstrap" in prompt
        assert "hunter_code_edit" in prompt
        # Should NOT contain normal mode task
        assert "Do nothing" not in prompt

    def test_normal_mode_task(self, overseer):
        from hunter.bootstrap import BootstrapState
        overseer._bootstrap_state = BootstrapState(
            is_bootstrap=False, reason="ok", python_count=5, skills_count=5,
        )

        status = _make_budget_status()
        prompt = overseer._build_iteration_prompt(status)
        assert "Do nothing" in prompt
        assert "Bootstrap" not in prompt

    def test_transition_clears_bootstrap(
        self, overseer, mock_controller, mock_agent, mock_memory,
    ):
        from hunter.bootstrap import (
            BootstrapState,
            TRANSITION_MIN_COMMITS,
            TRANSITION_MIN_PYTHON_FILES,
            TRANSITION_MIN_SKILLS,
        )
        overseer._bootstrap_state = BootstrapState(
            is_bootstrap=True, reason="empty", python_count=0, skills_count=0,
        )
        overseer._bootstrap_prompt = "BUILD THE HUNTER"

        # Mock worktree to meet transition criteria
        def list_files(relative_dir=".", pattern="*"):
            if pattern == "*.py":
                return [f"f{i}.py" for i in range(TRANSITION_MIN_PYTHON_FILES)]
            if relative_dir == "skills" and pattern == "*.md":
                return [f"s{i}.md" for i in range(TRANSITION_MIN_SKILLS)]
            return []
        mock_controller.worktree.list_files.side_effect = list_files
        mock_controller.worktree.get_recent_commits.return_value = [
            MagicMock() for _ in range(TRANSITION_MIN_COMMITS)
        ]

        overseer._iteration()

        assert overseer._bootstrap_state.is_bootstrap is False
        assert overseer._bootstrap_prompt is None

        # Should have recorded the transition in memory
        mock_memory.extract_decision.assert_any_call(
            "Bootstrap mode complete. "
            "Transitioning to normal improvement mode.",
            meta=pytest.approx({
                "type": "bootstrap_transition",
                "skills": TRANSITION_MIN_SKILLS,
                "python_files": TRANSITION_MIN_PYTHON_FILES,
                "commits": TRANSITION_MIN_COMMITS,
            }),
        )
