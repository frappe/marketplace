"""Running pilot's install-time checks over published releases.

Unlike the PR gate, an audit wants the whole picture: every check runs even
after an earlier one failed, and a check that raises is recorded rather than
ending the release. Findings are keyed by check so identical results across
several releases of an app collapse into one entry in the report.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tools.audit.registry import releases_of, resolve_dependencies
from tools.audit.workspace import CloneCache, FrappeCache, ReleaseWorkspace

from pilot.core.app.validator import Validator as InstallValidator
from pilot.exceptions import AppValidationError

PASSED = "passed"
FAILED = "failed"
CRASHED = "crashed"
SKIPPED = "skipped"

# Checks that quietly no-op without an environment to resolve against, so a
# run without one must not report them as passed.
ENVIRONMENT_DEPENDENT_CHECKS = ("DependencyResolutionCheck",)


@dataclass
class CheckOutcome:
    check: str
    status: str
    message: str = ""


@dataclass
class ReleaseAudit:
    version: str
    branch: str
    commit: str
    channel: str
    frappe_core: str
    frappe_branch: str = ""
    clone_error: str = ""
    environment_error: str = ""
    dependency_error: str = ""
    outcomes: list[CheckOutcome] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckOutcome]:
        return [outcome for outcome in self.outcomes if outcome.status in (FAILED, CRASHED)]

    @property
    def is_clean(self) -> bool:
        return not self.clone_error and not self.failures


@dataclass
class AppAudit:
    name: str
    repo: str
    generated_at: str
    releases: list[ReleaseAudit] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return all(release.is_clean for release in self.releases)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppAudit":
        releases = [
            ReleaseAudit(
                **{key: value for key, value in release.items() if key != "outcomes"},
                outcomes=[CheckOutcome(**outcome) for outcome in release["outcomes"]],
            )
            for release in data["releases"]
        ]
        return cls(
            name=data["name"], repo=data["repo"], generated_at=data["generated_at"], releases=releases
        )


class Auditor:
    """Audits registry apps against pilot's checks, one release at a time."""

    def __init__(self, apps: dict[str, dict], cache_root: Path, *, build_environment: bool = True) -> None:
        self.apps = apps
        self.clones = CloneCache(cache_root / "clones")
        self.frappe = FrappeCache(cache_root / "frappe", build_environment=build_environment)

    def audit_app(self, app: dict) -> AppAudit:
        audit = AppAudit(name=app["name"], repo=app["repo"], generated_at=_now())
        for release in releases_of(app):
            print(f"\n=== {app['name']} {release['version']} ({release['commit'][:8]}) ===", flush=True)
            audit.releases.append(self.audit_release(release))
        return audit

    def audit_release(self, release: dict) -> ReleaseAudit:
        audit = ReleaseAudit(
            version=release.get("version", ""),
            branch=release.get("branch", ""),
            commit=release.get("commit", ""),
            channel=release.get("channel", ""),
            frappe_core=release.get("frappe_core", ""),
        )
        dependencies = resolve_dependencies(self.apps, release)
        audit.dependency_error = _unresolved_dependencies(release, dependencies)
        workspace = ReleaseWorkspace(release, dependencies, self.clones, self.frappe)
        try:
            with workspace as (app, bench):
                audit.frappe_branch = workspace.frappe_branch
                audit.environment_error = workspace.environment_error
                outcomes = run_checks(app, skip=bool(workspace.environment_error))
                audit.outcomes = [_readable(outcome, bench.path, app.config.name) for outcome in outcomes]
        except Exception as exc:  # a clone or workspace failure ends this release only
            audit.clone_error = str(exc)
            print(f"  SETUP FAILED: {exc}")
        return audit


def run_checks(app, *, skip: bool = False) -> list[CheckOutcome]:
    """Every check pilot would run on install, each one reported separately."""
    outcomes = []
    for check in InstallValidator(app).checks:
        name = type(check).__name__
        if skip and name in ENVIRONMENT_DEPENDENT_CHECKS:
            outcomes.append(CheckOutcome(check=name, status=SKIPPED))
            print(f"  {name}: skipped — no environment")
            continue
        try:
            check.run(app)
            outcomes.append(CheckOutcome(check=name, status=PASSED))
            print(f"  {name}: passed")
        except AppValidationError as exc:
            outcomes.append(CheckOutcome(check=name, status=FAILED, message=str(exc)))
            print(f"  {name}: FAILED")
        except Exception as exc:
            outcomes.append(CheckOutcome(check=name, status=CRASHED, message=f"{exc!r}"))
            print(f"  {name}: CRASHED — {exc!r}")
    return outcomes


def _readable(outcome: CheckOutcome, workspace: Path, app_name: str) -> CheckOutcome:
    """Strip the machine the audit ran on out of a finding.

    Check messages quote tool output full of throwaway paths - the workspace,
    uv's build directories, the auditor's home. None of that means anything to
    the app's maintainers, and it makes otherwise identical findings across two
    releases look different.
    """
    if not outcome.message:
        return outcome
    message = outcome.message.replace(f"{workspace}/apps/{app_name}", app_name).replace(str(workspace), app_name)
    message = re.sub(rf"(/private)?{re.escape(tempfile.gettempdir().rstrip('/'))}/\S+", "<temporary path>", message)
    message = message.replace(str(Path.home()), "~")
    return CheckOutcome(check=outcome.check, status=outcome.status, message=message)


def _unresolved_dependencies(release: dict, dependencies: list[dict]) -> str:
    """Recorded on the release so a degraded run is visible in its report."""
    declared = set(release.get("dependencies", {})) - {"frappe"}
    resolved = {dependency["name"] for dependency in dependencies}
    missing = sorted(declared - resolved)
    return f"dependencies not resolvable from the registry: {', '.join(missing)}" if missing else ""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
