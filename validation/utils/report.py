#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass, field

# Semgrep's severity words, mapped to the labels the marketplace reports in.
SEVERITY_LABELS = {
    "CRITICAL": "Critical",
    "ERROR": "Critical",
    "HIGH": "Major",
    "WARNING": "Minor",
    "MEDIUM": "Minor",
    "LOW": "Info",
    "INFO": "Info",
}
SEVERITY_ORDER = ["Critical", "Major", "Minor", "Info"]
SEVERITY_ICONS = {"Critical": "🔴", "Major": "🟠", "Minor": "🟡", "Info": "🔵"}

COMMENT_MARKER = "<!-- marketplace-app-check"  # CI greps this to find its own comment
MAX_REPORT_CHARS = 60000  # GitHub rejects comment bodies over 65536
MESSAGE_INLINE_CHARS = 200  # longer than this reads better as a block


def severity_label(semgrep_severity: str) -> str:
    return SEVERITY_LABELS.get(str(semgrep_severity).upper(), "Info")


@dataclass
class Finding:
    """One thing wrong with an app, from any of the checks."""

    message: str
    severity: str = "Critical"
    rule: str = ""
    path: str = ""
    line: int | None = None

    @property
    def location(self) -> str:
        if not self.path:
            return ""
        return f"{self.path}:{self.line}" if self.line else self.path


@dataclass
class CheckResult:
    name: str
    status: str  # passed | failed | skipped
    findings: list[Finding] = field(default_factory=list)
    notes: list[Finding] = field(default_factory=list)  # non-blocking


@dataclass
class Section:
    """One app, or one of its targets, and how each check went for it."""

    title: str
    subtitle: str = ""
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.status != "failed" for check in self.checks)


STATUS_ICONS = {"passed": "✅", "failed": "❌", "skipped": "⏭️"}


class Report:
    """Findings from every check, rendered as markdown for the PR."""

    def __init__(self, commit: str = "") -> None:
        self.sections: list[Section] = []
        self.commit = commit  # named in the report, so a stale comment is visible

    def section(self, title: str, subtitle: str = "") -> Section:
        section = Section(title=title, subtitle=subtitle)
        self.sections.append(section)
        return section

    @property
    def passed(self) -> bool:
        return all(section.passed for section in self.sections)

    @property
    def _marker(self) -> str:
        """Names the commit, so CI can refuse to overwrite a newer report."""
        return f"{COMMENT_MARKER} {self.commit} -->" if self.commit else f"{COMMENT_MARKER} -->"

    @property
    def _commit_line(self) -> str:
        return f"<sub>Checked commit `{self.commit[:7]}`.</sub>\n" if self.commit else ""

    def render(self) -> str:
        if not self.sections:
            return (
                f"{self._marker}\n## Marketplace app check\n\n"
                f"No app changes to check.\n{self._commit_line}"
            )

        lines = [self._marker, "## Marketplace app check", ""]
        if self.commit:
            lines += [self._commit_line.rstrip("\n"), ""]
        lines += self._render_overview()
        for section in self.sections:
            lines += self._render_section(section)
        lines += [
            "",
            "---",
            "<sub>Re-run these checks by pushing to the branch. See "
            "[what CI checks](https://github.com/frappe/marketplace/blob/main/README.md#what-ci-checks) "
            "for what each one does.</sub>",
        ]
        return self._cap("\n".join(lines) + "\n")

    @staticmethod
    def _cap(body: str) -> str:
        if len(body) <= MAX_REPORT_CHARS:
            return body
        notice = "\n\n> Report truncated — see the job log for the full list of findings.\n"
        return body[: MAX_REPORT_CHARS - len(notice)] + notice

    def _render_overview(self) -> list[str]:
        verdict = "All checks passed." if self.passed else "Some checks failed — details below."
        lines = [verdict, "", "| App | Check | Result |", "| --- | --- | --- |"]
        for section in self.sections:
            for check in section.checks:
                icon = STATUS_ICONS.get(check.status, "")
                lines.append(f"| `{section.title}` | {check.name} | {icon} {self._result_text(check)} |")
        lines.append("")
        return lines

    @staticmethod
    def _result_text(check: CheckResult) -> str:
        if check.status == "skipped":
            return "skipped"
        if check.status == "passed":
            return "passed" if not check.notes else f"passed ({len(check.notes)} advisory)"
        return f"{len(check.findings)} blocking issue(s)"

    def _render_section(self, section: Section) -> list[str]:
        if section.passed and not any(check.notes for check in section.checks):
            return []

        heading = f"### {section.title}"
        lines = ["", heading]
        if section.subtitle:
            lines.append(f"<sub>{section.subtitle}</sub>")

        for check in section.checks:
            if check.findings:
                lines += ["", f"#### {check.name} — {len(check.findings)} blocking"]
                lines += self._render_rule_summary(check.findings)
                lines += self._render_findings(check.findings)
            if check.notes:
                lines += ["", "<details>", f"<summary>{len(check.notes)} advisory finding(s) "
                          f"from {check.name} — not blocking</summary>", ""]
                lines += self._render_findings(check.notes)
                lines += ["", "</details>"]
        return lines

    @staticmethod
    def _render_rule_summary(findings: list[Finding]) -> list[str]:
        """Rule-level counts, so the scale is clear before the detail."""
        counts: dict[tuple[str, str], int] = {}
        for finding in findings:
            if finding.rule:
                counts[(finding.rule, finding.severity)] = counts.get((finding.rule, finding.severity), 0) + 1
        if not counts:
            return []
        rows = sorted(
            counts.items(),
            key=lambda item: (SEVERITY_ORDER.index(item[0][1]) if item[0][1] in SEVERITY_ORDER else 99,
                              -item[1]),
        )
        lines = ["", "| Rule | Severity | Count |", "| --- | --- | --- |"]
        for (rule, severity), count in rows:
            lines.append(f"| `{rule}` | {SEVERITY_ICONS.get(severity, '')} {severity} | {count} |")
        return lines

    def _render_findings(self, findings: list[Finding]) -> list[str]:
        lines: list[str] = []
        for (rule, severity, message), group in self._group(findings):
            icon = SEVERITY_ICONS.get(severity, "")
            # Tool output (a failed clone, a failed install) runs to several
            # lines and would break the markdown if inlined in the heading.
            if "\n" in message or len(message) > MESSAGE_INLINE_CHARS:
                lines += ["", f"{icon} **{severity}**", "", "```", message.strip(), "```"]
            else:
                lines += ["", f"{icon} **{severity}** — {message}"]
            if rule:
                lines.append(f"<sub>rule: `{rule}`</sub>")
            locations = [finding.location for finding in group if finding.location]
            lines += self._render_locations(locations)
        return lines

    @staticmethod
    def _render_locations(locations: list[str]) -> list[str]:
        if not locations:
            return []
        if len(locations) <= 5:
            return [""] + [f"- `{location}`" for location in locations]
        listed = "\n".join(f"- `{location}`" for location in locations)
        return ["", "<details>", f"<summary>{len(locations)} locations</summary>", "", listed, "", "</details>"]

    @staticmethod
    def _group(findings: list[Finding]):
        """Group by (rule, severity, message), preserving worst-severity-first order."""
        grouped: dict[tuple[str, str, str], list[Finding]] = {}
        for finding in findings:
            grouped.setdefault((finding.rule, finding.severity, finding.message), []).append(finding)
        return sorted(
            grouped.items(),
            key=lambda item: (SEVERITY_ORDER.index(item[0][1]) if item[0][1] in SEVERITY_ORDER else 99,
                              -len(item[1])),
        )
