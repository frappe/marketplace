#!/usr/bin/env python3
"""
Run pilot's own get-app on the release, into a real bench on the Frappe
version its frappe_core advertises: clone at the advertised commit, pilot's
app validator (repo structure, syntax, dependency declarations, imports,
Frappe compatibility), the install into the bench's venv, and the assets,
prebuilt when the app publishes them, else built. Catches install-breaking
bugs that pyproject/hooks.py inspection alone can't see. Asset builds run
under the bench's memory cap (utils/bench.py), so one too heavy for a small
bench fails here.

Requires the `pilot` package installed and what `pilot init` needs on the
host (see .github/workflows and utils/bench.py).
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, Specifier, SpecifierSet
from packaging.version import InvalidVersion, Version

sys.path.insert(0, str(Path(__file__).parent))
from utils.base import Validator
from utils.bench import BUILD_MEMORY_LIMIT_MB, ValidationBench, build_oom_kills, report

from pilot.core.app import App
from pilot.exceptions import AppValidationError, BenchError

FRAPPE_REPO = "https://github.com/frappe/frappe"
FRAPPE_DEVELOP_VERSION_URL = "https://raw.githubusercontent.com/frappe/frappe/develop/frappe/__init__.py"
LS_REMOTE_NO_MATCH = 2  # `git ls-remote --exit-code` found no matching ref


def frappe_branch_for(frappe_core: str) -> str:
    """The frappe branch to validate against: the major version of the lowest
    Frappe the range admits, e.g. '>=15.0.0,<17.0.0' -> 'version-15'. A range
    with no lower bound is rejected rather than guessed at."""
    try:
        specifiers = SpecifierSet(frappe_core, prereleases=True)
    except InvalidSpecifier as exc:
        raise AppValidationError(f"frappe_core {frappe_core!r} is not a valid version range ({exc})") from exc

    # A specifier set is an AND, so the highest floor is the effective one.
    floors = [floor for floor in map(_lower_bound, specifiers) if floor]
    if not floors:
        raise AppValidationError(
            f"frappe_core {frappe_core!r} declares no lower bound, so there is no "
            "Frappe version to validate against — declare one, e.g. '>=15.0.0,<16.0.0'"
        )
    return frappe_branch_for_major(max(floors).major, specifiers)


def frappe_branch_for_major(major: int, specifiers: SpecifierSet) -> str:
    """version-N when frappe has it; develop only when develop is Frappe N and in the range."""
    branch = f"version-{major}"
    if frappe_has_branch(branch):
        return branch
    develop = frappe_develop_version()
    if develop.major == major and develop in specifiers:
        return "develop"
    if develop.major == major:
        fix = f"to validate against develop, declare a dev floor, e.g. '>={major}.0.0-dev'"
    else:
        fix = "check the range's lower bound"
    raise AppValidationError(
        f"frappe_core {str(specifiers)!r} needs Frappe {major}, which has no {branch} branch, "
        f"and develop ({develop}) does not fit it — {fix}"
    )


def frappe_has_branch(branch: str) -> bool:
    """Raises on a failed lookup, so an outage is never read as "no such branch"."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", FRAPPE_REPO, f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise AppValidationError(f"Timed out looking up frappe@{branch}; run /rereview to try again") from exc
    if result.returncode == LS_REMOTE_NO_MATCH:
        return False
    if result.returncode != 0:
        raise AppValidationError(
            f"Could not look up frappe@{branch} ({result.stderr.strip()}); run /rereview to try again"
        )
    return True


def frappe_develop_version() -> Version:
    """The Frappe version on develop, from its __version__, e.g. '17.0.0-dev'."""
    try:
        with urllib.request.urlopen(FRAPPE_DEVELOP_VERSION_URL, timeout=30) as response:
            source = response.read().decode()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AppValidationError(f"Could not read Frappe's develop version ({exc}); run /rereview to try again") from exc
    match = re.search(r"^__version__\s*=\s*[\"']([^\"']+)", source, re.MULTILINE)
    if not match:
        raise AppValidationError(f"No __version__ in {FRAPPE_DEVELOP_VERSION_URL}")
    return Version(match.group(1))


