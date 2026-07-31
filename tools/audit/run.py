#!/usr/bin/env python3
"""
Phase one of the audit: validate published releases and write reports.

Runs pilot's install-time checks against every release the selected apps
advertise, and writes one report per app for a human to read before anything
is filed anywhere. Nothing here touches GitHub beyond cloning.

Run (from the registry root, with pilot importable):
    python3 -m tools.audit.run --first-party
    python3 -m tools.audit.run --app crm --app hrms
    python3 -m tools.audit.run --all

Results land in reports/<app>.md, with the raw findings in
reports/raw/<app>.json so the report can be re-rendered, and issues filed,
without validating again. Apps already audited are skipped unless --refresh
is passed, so an interrupted sweep resumes where it stopped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.audit.registry import UnknownAppError, load_registry, select_apps
from tools.audit.report import render
from tools.audit.runner import AppAudit, Auditor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--registry", type=Path, default=Path.cwd(), help="registry root (apps.json lives here)")
    parser.add_argument("--app", action="append", default=[], help="audit this app; repeatable")
    parser.add_argument("--first-party", action="store_true", help="audit every github.com/frappe app")
    parser.add_argument("--all", action="store_true", help="audit every app in the registry")
    parser.add_argument("--reports", type=Path, default=Path("reports"), help="where reports are written")
    parser.add_argument("--cache", type=Path, default=Path(".audit-cache"), help="clone and environment cache")
    parser.add_argument("--refresh", action="store_true", help="re-audit apps that already have results")
    parser.add_argument(
        "--no-environment",
        action="store_true",
        help="skip building a Frappe environment (faster; dependency resolution is not exercised)",
    )
    args = parser.parse_args()
    if not (args.app or args.first_party or args.all):
        parser.error("choose what to audit: --app NAME, --first-party, or --all")
    return args


def main() -> None:
    args = parse_args()
    apps = load_registry(args.registry)
    try:
        selected = select_apps(apps, names=args.app, first_party=args.first_party)
    except UnknownAppError as exc:
        raise SystemExit(f"error: {exc}") from exc

    raw_directory = args.reports / "raw"
    raw_directory.mkdir(parents=True, exist_ok=True)
    auditor = Auditor(apps, args.cache, build_environment=not args.no_environment)
    registry_commit = _registry_commit(args.registry)

    audits = []
    for app in selected:
        raw_path = raw_directory / f"{app['name']}.json"
        if raw_path.exists() and not args.refresh:
            print(f"\n### {app['name']} — already audited, skipping (--refresh to redo)")
            audits.append(AppAudit.from_dict(json.loads(raw_path.read_text())))
            continue
        print(f"\n### {app['name']} ({app['repo']})")
        audit = auditor.audit_app(app)
        raw_path.write_text(json.dumps(audit.to_dict(), indent=2) + "\n")
        _write_report(audit, args.reports, registry_commit)
        audits.append(audit)

    _print_summary(audits, args.reports)


def _write_report(audit: AppAudit, reports: Path, registry_commit: str) -> None:
    path = reports / f"{audit.name}.md"
    path.write_text(render(audit, registry_commit=registry_commit))
    print(f"  wrote {path}")


def _registry_commit(registry: Path) -> str:
    """The registry revision the audited release list came from."""
    result = subprocess.run(
        ["git", "-C", str(registry), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _print_summary(audits: list[AppAudit], reports: Path) -> None:
    with_findings = [audit for audit in audits if not audit.is_clean]
    print(f"\n\nAudited {len(audits)} app(s). {len(with_findings)} have findings:\n")
    for audit in with_findings:
        failures = sum(len(release.failures) for release in audit.releases)
        unchecked = sum(1 for release in audit.releases if release.clone_error)
        detail = f"{failures} failed check(s) across {len(audit.releases)} release(s)"
        if unchecked:
            detail += f", {unchecked} release(s) could not be checked at all"
        print(f"  {audit.name:30} {detail}")
    if with_findings:
        print(f"\nReview {reports}/<app>.md, then file with:")
        print(f"  python3 -m tools.audit.file_issues --apps {','.join(a.name for a in with_findings)}")


if __name__ == "__main__":
    main()
