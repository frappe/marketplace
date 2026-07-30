#!/usr/bin/env python3
"""
Split the flat apps.json into a metadata index plus one commit-scoped release
file per app.

For every app, fetches pyproject.toml across the known Frappe branches,
resolves each branch to its current tip commit, and writes the releases to
apps/<name>.json; apps.json keeps only metadata and a "releases" pointer.
Apps with no resolvable release are dropped.

This is a one-shot migration, not a cron job: after it runs, releases only
change through an app owner's PR, which is what makes every advertised
commit one the marketplace checks have seen.

Usage:
    GITHUB_TOKEN=ghp_... python3 tools/migrate_registry.py
    GITHUB_TOKEN=ghp_... python3 tools/migrate_registry.py --limit 5 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from packaging.version import Version, InvalidVersion

ROOT = Path(__file__).parent.parent
REGISTRY = ROOT / "apps.json"
APPS_DIR = ROOT / "apps"
NIGHTLY_BRANCHES = ("develop",)
PILOT_BRANCHES = ["version-16", "version-15", "develop", "main", "master"]
FRAPPE_KEY = "frappe"
REQUEST_TIMEOUT = 10
MAX_WORKERS = 8
MAX_RETRIES = 2

# Log lines are collected per app, not printed directly — worker threads
# would otherwise interleave output into an unreadable mess.
Log = list[str]


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github.v3.raw"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def fetch_raw(self, url: str, log: Log, attempt: int = 1) -> bytes | None:
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
            # Covers DNS failures, timeouts, and transient connection drops
            # (e.g. RemoteDisconnected) — retry a couple of times before
            # giving up on this one URL, rather than crashing the whole run.
            if attempt < MAX_RETRIES:
                log.append(f"connection error ({error}) — retrying")
                return self.fetch_raw(url, log, attempt + 1)
            log.append(f"connection error, giving up: {error}")
            return None

    def fetch_pyproject(self, repo_url: str, branch: str, log: Log) -> dict | None:
        owner_repo = repo_url.removeprefix("https://github.com/").rstrip("/")
        url = f"https://api.github.com/repos/{owner_repo}/contents/pyproject.toml?ref={branch}"
        raw = self.fetch_raw(url, log)
        if raw is None:
            return None
        try:
            return tomllib.loads(raw.decode())
        except tomllib.TOMLDecodeError as error:
            log.append(f"TOML parse error on {branch}: {error}")
            return None

    def repo_exists(self, repo_url: str, log: Log, attempt: int = 1) -> bool:
        owner_repo = repo_url.removeprefix("https://github.com/").rstrip("/")
        url = f"https://api.github.com/repos/{owner_repo}"
        req = Request(url, headers=self._headers())
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT):
                return True
        except HTTPError as error:
            if error.code == 404:
                return False
            if error.code in (403, 429):
                log.append("rate limited — sleeping 60s")
                time.sleep(60)
                return self.repo_exists(repo_url, log, attempt)
            return True  # assume exists on other errors
        except (URLError, OSError) as error:
            if attempt < MAX_RETRIES:
                return self.repo_exists(repo_url, log, attempt + 1)
            log.append(f"connection error checking repo, assuming it exists: {error}")
            return True

    def fetch_dynamic_version(self, repo_url: str, branch: str, app_name: str, log: Log) -> str | None:
        """Read __version__ from {app_name}/__init__.py for apps using dynamic versioning."""
        owner_repo = repo_url.removeprefix("https://github.com/").rstrip("/")
        url = f"https://api.github.com/repos/{owner_repo}/contents/{app_name}/__init__.py?ref={branch}"
        raw = self.fetch_raw(url, log)
        if raw is None:
            return None
        for line in raw.decode().splitlines():
            if line.startswith("__version__"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip("\"'")


def _is_dynamic_version(toml: dict) -> bool:
    return "version" in toml.get("project", {}).get("dynamic", [])


def branch_sha(repo_url: str, branch: str, log: Log) -> str | None:
    """The branch's current tip commit, straight from the remote."""
    result = subprocess.run(
        ["git", "ls-remote", repo_url, f"refs/heads/{branch}"], capture_output=True, text=True
    )
    if result.returncode != 0:
        log.append(f"{branch}: ls-remote failed — {result.stderr.strip()}")
        return None
    line = result.stdout.strip().splitlines()
    return line[0].split("\t", 1)[0] if line else None


def parse_release(toml: dict, branch: str, dynamic_version: str | None = None) -> dict | None:
    project = toml.get("project", {})
    version = project.get("version") or dynamic_version
    if not version:
        return None

    bench_deps: dict = (
        toml.get("tool", {}).get("bench", {}).get("frappe-dependencies", {})
    )
    frappe_core = bench_deps.get(FRAPPE_KEY)
    dependencies = {k: v for k, v in bench_deps.items() if k != FRAPPE_KEY}

    return {
        "version": version,
        "branch": branch,
        "commit": "",  # filled in from the branch tip once the release is kept
        "frappe_core": frappe_core,
        "dependencies": dependencies,
        "channel": "stable",  # set once every branch is known - see assign_channels()
    }


def sort_key(release: dict) -> Version:
    try:
        return Version(release["version"])
    except InvalidVersion:
        return Version("0")