def _lower_bound(specifier: Specifier) -> Version | None:
    if specifier.operator not in (">=", ">", "==", "~="):
        return None
    try:
        return Version(specifier.version.removesuffix(".*"))
    except InvalidVersion:
        return None


class GetAppValidator(Validator):
    name = "get-app validator"

    def __init__(self, release: dict, clone_dir: Path) -> None:
        super().__init__()
        self.target = release
        self.clone_dir = clone_dir

    def fail(self, message: str, **details) -> None:
        """Report install output against the app's own name, not the temp checkout."""
        super().fail(message.replace(str(self.clone_dir), self.target["name"]), **details)

    def validate(self) -> None:
        frappe_core = self.target.get("frappe_core")
        if not frappe_core:
            self.fail("No frappe_core declared — cannot determine which Frappe version to validate against")
            return

        self._reject_untruthful_metadata(frappe_core)
        try:
            frappe_branch = frappe_branch_for(frappe_core)
        except AppValidationError as exc:
            self.fail(str(exc))
            return

        self._get_app(frappe_branch)

    def _get_app(self, frappe_branch: str) -> None:
        try:
            bench = ValidationBench(frappe_branch).get_fresh_bench()
        except Exception as exc:
            self.fail(f"Could not set up a Frappe {frappe_branch} bench to validate against: {exc}")
            return

        oom_kills = build_oom_kills()
        try:
            app = App.from_repo(bench, self.target["repo"], self.target["branch"])
            self.validate_listed_release(app)
            # As pilot's own get-app: a marketplace app brings its dependencies, a bare repo does not.
            app.install(commit=self.target["commit"], install_dependencies=app.is_marketplace, on_progress=report)
        except BenchError as exc:
            self.fail_install(exc, was_killed=build_oom_kills() > oom_kills)
        except Exception as exc:
            # Anything else (unexpected pilot API change, filesystem issue,
            # etc.) must still surface as a failed check, not crash the
            # whole CI run for every remaining target.
            self.fail(f"get-app crashed unexpectedly: {exc!r}")

    def validate_listed_release(self, app: App) -> None:
        """get-app validates only apps the live registry does not list, so a listed
        app's new release is cloned at its commit and validated here first."""
        if not app.is_marketplace:
            return
        app.clone(report)
        app.checkout_commit(self.target["commit"])
        app.validate()

    def fail_install(self, exc: BenchError, was_killed: bool) -> None:
        """Pilot's own message, unless the cap killed a build: pilot then reports only a failed
        command, or advice meant for the bench's operator."""
        if not was_killed:
            self.fail(str(exc))
            return
        self.fail(
            f"The asset build needed more than {BUILD_MEMORY_LIMIT_MB} MiB, so a small bench kills it "
            "mid-install. Publish prebuilt assets for each release commit, split large bundles, or trim "
            "heavy build plugins",
            severity="Major",
            rule="get-app-memory",
        )

    def _reject_untruthful_metadata(self, frappe_core: str) -> None:
        """The advertised version and frappe_core must match the code at this commit."""
        pyproject = self.clone_dir / "pyproject.toml"
        if not pyproject.is_file():
            self.fail("No pyproject.toml at the advertised commit")
            return

        toml = tomllib.loads(pyproject.read_text())
        project = toml.get("project", {})
        declared = project.get("version") or self._dynamic_version(project.get("name", ""))
        advertised = self.target.get("version")
        if declared and declared != advertised:
            self.fail(f"advertised version {advertised!r} but the commit declares {declared!r}")

        in_repo = toml.get("tool", {}).get("bench", {}).get("frappe-dependencies", {}).get("frappe")
        if in_repo and in_repo != frappe_core:
            self.fail(f"advertised frappe_core {frappe_core!r} but the commit declares {in_repo!r}")

    def _dynamic_version(self, project_name: str) -> str:
        """__version__ from <module>/__init__.py, for apps using dynamic versioning."""
        init = self.clone_dir / project_name / "__init__.py"
        if not project_name or not init.is_file():
            return ""
        for line in init.read_text().splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[-1].strip().strip("\"'")
        return ""
