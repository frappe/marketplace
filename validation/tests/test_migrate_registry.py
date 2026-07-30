"""Tests for the one-shot registry split."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))
from migrate_registry import assign_channels


def release(branch: str) -> dict:
    return {"version": "1.0.0", "branch": branch, "commit": "a" * 40}


def channels(*branches: str) -> list[str]:
    releases = [release(branch) for branch in branches]
    assign_channels(releases)
    return [r["channel"] for r in releases]


def test_develop_is_nightly_when_the_app_also_cuts_releases() -> None:
    assert channels("version-16", "version-15", "develop") == ["stable", "stable", "nightly"]


def test_develop_only_app_is_stable() -> None:
    """telephony's single branch is the line every bench runs, not a dev build."""
    assert channels("develop") == ["stable"]


def test_main_and_develop_keeps_develop_nightly() -> None:
    assert channels("main", "develop") == ["stable", "nightly"]


def test_release_branches_only_are_all_stable() -> None:
    assert channels("version-16", "version-15") == ["stable", "stable"]
