#!/usr/bin/env python3
"""
Clone a marketplace app's repo and run it through validation/semgrep-rules/.
Exits non-zero if any finding is blocking, so CI can fail the PR.

Blocking logic: a finding blocks if its rule metadata sets is_blocking: true,
or its Semgrep severity maps to Critical/Major (ERROR/CRITICAL/HIGH).
With a baseline clone, only findings the baseline does not have are reported.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils.base import Validator
from utils.report import severity_label

RULES_DIR = Path(__file__).parent / "semgrep-rules"

BLOCKING_AUDIT_SEVERITIES = {"Critical", "Major"}


def scan_target(target_dir: Path) -> list[dict]:
    """Findings with repo-relative paths, so two clones' findings compare."""
    result = subprocess.run(
        ["semgrep", "scan", "--config", str(RULES_DIR), "--json", "--quiet", "."],
        capture_output=True,
        text=True,
        cwd=target_dir,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Semgrep failed: {result.stderr.strip()}")
    return json.loads(result.stdout)["results"]


def finding_key(finding: dict, source_dir: Path) -> tuple:
    """Rule, file and matched code, so a finding survives unrelated lines moving."""
    start, end = finding["start"]["offset"], finding["end"]["offset"]
    code = (source_dir / finding["path"]).read_bytes()[start:end]
    return finding.get("check_id"), finding["path"], b" ".join(code.split())


def new_findings(findings: list[dict], clone_dir: Path, baseline_dir: Path) -> list[dict]:
    """Findings the baseline lacks; each baseline match excuses one identical finding."""
    known = Counter(finding_key(finding, baseline_dir) for finding in scan_target(baseline_dir))
    new = []
    for finding in findings:
        key = finding_key(finding, clone_dir)
        if known[key]:
            known[key] -= 1
        else:
            new.append(finding)
    return new


def is_blocking(finding: dict) -> bool:
    metadata = finding.get("extra", {}).get("metadata", {})
    if metadata.get("is_blocking") is True:
        return True
    return severity_label(finding.get("extra", {}).get("severity", "INFO")) in BLOCKING_AUDIT_SEVERITIES


def rule_name(check_id: str) -> str:
    """Semgrep reports the rule's full dotted path; only the last part names it."""
    return check_id.rsplit(".", 1)[-1]


class SemgrepValidator(Validator):
    name = "semgrep scan"

    def __init__(self, clone_dir: Path, label: str, baseline_dir: Path | None = None):
        super().__init__()
        self.clone_dir = clone_dir
        self.label = label
        self.baseline_dir = baseline_dir
        if baseline_dir:
            self.name = "semgrep scan (new findings only)"

    def validate(self) -> None:
        try:
            findings = scan_target(self.clone_dir)
            if self.baseline_dir:
                findings = new_findings(findings, self.clone_dir, self.baseline_dir)
        except (RuntimeError, json.JSONDecodeError, KeyError, OSError) as exc:
            # A crashed scan is a failed check, not a crashed run: the
            # get-app check after it must still report.
            self.fail(f"semgrep scan crashed: {exc}")
            return
        blocking = [f for f in findings if is_blocking(f)]
        print(f"  Scanned {self.label}: {len(findings)} finding(s), {len(blocking)} blocking.")
        for finding in findings:
            record = self.fail if is_blocking(finding) else self.note
            record(**self._describe_finding(finding))

    def _describe_finding(self, finding: dict) -> dict:
        extra = finding.get("extra", {})
        return {
            "message": " ".join(extra.get("message", "").split()),
            "severity": severity_label(extra.get("severity", "INFO")),
            "rule": rule_name(finding.get("check_id", "")),
            "path": finding["path"],
            "line": finding.get("start", {}).get("line"),
        }
