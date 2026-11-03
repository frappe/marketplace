#!/usr/bin/env python3
"""
Decide whether a publish PR may merge without a reviewer.

Only the change tools/add_release.py makes, by apps of a trusted GitHub
organisation, qualifies: the PR comes from a `publish/` branch, changes
exactly one apps/<app>.json, and in it only adds releases or replaces one with
the same version and branch. The marketplace check validates exactly those
(a new version, branch and commit), so an in-place edit of a release's other
fields, or a removed release, which it never checks, waits for a person. The
app must already be listed, and its owner is read from the base registry, so
a PR cannot make itself eligible by editing its own repo URL.

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
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
from utils.diff import release_identity

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
    base_payload, head_payload = read_json(base / changed_files[0]), read_json(head / changed_files[0])
    if without_releases(base_payload) != without_releases(head_payload):
        blockers.append(f"it changes more than the releases in {changed_files[0]}")
    blockers += get_release_blockers(base_payload.get("releases", []), head_payload.get("releases", []))
    return blockers


def get_release_blockers(base: list[dict], head: list[dict]) -> list[str]:
    """Releases changed in a way the check does not validate: every new or changed release
    must have a new identity, and a release may only go away when one with its version and
    branch takes its place, as add_release does."""
    base_identities = {release_identity(release) for release in base}
    unchecked = [r for r in head if r not in base and release_identity(r) in base_identities]
    replaced = {(r.get("version"), r.get("branch")) for r in head}
    removed = [r for r in base if r not in head and (r.get("version"), r.get("branch")) not in replaced]
    blockers = [f"it edits release {describe(r)} in place, which the check does not validate" for r in unchecked]
    blockers += [f"it removes release {describe(r)}" for r in removed]
    return blockers


def describe(release: dict) -> str:
    return f"{release.get('version')} on {release.get('branch')} at {str(release.get('commit'))[:8]}"


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
