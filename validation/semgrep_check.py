#!/usr/bin/env python3
"""
Clone a marketplace app's repo and run it through validation/semgrep-rules/.
Exits non-zero if any finding is blocking, so CI can fail the PR.

Blocking logic: a finding blocks if its rule metadata sets is_blocking: true,
or its Semgrep severity maps to Critical/Major (ERROR/CRITICAL/HIGH).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils.base import Validator
from utils.report import severity_label

RULES_DIR = Path(__file__).parent / "semgrep-rules"

BLOCKING_AUDIT_SEVERITIES = {"Critical", "Major"}


def scan_target(target_dir: Path) -> list[dict]:
    result = subprocess.run(
        ["semgrep", "scan", "--config", str(RULES_DIR), "--json", "--quiet", str(target_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Semgrep failed: {result.stderr.strip()}")
    return json.loads(result.stdout)["results"]


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

    def __init__(self, clone_dir: Path, label: str):
        super().__init__()
        self.clone_dir = clone_dir
        self.label = label

    def validate(self) -> None:
        findings = scan_target(self.clone_dir)
        blocking = [f for f in findings if is_blocking(f)]
        print(f"  Scanned {self.label}: {len(findings)} finding(s), {len(blocking)} blocking.")
        for finding in findings:
            record = self.fail if is_blocking(finding) else self.note
            record(**self._describe_finding(finding))

    def _describe_finding(self, finding: dict) -> dict:
        extra = finding.get("extra", {})
        # Absolute inside the throwaway clone dir; report them repo-relative.
        path = str(Path(finding["path"]).relative_to(self.clone_dir))
        return {
            "message": " ".join(extra.get("message", "").split()),
            "severity": severity_label(extra.get("severity", "INFO")),
            "rule": rule_name(finding.get("check_id", "")),
            "path": path,
            "line": finding.get("start", {}).get("line"),
        }
