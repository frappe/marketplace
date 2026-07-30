"""Tests for cloning a release at its advertised commit."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.clone import clone_release


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def commit(repo: Path, message: str) -> str:
    (repo / "file.txt").write_text(message)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "-q", "-b", "main")
    git(remote, "config", "user.email", "test@example.com")
    git(remote, "config", "user.name", "Test")
    # Local clients refuse to fetch a bare SHA unless the server allows it.
    git(remote, "config", "uploadpack.allowReachableSHA1InWant", "true")
    return remote


def test_clone_checks_out_the_advertised_commit(tmp_path: Path) -> None:
    remote = make_remote(tmp_path)
    published = commit(remote, "c1")
    commit(remote, "c2")  # branch moved on after publication
    clone_dir = tmp_path / "app"

    clone_release(str(remote), "main", published, clone_dir)

    assert git(clone_dir, "rev-parse", "HEAD") == published


def test_clone_rejects_a_commit_that_is_not_on_the_branch(tmp_path: Path) -> None:
    remote = make_remote(tmp_path)
    commit(remote, "c1")
    git(remote, "checkout", "-q", "-b", "side")
    off_branch = commit(remote, "only-on-side")
    git(remote, "checkout", "-q", "main")

    with pytest.raises(RuntimeError, match="not reachable"):
        clone_release(str(remote), "main", off_branch, tmp_path / "app")


def test_clone_rejects_an_unknown_commit(tmp_path: Path) -> None:
    remote = make_remote(tmp_path)
    commit(remote, "c1")

    with pytest.raises(RuntimeError):
        clone_release(str(remote), "main", "0" * 40, tmp_path / "app")
