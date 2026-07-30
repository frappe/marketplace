"""Tests for the index-entry and release schema checks."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from schema_check import ReleaseSchemaValidator, SchemaValidator

APP = {
    "name": "helpdesk",
    "title": "Helpdesk",
    "description": "Ticketing",
    "repo": "https://github.com/frappe/helpdesk",
    "logo_url": "https://example.com/logo.png",
    "website": "https://frappe.io/helpdesk",
    "documentation": "https://docs.frappe.io/helpdesk",
    "category": "Applications",
    "categories": ["Featured"],
    "stars": 10,
    "releases_path": "apps/helpdesk.json",
}
RELEASE = {
    "version": "1.27.0",
    "branch": "main",
    "commit": "a" * 40,
    "frappe_core": ">=15.0.0,<17.0.0",
    "dependencies": {},
    "channel": "stable",
}


def messages(validator) -> str:
    validator.validate()
    return " | ".join(finding.message for finding in validator.findings)


def test_valid_index_entry_passes() -> None:
    assert messages(SchemaValidator(APP)) == ""


def test_index_entry_requires_the_apps_releases_pointer() -> None:
    app = {**APP, "releases_path": "elsewhere/helpdesk.json"}
    assert "apps/helpdesk.json" in messages(SchemaValidator(app))


def test_index_entry_requires_metadata() -> None:
    app = {**APP, "title": "", "stars": None}
    reported = messages(SchemaValidator(app))
    assert "title" in reported
    assert "stars" in reported


def test_index_entry_only_advises_on_a_bare_listing() -> None:
    """Listing polish is documented as optional - it must not block a release."""
    app = {**APP, "categories": [], "logo_url": "", "website": "", "documentation": ""}
    validator = SchemaValidator(app)

    assert messages(validator) == ""
    assert len(validator.notes) == 4


def test_valid_release_passes() -> None:
    assert messages(ReleaseSchemaValidator({**APP, "releases": [RELEASE]})) == ""


def test_release_rejects_a_short_commit() -> None:
    release = {**RELEASE, "commit": "a1b2c3d"}
    assert "40-character SHA" in messages(ReleaseSchemaValidator({**APP, "releases": [release]}))


def test_release_rejects_an_unparseable_frappe_core() -> None:
    release = {**RELEASE, "frappe_core": "not-a-range"}
    assert "not a version range" in messages(ReleaseSchemaValidator({**APP, "releases": [release]}))


def test_release_rejects_an_unknown_channel() -> None:
    release = {**RELEASE, "channel": "beta"}
    assert "channel" in messages(ReleaseSchemaValidator({**APP, "releases": [release]}))


def test_release_requires_a_branch() -> None:
    release = {**RELEASE, "branch": ""}
    assert "missing 'branch'" in messages(ReleaseSchemaValidator({**APP, "releases": [release]}))


def test_release_requires_a_dependencies_object() -> None:
    release = {k: v for k, v in RELEASE.items() if k != "dependencies"}
    assert "dependencies" in messages(ReleaseSchemaValidator({**APP, "releases": [release]}))


def test_release_rejects_a_repeated_commit_on_one_branch() -> None:
    releases = [RELEASE, {**RELEASE, "version": "1.28.0"}]
    assert "repeats a commit" in messages(ReleaseSchemaValidator({**APP, "releases": releases}))


def test_release_allows_two_branches_on_the_same_commit() -> None:
    """An app whose develop and main haven't diverged advertises one commit twice."""
    releases = [RELEASE, {**RELEASE, "branch": "develop", "channel": "nightly"}]
    assert messages(ReleaseSchemaValidator({**APP, "releases": releases})) == ""


def test_release_rejects_the_same_version_twice_on_one_branch() -> None:
    releases = [RELEASE, {**RELEASE, "commit": "b" * 40}]
    reported = messages(ReleaseSchemaValidator({**APP, "releases": releases}))
    assert "repeats a version" in reported


def test_release_allows_the_same_version_on_two_branches() -> None:
    releases = [RELEASE, {**RELEASE, "branch": "version-15", "commit": "b" * 40}]
    assert messages(ReleaseSchemaValidator({**APP, "releases": releases})) == ""


def test_app_without_releases_fails() -> None:
    assert "no releases" in messages(ReleaseSchemaValidator({**APP, "releases": []}))
