#!/usr/bin/env python3
"""
Automatically detect and add new releases from GitHub for all apps.

For each app in apps.json:
  1. Query GitHub for commits on tracked branches
  2. Fetch pyproject.toml from each latest commit
  3. Compare versions with existing releases in apps/<app>.json
  4. Add any new releases found (by commit SHA or version+branch)

Supports public apps without authentication. For private repos or higher
rate limits, set GITHUB_TOKEN environment variable.

Usage:
    python3 tools/fetch_latest_versions.py
    python3 tools/fetch_latest_versions.py --dry-run
    python3 tools/fetch_latest_versions.py --dry-run --app helpdesk
    GITHUB_TOKEN=ghp_... python3 tools/fetch_latest_versions.py  # optional
"""

from __future__ import annotations

import argparse
import json
import os
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from packaging.version import Version, InvalidVersion

ROOT = Path(__file__).parent.parent
REGISTRY = ROOT / "apps.json"
APPS_DIR = ROOT / "apps"
TRACKED_BRANCHES = ["main", "develop", "version-16", "version-15", "master"]
NIGHTLY_BRANCHES = ("develop",)
FRAPPE_KEY = "frappe"
REQUEST_TIMEOUT = 10
MAX_WORKERS = 4
MAX_RETRIES = 2

Log: TypeAlias = list[str]


