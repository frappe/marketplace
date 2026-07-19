#!/usr/bin/env python3
"""
Validate the structural integrity of an app that is new or changed in
apps.json — catches malformed or incomplete entries (missing metadata,
no targets, a target missing required fields) early in CI, before any
clone/scan work runs for any of its targets.

Only apps that differ from the base revision are checked, so pre-existing
registry entries left untouched by a PR don't fail unrelated checks.

Checks per new/changed app:
  1. every required app-level field is present with a value (see
     REQUIRED_APP_STRING_FIELDS, plus "categories" and "stars" below)
  2. "targets" is a non-empty list
  3. each target has every required field (see REQUIRED_TARGET_STRING_FIELDS),
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
REQUIRED_TARGET_STRING_FIELDS = ("version", "target_type", "target", "frappe_core")


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

        targets = self.app.get("targets")
        if not targets:
            self.fail("app has no targets — add at least one target")
            return

        for index, target in enumerate(targets):
            self._validate_target(index, target)

    def _validate_target(self, index: int, target: dict) -> None:
        for field in REQUIRED_TARGET_STRING_FIELDS:
            if not target.get(field):
                self.fail(f"target {index} missing '{field}'")

        if not isinstance(target.get("dependencies"), dict):
            self.fail(f"target {index} missing 'dependencies' (must be an object - {{}} is fine)")