def assign_channels(releases: list[dict]) -> None:
    """Mark develop releases nightly only when the app also cuts releases elsewhere.

    An app whose only branch is develop (e.g. telephony) publishes one code line
    that every bench runs, so calling it nightly would make every stable bench
    look like it fell back to a dev build.
    """
    cuts_releases = any(release["branch"] not in NIGHTLY_BRANCHES for release in releases)
    for release in releases:
        nightly = cuts_releases and release["branch"] in NIGHTLY_BRANCHES
        release["channel"] = "nightly" if nightly else "stable"


def build_releases(repo_url: str, client: GitHubClient, log: Log) -> list[dict]:
    releases = []

    for branch in PILOT_BRANCHES:
        toml = client.fetch_pyproject(repo_url, branch, log)
        if toml is None:
            log.append(f"{branch}: not found")
            continue

        dynamic_version = None
        if _is_dynamic_version(toml):
            app_name = toml.get("project", {}).get("name", "")
            dynamic_version = client.fetch_dynamic_version(repo_url, branch, app_name, log)

        release = parse_release(toml, branch, dynamic_version)
        if release is None:
            log.append(f"{branch}: no version field")
            continue
        if not release["frappe_core"]:
            log.append(f"{branch}: no frappe declared in [tool.bench.frappe-dependencies] — skipping")
            continue
        commit = branch_sha(repo_url, branch, log)
        if not commit:
            log.append(f"{branch}: could not resolve a commit — skipping")
            continue
        release["commit"] = commit
        releases.append(release)
        log.append(f"{branch}: v{release['version']} @ {commit[:8]}")

    assign_channels(releases)
    releases.sort(key=sort_key, reverse=True)
    return releases


def build_app(app: dict, client: GitHubClient) -> tuple[dict | None, Log]:
    """The app's index entry plus its releases, or None when nothing is installable."""
    log: Log = []
    repo = app.get("repo")
    if not repo:
        return None, log

    if not client.repo_exists(repo, log):
        log.append("repo not found — skipping")
        return None, log

    releases = build_releases(repo, client, log)
    if not releases:
        log.append("no installable release — dropping from the registry")
        return None, log

    entry = {
        "name": app["name"],
        "title": app["title"],
        "description": app.get("description"),
        "repo": repo,
        "logo_url": app.get("logo_url"),
        "website": app.get("website"),
        "documentation": app.get("documentation"),
        "categories": app.get("categories", []),
        "category": app.get("category"),
        "stars": app.get("stars") or 0,  # the schema check requires an int
        "releases": f"apps/{app['name']}.json",
    }
    return {"entry": entry, "releases": releases}, log


def write_registry(index: list[dict], releases_by_name: dict[str, list[dict]]) -> None:
    APPS_DIR.mkdir(exist_ok=True)
    REGISTRY.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    for name, releases in releases_by_name.items():
        payload = {"name": name, "releases": releases}
        (APPS_DIR / f"{name}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    # A release file with no index entry is unreachable - pilot only ever reads
    # apps/<name>.json for a name the index lists.
    for stale in set(APPS_DIR.glob("*.json")) - {APPS_DIR / f"{name}.json" for name in releases_by_name}:
        stale.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print first 3 results without writing")
    parser.add_argument("--limit", type=int, help="Process only N apps")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Warning: GITHUB_TOKEN not set — unauthenticated (60 req/hr limit)")

    apps: list[dict] = json.loads(REGISTRY.read_text())
    to_process = apps[: args.limit] if args.limit else apps

    client = GitHubClient(token=token)
    # build_app only reads GitHubClient/network state — safe to run
    # concurrently. Every write (the dicts below, the files at the end) stays
    # on the main thread, so nothing needs a lock.
    built_by_name: dict[str, dict] = {}

    skipped = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(build_app, app, client): app for app in to_process}
        for index, future in enumerate(as_completed(futures), 1):
            app = futures[future]
            print(f"[{index}/{len(to_process)}] {app['name']}")
            try:
                built, log = future.result()
            except Exception as error:
                print(f"    unexpected error, skipping: {error}")
                skipped += 1
                continue
            for line in log:
                print(f"    {line}")
            if built is None:
                skipped += 1
            else:
                built_by_name[app["name"]] = built

    # Keep the registry's original order, and drop every app that has no
    # release — a metadata-only entry is not installable and pilot rejects it.
    index = [built_by_name[app["name"]]["entry"] for app in apps if app["name"] in built_by_name]
    releases_by_name = {name: built["releases"] for name, built in built_by_name.items()}
    total_releases = sum(len(releases) for releases in releases_by_name.values())

    if args.dry_run:
        print("\n--- sample output (first 3) ---")
        print(json.dumps(list(built_by_name.values())[:3], indent=2, ensure_ascii=False))
        print(f"\n{len(index)} apps would be kept with {total_releases} releases, {skipped} dropped")
        return

    write_registry(index, releases_by_name)
    print(f"\nWrote {len(index)} apps → {REGISTRY} and {APPS_DIR}")
    print(f"  {total_releases} releases across {len(releases_by_name)} apps")
    print(f"  {skipped} dropped (no repo, 404, or no installable release)")


if __name__ == "__main__":
    main()
