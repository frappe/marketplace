#!/usr/bin/env python3
"""
Phase two of the audit: file the reviewed reports as issues.

Only apps named on --apps are filed, and only from reports already on disk -
this never validates anything, so what gets posted is exactly what was
reviewed. Requires `gh` authenticated with issue write access on each repo.

Run:
    python3 -m tools.audit.file_issues --apps crm,hrms --dry-run
    python3 -m tools.audit.file_issues --apps crm,hrms

An app that already has an open issue with this label is left alone unless
--update is passed, which edits that issue's body instead of opening a
second one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.audit.report import ISSUE_LABEL, ISSUE_TITLE
from tools.audit.runner import AppAudit


class FilingError(Exception):
    """This app cannot be filed; the rest of the run continues."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--apps", required=True, help="comma-separated app names to file")
    parser.add_argument("--reports", type=Path, default=Path("reports"), help="where phase one wrote reports")
    parser.add_argument("--dry-run", action="store_true", help="print what would be filed, post nothing")
    parser.add_argument("--update", action="store_true", help="edit an existing open issue instead of skipping")
    parser.add_argument("--create-label", action="store_true", help=f"create {ISSUE_LABEL!r} where it is missing")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    names = [name.strip() for name in args.apps.split(",") if name.strip()]
    failures = []
    for name in names:
        try:
            file_app(name, args)
        except FilingError as exc:
            failures.append(name)
            print(f"  SKIPPED {name}: {exc}")
    if failures:
        raise SystemExit(f"\n{len(failures)} app(s) not filed: {', '.join(failures)}")


def file_app(name: str, args: argparse.Namespace) -> None:
    audit, body_path = _load(name, args.reports)
    repo = _repo_slug(audit.repo)
    print(f"\n=== {name} ({repo}) ===")

    if args.dry_run:
        _print_plan(repo, body_path)
        return

    _ensure_label(repo, create=args.create_label)
    existing = _open_issue(repo)
    if existing and not args.update:
        raise FilingError(f"issue #{existing} already open with label {ISSUE_LABEL!r}; pass --update to edit it")
    if existing:
        _gh(["issue", "edit", str(existing), "--repo", repo, "--body-file", str(body_path)])
        print(f"  updated {repo}#{existing}")
        return

    url = _gh(
        [
            "issue", "create",
            "--repo", repo,
            "--title", ISSUE_TITLE,
            "--label", ISSUE_LABEL,
            "--body-file", str(body_path),
        ]
    )
    print(f"  filed {url}")


def _load(name: str, reports: Path) -> tuple[AppAudit, Path]:
    raw_path = reports / "raw" / f"{name}.json"
    body_path = reports / f"{name}.md"
    if not raw_path.exists() or not body_path.exists():
        raise FilingError(f"no report on disk — run 'python3 -m tools.audit.run --app {name}' first")
    return AppAudit.from_dict(json.loads(raw_path.read_text())), body_path


def _print_plan(repo: str, body_path: Path) -> None:
    print(f"  would file on {repo}")
    print(f"    title: {ISSUE_TITLE}")
    print(f"    label: {ISSUE_LABEL}")
    print(f"    body:  {body_path} ({len(body_path.read_text())} chars)")


def _repo_slug(repo: str) -> str:
    """owner/name, as gh --repo wants it."""
    slug = repo.rstrip("/").removesuffix(".git").split("github.com/")[-1]
    if slug.count("/") != 1:
        raise FilingError(f"cannot derive an owner/name from {repo!r}")
    return slug


def _ensure_label(repo: str, *, create: bool) -> None:
    labels = json.loads(_gh(["label", "list", "--repo", repo, "--limit", "200", "--json", "name"]) or "[]")
    if any(label["name"] == ISSUE_LABEL for label in labels):
        return
    if not create:
        raise FilingError(f"label {ISSUE_LABEL!r} does not exist on {repo}; create it or pass --create-label")
    _gh(["label", "create", ISSUE_LABEL, "--repo", repo, "--description", "Blocks this app on Frappe v2"])
    print(f"  created label {ISSUE_LABEL!r}")


def _open_issue(repo: str) -> int | None:
    """The open issue already carrying this label, if any."""
    issues = json.loads(
        _gh(["issue", "list", "--repo", repo, "--label", ISSUE_LABEL, "--state", "open", "--json", "number"])
        or "[]"
    )
    return issues[0]["number"] if issues else None


def _gh(argv: list[str]) -> str:
    result = subprocess.run(["gh", *argv], capture_output=True, text=True)
    if result.returncode != 0:
        raise FilingError(result.stderr.strip() or f"gh {' '.join(argv[:2])} failed")
    return result.stdout.strip()


if __name__ == "__main__":
    main()
