"""A bench-shaped workspace the pilot validators can be pointed at.

Nothing here is a real bench: it is a directory with apps/ and env/ laid out
the way the checks read them. Frappe checkouts, app clones and environments
are cached under one directory and shared by every release in a run - a
sweep over the whole registry clones erpnext once, not once per dependent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation"))
from get_app_check import DEFAULT_BRANCH, frappe_branch_for
from utils.clone import clone_release

from pilot.config import AppConfig
from pilot.core.app import App
from pilot.exceptions import BenchError
from pilot.managers.platform import add_mysqlclient_flags

FRAPPE_REPO = "https://github.com/frappe/frappe"
ENVIRONMENT_TIMEOUT_SECONDS = 900

# Newest first: frappe v16 requires 3.14, v15 tops out earlier. uv downloads
# whichever one is picked if the machine doesn't have it.
PYTHON_VERSIONS = ("3.14", "3.13", "3.12", "3.11", "3.10")


@dataclass
class Workspace:
    """Stands in for a Bench: the attributes the validators actually read."""

    path: Path
    staged_apps: tuple[str, ...] = ()

    @property
    def apps_path(self) -> Path:
        return self.path / "apps"

    @property
    def staging_path(self) -> Path:
        return self.path / ".staging"

    @property
    def env_path(self) -> Path:
        return self.path / "env"

    def app(self, name: str) -> App:
        if not (self.apps_path / name).is_dir():
            raise BenchError(f"App {name} not found")
        return App(AppConfig(name=name, repo="", branch=""), self)

    def apps(self) -> list[App]:
        return [self.app(entry.name) for entry in sorted(self.apps_path.iterdir()) if entry.is_dir()]


class CloneCache:
    """Repo checkouts, keyed by commit, shared across the whole run."""

    def __init__(self, root: Path) -> None:
        # Absolute: these paths become symlink targets inside a temporary
        # workspace, where a relative one resolves against the wrong directory.
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def get(self, name: str, repo: str, branch: str, commit: str) -> Path:
        """Check out `commit`, or reuse the checkout from an earlier release."""
        path = self.root / f"{name}-{commit[:12]}"
        if path.is_dir():
            return path
        staging = path.with_name(f"{path.name}.partial")
        # A dropped connection mid-sweep would otherwise report as the app's
        # commit being unreachable. Cloning again is safe: the checkout is
        # thrown away first, and the commit is immutable either way.
        for attempt in (1, 2):
            shutil.rmtree(staging, ignore_errors=True)
            try:
                clone_release(repo, branch, commit, staging)
                break
            except Exception:
                if attempt == 2:
                    shutil.rmtree(staging, ignore_errors=True)
                    raise
                print(f"  clone of {name}@{commit[:8]} failed, retrying once")
        staging.rename(path)
        return path


class FrappeCache:
    """One frappe checkout and one environment per frappe branch.

    The environment exists so the dependency-resolution check has a python to
    resolve against; it only ever sees --dry-run installs, so releases can
    share it. Without it that check no-ops and the import check falls back to
    its own throwaway venv.
    """

    def __init__(self, root: Path, *, build_environment: bool = True) -> None:
        self.root = root.resolve()  # symlinked into each workspace; see CloneCache
        self.root.mkdir(parents=True, exist_ok=True)
        self.build_environment = build_environment
        self._environment_errors: dict[str, str] = {}
        self._branches: dict[str, str] = {}

    def branch_for(self, frappe_core: str) -> str:
        """The frappe branch to validate against, as a branch that exists.

        A release advertising the next major (`>=17.0.0-dev`) names a
        version-17 branch that frappe has not cut yet - that work is on
        develop, which is exactly what such a release was built against.
        """
        branch = frappe_branch_for(frappe_core)
        if branch not in self._branches:
            self._branches[branch] = branch if self._exists(branch) else DEFAULT_BRANCH
        return self._branches[branch]

    @staticmethod
    def _exists(branch: str) -> bool:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", FRAPPE_REPO, branch], capture_output=True, text=True
        )
        return result.returncode == 0 and bool(result.stdout.strip())

    def checkout(self, branch: str) -> Path:
        path = self.root / f"frappe-{branch}"
        if not path.is_dir():
            staging = path.with_name(f"{path.name}.partial")
            shutil.rmtree(staging, ignore_errors=True)
            _run(["git", "clone", "-q", "--depth", "1", "--branch", branch, FRAPPE_REPO, str(staging)])
            staging.rename(path)
        return path

    def environment(self, branch: str) -> tuple[Path | None, str]:
        """(env path, error). The error is reported, not raised: an audit is
        still worth running with the checks an environment would have deepened."""
        if not self.build_environment:
            return None, "environment build disabled (--no-environment)"
        if branch in self._environment_errors:
            return None, self._environment_errors[branch]

        path = self.root / f"env-{branch}"
        if (path / "bin" / "python").exists():
            return path, ""
        try:
            self._build(branch, path)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            shutil.rmtree(path, ignore_errors=True)
            self._environment_errors[branch] = str(exc)
            return None, str(exc)
        return path, ""

    def _build(self, branch: str, path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)
        checkout = self.checkout(branch)
        environment = os.environ.copy()
        add_mysqlclient_flags(environment)
        _run(["uv", "venv", "--python", python_version_for(checkout), str(path)])
        _run(
            ["uv", "pip", "install", "--python", str(path / "bin" / "python"), "-e", str(checkout)],
            env=environment,
            timeout=ENVIRONMENT_TIMEOUT_SECONDS,
        )


def python_version_for(checkout: Path) -> str:
    """The newest python this frappe checkout declares support for.

    Frappe pins a narrow range that moves between major versions - v16 wants
    3.14 - so the environment has to follow the branch, not the machine. A
    branch old enough to predate pyproject.toml gets the oldest python here:
    those releases pull dependencies that no longer build on a new one.
    """
    pyproject = checkout / "pyproject.toml"
    requires = ""
    if pyproject.is_file():
        requires = tomllib.loads(pyproject.read_text()).get("project", {}).get("requires-python", "")
    if not requires:
        return PYTHON_VERSIONS[-1]
    try:
        allowed = SpecifierSet(requires)
    except InvalidSpecifier:
        return PYTHON_VERSIONS[0]
    supported = [version for version in PYTHON_VERSIONS if Version(f"{version}.0") in allowed]
    return supported[0] if supported else PYTHON_VERSIONS[0]


class ReleaseWorkspace:
    """Context manager giving one release a workspace to be validated in."""

    def __init__(self, release: dict, dependencies: list[dict], clones: CloneCache, frappe: FrappeCache) -> None:
        self.release = release
        self.dependencies = dependencies
        self.clones = clones
        self.frappe = frappe
        self.environment_error = ""
        self.frappe_branch = ""
        self._temporary: tempfile.TemporaryDirectory | None = None

    def __enter__(self) -> tuple[App, Workspace]:
        self._temporary = tempfile.TemporaryDirectory(prefix="marketplace-audit-")
        workspace = Workspace(path=Path(self._temporary.name))
        workspace.apps_path.mkdir(parents=True)

        branch = self.frappe_branch = self.frappe.branch_for(self.release.get("frappe_core", ""))
        _link(self.frappe.checkout(branch), workspace.apps_path / "frappe")
        environment, self.environment_error = self.frappe.environment(branch)
        if environment:
            _link(environment, workspace.env_path)

        for dependency in self.dependencies:
            _link(self._clone(dependency), workspace.apps_path / dependency["name"])
        _link(self._clone(self.release), workspace.apps_path / self.release["name"])

        return workspace.app(self.release["name"]), workspace

    def __exit__(self, *exc_info) -> None:
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None

    def _clone(self, release: dict) -> Path:
        return self.clones.get(release["name"], release["repo"], release["branch"], release["commit"])


def _link(target: Path, link: Path) -> None:
    if not target.exists():
        raise RuntimeError(f"cannot link {link.name} into the workspace: {target} does not exist")
    link.symlink_to(target.resolve())


def _run(argv: list[str], env: dict | None = None, timeout: int | None = None) -> None:
    result = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{' '.join(argv[:3])} failed")
