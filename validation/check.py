#!/usr/bin/env python3
"""
Orchestrates the marketplace app PR check: find which targets changed
(utils/diff.py), then run schema, semgrep, and get-app checks against
each, in that order, stopping at the first failure. Exits non-zero if
any fail.

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


def apps_missing_targets(old_apps: dict[str, dict], new_apps: dict[str, dict]) -> list[str]:
    """New or edited apps that declare no targets — the target-driven scan can't
    see them (they produce zero targets), so they'd slip through unchecked."""
    return [
        name
        for name, app in new_apps.items()
        if old_apps.get(name) != app and not app.get("targets")
    ]


def check_target(target: dict) -> bool:
    print(f"\n=== Checking {target['name']} ({target.get('repo')}@{target.get('target')}) ===", flush=True)

    if not SchemaValidator(target).run():
        return False

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
    """Run clone-dependent checks in order; stop at the first failure and
    print a SKIPPED line for every check that didn't get to run.

    get-app installs into a throwaway venv - real work skipped once an
    earlier check already flagged the target as failing.
    """
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

    missing_targets = apps_missing_targets(marketplace, new_apps)
    if missing_targets:
        print(f"\nFAILED: {', '.join(missing_targets)} has no targets — add at least one target.")
        sys.exit(1)

    changed_targets = find_changed_targets(marketplace, new_apps)

    if not changed_targets:
        print("No app code changes detected — nothing to scan.")
        return

    results = {f"{t['name']}@{t['target']}": check_target(t) for t in changed_targets}
    failed = [key for key, passed in results.items() if not passed]

    if failed:
        print(f"\nFAILED: {', '.join(failed)} did not pass the marketplace checks.")
        sys.exit(1)

    print(f"\nAll {len(changed_targets)} changed target(s) passed.")


if __name__ == "__main__":
    main()
