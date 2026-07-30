"""Tests for loading a registry directory and finding changed releases."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.diff import find_changed_releases, load_registry

RELEASE = {
    "version": "1.27.0",
    "branch": "main",
    "commit": "a" * 40,
    "frappe_core": ">=15.0.0,<17.0.0",
    "dependencies": {},
    "channel": "stable",
}


def write_registry(root: Path, releases: list[dict], repo: str = "https://github.com/frappe/helpdesk") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "apps").mkdir(exist_ok=True)
    index = [{"name": "helpdesk", "repo": repo, "releases": "apps/helpdesk.json"}]
    (root / "apps.json").write_text(json.dumps(index))
    (root / "apps" / "helpdesk.json").write_text(json.dumps({"name": "helpdesk", "releases": releases}))
    return root


def test_load_registry_inlines_releases_and_keeps_the_pointer(tmp_path: Path) -> None:
    apps = load_registry(write_registry(tmp_path / "new", [RELEASE]))

    assert apps["helpdesk"]["releases"] == [RELEASE]
    assert apps["helpdesk"]["releases_path"] == "apps/helpdesk.json"


def test_only_the_added_release_is_scanned(tmp_path: Path) -> None:
    added = {**RELEASE, "version": "1.28.0", "commit": "b" * 40}
    old = load_registry(write_registry(tmp_path / "old", [RELEASE]))
    new = load_registry(write_registry(tmp_path / "new", [added, RELEASE]))

    changed = find_changed_releases(old, new)

    assert [release["commit"] for release in changed] == ["b" * 40]


def test_republishing_the_same_version_at_a_new_commit_is_scanned(tmp_path: Path) -> None:
    moved = {**RELEASE, "commit": "c" * 40}
    old = load_registry(write_registry(tmp_path / "old", [RELEASE]))
    new = load_registry(write_registry(tmp_path / "new", [moved]))

    assert [release["commit"] for release in find_changed_releases(old, new)] == ["c" * 40]


def test_untouched_releases_are_not_scanned(tmp_path: Path) -> None:
    old = load_registry(write_registry(tmp_path / "old", [RELEASE]))
    new = load_registry(write_registry(tmp_path / "new", [RELEASE]))

    assert find_changed_releases(old, new) == []


def test_a_changed_repo_rescans_every_release(tmp_path: Path) -> None:
    old = load_registry(write_registry(tmp_path / "old", [RELEASE]))
    new = load_registry(
        write_registry(tmp_path / "new", [RELEASE], repo="https://github.com/someone/helpdesk")
    )

    assert len(find_changed_releases(old, new)) == 1


def test_a_new_app_scans_every_release(tmp_path: Path) -> None:
    new = load_registry(write_registry(tmp_path / "new", [RELEASE]))

    assert len(find_changed_releases({}, new)) == 1
