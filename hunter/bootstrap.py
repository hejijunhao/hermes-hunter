"""Bootstrap mode — detect empty Hunter repos and guide the Overseer to build one.

When the Hunter repository is empty or too minimal to function, the Overseer
enters bootstrap mode. This module provides:

- detect_bootstrap()          — check if the repo needs bootstrapping
- check_transition()          — evaluate whether bootstrap exit criteria are met
- load_bootstrap_prompt()     — load the bootstrap prompt augmentation
- seed_architecture_knowledge() — one-time extraction of architecture docs to Elephantasm
- BOOTSTRAP_TESTING_TARGETS   — known-vulnerable repos for validation testing
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from hunter.backends.base import WorktreeBackend
    from hunter.memory import OverseerMemoryBridge

logger = logging.getLogger(__name__)


# =============================================================================
# Bootstrap detection (D1)
# =============================================================================

@dataclass
class BootstrapState:
    """Result of bootstrap detection."""

    is_bootstrap: bool
    reason: str
    python_count: int
    skills_count: int


def detect_bootstrap(worktree: WorktreeBackend) -> BootstrapState:
    """Check if the Hunter repo is empty or too minimal to function.

    Detection heuristic: if the worktree has no Python files AND no skill
    files (Markdown in a ``skills/`` directory), it is in bootstrap state.
    This handles empty repos, repos with only a README, and repos without
    real Hunter code.

    Args:
        worktree: The Hunter's worktree backend (local or remote).

    Returns:
        BootstrapState with detection result and counts.
    """
    python_files = worktree.list_files(".", "*.py")
    skills_files = worktree.list_files("skills", "*.md")

    python_count = len(python_files)
    skills_count = len(skills_files)

    if python_count == 0 and skills_count == 0:
        return BootstrapState(
            is_bootstrap=True,
            reason="No Python files and no skills found — repo is empty or minimal",
            python_count=python_count,
            skills_count=skills_count,
        )

    if python_count == 0:
        return BootstrapState(
            is_bootstrap=True,
            reason=f"No Python files found ({skills_count} skills present but no code)",
            python_count=python_count,
            skills_count=skills_count,
        )

    if skills_count == 0:
        return BootstrapState(
            is_bootstrap=True,
            reason=f"No skills found ({python_count} Python files but no security skills)",
            python_count=python_count,
            skills_count=skills_count,
        )

    return BootstrapState(
        is_bootstrap=False,
        reason=f"Repo has {python_count} Python files and {skills_count} skills",
        python_count=python_count,
        skills_count=skills_count,
    )


# =============================================================================
# Testing targets (D4)
# =============================================================================

BOOTSTRAP_TESTING_TARGETS = [
    {
        "name": "OWASP Juice Shop",
        "repo": "juice-shop/juice-shop",
        "stack": "Node.js/TypeScript",
        "vulns": ["XSS", "SQLi", "IDOR", "Auth Bypass"],
    },
    {
        "name": "DVWA",
        "repo": "digininja/DVWA",
        "stack": "PHP",
        "vulns": ["SQLi", "XSS", "Command Injection", "File Upload"],
    },
    {
        "name": "WebGoat",
        "repo": "WebGoat/WebGoat",
        "stack": "Java/Spring",
        "vulns": ["OWASP Top 10"],
    },
    {
        "name": "crAPI",
        "repo": "OWASP/crAPI",
        "stack": "Python/Java/Go",
        "vulns": ["BOLA", "Broken Auth", "Excessive Data Exposure"],
    },
]


def get_testing_targets() -> list[dict[str, Any]]:
    """Return the list of known-vulnerable repos for bootstrap testing."""
    return list(BOOTSTRAP_TESTING_TARGETS)


# =============================================================================
# Transition logic (D5)
# =============================================================================

TRANSITION_MIN_SKILLS = 5
TRANSITION_MIN_PYTHON_FILES = 3
TRANSITION_MIN_COMMITS = 10


@dataclass
class TransitionResult:
    """Result of bootstrap transition evaluation."""

    ready: bool
    skills_count: int
    python_count: int
    commits_count: int
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "skills_count": self.skills_count,
            "python_count": self.python_count,
            "commits_count": self.commits_count,
            "missing": self.missing,
        }


def check_transition(worktree: WorktreeBackend) -> TransitionResult:
    """Evaluate whether the Hunter repo meets bootstrap exit criteria.

    Criteria:
        - At least TRANSITION_MIN_SKILLS ``.md`` files in ``skills/``
        - At least TRANSITION_MIN_PYTHON_FILES ``.py`` files
        - At least TRANSITION_MIN_COMMITS commits in history

    Args:
        worktree: The Hunter's worktree backend.

    Returns:
        TransitionResult with readiness assessment.
    """
    skills_files = worktree.list_files("skills", "*.md")
    python_files = worktree.list_files(".", "*.py")
    commits = worktree.get_recent_commits(TRANSITION_MIN_COMMITS)

    skills_count = len(skills_files)
    python_count = len(python_files)
    commits_count = len(commits)

    missing = []
    if skills_count < TRANSITION_MIN_SKILLS:
        missing.append(
            f"skills: {skills_count}/{TRANSITION_MIN_SKILLS}"
        )
    if python_count < TRANSITION_MIN_PYTHON_FILES:
        missing.append(
            f"python files: {python_count}/{TRANSITION_MIN_PYTHON_FILES}"
        )
    if commits_count < TRANSITION_MIN_COMMITS:
        missing.append(
            f"commits: {commits_count}/{TRANSITION_MIN_COMMITS}"
        )

    return TransitionResult(
        ready=len(missing) == 0,
        skills_count=skills_count,
        python_count=python_count,
        commits_count=commits_count,
        missing=missing,
    )


# =============================================================================
# Bootstrap prompt loading (D2b)
# =============================================================================

def load_bootstrap_prompt() -> str:
    """Load the bootstrap mode prompt from ``hunter/prompts/bootstrap.md``.

    Returns:
        The bootstrap prompt content as a string.

    Raises:
        FileNotFoundError: If the prompt file is missing.
    """
    path = Path(__file__).parent / "prompts" / "bootstrap.md"
    return path.read_text(encoding="utf-8")


# =============================================================================
# Architecture seeding (D3)
# =============================================================================

_ARCHITECTURE_SEEDED_KEY = "architecture_seeded"

_ARCHITECTURE_DOC_PATH = Path(__file__).parent.parent / "hjjh" / "architecture.md"

# Sections from hjjh/architecture.md to seed into Elephantasm.
# These are the most relevant for the Overseer during bootstrap.
_SEED_SECTION_PREFIXES = [
    "## 3. Hunter Architecture",
    "## 8. Code Evolution",
]


def seed_architecture_knowledge(
    memory: OverseerMemoryBridge,
    cache_path: Optional[Path] = None,
) -> bool:
    """Extract key architecture doc sections to Elephantasm (one-time).

    Reads ``hjjh/architecture.md`` from the Overseer's own repo, splits it
    into sections, and extracts the Hunter-relevant sections as high-importance
    Elephantasm events. Idempotent — skips if already seeded (tracked via the
    anima cache file).

    Args:
        memory: The Overseer's memory bridge.
        cache_path: Override for the anima cache path (testing).

    Returns:
        True if seeding was performed, False if already seeded or skipped.
    """
    from hunter.config import get_anima_cache_path

    cache_path = cache_path or get_anima_cache_path()

    # Check if already seeded
    cache = _load_json(cache_path)
    if cache.get(_ARCHITECTURE_SEEDED_KEY):
        logger.debug("Architecture docs already seeded — skipping")
        return False

    # Locate architecture doc
    arch_path = _ARCHITECTURE_DOC_PATH
    if not arch_path.exists():
        logger.warning("Architecture doc not found at %s — skipping seed", arch_path)
        return False

    arch_content = arch_path.read_text(encoding="utf-8")

    # Split into sections by ## headers
    sections = _split_sections(arch_content)

    # Extract relevant sections
    seeded = 0
    for prefix in _SEED_SECTION_PREFIXES:
        section_content = sections.get(prefix)
        if not section_content:
            logger.warning("Section '%s' not found in architecture doc", prefix)
            continue

        memory.extract_decision(
            f"[Architecture Reference] {prefix}\n\n{section_content}",
            meta={
                "type": "architecture_seed",
                "section": prefix,
                "importance": 1.0,
            },
        )
        seeded += 1
        logger.info("Seeded architecture section: %s", prefix)

    # Mark as seeded in cache
    if seeded > 0:
        cache[_ARCHITECTURE_SEEDED_KEY] = True
        _save_json(cache_path, cache)
        logger.info("Architecture seeding complete: %d sections extracted", seeded)

    return seeded > 0


def _split_sections(content: str) -> dict[str, str]:
    """Split a Markdown document into sections keyed by ``## `` headers.

    Returns a dict mapping header line (e.g. ``## 3. Hunter Architecture``)
    to the full content of that section (header + body up to the next ``## ``).
    """
    sections: dict[str, str] = {}
    current_header: Optional[str] = None
    current_lines: list[str] = []
    in_code_block = False

    for line in content.split("\n"):
        if line.startswith("```"):
            in_code_block = not in_code_block
        if not in_code_block and line.startswith("## "):
            # Save previous section
            if current_header is not None:
                sections[current_header] = "\n".join(current_lines)
            current_header = line.strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    # Save final section
    if current_header is not None:
        sections[current_header] = "\n".join(current_lines)

    return sections


def _load_json(path: Path) -> dict:
    """Load a JSON file, returning empty dict on any error."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_json(path: Path, data: dict) -> None:
    """Save a dict as JSON, creating parent dirs if needed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
    except OSError as exc:
        logger.warning("Failed to write %s: %s", path, exc)
