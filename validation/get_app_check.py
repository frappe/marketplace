#!/usr/bin/env python3
"""
Run pilot's real get-app validator against a cloned app — the same checks
(repo structure, syntax, dependency declarations, and a real `uv pip
install` into a throwaway venv alongside a Frappe checkout) that
`bench get-app` itself runs before installing an app. Catches install-
breaking bugs (missing imports, undeclared dependencies) that pyproject/
hooks.py inspection alone can't see.

Requires the `pilot` package installed (see .github/workflows) and `uv` on
PATH.
"""

from __future__ import annotations

import re
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils.base import Validator

from pilot.config import AppConfig
from pilot.core.app import App
from pilot.core.app.validator import Validator as InstallValidator
from pilot.exceptions import AppValidationError, BenchError

FRAPPE_REPO = "https://github.com/frappe/frappe"
DEFAULT_BRANCH = "develop"


@dataclass
class _FakeBench:
    path: Path  # App._remote_url reads this for credential lookup on clone
    apps_path: Path

    def app(self, name: str) -> App:
        path = self.apps_path / name
        if not path.is_dir():
            raise BenchError(f"App {name} not found")
        return App(AppConfig(name=name, repo="", branch=""), self)


def frappe_branch_for(frappe_core: str) -> str:
    """Map an advertised frappe_core range to the frappe branch to validate
    against — the range's lower-bound major version, e.g. '>=15.0.0,<17.0.0'
    -> 'version-15'. No lower bound (or a -dev prerelease) -> develop."""
    match = re.search(r">=\s*(\d+)", frappe_core)
    if not match:
        return DEFAULT_BRANCH
    return f"version-{match.group(1)}"


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
            self._install_and_check(frappe_core)
        except AppValidationError as exc:
            self.fail(str(exc))
        except BenchError as exc:
            self.fail(str(exc))
        except Exception as exc:
            # Anything else (unexpected pilot API change, filesystem issue,
            # etc.) must still surface as a failed check, not crash the
            # whole CI run for every remaining target.
            self.fail(f"get-app validation crashed unexpectedly: {exc!r}")

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

    def _install_and_check(self, frappe_core: str) -> None:
        branch = frappe_branch_for(frappe_core)
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            bench = _FakeBench(path=workdir, apps_path=workdir / "apps")
            bench.apps_path.mkdir(parents=True)

            frappe_app = App(AppConfig(name="frappe", repo=FRAPPE_REPO, branch=branch), bench)
            try:
                frappe_app.clone()
            except BenchError as exc:
                raise BenchError(f"Could not clone frappe@{branch}: {exc}") from exc

            app_name = self.target["name"]
            (bench.apps_path / app_name).symlink_to(self.clone_dir)

            app = App(
                AppConfig(name=app_name, repo=self.target["repo"], branch=self.target["branch"]), bench
            )
            InstallValidator(app).validate()
