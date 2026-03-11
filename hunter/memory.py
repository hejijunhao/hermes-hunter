"""Elephantasm integration — long-term agentic memory for both agents.

Provides OverseerMemoryBridge and HunterMemoryBridge as clean wrappers
around the Elephantasm SDK. Both agents use extract() to capture events
and inject() to retrieve relevant memory context.

All Elephantasm calls are non-fatal — if the API is down, agents continue
without memory context.

Classes:
    AnimaManager         — one-time Anima creation + local ID cache
    OverseerMemoryBridge — extract decisions/observations, inject strategy memory
    HunterMemoryBridge   — extract steps/findings/results, inject task memory, dedup
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hunter.config import (
    HUNTER_ANIMA_NAME,
    OVERSEER_ANIMA_NAME,
    get_anima_cache_path,
)

try:
    from elephantasm import (
        Elephantasm,
        EventType,
        RateLimitError,
    )

    _HAS_ELEPHANTASM = True
except ImportError:
    _HAS_ELEPHANTASM = False
    Elephantasm = None  # type: ignore[assignment,misc]
    EventType = None  # type: ignore[assignment,misc]
    RateLimitError = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


# =============================================================================
# Severity → importance mapping
# =============================================================================

_SEVERITY_IMPORTANCE = {
    "critical": 1.0,
    "high": 0.9,
    "medium": 0.7,
    "low": 0.5,
    "info": 0.3,
}


def _severity_to_importance(severity: str) -> float:
    """Map a vulnerability severity string to an Elephantasm importance score."""
    return _SEVERITY_IMPORTANCE.get(severity.lower(), 0.5)


# =============================================================================
# Safe wrappers — all Elephantasm calls go through these
# =============================================================================

def _safe_extract(client, *args, **kwargs):
    """Call client.extract() with error handling.  Never raises."""
    try:
        return client.extract(*args, **kwargs)
    except Exception as exc:
        _handle_error(exc, "extract")
    return None


def _safe_inject(client, **kwargs):
    """Call client.inject() with error handling.  Returns MemoryPack or None."""
    try:
        return client.inject(**kwargs)
    except Exception as exc:
        _handle_error(exc, "inject")
    return None


def _handle_error(exc: Exception, operation: str):
    """Log an Elephantasm error, retry once on rate-limit."""
    if RateLimitError is not None and isinstance(exc, RateLimitError):
        logger.warning("Elephantasm %s rate-limited, retrying in 5s", operation)
        time.sleep(5)
        return  # caller should retry — but our safe wrappers are fire-and-forget
    logger.warning("Elephantasm %s failed: %s", operation, exc)


# =============================================================================
# AnimaManager — one-time setup, local ID cache
# =============================================================================

class AnimaManager:
    """Manages Elephantasm Anima creation and local ID caching.

    Anima IDs are cached in ~/.hermes/hunter/animas.json so we don't need
    to call create_anima on every startup.  If the cache is missing or stale,
    we create/re-create the Animas and update the cache.
    """

    _ANIMA_DEFS = [
        (OVERSEER_ANIMA_NAME, "Meta-agent that monitors and improves the Hunter"),
        (HUNTER_ANIMA_NAME, "Bug bounty Hunter agent that finds vulnerabilities"),
    ]

    @staticmethod
    def ensure_animas(cache_path: Optional[Path] = None) -> dict[str, str]:
        """Create both Animas if they don't exist.  Returns ``{name: id}`` map.

        Idempotent — if the Animas already exist on the server (409/conflict),
        we catch the error and fall back to the cached ID.  If there is no
        cache either, we log a warning and return what we can.
        """
        cache_path = cache_path or get_anima_cache_path()
        cached = AnimaManager._load_cache(cache_path)

        # If cache already has both, return early
        names_needed = {name for name, _ in AnimaManager._ANIMA_DEFS}
        if names_needed <= set(cached.keys()):
            logger.debug("Anima cache hit — both Animas present")
            return cached

        if not _HAS_ELEPHANTASM:
            logger.warning("elephantasm not installed — memory features disabled")
            return cached

        client = Elephantasm()
        try:
            for name, description in AnimaManager._ANIMA_DEFS:
                if name in cached:
                    continue
                try:
                    anima = client.create_anima(name, description=description)
                    cached[name] = str(anima.id)
                    logger.info("Created Elephantasm Anima: %s → %s", name, anima.id)
                except Exception as exc:
                    logger.warning(
                        "Could not create Anima '%s': %s. "
                        "If it already exists, add its ID to %s manually.",
                        name, exc, cache_path,
                    )
        finally:
            client.close()

        AnimaManager._save_cache(cache_path, cached)
        return cached

    @staticmethod
    def get_anima_id(name: str, cache_path: Optional[Path] = None) -> Optional[str]:
        """Look up an Anima ID by name from the local cache."""
        cache_path = cache_path or get_anima_cache_path()
        cached = AnimaManager._load_cache(cache_path)
        return cached.get(name)

    @staticmethod
    def _load_cache(path: Path) -> dict[str, str]:
        """Load {name: id} map from JSON cache file."""
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read anima cache %s: %s", path, exc)
            return {}

    @staticmethod
    def _save_cache(path: Path, data: dict[str, str]):
        """Persist {name: id} map to JSON cache file."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2))
        except OSError as exc:
            logger.warning("Failed to write anima cache %s: %s", path, exc)


# =============================================================================
# OverseerMemoryBridge
# =============================================================================

