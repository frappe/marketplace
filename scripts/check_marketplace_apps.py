#!/usr/bin/env python3
"""
Orchestrates the marketplace app PR check: find which targets changed
(diff_marketplace_apps.py), clone each once, then run semgrep and a real
get-app install against the clone. Exits non-zero if either fails.

Run:
    python3 scripts/check_marketplace_apps.py <old-apps.json> <new-apps.json>
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from clone_utils import clone_app
from diff_marketplace_apps import find_changed_targets, load_apps
from run_get_app_validation import GetAppValidator
from run_semgrep_validations import SemgrepValidator
from validate_registry_schema import SchemaValidator


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

    repo, ref, target_type = target["repo"], target["target"], target["target_type"]
    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "app"
        try:
            clone_app(repo, ref, target_type, clone_dir)
        except RuntimeError as exc:
            print(f"  FAIL: {exc}")
            return False

        semgrep_passed = SemgrepValidator(clone_dir, f"{repo}@{ref}").run()

        # A get-app install (uv pip install into a throwaway venv) is real
        # work — skip it for a target semgrep already flagged as insecure.
        if not semgrep_passed:
            print("\n--- get-app validator ---\n  SKIPPED — semgrep failed for this target.")
            return False

        return GetAppValidator(target, clone_dir).run()


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: check_marketplace_apps.py <old-apps.json> <new-apps.json>", file=sys.stderr)
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
