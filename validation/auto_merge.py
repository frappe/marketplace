#!/usr/bin/env python3
"""
Decide whether a publish PR may merge without a reviewer.

Only release-only changes by apps of a trusted GitHub organisation qualify:
the PR comes from a `publish/` branch, changes exactly one apps/<app>.json,
and changes nothing in it but `releases`. The app must already be listed, and
its owner is read from the base registry, so a PR cannot make itself
eligible by editing its own repo URL. Every other PR waits for a person.

Reads the two registries as data only; nothing from the PR runs.

Usage:
    python3 validation/auto_merge.py <base registry> <head registry> \\
        --branch publish/lms-develop --changed-files changed.txt

Prints why a PR is held, and writes mergeable=true|false to $GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

TRUSTED_OWNERS = ("frappe",)
PUBLISH_BRANCH_PREFIX = "publish/"
RELEASES_FILE = re.compile(r"apps/([a-z][a-z0-9_]*)\.json")


def get_merge_blockers(base: Path, head: Path, changed_files: list[str], branch: str) -> list[str]:
    """Why the PR needs a reviewer; empty when it may merge on its own."""
    if not branch.startswith(PUBLISH_BRANCH_PREFIX):
        return [f"{branch!r} is not a publish branch"]
    match = RELEASES_FILE.fullmatch(changed_files[0]) if len(changed_files) == 1 else None
    if not match:
        return [f"it changes {changed_files}, not exactly one apps/<app>.json"]

    name = match.group(1)
    app = {entry["name"]: entry for entry in read_json(base / "apps.json")}.get(name)
    if app is None:
        return [f"{name} is not listed yet; a new app needs a reviewer"]

    blockers = []
    owner = get_github_owner(app.get("repo", ""))
    if owner not in TRUSTED_OWNERS:
        blockers.append(f"{name} is hosted by {owner or app.get('repo')!r}, not {' or '.join(TRUSTED_OWNERS)}")
    if without_releases(read_json(base / changed_files[0])) != without_releases(read_json(head / changed_files[0])):
        blockers.append(f"it changes more than the releases in {changed_files[0]}")
    return blockers


def get_github_owner(repo: str) -> str:
    """The lowercased owner of a github.com repo URL, or '' for any other host."""
    url = urlparse(repo)
    parts = url.path.strip("/").split("/")
    if url.scheme != "https" or url.hostname != "github.com" or len(parts) != 2:
        return ""
    return parts[0].lower()


def without_releases(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "releases"}


def read_json(path: Path):
    return json.loads(path.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base", type=Path, help="registry at the PR's base branch")
    parser.add_argument("head", type=Path, help="registry at the PR's head commit")
    parser.add_argument("--branch", required=True, help="the PR's head branch")
    parser.add_argument("--changed-files", type=Path, required=True, help="file listing the PR's changed paths")
    args = parser.parse_args()

    changed_files = args.changed_files.read_text().split()
    blockers = get_merge_blockers(args.base, args.head, changed_files, args.branch)
    for blocker in blockers:
        print(f"Held for review: {blocker}.")
    if not blockers:
        print("Release-only change by a trusted app: merging.")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as file:
            file.write(f"mergeable={'false' if blockers else 'true'}\n")


if __name__ == "__main__":
    main()
