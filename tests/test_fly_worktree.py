"""Tests for hunter.backends.fly_worktree — FlyWorktreeManager."""

import subprocess
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from hunter.backends.fly_worktree import FlyWorktreeManager
from hunter.worktree import WorktreeError


@pytest.fixture
def clone_path(tmp_path):
    return tmp_path / "hunter-repo"


@pytest.fixture
def fly_wt(clone_path):
    """A FlyWorktreeManager that hasn't been set up yet."""
    return FlyWorktreeManager(
        repo_url="user/hermes-hunter",
        clone_path=clone_path,
        github_pat="ghp_test123",
    )


class TestInit:

    def test_sets_attributes(self, fly_wt, clone_path):
        assert fly_wt.worktree_path == clone_path
        assert fly_wt.branch == "main"
        assert fly_wt.repo_root == clone_path

    def test_builds_authenticated_url(self, fly_wt):
        assert "ghp_test123@github.com" in fly_wt._repo_url
        assert fly_wt._repo_url.endswith(".git")

    def test_safe_url_redacts_pat(self, fly_wt):
        assert "ghp_test123" not in fly_wt._safe_url()
        assert "***" in fly_wt._safe_url()


class TestSetup:

    @patch("hunter.backends.fly_worktree.subprocess.run")
    def test_clones_when_not_present(self, mock_run, fly_wt, clone_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="abc12345\n")

        # is_setup() returns False since clone_path doesn't exist
        assert not fly_wt.is_setup()

        # We need to mock get_head_commit which is called at end of setup()
        with patch.object(fly_wt, "get_head_commit", return_value="abc12345678"):
            fly_wt.setup()

        # Should have called git clone
        clone_call = mock_run.call_args_list[0]
        assert clone_call[0][0][0] == "git"
        assert clone_call[0][0][1] == "clone"

    @patch.object(FlyWorktreeManager, "_run_git")
    def test_pulls_when_already_cloned(self, mock_run_git, fly_wt, clone_path):
        # Create fake .git directory to simulate existing clone
        git_dir = clone_path / ".git"
        git_dir.mkdir(parents=True)

        # _run_git for status check in is_setup() and for pull
        mock_run_git.return_value = MagicMock(returncode=0, stdout="abc12345\n")

        with patch.object(fly_wt, "get_head_commit", return_value="abc12345678"):
            fly_wt.setup()

        # Should have called pull, not clone
        calls = mock_run_git.call_args_list
        pull_calls = [c for c in calls if "pull" in str(c)]
        assert len(pull_calls) > 0


class TestSetupCloneFailure:
    """H2 + M1: partial clone cleanup on failure."""

    @patch("hunter.backends.fly_worktree.subprocess.run")
    def test_timeout_cleans_up_partial_clone(self, mock_run, fly_wt, clone_path):
        """TimeoutExpired during clone should remove partial dir and raise WorktreeError."""
        # Create the partial clone directory as subprocess would
        clone_path.mkdir(parents=True)
        (clone_path / "partial_file").write_text("data")

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git clone", timeout=120)

        with pytest.raises(WorktreeError, match="timed out"):
            fly_wt.setup()

        assert not clone_path.exists()

    @patch("hunter.backends.fly_worktree.subprocess.run")
    def test_clone_error_cleans_up_partial_clone(self, mock_run, fly_wt, clone_path):
        """CalledProcessError during clone should remove partial dir and raise WorktreeError."""
        clone_path.mkdir(parents=True)
        (clone_path / "partial_file").write_text("data")

        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=128, cmd="git clone", stderr="fatal: repo not found",
        )

        with pytest.raises(WorktreeError, match="Clone failed"):
            fly_wt.setup()

        assert not clone_path.exists()

    @patch("hunter.backends.fly_worktree.subprocess.run")
    def test_timeout_no_dir_still_raises(self, mock_run, fly_wt, clone_path):
        """TimeoutExpired when no partial dir was created still raises WorktreeError."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git clone", timeout=120)

        with pytest.raises(WorktreeError, match="timed out"):
            fly_wt.setup()


class TestTeardown:

    def test_removes_clone_directory(self, fly_wt, clone_path):
        # Create the clone directory
        clone_path.mkdir(parents=True)
        (clone_path / "somefile.txt").write_text("data")

        fly_wt.teardown()
        assert not clone_path.exists()

    def test_no_error_if_not_present(self, fly_wt, clone_path):
        # Should not raise even if directory doesn't exist
        fly_wt.teardown()


class TestIsSetup:

    def test_false_when_no_directory(self, fly_wt, clone_path):
        assert not fly_wt.is_setup()

    def test_false_when_no_git_dir(self, fly_wt, clone_path):
        clone_path.mkdir(parents=True)
        assert not fly_wt.is_setup()

    @patch.object(FlyWorktreeManager, "_run_git")
    def test_true_when_valid_clone(self, mock_run_git, fly_wt, clone_path):
        git_dir = clone_path / ".git"
        git_dir.mkdir(parents=True)
        mock_run_git.return_value = MagicMock(returncode=0)
        assert fly_wt.is_setup()

    def test_false_when_git_is_file_not_dir(self, fly_wt, clone_path):
        # In worktrees, .git is a file. For clones, it must be a directory.
        clone_path.mkdir(parents=True)
        (clone_path / ".git").write_text("gitdir: /some/path")
        assert not fly_wt.is_setup()


class TestPush:

    @patch.object(FlyWorktreeManager, "_run_git")
    @patch.object(FlyWorktreeManager, "is_setup", return_value=True)
    def test_pushes_to_origin(self, _is_setup, mock_run_git, fly_wt):
        mock_run_git.return_value = MagicMock(returncode=0)
        fly_wt.push()
        mock_run_git.assert_called_with("push", "origin", "main")

    def test_raises_if_not_setup(self, fly_wt):
        with pytest.raises(WorktreeError, match="not set up"):
            fly_wt.push()


class TestInheritedMethods:
    """Verify that inherited WorktreeManager methods work with clone paths."""

    @patch.object(FlyWorktreeManager, "_run_git")
    @patch.object(FlyWorktreeManager, "is_setup", return_value=True)
    def test_commit_delegates_to_parent(self, _is_setup, mock_run_git, fly_wt):
        # Make diff --cached indicate there are staged changes
        def side_effect(*args, **kwargs):
            if args[0] == "diff" and "--cached" in args:
                return MagicMock(returncode=1)  # Non-zero = has changes
            return MagicMock(returncode=0, stdout="abc123\n")

        mock_run_git.side_effect = side_effect
        fly_wt.commit("test commit")

        # Should have called git add and git commit
        add_calls = [c for c in mock_run_git.call_args_list if c[0][0] == "add"]
        commit_calls = [c for c in mock_run_git.call_args_list if c[0][0] == "commit"]
        assert len(add_calls) > 0
        assert len(commit_calls) > 0

    @patch.object(FlyWorktreeManager, "is_setup", return_value=True)
    def test_read_file_uses_clone_path(self, _is_setup, fly_wt, clone_path):
        clone_path.mkdir(parents=True)
        test_file = clone_path / "test.txt"
        test_file.write_text("hello")

        content = fly_wt.read_file("test.txt")
        assert content == "hello"

    @patch.object(FlyWorktreeManager, "is_setup", return_value=True)
    def test_write_file_uses_clone_path(self, _is_setup, fly_wt, clone_path):
        clone_path.mkdir(parents=True)
        fly_wt.write_file("subdir/test.txt", "world")
        assert (clone_path / "subdir" / "test.txt").read_text() == "world"
