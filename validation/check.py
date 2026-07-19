#!/usr/bin/env python3
"""
Orchestrates the marketplace app PR check: run the schema check once per
changed/new app, then find which of its targets changed (utils/diff.py)
and run semgrep and get-app checks against each, in that order, stopping
at the first failure. A schema-failed app's targets are skipped entirely.
Exits non-zero if anything fails.

Run:
    python3 validation/check.py <old-apps.json> <new-apps.json>
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from get_app_check import GetAppValidator
from schema_check import SchemaValidator
from semgrep_check import SemgrepValidator
from utils.clone import clone_app
from utils.diff import find_changed_targets, load_apps


def changed_apps(old_apps: dict[str, dict], new_apps: dict[str, dict]) -> dict[str, dict]:
    return {name: app for name, app in new_apps.items() if old_apps.get(name) != app}


def check_app_schema(name: str, app: dict) -> bool:
    """Gate a changed app's schema before any of its targets are cloned."""
    print(f"\n=== Checking {name} (schema) ===", flush=True)
    return SchemaValidator(app).run()


def check_target(target: dict) -> bool:
    print(f"\n=== Checking {target['name']} ({target.get('repo')}@{target.get('target')}) ===", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "app"
        if not _clone(target, clone_dir):
            return False
        return _run_post_clone_checks(target, clone_dir)


def _clone(target: dict, clone_dir: Path) -> bool:
    try:
        clone_app(target["repo"], target["target"], target["target_type"], clone_dir)
        return True
    except RuntimeError as exc:
        print(f"  FAIL: {exc}")
        return False


def _run_post_clone_checks(target: dict, clone_dir: Path) -> bool:
    """Run clone-dependent checks in order, stopping at the first failure."""
    repo, ref = target["repo"], target["target"]
    checks = [
        ("semgrep", SemgrepValidator(clone_dir, f"{repo}@{ref}")),
        ("get-app", GetAppValidator(target, clone_dir)),
    ]
    failed_at: str | None = None
    for name, check in checks:
        if failed_at is not None:
            print(f"\n--- {name} ---\n  SKIPPED — {failed_at} failed for this target.")
            continue
        if not check.run():
            failed_at = name
    return failed_at is None


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: validation/check.py <old-apps.json> <new-apps.json>", file=sys.stderr)
        sys.exit(1)

    marketplace = load_apps(Path(sys.argv[1]))
    new_apps = load_apps(Path(sys.argv[2]))

    apps = changed_apps(marketplace, new_apps)
    if not apps:
        print("No app code changes detected — nothing to scan.")
        return

    schema_failed = {name for name, app in apps.items() if not check_app_schema(name, app)}

    # Excluded before find_changed_targets(), not filtered after - it
    # indexes app["repo"] directly and would crash on a schema-broken app.
    valid_new_apps = {name: app for name, app in new_apps.items() if name not in schema_failed}
    changed_targets = find_changed_targets(marketplace, valid_new_apps)
    target_results = {f"{t['name']}@{t['target']}": check_target(t) for t in changed_targets}

    failed = sorted(schema_failed) + [key for key, passed in target_results.items() if not passed]
    if failed:
        print(f"\nFAILED: {', '.join(failed)} did not pass the marketplace checks.")
        sys.exit(1)

    print(f"\nAll {len(apps)} changed app(s) passed.")


if __name__ == "__main__":
    main()
