#!/usr/bin/env python3
"""
Base class for marketplace app validators.

Each validator collects failures via fail() while validate() runs, then
run() reports an overall pass/fail. Subclasses take only the inputs they
need and implement validate(). Findings are structured so the PR report
can group and label them.
"""

from __future__ import annotations

from utils.report import Finding


class Validator:
    name = "validation"

    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.notes: list[Finding] = []

    @property
    def errors(self) -> list[Finding]:
        return self.findings

    def fail(self, message: str, **details) -> None:
        """Record a blocking finding; `details` matches Finding's fields."""
        finding = Finding(message=message, **details)
        self.findings.append(finding)
        print(f"  FAIL: {self._describe(finding)}")

    def note(self, message: str, **details) -> None:
        """Record an advisory finding; reported, but the check still passes."""
        finding = Finding(message=message, **details)
        self.notes.append(finding)
        print(f"  NOTE: {self._describe(finding)}")

    @staticmethod
    def _describe(finding: Finding) -> str:
        where = f" {finding.location}" if finding.location else ""
        rule = f" ({finding.rule})" if finding.rule else ""
        return f"[{finding.severity}]{where} {finding.message}{rule}"

    def validate(self) -> None:
        raise NotImplementedError

    def run(self) -> bool:
        print(f"\n--- {self.name} ---", flush=True)
        self.validate()
        if self.findings:
            print(f"  {len(self.findings)} issue(s) found.")
        else:
            print("  PASSED." if not self.notes else f"  PASSED ({len(self.notes)} advisory).")
        return not self.findings
