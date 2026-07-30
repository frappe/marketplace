from __future__ import annotations

import subprocess
from pathlib import Path


def clone_release(repo: str, branch: str, commit: str, clone_dir: Path) -> None:
    """Check out exactly `commit`, and prove it is reachable from `branch`.

    A release advertises an immutable commit, so the checks must run on that
    commit and nothing else. The ancestry check is what stops a publisher
    pointing the registry at a fork or an unmerged PR commit.
    """
    clone_dir.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q", str(clone_dir)])
    _run(["git", "-C", str(clone_dir), "remote", "add", "origin", repo])
    _fetch_commit(clone_dir, branch, commit)
    _run(["git", "-C", str(clone_dir), "checkout", "-q", commit])
    _reject_commit_outside_branch(clone_dir, repo, branch, commit)


def _fetch_commit(clone_dir: Path, branch: str, commit: str) -> None:
    """Fetch the commit directly, falling back to the branch history.

    Fetching a bare SHA needs uploadpack.allowReachableSHA1InWant on the
    server; GitHub allows it, other hosts may not.
    """
    try:
        _run(["git", "-C", str(clone_dir), "fetch", "-q", "--depth", "1", "origin", commit])
    except RuntimeError:
        _run(["git", "-C", str(clone_dir), "fetch", "-q", "origin", branch])


def _reject_commit_outside_branch(clone_dir: Path, repo: str, branch: str, commit: str) -> None:
    _run(["git", "-C", str(clone_dir), "fetch", "-q", "origin", f"+{branch}:refs/remotes/origin/{branch}"])
    contained = subprocess.run(
        ["git", "-C", str(clone_dir), "merge-base", "--is-ancestor", commit, f"refs/remotes/origin/{branch}"],
        capture_output=True,
        text=True,
    )
    if contained.returncode != 0:
        raise RuntimeError(f"{commit} is not reachable from {repo}@{branch} — advertise a commit on that branch")


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{' '.join(cmd[:3])} failed")