class GitHubClient:
    """Fetch data from GitHub API."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github.v3.raw"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def fetch_raw(self, url: str, log: Log, attempt: int = 1) -> bytes | None:
        """Fetch raw bytes from URL with retry logic."""
        req = Request(url, headers=self._headers())
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.read()
        except HTTPError as error:
            if error.code == 404:
                return None
            if error.code in (403, 429):
                log.append("rate limited — sleeping 60s")
                time.sleep(60)
                return self.fetch_raw(url, log, attempt)
            log.append(f"HTTP {error.code}: {url}")
            return None
        except (URLError, OSError) as error:
            if attempt < MAX_RETRIES:
                log.append(f"connection error ({error}) — retrying")
                return self.fetch_raw(url, log, attempt + 1)
            log.append(f"connection error, giving up: {error}")
            return None

    def fetch_pyproject(self, repo_url: str, branch: str, log: Log) -> dict | None:
        """Fetch pyproject.toml from a branch."""
        owner_repo = repo_url.removeprefix("https://github.com/").rstrip("/")
        url = f"https://api.github.com/repos/{owner_repo}/contents/pyproject.toml?ref={branch}"
        raw = self.fetch_raw(url, log)
        if raw is None:
            return None
        try:
            return tomllib.loads(raw.decode())
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
            log.append(f"parse error on {branch}: {error}")
            return None

    def resolve_branch_to_commit(self, repo_url: str, branch: str, log: Log) -> str | None:
        """Get the latest commit SHA on a branch."""
        owner_repo = repo_url.removeprefix("https://github.com/").rstrip("/")
        url = f"https://api.github.com/repos/{owner_repo}/commits?sha={branch}&per_page=1"
        raw = self.fetch_raw(url, log)
        if raw is None:
            return None
        try:
            commits = json.loads(raw.decode())
            if isinstance(commits, list) and len(commits) > 0:
                return commits[0].get("sha")
            return None
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as error:
            log.append(f"failed to parse commits for {branch}: {error}")
            return None


def extract_version(toml: dict) -> str:
    """Extract version from pyproject.toml."""
    project = toml.get("project", {})
    return project.get("version", "")


def extract_frappe_core(toml: dict) -> str:
    """Extract Frappe core version requirement from pyproject.toml."""
    bench_dependencies = toml.get("tool", {}).get("bench", {}).get("frappe-dependencies", {})
    return bench_dependencies.get(FRAPPE_KEY, "")


def extract_dependencies(toml: dict) -> dict:
    """Extract marketplace app dependencies from pyproject.toml."""
    bench_dependencies = toml.get("tool", {}).get("bench", {}).get("frappe-dependencies", {})
    return {name: spec for name, spec in bench_dependencies.items() if name != FRAPPE_KEY}


def is_commit_already_released(app_name: str, commit: str) -> bool:
    """Check if a commit is already advertised in apps/<app>.json."""
    path = APPS_DIR / f"{app_name}.json"
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text())
        releases = payload.get("releases", [])
        return any(r.get("commit") == commit for r in releases)
    except (json.JSONDecodeError, OSError):
        return False


def sort_key(release: dict) -> tuple:
    """Sort key for releases: newest first."""
    try:
        return (Version(release["version"]),)
    except (InvalidVersion, KeyError):
        return (Version("0"),)


def get_new_releases(
    app_name: str, repo_url: str, client: GitHubClient, log: Log
) -> list[dict]:
    """
    For each tracked branch:
      1. Get latest commit
      2. Fetch pyproject.toml
      3. Check if already released
      4. If new: create release entry
    Returns list of new release dicts.
    """
    new_releases = []

    for branch in TRACKED_BRANCHES:
        commit = client.resolve_branch_to_commit(repo_url, branch, log)
        if not commit:
            log.append(f"  {branch}: no commits found")
            continue

        # Check if this commit is already advertised
        if is_commit_already_released(app_name, commit):
            log.append(f"  {branch}: commit {commit[:8]} already released")
            continue

        # Fetch metadata
        toml = client.fetch_pyproject(repo_url, branch, log)
        if not toml:
            log.append(f"  {branch}: could not fetch pyproject.toml")
            continue

        # Extract version and dependencies
        version = extract_version(toml)
        frappe_core = extract_frappe_core(toml)
        dependencies = extract_dependencies(toml)

        if not version or not frappe_core:
            log.append(f"  {branch}: missing version or frappe_core")
            continue

        # Build release entry
        release = {
            "version": version,
            "branch": branch,
            "commit": commit,
            "frappe_core": frappe_core,
            "dependencies": dependencies,
            "channel": "nightly" if branch in NIGHTLY_BRANCHES else "stable",
        }

        new_releases.append(release)
        log.append(f"  {branch}: v{version} @ {commit[:8]}")

    return new_releases


def update_app_releases(app_name: str, new_releases: list[dict]) -> bool:
    """
    Add new releases to apps/<app>.json.
    Returns True if changes were made.
    """
    path = APPS_DIR / f"{app_name}.json"

    if not path.exists():
        return False

    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    releases = payload.setdefault("releases", [])
    changes_made = False

    for release in new_releases:
        # Check if exact commit already exists
        if any(r.get("commit") == release["commit"] for r in releases):
            continue

        # Check if (version, branch) exists and replace it (version bump on same branch)
        existing_idx = next(
            (
                i
                for i, r in enumerate(releases)
                if (r.get("version"), r.get("branch"))
                == (release["version"], release["branch"])
            ),
            None,
        )

        if existing_idx is not None:
            releases[existing_idx] = release
        else:
            releases.append(release)

        changes_made = True

    if changes_made:
        # Sort newest first
        releases.sort(key=sort_key, reverse=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    return changes_made


def process_app(
    app: dict, client: GitHubClient, dry_run: bool = False
) -> tuple[str, bool, Log]:
    """
    Process a single app.
    Returns (app_name, changes_made, log_lines).
    """
    app_name = app["name"]
    log: Log = []

    if not app.get("repo"):
        return app_name, False, log

    if app.get("skip_auto_publish"):
        log.append("skipped (skip_auto_publish)")
        return app_name, False, log

    try:
        new_releases = get_new_releases(app_name, app["repo"], client, log)

        if new_releases:
            if dry_run:
                log.insert(0, f"Would add {len(new_releases)} release(s):")
                changes_made = False
            else:
                changes_made = update_app_releases(app_name, new_releases)
                if changes_made:
                    log.insert(0, f"✓ Added {len(new_releases)} release(s)")
                else:
                    log.insert(0, "No changes (all releases already present)")
        else:
            log.insert(0, "No new releases")
            changes_made = False

    except Exception as e:
        log.insert(0, f"✗ Error: {e}")
        changes_made = False

    return app_name, changes_made, log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Show changes without writing"
    )
    parser.add_argument("--app", type=str, help="Process only this app (optional)")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    client = GitHubClient(token)

    try:
        apps = json.loads(REGISTRY.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Failed to read {REGISTRY}: {e}")
        return 1

    if not isinstance(apps, list):
        print(f"Expected apps.json to be an array, got {type(apps)}")
        return 1

    # Filter apps
    if args.app:
        apps = [a for a in apps if a.get("name") == args.app]
        if not apps:
            print(f"App '{args.app}' not found in registry")
            return 1

    print(f"Processing {len(apps)} app(s)...\n")

    total_changes = 0
    mode = "DRY RUN" if args.dry_run else "LIVE"

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_app, app, client, args.dry_run): app["name"]
            for app in apps
        }

        for future in as_completed(futures):
            app_name, changes_made, log = future.result()

            if log:
                print(f"{app_name}:")
                for line in log:
                    print(f"  {line}")
                if changes_made:
                    total_changes += 1
            else:
                print(f"{app_name}: skipped")
            print()

    print(f"\n{mode} Summary:")
    print(f"  Apps with changes: {total_changes}")
    print(f"  Total apps processed: {len(apps)}")

    return 0


if __name__ == "__main__":
    exit(main())
