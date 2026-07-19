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
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from validator import Validator

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

    def __init__(self, target: dict, clone_dir: Path) -> None:
        super().__init__()
        self.target = target
        self.clone_dir = clone_dir

    def validate(self) -> None:
        frappe_core = self.target.get("frappe_core")
        if not frappe_core:
            self.fail("No frappe_core declared — cannot determine which Frappe version to validate against")
            return

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
                AppConfig(name=app_name, repo=self.target["repo"], branch=self.target["target"]), bench
            )
            InstallValidator(app).validate()
