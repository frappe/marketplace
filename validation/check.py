#!/usr/bin/env python3
"""
Orchestrates the marketplace app PR check: run the schema check once per
changed/new app, then find which of its targets changed (utils/diff.py)
and run semgrep and get-app checks against each, in that order, stopping
at the first failure. A schema-failed app's targets are skipped entirely.
Exits non-zero if anything fails.

Run:
    python3 validation/check.py <old-apps.json> <new-apps.json> [--report report.md]
        [--commit SHA] [--run-url URL]

--report writes a markdown summary of every finding, for CI to post on the PR.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).parent))
from get_app_check import GetAppValidator
from schema_check import SchemaValidator
from semgrep_check import SemgrepValidator
from utils.clone import clone_app
from utils.diff import find_changed_targets, load_apps
from utils.report import CheckResult, Finding, Report, Section


def changed_apps(old_apps: dict[str, dict], new_apps: dict[str, dict]) -> dict[str, dict]:
    return {name: app for name, app in new_apps.items() if old_apps.get(name) != app}


def check_app_schema(name: str, app: dict, section: Section) -> bool:
    """Gate a changed app's schema before any of its targets are cloned."""
    print(f"\n=== Checking {name} (schema) ===", flush=True)
    validator = SchemaValidator(app)
    passed = validator.run()
    section.checks.append(_result(validator, passed))
    return passed


def check_target(target: dict, section: Section) -> bool:
    print(f"\n=== Checking {target['name']} ({target.get('repo')}@{target.get('target')}) ===", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "app"
        if not _clone(target, clone_dir, section):
            return False
        return _run_post_clone_checks(target, clone_dir, section)


def _clone(target: dict, clone_dir: Path, section: Section) -> bool:
    try:
        clone_app(target["repo"], target["target"], target["target_type"], clone_dir)
        return True
    except RuntimeError as exc:
        print(f"  FAIL: {exc}")
        message = str(exc).replace(str(clone_dir), target["name"])
        section.checks.append(
            CheckResult(name="clone", status="failed", findings=[Finding(message=message)])
        )
        return False


def _run_post_clone_checks(target: dict, clone_dir: Path, section: Section) -> bool:
    """Run clone-dependent checks in order, stopping at the first failure."""
    repo, ref = target["repo"], target["target"]
    checks = [
        ("semgrep", SemgrepValidator(clone_dir, f"{repo}@{ref}")),
        ("get-app", GetAppValidator(target, clone_dir)),
    ]
    failed_at: str | None = None
    for name, check in checks:
        if failed_at is not None:
            print(f"\n--- {check.name} ---\n  SKIPPED — {failed_at} failed for this target.")
            section.checks.append(CheckResult(name=check.name, status="skipped"))
            continue
        passed = check.run()
        section.checks.append(_result(check, passed))
        if not passed:
            failed_at = name
    return failed_at is None


# apps.json is contributor-supplied and nothing validates these as a git ref or
# a URL, so both are escaped before going into a comment CI posts.
MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>])")
URL_SAFE = ":/?&=@$,;+~%'"


def escape_markdown(text: str) -> str:
    return MARKDOWN_SPECIALS.sub(r"\\\1", text)


def target_link(target: dict) -> str:
    """`owner/app@branch`, linked to the branch — `repo@branch` is not a URL and
    renders as a dead autolink."""
    repo = (target.get("repo") or "").rstrip("/").removesuffix(".git")
    ref = target.get("target", "")
    if not repo:
        return escape_markdown(ref)
    label = escape_markdown(f"{urlparse(repo).path.strip('/')}@{ref}")
    url = quote(f"{repo}/tree/{ref}", safe=URL_SAFE)
    return f"[{label}]({url})"


def _result(validator, passed: bool) -> CheckResult:
    return CheckResult(
        name=validator.name,
        status="passed" if passed else "failed",
        findings=list(validator.findings),
        notes=list(validator.notes),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the marketplace app checks over an apps.json change.")
    parser.add_argument("old_apps", type=Path, help="apps.json at the base revision")
    parser.add_argument("new_apps", type=Path, help="apps.json as proposed")
    parser.add_argument("--report", type=Path, help="write a markdown report of all findings here")
    parser.add_argument("--commit", default="", help="head SHA the report describes")
    parser.add_argument("--run-url", default="", help="link back to the workflow run")
    args = parser.parse_args()

    marketplace = load_apps(args.old_apps)
    new_apps = load_apps(args.new_apps)
    report = Report(commit=args.commit, run_url=args.run_url)

    apps = changed_apps(marketplace, new_apps)
    if not apps:
        print("No app code changes detected — nothing to scan.")
        _write_report(report, args.report)
        return

    sections = {name: report.section(name) for name in apps}
    schema_failed = {name for name, app in apps.items() if not check_app_schema(name, app, sections[name])}

    # Excluded before find_changed_targets(), not filtered after - it
    # indexes app["repo"] directly and would crash on a schema-broken app.
    valid_new_apps = {name: app for name, app in new_apps.items() if name not in schema_failed}
    changed_targets = find_changed_targets(marketplace, valid_new_apps)
    target_results = {}
    for target in changed_targets:
        section = report.section(f"{target['name']}@{target['target']}", subtitle=target_link(target))
        target_results[f"{target['name']}@{target['target']}"] = check_target(target, section)

    _write_report(report, args.report)

    failed = sorted(schema_failed) + [key for key, passed in target_results.items() if not passed]
    if failed:
        print(f"\nFAILED: {', '.join(failed)} did not pass the marketplace checks.")
        sys.exit(1)

    print(f"\nAll {len(apps)} changed app(s) passed.")


def _write_report(report: Report, path: Path | None) -> None:
    if path is None:
        return
    path.write_text(report.render())
    print(f"\nWrote report to {path}")


if __name__ == "__main__":
    main()
