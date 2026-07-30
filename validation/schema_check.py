#!/usr/bin/env python3
"""
Validate the structural integrity of registry entries that are new or changed
in a PR — malformed metadata in `apps.json`, or a malformed release in
`apps/<name>.json` — before any clone/scan work runs.

Only apps that differ from the base revision are checked, so pre-existing
registry entries left untouched by a PR don't fail unrelated checks.

SchemaValidator checks one index entry:
  1. every required app-level field is present with a value (see
     REQUIRED_APP_STRING_FIELDS, plus "categories" and "stars")
  2. "releases" points at exactly "apps/<name>.json" — pilot resolves the
     release file from that pointer and rejects anything else

ReleaseSchemaValidator checks that app's releases:
  1. every required release field is present (see REQUIRED_RELEASE_FIELDS)
  2. "commit" is a full 40-hex SHA — short SHAs and refs are not immutable
  3. "frappe_core" parses as a version specifier
  4. "channel" is "stable" or "nightly"
  5. "dependencies" is an object - {} is fine, but the key must exist
  6. no two releases share a commit, or a (version, branch) pair
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet

sys.path.insert(0, str(Path(__file__).parent))
from utils.base import Validator

REQUIRED_APP_STRING_FIELDS = ("name", "title", "description", "repo", "category")
# Wanted for a good marketplace listing, but never a reason to block a release.
ADVISORY_APP_FIELDS = ("logo_url", "website", "documentation")
REQUIRED_RELEASE_FIELDS = ("version", "branch", "commit", "frappe_core")
CHANNELS = ("stable", "nightly")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")


class SchemaValidator(Validator):
    name = "schema"

    def __init__(self, app: dict):
        super().__init__()
        self.app = app

    def validate(self) -> None:
        for field in REQUIRED_APP_STRING_FIELDS:
            if not self.app.get(field):
                self.fail(f"app missing '{field}'")

        for field in (*ADVISORY_APP_FIELDS, "categories"):
            if not self.app.get(field):
                self.note(
                    f"app has no '{field}' - the marketplace listing will look bare", severity="Info"
                )

        if not isinstance(self.app.get("stars"), int):
            self.fail("app missing 'stars' (must be an integer - 0 is fine)")

        expected = f"apps/{self.app.get('name')}.json"
        if self.app.get("releases_path") != expected:
            self.fail(f"app must set 'releases' to {expected!r} - that is the only path pilot will read")


class ReleaseSchemaValidator(Validator):
    name = "release schema"

    def __init__(self, app: dict):
        super().__init__()
        self.app = app

    def validate(self) -> None:
        releases = self.app.get("releases")
        if not releases:
            self.fail(f"apps/{self.app.get('name')}.json has no releases — add at least one release")
            return

        for index, release in enumerate(releases):
            self._validate_release(index, release)
        self._reject_duplicates(releases)

    def _validate_release(self, index: int, release: dict) -> None:
        for field in REQUIRED_RELEASE_FIELDS:
            if not release.get(field):
                self.fail(f"release {index} missing '{field}'")

        commit = release.get("commit") or ""
        if commit and not COMMIT_SHA.fullmatch(commit):
            self.fail(f"release {index} 'commit' must be a full 40-character SHA, got {commit!r}")

        frappe_core = release.get("frappe_core")
        if frappe_core and not _is_specifier(frappe_core):
            self.fail(f"release {index} 'frappe_core' is not a version range: {frappe_core!r}")

        if release.get("channel") not in CHANNELS:
            self.fail(f"release {index} 'channel' must be one of {CHANNELS}")

        if not isinstance(release.get("dependencies"), dict):
            self.fail(f"release {index} missing 'dependencies' (must be an object - {{}} is fine)")

    def _reject_duplicates(self, releases: list[dict]) -> None:
        """Two branches may legitimately sit on one commit (they just haven't
        diverged yet), so uniqueness is per branch, not registry-wide."""
        for fields, label in ((("branch", "commit"), "commit"), (("version", "branch"), "version")):
            seen = set()
            for index, release in enumerate(releases):
                identity = tuple(release.get(field) for field in fields)
                if identity in seen:
                    self.fail(f"release {index} repeats a {label} already advertised for its branch")
                seen.add(identity)


def _is_specifier(frappe_core: str) -> bool:
    try:
        SpecifierSet(frappe_core, prereleases=True)
    except InvalidSpecifier:
        return False
    return True
