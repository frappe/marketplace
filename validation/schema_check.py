#!/usr/bin/env python3
"""
Validate the structural integrity of an app that is new or changed in the
registry — catches malformed or incomplete entries (missing metadata, no
releases, a release missing required fields) early in CI, before any
clone/scan work runs for any of its releases.

Only apps that differ from the base revision are checked, so pre-existing
registry entries left untouched by a PR don't fail unrelated checks.

Checks per new/changed app:
  1. every required app-level field is present with a value (see
     REQUIRED_APP_STRING_FIELDS, plus "categories" and "stars" below)
  2. "releases" points at exactly "apps/<name>.json" — the only path pilot
     reads an app's releases from
  3. that file's "releases" is a non-empty list
  4. each release has every required field (see REQUIRED_RELEASE_FIELDS),
     plus a "dependencies" object - {} is fine, but the key must exist
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils.base import Validator

REQUIRED_APP_STRING_FIELDS = (
    "name",
    "title",
    "description",
    "repo",
    "logo_url",
    "website",
    "documentation",
    "category",
)
REQUIRED_RELEASE_FIELDS = ("version", "branch", "commit", "frappe_core")


class SchemaValidator(Validator):
    name = "schema"

    def __init__(self, app: dict):
        super().__init__()
        self.app = app

    def validate(self) -> None:
        for field in REQUIRED_APP_STRING_FIELDS:
            if not self.app.get(field):
                self.fail(f"app missing '{field}'")

        if not self.app.get("categories"):
            self.fail("app missing 'categories' (must be a non-empty list)")

        if not isinstance(self.app.get("stars"), int):
            self.fail("app missing 'stars' (must be an integer - 0 is fine)")

        expected = f"apps/{self.app.get('name')}.json"
        if self.app.get("releases_path") != expected:
            self.fail(f"app must set 'releases' to {expected!r} - that is the only path pilot will read")

        releases = self.app.get("releases")
        if not releases:
            self.fail(f"{expected} has no releases — add at least one release")
            return

        for index, release in enumerate(releases):
            self._validate_release(index, release)

    def _validate_release(self, index: int, release: dict) -> None:
        for field in REQUIRED_RELEASE_FIELDS:
            if not release.get(field):
                self.fail(f"release {index} missing '{field}'")

        if not isinstance(release.get("dependencies"), dict):
            self.fail(f"release {index} missing 'dependencies' (must be an object - {{}} is fine)")
