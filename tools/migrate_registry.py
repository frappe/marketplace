#!/usr/bin/env python3
"""
Refresh apps.json's targets from each app's pyproject.toml on GitHub.

Run periodically against this repo: for every app, fetches pyproject.toml
across the known Frappe branches and rebuilds its targets array (version,
frappe_core compatibility range, sibling app dependencies). Rewrites
apps.json in place.

Usage:
    GITHUB_TOKEN=ghp_... python3 tools/migrate_registry.py
    GITHUB_TOKEN=ghp_... python3 tools/migrate_registry.py --limit 5 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from packaging.version import Version, InvalidVersion

REGISTRY = Path(__file__).parent.parent / "apps.json"
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


def parse_target(toml: dict, branch: str, dynamic_version: str | None = None) -> dict | None:
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
        "target_type": "branch",
        "target": branch,
        "frappe_core": frappe_core,
        "dependencies": dependencies,
    }


def sort_key(target: dict) -> Version:
    try:
        return Version(target["version"])
    except InvalidVersion:
        return Version("0")


def build_targets(repo_url: str, client: GitHubClient, log: Log) -> list[dict]:
    targets = []

    for branch in PILOT_BRANCHES:
        toml = client.fetch_pyproject(repo_url, branch, log)
        if toml is None:
            log.append(f"{branch}: not found")
            continue

        dynamic_version = None
        if _is_dynamic_version(toml):
            app_name = toml.get("project", {}).get("name", "")
            dynamic_version = client.fetch_dynamic_version(repo_url, branch, app_name, log)

        target = parse_target(toml, branch, dynamic_version)
        if target is None:
            log.append(f"{branch}: no version field")
            continue
        if not target["frappe_core"]:
            log.append(f"{branch}: no frappe declared in [tool.bench.frappe-dependencies] — skipping")
            continue
        targets.append(target)
        log.append(f"{branch}: v{target['version']}")

    targets.sort(key=sort_key, reverse=True)
    return targets


def refresh_app(app: dict, client: GitHubClient) -> tuple[dict | None, Log]:
    log: Log = []
    repo = app.get("repo")
    if not repo:
        return None, log

    if not client.repo_exists(repo, log):
        log.append("repo not found — skipping")
        return None, log

    targets = build_targets(repo, client, log)

    refreshed = {
        "name": app["name"],
        "title": app["title"],
        "description": app.get("description"),
        "repo": repo,
        "logo_url": app.get("logo_url"),
        "website": app.get("website"),
        "documentation": app.get("documentation"),
        "categories": app.get("categories", []),
        "category": app.get("category"),
        "stars": app.get("stars"),
        "targets": targets,
    }
    return refreshed, log


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
    process_names = {app["name"] for app in to_process}

    client = GitHubClient(token=token)
    # refresh_app only reads GitHubClient/network state — safe to run
    # concurrently. Every write (the dict below, the file at the end) stays
    # on the main thread, so nothing needs a lock.
    refreshed_by_name: dict[str, dict] = {}

    skipped = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(refresh_app, app, client): app for app in to_process}
        for index, future in enumerate(as_completed(futures), 1):
            app = futures[future]
            print(f"[{index}/{len(to_process)}] {app['name']}")
            try:
                refreshed, log = future.result()
            except Exception as error:
                print(f"    unexpected error, skipping: {error}")
                skipped += 1
                continue
            for line in log:
                print(f"    {line}")
            if refreshed is None:
                skipped += 1
            else:
                refreshed_by_name[app["name"]] = refreshed

    # Apps outside --limit's scope are carried over untouched, in their
    # original position — input and output are the same file, so a
    # partial/test run must never drop or reorder the rest of the registry.
    result = [
        refreshed_by_name[app["name"]] if app["name"] in process_names else app
        for app in apps
        if app["name"] not in process_names or app["name"] in refreshed_by_name
    ]

    with_targets = sum(1 for a in refreshed_by_name.values() if a["targets"])

    if args.dry_run:
        print("\n--- sample output (first 3) ---")
        print(json.dumps(list(refreshed_by_name.values())[:3], indent=2, ensure_ascii=False))
        print(f"\n{with_targets}/{len(refreshed_by_name)} apps would have targets, {skipped} would be removed")
        return

    REGISTRY.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"\nWrote {len(result)} apps → {REGISTRY}")
    print(f"  {with_targets} with targets, {len(refreshed_by_name) - with_targets} without targets")
    print(f"  {skipped} removed (no repo or 404)")


if __name__ == "__main__":
    main()
