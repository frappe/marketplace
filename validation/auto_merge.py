#!/usr/bin/env python3
"""
Decide whether a registry PR may merge without a reviewer, from anyone, for
any app, once the marketplace check has passed.

Only changes the check validates qualify: the PR touches nothing but
apps.json and apps/<app>.json, and per app it either adds a new app or adds
releases to a listed one (or replaces a release with the same version and
branch, as tools/add_release.py does). A listed app's apps.json entry is
unchanged, a new app does not claim the Featured category, each new
release's commit must be ahead of every release already advertised on its
branch, and no release is edited in place or removed. Everything else waits for a person.

Reads the two registries as data only; nothing from the PR runs.

Usage:
    python3 validation/auto_merge.py <base registry> <head registry> --changed-files changed.txt

Prints why a PR is held, and writes mergeable=true|false to $GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).parent))
from utils.diff import release_identity

REGISTRY_FILE = re.compile(r"apps\.json|apps/([a-z][a-z0-9_]*)\.json")
FORWARD_ORDERS = ("ahead", "identical")
FEATURED = "Featured"

# (repo URL, advertised commit, new commit) -> GitHub's compare status: ahead, behind, diverged or identical.
CommitOrder = Callable[[str, str, str], str]


def get_merge_blockers(base: Path, head: Path, changed_files: list[str], get_commit_order: CommitOrder) -> list[str]:
    """Why the PR needs a reviewer; empty when it may merge on its own."""
    outside = [path for path in changed_files if not REGISTRY_FILE.fullmatch(path)]
    if outside or not changed_files:
        return [f"it changes {outside or 'nothing'} outside apps.json and apps/<app>.json"]

    head_entries = read_json(head / "apps.json")
    base_index, head_index = index_by_name(read_json(base / "apps.json")), index_by_name(head_entries)
    blockers = get_index_blockers(base_index, head_index, head_entries)
    for path in changed_files:
        if name := REGISTRY_FILE.fullmatch(path).group(1):
            blockers += get_releases_file_blockers(base, head, name, base_index, head_index, get_commit_order)
    return blockers


def get_index_blockers(base_index: dict, head_index: dict, head_entries: list[dict]) -> list[str]:
    """A listed app's entry stays as it is, since the check validates none of its fields,
    and a new app does not feature itself."""
    blockers = [f"apps.json lists {name} more than once" for name, count in count_names(head_entries) if count > 1]
    for name, entry in base_index.items():
        if name not in head_index:
            blockers.append(f"it removes {name} from apps.json")
        elif changed := get_changed_fields(entry, head_index[name]):
            blockers.append(f"it changes {name}'s {', '.join(changed)} in apps.json")
    blockers += [
        f"new app {name} claims the {FEATURED} category"
        for name, entry in head_index.items()
        if name not in base_index and FEATURED in (entry.get("categories") or [])
    ]
    return blockers


def get_releases_file_blockers(
    base: Path, head: Path, name: str, base_index: dict, head_index: dict, get_commit_order: CommitOrder
) -> list[str]:
    path = f"apps/{name}.json"
    if not (head / path).is_file():
        return [f"it deletes {path}"]
    if name not in head_index:
        return [f"{path} belongs to no app in apps.json"]
    if name not in base_index:
        return []  # A new app: the check validated every one of its releases.

    base_payload, head_payload = read_json(base / path), read_json(head / path)
    if without_releases(base_payload) != without_releases(head_payload):
        return [f"it changes more than the releases in {path}"]
    base_releases, head_releases = base_payload.get("releases", []), head_payload.get("releases", [])
    blockers = get_release_blockers(base_releases, head_releases)
    blockers += get_downgrade_blockers(base_index[name]["repo"], base_releases, head_releases, get_commit_order)
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


def get_downgrade_blockers(repo: str, base: list[dict], head: list[dict], get_commit_order: CommitOrder) -> list[str]:
    """Each new release must move its branch forward from every release advertised on it."""
    base_identities = {release_identity(release) for release in base}
    blockers = []
    for release in (r for r in head if release_identity(r) not in base_identities):
        for advertised in (r for r in base if r.get("branch") == release.get("branch")):
            order = get_commit_order(repo, advertised["commit"], release["commit"])
            if order not in FORWARD_ORDERS:
                blockers.append(
                    f"release {describe(release)} is not ahead of the advertised {describe(advertised)} ({order})"
                )
    return blockers


def get_github_commit_order(repo: str, base_commit: str, head_commit: str) -> str:
    """GitHub's compare status of head_commit against base_commit."""
    owner_and_name = get_github_repo(repo)
    if not owner_and_name:
        return "not a github.com repo, so the commits cannot be compared"
    url = f"https://api.github.com/repos/{owner_and_name}/compare/{quote(base_commit, safe='')}...{quote(head_commit, safe='')}"
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return json.load(response)["status"]


def get_github_repo(repo: str) -> str:
    """'owner/name' of a github.com repo URL, or '' for any other host."""
    url = urlparse(repo)
    parts = url.path.strip("/").removesuffix(".git").split("/")
    if url.scheme != "https" or url.hostname != "github.com" or len(parts) != 2 or not all(parts):
        return ""
    return "/".join(parts)


def get_changed_fields(base: dict, head: dict) -> list[str]:
    return sorted(key for key in base.keys() | head.keys() if base.get(key) != head.get(key))


def describe(release: dict) -> str:
    return f"{release.get('version')} on {release.get('branch')} at {str(release.get('commit'))[:8]}"


def count_names(entries: list[dict]) -> list[tuple[str, int]]:
    return Counter(entry.get("name") for entry in entries).most_common()


def index_by_name(entries: list[dict]) -> dict[str, dict]:
    return {entry.get("name"): entry for entry in entries}


def without_releases(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "releases"}


def read_json(path: Path):
    return json.loads(path.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base", type=Path, help="registry at the PR's base branch")
    parser.add_argument("head", type=Path, help="registry at the PR's head commit")
    parser.add_argument("--changed-files", type=Path, required=True, help="file listing the PR's changed paths")
    args = parser.parse_args()

    changed_files = args.changed_files.read_text().split()
    blockers = get_merge_blockers(args.base, args.head, changed_files, get_github_commit_order)
    for blocker in blockers:
        print(f"Held for review: {blocker}.")
    if not blockers:
        print("Registry change the check validated: merging.")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as file:
            file.write(f"mergeable={'false' if blockers else 'true'}\n")


if __name__ == "__main__":
    main()
