#!/usr/bin/env python3
"""
Validate the structural integrity of an app that is new or changed in
apps.json — catches malformed entries (missing name/repo, no targets, or
a target missing required fields) early in CI, before any clone/scan
work runs for any of its targets.

Only apps that differ from the base revision are checked, so pre-existing
registry entries left untouched by a PR don't fail unrelated checks.

Checks per new/changed app:
  1. "name" and "repo" are present
  2. "targets" is a non-empty list
  3. each target has "version", "target_type", and "target"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils.base import Validator

REQUIRED_TARGET_FIELDS = ("version", "target_type", "target")


class SchemaValidator(Validator):
    name = "schema"

    def __init__(self, app: dict):
        super().__init__()
        self.app = app

    def validate(self) -> None:
        if not self.app.get("name"):
            self.fail("app missing 'name'")
        if not self.app.get("repo"):
            self.fail("app missing 'repo'")

        targets = self.app.get("targets")
        if not targets:
            self.fail("app has no targets — add at least one target")
            return

        for index, target in enumerate(targets):
            for field in REQUIRED_TARGET_FIELDS:
                if not target.get(field):
                    self.fail(f"target {index} missing '{field}'")
