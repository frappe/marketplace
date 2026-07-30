#!/usr/bin/env python3
"""
Find releases that changed between two revisions of the registry, so only
those need a fresh scan — not the whole registry.

A release is identified by (version, branch, commit); the commit makes that
key unique, so a changed release is simply one the base revision never
advertised. If the app is new or its repo changed, the code location itself
moved, so every release is emitted even when the entries are identical.

load_registry() reads a registry directory (apps.json plus apps/<name>.json)
into one dict per app: the index entry, its releases inlined under
"releases", and the raw index pointer kept as "releases_path" so the schema
check can validate it.

Output: JSON list of {name, repo, version, branch, commit, ...} items.

Run:
    python3 validation/utils/diff.py <old-registry-dir> <new-registry-dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_registry(root: Path) -> dict[str, dict]:
    index = json.loads((root / "apps.json").read_text())
    return {app["name"]: _with_releases(root, app) for app in index}


def _with_releases(root: Path, app: dict) -> dict:
    pointer = app.get("releases")
    path = root / "apps" / f"{app['name']}.json"
    releases = json.loads(path.read_text()).get("releases", []) if path.exists() else []
    return {**app, "releases": releases, "releases_path": pointer}


def release_identity(release: dict) -> tuple:
    return (release.get("version"), release.get("branch"), release.get("commit"))


def changed_releases(old_app: dict, app: dict) -> list[dict]:
    published = {release_identity(release) for release in old_app.get("releases", [])}
    return [r for r in app.get("releases", []) if release_identity(r) not in published]


def find_changed_releases(old_apps: dict[str, dict], new_apps: dict[str, dict]) -> list[dict]:
    changed = []
    for name, app in new_apps.items():
        old_app = old_apps.get(name)
        if old_app is None or old_app.get("repo") != app.get("repo"):
            releases = app.get("releases", [])
        else:
            releases = changed_releases(old_app, app)
        changed.extend({"name": name, "repo": app["repo"], **release} for release in releases)

    return changed


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: validation/utils/diff.py <old-registry-dir> <new-registry-dir>", file=sys.stderr)
        sys.exit(1)

    old_apps = load_registry(Path(sys.argv[1]))
    new_apps = load_registry(Path(sys.argv[2]))
    print(json.dumps(find_changed_releases(old_apps, new_apps), indent=2))


if __name__ == "__main__":
    main()
