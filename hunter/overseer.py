"""Overseer main control loop.

The Overseer is an AIAgent with a specialised toolset (hunter-overseer) whose
task is the continuous monitoring and improvement of the Hunter agent. Each
loop iteration it: reloads budget, injects Elephantasm memory, evaluates
the Hunter's status, and lets the agent decide what action to take.

Architecture:
    ┌──────────────────────────────────────────────────┐
    │                 OverseerLoop.run()                │
    │                                                   │
    │  while running:                                   │
    │    1. reload budget → hard stop check             │
    │    2. inject Elephantasm memory                   │
    │    3. build iteration prompt (status + logs)      │
    │    4. AIAgent.run_conversation()                  │
    │       └─ agent calls hunter_* tools as needed     │
    │    5. append to conversation history              │
    │    6. extract decision to Elephantasm             │
    │    7. record Overseer's own API spend             │
    │    8. sleep(check_interval)                       │
    └──────────────────────────────────────────────────┘

The agent has full discretion over which tools to call. The loop only provides
the status update and lets the LLM decide what action (if any) to take.
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hunter.budget import BudgetManager
from hunter.config import (
    OVERSEER_DEFAULT_CHECK_INTERVAL,
    OVERSEER_MAX_ITERATIONS_PER_LOOP,
    ensure_hunter_home,
)
from hunter.memory import AnimaManager, OverseerMemoryBridge

logger = logging.getLogger(__name__)


# =============================================================================
# Prompt loading
# =============================================================================

def _load_overseer_system_prompt() -> str:
    """Load the Overseer system prompt + all reference documents.

    Reads ``hunter/prompts/overseer_system.md`` as the main prompt, then
    appends all ``.md`` files from ``hunter/prompts/references/`` sorted by
    filename for deterministic ordering.

    Returns:
        The complete system prompt string.

    Raises:
        FileNotFoundError: If the main prompt file is missing.
    """
    prompts_dir = Path(__file__).parent / "prompts"
    main_path = prompts_dir / "overseer_system.md"
    main = main_path.read_text(encoding="utf-8")

    refs_dir = prompts_dir / "references"
    if refs_dir.exists():
        for ref_path in sorted(refs_dir.glob("*.md")):
            ref_content = ref_path.read_text(encoding="utf-8")
            main += f"\n\n---\n\n{ref_content}"

    return main


# =============================================================================
# OverseerLoop
# =============================================================================

class OverseerLoop:
    """Continuous monitoring and improvement loop for the Hunter agent.

    Wraps an AIAgent with the ``hunter-overseer`` toolset. Each iteration
    builds a status prompt, lets the agent decide what to do (call tools,
    inject instructions, edit code, etc.), and records the outcome.

    Usage::

        loop = OverseerLoop(model="qwen/qwen3.5-72b")
        loop.run()  # Blocks until KeyboardInterrupt or stop()
    """

    def __init__(
        self,
        model: str = "anthropic/claude-opus-4.6",
        budget: Optional["BudgetManager"] = None,
        memory: Optional["OverseerMemoryBridge"] = None,
        controller: Optional["HunterController"] = None,
        check_interval: float = OVERSEER_DEFAULT_CHECK_INTERVAL,
        history_max_messages: int = 40,
        history_keep_messages: int = 20,
    ):
        """
        Args:
            model: LLM the Overseer itself uses (not the Hunter's model).
            budget: Pre-configured BudgetManager. Created in _setup() if None.
            memory: Pre-configured OverseerMemoryBridge. Created in _setup() if None.
            controller: Pre-configured HunterController. Created in _setup() if None.
                If provided, it is injected into all tool modules so they share
                the same controller instance.
            check_interval: Seconds between loop iterations (default 30).
            history_max_messages: Trim conversation history when it exceeds this.
            history_keep_messages: How many messages to keep after trimming.
        """
        self.model = model
        self.budget = budget
        self.memory = memory
        self.controller = controller
        self.check_interval = check_interval
        self.history_max_messages = history_max_messages
        self.history_keep_messages = history_keep_messages

        # Runtime state
        self._running: bool = False
        self._agent = None  # Created in _setup()
        self._controller = None  # Created in _setup()
        self._conversation_history: List[Dict[str, Any]] = []
        self._iteration_count: int = 0
        self._bootstrap_state = None  # Set in _setup()
        self._bootstrap_prompt: Optional[str] = None  # Set in _setup()

    # ── Public API ──────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the Overseer loop. Blocks until KeyboardInterrupt or stop().

        Each iteration checks the Hunter's health, builds a status prompt,
        runs the AIAgent for one conversational turn, and extracts the
        decision to Elephantasm memory.
        """
        self._running = True
        self._setup()

        logger.info(
            "Overseer loop starting: model=%s check_interval=%.0fs",
            self.model, self.check_interval,
        )

        try:
            while self._running:
                try:
                    self._iteration()
                except KeyboardInterrupt:
                    raise  # Propagate to outer handler
                except Exception as e:
                    logger.error("Overseer iteration %d error: %s",
                                 self._iteration_count, e, exc_info=True)
                    if self.memory:
                        try:
                            self.memory.extract_decision(
                                f"Loop iteration error: {e}",
                                meta={"type": "error", "iteration": self._iteration_count},
                            )
                        except Exception:
                            pass
                    # Continue loop — resilience is critical

                if self._running:
                    time.sleep(self.check_interval)
        except KeyboardInterrupt:
            logger.info("Overseer received KeyboardInterrupt")
        finally:
            self._shutdown()

    def stop(self) -> None:
        """Signal the loop to stop after the current iteration."""
        self._running = False

    # ── Setup & teardown ────────────────────────────────────────────────

    def _setup(self) -> None:
        """One-time initialisation at loop start.

        Creates the shared HunterController and injects it into all tool
        modules so the loop and tools operate on the same Hunter process.
        """
        ensure_hunter_home()

        # Create shared infrastructure
        if self.budget is None:
            self.budget = BudgetManager()

        if self.controller is not None:
            self._controller = self.controller
        else:
            from hunter.backends import create_controller
            self._controller = create_controller(budget=self.budget)

        # Inject shared controller into all tool modules so they see the
        # same Hunter process. Without this, each module's lazy singleton
        # creates a separate controller and spawn() in one module wouldn't
        # be visible to get_status() in another.
        from hunter.tools import budget_tools, code_tools, inject_tools, process_tools
        for mod in (process_tools, inject_tools, code_tools, budget_tools):
            mod._set_controller(self._controller)

        # Ensure Elephantasm Animas exist (non-fatal)
        try:
            AnimaManager.ensure_animas()
        except Exception as e:
            logger.warning("Elephantasm Anima setup failed (non-fatal): %s", e)

        # Ensure budget config exists on disk
        self.budget.create_default_config()

        # Ensure worktree is set up
        if not self._controller.worktree.is_setup():
            self._controller.worktree.setup()

        # Detect bootstrap mode (D1)
        from hunter.bootstrap import detect_bootstrap
        self._bootstrap_state = detect_bootstrap(self._controller.worktree)
        if self._bootstrap_state.is_bootstrap:
            logger.info("Bootstrap mode ACTIVE: %s", self._bootstrap_state.reason)

        # Initialise memory bridge (non-fatal)
        if self.memory is None:
            try:
                self.memory = OverseerMemoryBridge()
            except Exception as e:
                logger.warning(
                    "OverseerMemoryBridge unavailable: %s. Memory features disabled.", e
                )
                self.memory = None

        # Load bootstrap prompt and seed architecture docs (D2b, D3)
        if self._bootstrap_state.is_bootstrap:
            from hunter.bootstrap import load_bootstrap_prompt
            self._bootstrap_prompt = load_bootstrap_prompt()

            if self.memory:
                try:
                    from hunter.bootstrap import seed_architecture_knowledge
                    seed_architecture_knowledge(self.memory)
                except Exception as e:
                    logger.warning(
                        "Architecture seeding failed (non-fatal): %s", e
                    )

        # Create the Overseer's AIAgent
        self._agent = self._create_agent()

        logger.info("Overseer setup complete")

    def _shutdown(self) -> None:
        """Clean shutdown: extract final event, close memory bridge."""
        self._running = False
        logger.info("Overseer shutting down after %d iterations", self._iteration_count)

        if self.memory:
            try:
                self.memory.extract_decision(
                    f"Overseer shutting down after {self._iteration_count} iterations",
                    meta={"type": "shutdown", "iteration": self._iteration_count},
                )
                self.memory.close()
            except Exception:
                pass

    # ── Agent creation ──────────────────────────────────────────────────

    def _create_agent(self):
        """Create the Overseer's AIAgent instance.

        Returns an AIAgent configured with the ``hunter-overseer`` toolset,
        the Overseer system prompt, and settings optimised for background
        operation (quiet mode, no context files, no MEMORY.md).
        """
        from run_agent import AIAgent

        session_id = (
            f"overseer-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        )

        return AIAgent(
            model=self.model,
            enabled_toolsets=["hunter-overseer"],
            max_iterations=OVERSEER_MAX_ITERATIONS_PER_LOOP,
            ephemeral_system_prompt=_load_overseer_system_prompt(),
            session_id=session_id,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )

    # ── Core iteration ──────────────────────────────────────────────────

    def _iteration(self) -> None:
        """Execute one iteration of the Overseer control loop.

        Steps:
            1. Reload budget config, check hard stop
            2. Inject Elephantasm memory into ephemeral prompt
            3. Build iteration prompt with Hunter status + budget + logs
            4. Run AIAgent for one conversational turn
            5. Append user/assistant pair to conversation history
            6. Trim history if over threshold
            7. Extract decision to Elephantasm
            8. Record Overseer's own estimated spend
        """
        self._iteration_count += 1
        logger.info("Overseer iteration %d starting", self._iteration_count)

        # 0. Check bootstrap transition (D5)
        if self._bootstrap_state and self._bootstrap_state.is_bootstrap:
            from hunter.bootstrap import check_transition
            transition = check_transition(self._controller.worktree)
            if transition.ready:
                logger.info("Bootstrap complete — transitioning to normal mode")
                self._bootstrap_state.is_bootstrap = False
                self._bootstrap_prompt = None
                if self.memory:
                    self.memory.extract_decision(
                        "Bootstrap mode complete. "
                        "Transitioning to normal improvement mode.",
                        meta={
                            "type": "bootstrap_transition",
                            "skills": transition.skills_count,
                            "python_files": transition.python_count,
                            "commits": transition.commits_count,
                        },
                    )

        # 1. Reload budget, check hard stop
        self.budget.reload()
        budget_status = self.budget.check_budget()

        if budget_status.hard_stop:
            logger.warning(
                "Budget exhausted (%.0f%% used). Killing Hunter.",
                budget_status.percent_used,
            )
            self._controller.kill()
            if self.memory:
                self.memory.extract_decision(
                    "Budget exhausted — killed Hunter",
                    meta={
                        "type": "budget_hard_stop",
                        "budget_percent": budget_status.percent_used,
                    },
                )
            return  # Skip the agent turn — nothing to spend

        # 2. Inject Elephantasm memory into ephemeral prompt
        memory_context = None
        if self.memory:
            memory_context = self.memory.inject(
                query="recent interventions, hunter performance, current strategy"
            )

        base_prompt = _load_overseer_system_prompt()
        if memory_context:
            self._agent.ephemeral_system_prompt = (
                base_prompt + "\n\n## Your Memory Context\n" + memory_context
            )
        else:
            self._agent.ephemeral_system_prompt = base_prompt

        # 2b. Append bootstrap prompt if active (D2c)
        if self._bootstrap_prompt:
            self._agent.ephemeral_system_prompt += (
                "\n\n---\n\n## Bootstrap Mode \u2014 ACTIVE\n\n"
                + self._bootstrap_prompt
            )

        # 3. Build iteration prompt (the "user message")
        prompt = self._build_iteration_prompt(budget_status)

        # 4. Run the agent for one conversational turn
        result = self._agent.run_conversation(
            user_message=prompt,
            conversation_history=self._conversation_history,
        )

        # 5. Update conversation history with clean user/assistant pair.
        # We do NOT use result["messages"] because it includes internal
        # tool_call and tool response messages that would rapidly inflate
        # the context window. The agent's tool calling within a turn is
        # handled internally; we only track the high-level conversation.
        final_response = result.get("final_response") or ""
        self._conversation_history.append({"role": "user", "content": prompt})
        self._conversation_history.append({"role": "assistant", "content": final_response})

        # 6. Trim history if over threshold
        if len(self._conversation_history) > self.history_max_messages:
            self._conversation_history = (
                self._conversation_history[-self.history_keep_messages:]
            )

        # 7. Extract decision to Elephantasm
        if self.memory:
            self.memory.extract_decision(
                f"Iteration {self._iteration_count}: {final_response[:500]}",
                meta={
                    "iteration": self._iteration_count,
                    "budget_remaining": budget_status.remaining_usd,
                    "budget_percent": budget_status.percent_used,
                },
            )

        # 8. Record Overseer's own spend (estimated)
        api_calls = result.get("api_calls", 0)
        if api_calls > 0:
            estimated_cost = self.budget.estimate_cost(
                self.model,
                input_tokens=4000 * api_calls,
                output_tokens=1000 * api_calls,
            )
            if estimated_cost > 0:
                self.budget.record_spend(
                    estimated_cost,
                    model=self.model,
                    input_tokens=4000 * api_calls,
                    output_tokens=1000 * api_calls,
                    agent="overseer",
                )

        logger.info(
            "Overseer iteration %d complete: api_calls=%d response_len=%d",
            self._iteration_count, api_calls, len(final_response),
        )

    # ── Prompt building ─────────────────────────────────────────────────

    def _build_iteration_prompt(self, budget) -> str:
        """Build the user-message prompt for this loop iteration.

        Contains: budget status, Hunter status, recent logs, iteration
        count, and a task description listing the agent's options.
        """
        parts = []

        # Budget section
        parts.append("## Current Status")
        parts.append(f"**Budget:** {budget.summary()}")
        if budget.alert:
            parts.append(
                "**WARNING:** Budget alert threshold reached. "
                "Consider switching to a lighter model tier."
            )

        # Hunter status
        hunter_status = self._controller.get_status()
        parts.append(f"**Hunter:** {hunter_status.summary()}")

        # Recent logs (only if Hunter is or was running)
        if hunter_status.running or hunter_status.exit_code is not None:
            recent_logs = self._controller.get_logs(tail=30)
            if recent_logs.strip():
                parts.append(
                    f"\n## Recent Hunter Output\n```\n{recent_logs}\n```"
                )

        # Iteration metadata
        parts.append(f"\n**Overseer iteration:** {self._iteration_count}")

        # Task prompt — different for bootstrap vs normal mode
        if self._bootstrap_state and self._bootstrap_state.is_bootstrap:
            parts.append(
                "\n## Your Task \u2014 Bootstrap\n"
                "The Hunter repository is empty or minimal. "
                "Build its capabilities.\n\n"
                "1. Check what exists in the worktree (`hunter_code_read`)\n"
                "2. Decide what to build next (follow the build order above)\n"
                "3. Write it (`hunter_code_edit` with empty `old_string` "
                "creates new files)\n"
                "4. Commit when a logical unit is complete\n"
                "5. When enough exists for a test, `hunter_redeploy` "
                "and observe"
            )
        else:
            parts.append(
                "\n## Your Task\n"
                "Review the Hunter's status and recent activity. "
                "Decide whether to:\n"
                "- **Do nothing** \u2014 Hunter is performing well, let it work\n"
                "- **Inject instruction** \u2014 soft steering via "
                "`hunter_inject`\n"
                "- **Modify code + redeploy** \u2014 hard intervention via "
                "`hunter_code_edit` + `hunter_redeploy`\n"
                "- **Change model** \u2014 cost optimisation via "
                "`hunter_model_set`\n"
                "- **Spawn Hunter** \u2014 if it's not running and should be\n"
                "- **Adjust strategy** \u2014 check budget, review recent "
                "performance\n"
                "\n"
                "Use your tools to take action. If no action is needed, "
                "explain briefly why."
            )

        return "\n".join(parts)
