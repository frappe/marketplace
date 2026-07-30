#!/usr/bin/env python3
"""
Add one release to apps/<app>.json, reading the version, Frappe range and
dependencies from the app checkout's pyproject.toml.

Used by .github/workflows/publish-release.yml, and runnable by hand when an
owner would rather open the PR themselves. It only edits the file; the
marketplace checks still gate the merge.

Usage:
    APP=helpdesk BRANCH=main COMMIT=<sha> CHANNEL=stable \\
        python3 tools/add_release.py --app-dir app --registry .
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path

COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
CHANNELS = ("stable", "nightly")
FRAPPE_KEY = "frappe"


def read_metadata(app_dir: Path) -> tuple[str, str, dict]:
    """(version, frappe_core, dependencies) as declared by the checkout."""
    toml = tomllib.loads((app_dir / "pyproject.toml").read_text())
    project = toml.get("project", {})
    version = project.get("version") or dynamic_version(app_dir, project.get("name", ""))
    bench_dependencies = toml.get("tool", {}).get("bench", {}).get("frappe-dependencies", {})
    dependencies = {name: spec for name, spec in bench_dependencies.items() if name != FRAPPE_KEY}
    return version, bench_dependencies.get(FRAPPE_KEY, ""), dependencies


def dynamic_version(app_dir: Path, module: str) -> str:
    init = app_dir / module / "__init__.py"
    if not module or not init.is_file():
        return ""
    for line in init.read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[-1].strip().strip("\"'")
    return ""


def sort_key(release: dict) -> tuple:
    """Newest first, matching how pilot reads the file."""
    from packaging.version import InvalidVersion, Version

    try:
        return (Version(release["version"]),)
    except InvalidVersion:
        return (Version("0"),)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True, help="checkout of the app at the commit")
    parser.add_argument("--registry", type=Path, required=True, help="registry checkout")
    args = parser.parse_args()

    app, branch = os.environ["APP"], os.environ["BRANCH"]
    commit, channel = os.environ["COMMIT"], os.environ.get("CHANNEL", "stable")
    if not COMMIT_SHA.fullmatch(commit):
        sys.exit(f"COMMIT must be a full 40-character SHA, got {commit!r}")
    if channel not in CHANNELS:
        sys.exit(f"CHANNEL must be one of {CHANNELS}, got {channel!r}")

    version, frappe_core, dependencies = read_metadata(args.app_dir)
    if not version or not frappe_core:
        sys.exit(
            f"{app} declares version={version!r} and "
            f"[tool.bench.frappe-dependencies].frappe={frappe_core!r} - both are required"
        )

    path = args.registry / "apps" / f"{app}.json"
    if not path.is_file():
        sys.exit(f"{path} does not exist - add the app to apps.json first")
    payload = json.loads(path.read_text())
    releases = payload.setdefault("releases", [])

    release = {
        "version": version,
        "branch": branch,
        "commit": commit,
        "frappe_core": frappe_core,
        "dependencies": dependencies,
        "channel": channel,
    }
    if any(r.get("commit") == commit for r in releases):
        print(f"{app}@{commit[:8]} is already advertised.")
        return

    # A branch carries one release per version, so re-advertising a version at a
    # newer commit replaces that entry. Apps with a fixed version (telephony sits
    # at 0.0.1) always take this path.
    existing = next(
        (r for r in releases if (r.get("version"), r.get("branch")) == (version, branch)), None
    )
    if existing is not None:
        releases[releases.index(existing)] = release
    else:
        releases.append(release)
    releases.sort(key=sort_key, reverse=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Added {app} {version} ({channel}) at {commit[:8]} on {branch}.")


if __name__ == "__main__":
    main()