class OverseerMemoryBridge:
    """Elephantasm integration for the Overseer agent.

    The Overseer uses this to:
    - inject() learned strategies before each evaluation loop
    - extract_decision() after each intervention
    - extract_observation() when monitoring the Hunter
    - extract_intervention_result() to record what worked/failed
    """

    def __init__(self, anima_id: Optional[str] = None):
        self.anima_id = anima_id or AnimaManager.get_anima_id(OVERSEER_ANIMA_NAME)
        if not self.anima_id:
            raise ValueError(
                f"No Anima ID for '{OVERSEER_ANIMA_NAME}'. "
                "Run AnimaManager.ensure_animas() first."
            )
        self.client = Elephantasm(anima_id=self.anima_id)
        self._session_id = f"overseer-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    def inject(self, query: Optional[str] = None) -> Optional[str]:
        """Get relevant memory context for the current loop iteration.

        Returns a prompt-ready string or None if no memory is available.
        """
        pack = _safe_inject(self.client, query=query)
        if pack and pack.content:
            return pack.as_prompt()
        return None

    def extract_decision(self, decision: str, meta: Optional[dict] = None):
        """Record an Overseer decision (intervention, model change, etc.)."""
        _safe_extract(
            self.client,
            EventType.SYSTEM,
            content=decision,
            session_id=self._session_id,
            meta=meta or {},
        )

    def extract_observation(self, observation: str, meta: Optional[dict] = None):
        """Record an observation about the Hunter's behaviour."""
        _safe_extract(
            self.client,
            EventType.SYSTEM,
            content=observation,
            session_id=self._session_id,
            meta={"type": "observation", **(meta or {})},
        )

    def extract_intervention_result(
        self,
        intervention_id: str,
        verdict: str,
        metrics_before: dict,
        metrics_after: dict,
    ):
        """Record the outcome of an intervention for learning.

        Args:
            intervention_id: Unique identifier for the intervention.
            verdict: One of "improvement", "regression", "neutral".
            metrics_before: Metrics snapshot before the intervention.
            metrics_after: Metrics snapshot after the intervention.
        """
        _safe_extract(
            self.client,
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
        """Close the Elephantasm client connection."""
        try:
            self.client.close()
        except Exception:
            pass


# =============================================================================
# HunterMemoryBridge
# =============================================================================

class HunterMemoryBridge:
    """Elephantasm integration for the Hunter agent.

    Used inside hunter/runner.py's step_callback to capture events and
    at session start/end to inject memory and record results.
    """

    def __init__(self, anima_id: Optional[str] = None):
        self.anima_id = anima_id or AnimaManager.get_anima_id(HUNTER_ANIMA_NAME)
        if not self.anima_id:
            raise ValueError(
                f"No Anima ID for '{HUNTER_ANIMA_NAME}'. "
                "Run AnimaManager.ensure_animas() first."
            )
        self.client = Elephantasm(anima_id=self.anima_id)
        self._session_id: str = ""

    def set_session(self, session_id: str):
        """Bind this bridge to a specific Hunter session."""
        self._session_id = session_id

    def inject(self, query: Optional[str] = None) -> Optional[str]:
        """Get relevant memory for the Hunter's current task.

        Returns a prompt-ready string or None.
        """
        pack = _safe_inject(self.client, query=query)
        if pack and pack.content:
            return pack.as_prompt()
        return None

    def extract_step(self, step_info: dict):
        """Called by the Hunter's step_callback after each iteration.

        Captures tool calls and assistant messages as Elephantasm events.

        Args:
            step_info: Dict with optional keys 'tool_call' (name + args)
                       and 'assistant_message' (text content).
        """

        if "tool_call" in step_info:
            tc = step_info["tool_call"]
            args_str = json.dumps(tc.get("args", {}))
            _safe_extract(
                self.client,
                EventType.TOOL_CALL,
                content=f"{tc['name']}({args_str})",
                session_id=self._session_id,
                meta=step_info.get("meta", {}),
            )

        if "assistant_message" in step_info:
            _safe_extract(
                self.client,
                EventType.MESSAGE_OUT,
                content=step_info["assistant_message"][:2000],
                session_id=self._session_id,
                role="assistant",
            )

    def extract_finding(self, finding: dict):
        """Record a vulnerability finding.

        Args:
            finding: Dict with keys 'title', 'severity', and optionally
                     'cwe', 'target'.
        """
        _safe_extract(
            self.client,
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
        """Record the final result of a Hunter session.

        Args:
            result: Dict with session summary data (findings_count, etc.).
        """

        # Only include scalar values in meta
        safe_meta = {
            k: v for k, v in result.items()
            if isinstance(v, (str, int, float, bool))
        }
        _safe_extract(
            self.client,
            EventType.SYSTEM,
            content=f"Session complete. Findings: {result.get('findings_count', 0)}",
            session_id=self._session_id,
            meta={"type": "session_result", **safe_meta},
        )

    def check_duplicate(self, description: str) -> Optional[str]:
        """Check if a similar finding exists in memory.

        Uses semantic search via inject() to find similar past findings.

        Args:
            description: Natural language description of the finding.

        Returns:
            Matching memory summary if similarity > 0.85, else None.
        """
        pack = _safe_inject(self.client, query=description)
        if pack and pack.long_term_memories:
            top = pack.long_term_memories[0]
            if top.similarity is not None and top.similarity > 0.85:
                return top.summary
        return None

    def close(self):
        """Close the Elephantasm client connection."""
        try:
            self.client.close()
        except Exception:
            pass
