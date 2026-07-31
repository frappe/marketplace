"""Unit tests for the audit tooling: selection, resolution, reporting, filing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.audit import file_issues, registry
from tools.audit.report import group_findings, render
from tools.audit.runner import AppAudit, CheckOutcome, ReleaseAudit, _readable
from tools.audit.workspace import python_version_for


def app(name: str, repo: str, releases: list[dict]) -> dict:
    return {"name": name, "repo": repo, "releases": releases}


def release(version: str, commit: str, dependencies: dict | None = None) -> dict:
    return {
        "version": version,
        "branch": "develop",
        "commit": commit,
        "channel": "stable",
        "frappe_core": ">=16.0.0,<17.0.0",
        "dependencies": dependencies or {},
    }


@pytest.fixture
def apps() -> dict[str, dict]:
    return {
        "crm": app("crm", "https://github.com/frappe/crm", [release("1.0.0", "a" * 40)]),
        "hrms": app(
            "hrms",
            "https://github.com/frappe/hrms",
            [release("16.0.0", "b" * 40, {"erpnext": ">=16.0.0,<17.0.0"})],
        ),
        "erpnext": app(
            "erpnext",
            "https://github.com/frappe/erpnext",
            [
                release("15.0.0", "c" * 40),
                release("16.1.0", "d" * 40, {"payments": ">=1.0.0"}),
                release("16.2.0", "e" * 40, {"payments": ">=1.0.0"}),
            ],
        ),
        "payments": app("payments", "https://github.com/frappe/payments", [release("1.2.0", "f" * 40)]),
        "vendor_app": app("vendor_app", "https://github.com/acme/vendor_app", [release("2.0.0", "0" * 40)]),
    }


class TestSelection:
    def test_first_party_excludes_other_owners(self, apps):
        names = [entry["name"] for entry in registry.select_apps(apps, first_party=True)]
        assert names == ["crm", "erpnext", "hrms", "payments"]

    def test_named_apps_win_over_first_party(self, apps):
        selected = registry.select_apps(apps, names=["vendor_app"], first_party=True)
        assert [entry["name"] for entry in selected] == ["vendor_app"]

    def test_unknown_app_is_rejected(self, apps):
        with pytest.raises(registry.UnknownAppError, match="nosuchapp"):
            registry.select_apps(apps, names=["nosuchapp"])

    def test_all_apps_when_nothing_is_narrowed(self, apps):
        assert len(registry.select_apps(apps)) == len(apps)


class TestDependencyResolution:
    def test_highest_matching_version_wins(self, apps):
        match = registry.best_release(apps["erpnext"], ">=16.0.0,<17.0.0")
        assert match["version"] == "16.2.0"
        assert match["repo"] == "https://github.com/frappe/erpnext"

    def test_specifier_outside_every_release_resolves_to_nothing(self, apps):
        assert registry.best_release(apps["erpnext"], ">=99.0.0") is None

    def test_transitive_dependencies_are_included(self, apps):
        resolved = registry.resolve_dependencies(apps, apps["hrms"]["releases"][0])
        assert sorted(entry["name"] for entry in resolved) == ["erpnext", "payments"]

    def test_unknown_dependency_is_left_out(self, apps):
        resolved = registry.resolve_dependencies(apps, release("1.0.0", "9" * 40, {"ghost": ">=1.0.0"}))
        assert resolved == []


def audit_with(*releases: ReleaseAudit) -> AppAudit:
    return AppAudit(
        name="crm",
        repo="https://github.com/frappe/crm",
        generated_at="2026-07-31 10:00 UTC",
        releases=list(releases),
    )


def release_audit(commit: str, outcomes: list[CheckOutcome], **kwargs) -> ReleaseAudit:
    return ReleaseAudit(
        version="1.0.0",
        branch="develop",
        commit=commit,
        channel="stable",
        frappe_core=">=16.0.0,<17.0.0",
        outcomes=outcomes,
        **kwargs,
    )


class TestGrouping:
    def test_identical_findings_across_releases_collapse(self):
        outcome = CheckOutcome(check="HooksCheck", status="failed", message="broken hook")
        audit = audit_with(release_audit("a" * 40, [outcome]), release_audit("b" * 40, [outcome]))

        groups = group_findings(audit)

        assert len(groups) == 1
        assert groups[0].commits == ["a" * 40, "b" * 40]

    def test_different_messages_stay_separate(self):
        audit = audit_with(
            release_audit("a" * 40, [CheckOutcome(check="HooksCheck", status="failed", message="one")]),
            release_audit("b" * 40, [CheckOutcome(check="HooksCheck", status="failed", message="two")]),
        )
        assert len(group_findings(audit)) == 2

    def test_passing_checks_are_not_findings(self):
        audit = audit_with(release_audit("a" * 40, [CheckOutcome(check="SyntaxCheck", status="passed")]))
        assert group_findings(audit) == []

    def test_skipped_checks_are_not_findings(self):
        audit = audit_with(
            release_audit("a" * 40, [CheckOutcome(check="DependencyResolutionCheck", status="skipped")])
        )
        assert group_findings(audit) == []


class TestReadableMessages:
    def test_workspace_and_home_paths_are_stripped(self, tmp_path):
        outcome = CheckOutcome(
            check="ImportCheck",
            status="failed",
            message=f"failed to build {tmp_path}/apps/crm, see {Path.home()}/.cache/uv",
        )

        cleaned = _readable(outcome, tmp_path, "crm")

        assert str(tmp_path) not in cleaned.message
        assert str(Path.home()) not in cleaned.message
        assert "crm" in cleaned.message

    def test_identical_findings_survive_different_workspaces(self, tmp_path):
        first = tmp_path / "run-one"
        second = tmp_path / "run-two"
        message = "'crm' failed to install: {}/apps/crm/setup.py is broken"
        cleaned = [
            _readable(CheckOutcome(check="ImportCheck", status="failed", message=message.format(root)), root, "crm")
            for root in (first, second)
        ]

        assert cleaned[0].message == cleaned[1].message


class TestRender:
    def test_body_names_every_reviewed_commit(self):
        audit = audit_with(
            release_audit("a" * 40, [CheckOutcome(check="HooksCheck", status="failed", message="broken")]),
            release_audit("b" * 40, []),
        )

        body = render(audit, registry_commit="c" * 40)

        assert "Releases reviewed" in body
        assert body.count("https://github.com/frappe/crm/commit/" + "a" * 40) == 2  # table row and finding
        assert "https://github.com/frappe/crm/commit/" + "b" * 40 in body
        assert "registry `cccccccc`" in body

    def test_clean_audit_says_so(self):
        body = render(audit_with(release_audit("a" * 40, [CheckOutcome(check="SyntaxCheck", status="passed")])))
        assert "Every check passed" in body

    def test_caveats_report_a_degraded_run(self):
        audit = audit_with(
            release_audit("a" * 40, [], environment_error="uv venv failed"),
            release_audit("b" * 40, [], dependency_error="dependencies not resolvable: ghost"),
            release_audit("c" * 40, [], clone_error="commit not reachable"),
        )

        body = render(audit)

        assert "uv venv failed" in body
        assert "ghost" in body
        assert "could not be checked out" in body


class TestPythonSelection:
    def test_newest_version_the_checkout_allows_wins(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10,<3.13"\n')
        assert python_version_for(tmp_path) == "3.12"

    def test_frappe_v16_range_picks_3_14(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.14,<3.15"\n')
        assert python_version_for(tmp_path) == "3.14"

    def test_unreadable_or_missing_requirement_falls_back_to_newest(self, tmp_path):
        assert python_version_for(tmp_path) == "3.14"
        (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = "not a specifier"\n')
        assert python_version_for(tmp_path) == "3.14"


class TestFiling:
    def test_repo_slug_from_a_registry_url(self):
        assert file_issues._repo_slug("https://github.com/frappe/crm") == "frappe/crm"
        assert file_issues._repo_slug("https://github.com/frappe/crm.git") == "frappe/crm"

    def test_unusable_repo_url_is_rejected(self):
        with pytest.raises(file_issues.FilingError):
            file_issues._repo_slug("https://example.com/crm/extra/path")

    def test_missing_report_is_not_filed(self, tmp_path):
        with pytest.raises(file_issues.FilingError, match="no report on disk"):
            file_issues._load("crm", tmp_path)
