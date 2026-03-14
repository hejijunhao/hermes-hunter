"""WorktreeBackend backed by a local clone of a remote GitHub repo.

File operations work identically to ``WorktreeManager`` (same path, same
git commands). The differences:

- ``setup()`` clones from GitHub instead of creating a worktree.
- ``push()`` actually pushes to origin.
- ``teardown()`` removes the local clone (leaves the remote intact).
- ``is_setup()`` checks for ``.git`` directory (a full clone, not a worktree).
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import List

from hunter.worktree import CommitInfo, WorktreeError, WorktreeManager

logger = logging.getLogger(__name__)


class FlyWorktreeManager(WorktreeManager):
    """WorktreeBackend for the Fly.io remote backend.

    Manages a local clone of the Hunter's GitHub repo. The Overseer
    edits code locally, commits, and pushes. The Hunter machine pulls
    on boot.

    Inherits all file and git operations from ``WorktreeManager``.
    Only overrides initialisation, setup/teardown, and push.
    """

    def __init__(self, repo_url: str, clone_path: Path, github_pat: str):
        """Initialise without calling ``super().__init__()``.

        We set up paths differently — there's no repo root or worktree
        branch to find. We're a standalone clone.

        Args:
            repo_url: GitHub repo (e.g. ``"user/hermes-prime-hunter"``).
            clone_path: Where to clone locally (e.g. ``Path("/data/hunter-repo")``).
            github_pat: GitHub personal access token for clone/push.
        """
        # Authenticated HTTPS URL
        self._repo_url = f"https://{github_pat}@github.com/{repo_url}.git"
        self._github_pat = github_pat

        # Set attributes that WorktreeManager methods expect
        self.worktree_path = clone_path
        self.branch = "main"
        self.repo_root = clone_path  # In a clone, repo root == worktree path

    # ── Setup & teardown (override) ──────────────────────────────────────

    def setup(self) -> None:
        """Clone the repo if not present, pull if it exists."""
        if self.is_setup():
            # Already cloned — pull latest
            logger.info("Clone exists at %s — pulling latest", self.worktree_path)
            self._run_git("pull", "--ff-only", "origin", self.branch)
        else:
            # Fresh clone
            self.worktree_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Cloning %s to %s", self._safe_url(), self.worktree_path)
            try:
                subprocess.run(
                    ["git", "clone", self._repo_url, str(self.worktree_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                # Clean up partial clone directory before propagating.
                if self.worktree_path.exists():
                    shutil.rmtree(self.worktree_path)
                raise WorktreeError("Clone timed out after 120s")
            except subprocess.CalledProcessError as exc:
                if self.worktree_path.exists():
                    shutil.rmtree(self.worktree_path)
                raise WorktreeError(
                    f"Clone failed (exit {exc.returncode}): "
                    f"{(exc.stderr or '')[:200]}"
                ) from exc
        logger.info(
            "Clone ready: path=%s head=%s",
            self.worktree_path, self.get_head_commit()[:8],
        )

    def teardown(self) -> None:
        """Remove the local clone. Leaves the remote repo intact."""
        if self.worktree_path.exists():
            shutil.rmtree(self.worktree_path)
            logger.info("Clone removed: %s", self.worktree_path)

    def is_setup(self) -> bool:
        """Check if the clone exists and is a valid git repo."""
        git_dir = self.worktree_path / ".git"
        if not git_dir.exists():
            return False
        # For a clone, .git is a directory (not a file like in worktrees)
        if not git_dir.is_dir():
            return False
        # Quick sanity: can we run git status?
        result = self._run_git("status", "--porcelain", check=False)
        return result.returncode == 0

    # ── Push (override) ──────────────────────────────────────────────────

    def push(self) -> None:
        """Push committed changes to the remote Hunter repo."""
        self._require_setup()
        self._run_git("push", "origin", self.branch)
        logger.info("Pushed to remote Hunter repo")

    # ── Internal helpers ─────────────────────────────────────────────────

    def _safe_url(self) -> str:
        """Return the repo URL with the PAT redacted for logging."""
        return self._repo_url.replace(self._github_pat, "***")

    def _require_setup(self) -> None:
        """Raise if the clone isn't set up."""
        if not self.is_setup():
            raise WorktreeError(
                f"Clone not set up at {self.worktree_path}. "
                "Call setup() first."
            )

    # Override parent's _find_repo_root — not needed for clones.
    @staticmethod
    def _find_repo_root() -> Path:
        raise WorktreeError("FlyWorktreeManager does not use _find_repo_root()")

    def __repr__(self) -> str:
        setup = self.is_setup()
        head = self.get_head_commit()[:8] if setup else "N/A"
        return f"FlyWorktreeManager(path={self.worktree_path}, head={head}, setup={setup})"
