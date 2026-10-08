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

import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, Specifier, SpecifierSet
from packaging.version import InvalidVersion, Version

sys.path.insert(0, str(Path(__file__).parent))
from utils.base import Validator
from utils.clone import checkout_commit

from pilot.config import AppConfig, BenchConfig
from pilot.core.app import App
from pilot.core.app.validator import Validator as InstallValidator
from pilot.core.app.validator.frappe_compatibility import FrappeCompatibilityCheck
from pilot.core.bench import Bench
from pilot.exceptions import AppNotFoundError, AppValidationError, BenchError, DependencyResolutionError
from pilot.integrations.marketplace import Marketplace
from pilot.managers.environment import PythonEnvManager

FRAPPE_REPO = "https://github.com/frappe/frappe"


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
    return f"version-{max(floors).major}"


def frappe_requires_python(frappe_path: Path) -> str:
    """frappe's requires-python, e.g. '>=3.14,<3.15', as a uv Python request."""
    project = tomllib.loads((frappe_path / "pyproject.toml").read_text()).get("project", {})
    requires_python = project.get("requires-python")
    if not requires_python:
        raise AppValidationError(f"frappe at {frappe_path} declares no requires-python")
    return requires_python


def _lower_bound(specifier: Specifier) -> Version | None:
    if specifier.operator not in (">=", ">", "==", "~="):
        return None
    try:
        return Version(specifier.version.removesuffix(".*"))
    except InvalidVersion:
        return None


class GetAppValidator(Validator):
    name = "get-app validator"

    def __init__(self, release: dict, clone_dir: Path, registry: dict[str, dict] | None = None) -> None:
        super().__init__()
        self.target = release
        self.clone_dir = clone_dir
        self.registry = registry or {}

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
            # No bench.toml: checks needing one skip themselves.
            bench = Bench(BenchConfig.default(name="validation"), workdir)
            bench.apps_path.mkdir(parents=True)

            frappe_app = App(AppConfig(name="frappe", repo=FRAPPE_REPO, branch=branch), bench)
            try:
                frappe_app.clone()
            except BenchError as exc:
                raise BenchError(f"Could not clone frappe@{branch}: {exc}") from exc

            self.install_dependencies(bench, frappe_app)

            # The validator builds its throwaway venv on the bench's interpreter.
            bench.config.python_version = frappe_requires_python(frappe_app.path)
            PythonEnvManager(bench).create_venv()

            app_name = self.target["name"]
            (bench.apps_path / app_name).symlink_to(self.clone_dir)

            app = App(
                AppConfig(name=app_name, repo=self.target["repo"], branch=self.target["branch"]), bench
            )
            InstallValidator(app).validate()

    def install_dependencies(self, bench: Bench, frappe_app: App) -> None:
        if not self.registry:
            return

        name = self.target["name"]
        registry = {**self.registry, name: {**self.registry[name], "releases": [self.target]}}
        try:
            resolver = RegistryMarketplace(bench, frappe_app=frappe_app, apps=registry).find_app(name)
            dependencies = resolver.resolve()[:-1]
        except (AppNotFoundError, DependencyResolutionError) as exc:
            raise AppValidationError(str(exc)) from exc

        for dependency in dependencies:
            if dependency.app == "frappe":
                continue
            try:
                checkout_commit(
                    dependency.repo, dependency.branch, dependency.commit, bench.apps_path / dependency.app
                )
            except RuntimeError as exc:
                raise BenchError(f"Could not clone {dependency.app}@{dependency.commit[:8]}: {exc}") from exc


@dataclass
class RegistryMarketplace(Marketplace):
    frappe_app: App | None = None
    apps: dict[str, dict] = field(default_factory=dict)

    def get_current_frappe_version(self) -> str:
        return str(FrappeCompatibilityCheck._installed_version(self.frappe_app, "frappe"))

    def _load_registry(self) -> list[dict]:
        return [
            {key: value for key, value in app.items() if key not in ("releases", "releases_path")}
            for app in self.apps.values()
        ]

    def releases(self, app_name: str) -> tuple[dict, ...]:
        return self._newest_first(self.apps.get(app_name, {}).get("releases", []))
