#!/usr/bin/env python3
"""
Orchestrates the marketplace app PR check: run the index and release schema
checks once per changed/new app, then find which of its releases changed
(utils/diff.py) and run semgrep and get-app checks against each, in that
order, stopping at the first failure. A schema-failed app's releases are
skipped entirely. Exits non-zero if anything fails.

Run:
    python3 validation/check.py <old-registry-dir> <new-registry-dir>
        [--report report.md] [--commit SHA] [--run-url URL]

Each registry directory holds apps.json plus apps/<name>.json.
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
from schema_check import ReleaseSchemaValidator, SchemaValidator
from semgrep_check import SemgrepValidator
from utils.clone import clone_release
from utils.diff import find_changed_releases, load_registry
from utils.report import CheckResult, Finding, Report, Section


def changed_apps(old_apps: dict[str, dict], new_apps: dict[str, dict]) -> dict[str, dict]:
    return {name: app for name, app in new_apps.items() if old_apps.get(name) != app}


def check_app_schema(name: str, app: dict, section: Section) -> bool:
    """Gate a changed app's index entry and releases before anything is cloned."""
    print(f"\n=== Checking {name} (schema) ===", flush=True)
    passed = True
    for validator in (SchemaValidator(app), ReleaseSchemaValidator(app)):
        result = validator.run()
        section.checks.append(_result(validator, result))
        passed = passed and result
    return passed


def check_release(release: dict, section: Section) -> bool:
    print(
        f"\n=== Checking {release['name']} "
        f"({release.get('repo')}@{(release.get('commit') or '')[:8]}) ===",
        flush=True,
    )

    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "app"
        if not _clone(release, clone_dir, section):
            return False
        return _run_post_clone_checks(release, clone_dir, section)


def _clone(release: dict, clone_dir: Path, section: Section) -> bool:
    try:
        clone_release(release["repo"], release["branch"], release["commit"], clone_dir)
        return True
    except RuntimeError as exc:
        print(f"  FAIL: {exc}")
        message = str(exc).replace(str(clone_dir), release["name"])
        section.checks.append(
            CheckResult(name="clone", status="failed", findings=[Finding(message=message)])
        )
        return False


def _run_post_clone_checks(release: dict, clone_dir: Path, section: Section) -> bool:
    """Run clone-dependent checks in order, stopping at the first failure."""
    repo, commit = release["repo"], release["commit"]
    checks = [
        ("semgrep", SemgrepValidator(clone_dir, f"{repo}@{commit[:8]}")),
        ("get-app", GetAppValidator(release, clone_dir)),
    ]
    failed_at: str | None = None
    for name, check in checks:
        if failed_at is not None:
            print(f"\n--- {check.name} ---\n  SKIPPED — {failed_at} failed for this release.")
            section.checks.append(CheckResult(name=check.name, status="skipped"))
            continue
        passed = check.run()
        section.checks.append(_result(check, passed))
        if not passed:
            failed_at = name
    return failed_at is None


# The registry is contributor-supplied and nothing validates these as a git
# ref or a URL, so both are escaped before going into a comment CI posts.
MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>])")
URL_SAFE = ":/?&=@$,;+~%'"


def escape_markdown(text: str) -> str:
    return MARKDOWN_SPECIALS.sub(r"\\\1", text)


def release_link(release: dict) -> str:
    """`owner/app@commit`, linked to the commit — `repo@commit` is not a URL
    and renders as a dead autolink."""
    repo = (release.get("repo") or "").rstrip("/").removesuffix(".git")
    commit = release.get("commit") or ""
    if not repo:
        return escape_markdown(commit)
    label = escape_markdown(f"{urlparse(repo).path.strip('/')}@{commit[:8]}")
    url = quote(f"{repo}/commit/{commit}", safe=URL_SAFE)
    return f"[{label}]({url})"


def release_key(release: dict) -> str:
    return f"{release['name']}@{(release.get('commit') or '')[:8]}"


def _result(validator, passed: bool) -> CheckResult:
    return CheckResult(
        name=validator.name,
        status="passed" if passed else "failed",
        findings=list(validator.findings),
        notes=list(validator.notes),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the marketplace app checks over a registry change.")
    parser.add_argument("old_registry", type=Path, help="registry directory at the base revision")
    parser.add_argument("new_registry", type=Path, help="registry directory as proposed")
    parser.add_argument("--report", type=Path, help="write a markdown report of all findings here")
    parser.add_argument("--commit", default="", help="head SHA the report describes")
    parser.add_argument("--run-url", default="", help="link back to the workflow run")
    args = parser.parse_args()

    marketplace = load_registry(args.old_registry)
    new_apps = load_registry(args.new_registry)
    report = Report(commit=args.commit, run_url=args.run_url)

    apps = changed_apps(marketplace, new_apps)
    if not apps:
        print("No app code changes detected — nothing to scan.")
        _write_report(report, args.report)
        return

    sections = {name: report.section(name) for name in apps}
    schema_failed = {name for name, app in apps.items() if not check_app_schema(name, app, sections[name])}

    # Excluded before find_changed_releases(), not filtered after - it
    # indexes app["repo"] directly and would crash on a schema-broken app.
    valid_new_apps = {name: app for name, app in new_apps.items() if name not in schema_failed}
    release_results = {}
    for release in find_changed_releases(marketplace, valid_new_apps):
        section = report.section(release_key(release), subtitle=release_link(release))
        release_results[release_key(release)] = check_release(release, section)

    _write_report(report, args.report)

    failed = sorted(schema_failed) + [key for key, passed in release_results.items() if not passed]
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
